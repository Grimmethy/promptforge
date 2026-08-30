#!/usr/bin/env python3
"""One-off: split explicit entries out of the tracked data/*.yaml pools into the
gitignored data/nsfw/ overlay the server merges only while the NSFW toggle is on.

Idempotent — re-running only moves whatever is still in the base files. Reuses the
same comment-preserving YAML surgery (core/yaml_insert) the dashboard edit boxes
use, so the tracked files keep their comments.

    python scripts/split_nsfw.py
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

from core import data, yaml_insert  # noqa: E402

DATA = data.DATA_DIR
NSFW = data.NSFW_DIR

# flat pools: entry keys to relocate wholesale (base -> data/nsfw/<pool>.yaml)
FLAT_MOVES = {
    "positions": ["from behind hold", "doggystyle", "missionary", "cowgirl",
                  "restrained by partner"],
    "poses": ["all fours presenting", "arms bound overhead", "hogtied"],
}

# expressions: the base `aroused` stays (tame); the overlay carries an explicit
# override that wins only while NSFW is on. The explicit value is NOT hard-coded
# here (this file is tracked / public) — it is recovered from the agent-manager
# auto-stash, or left for the user to type into data/nsfw/expressions.yaml.
EXPR_OVERLAY_KEYS = ["aroused"]


def _explicit_from_stash(key):
    """Best-effort: pull `<key>: <value>` for an expression out of any git stash."""
    try:
        stashes = subprocess.run(["git", "stash", "list"], cwd=ROOT,
                                 capture_output=True, text=True).stdout
    except OSError:
        return None
    for i in range(stashes.count("\n") + 1):
        diff = subprocess.run(["git", "stash", "show", "-p", f"stash@{{{i}}}"],
                              cwd=ROOT, capture_output=True, text=True).stdout
        for line in diff.splitlines():
            if line.startswith("+") and line[1:].strip().startswith(key + ":"):
                return line[1:].strip().split(":", 1)[1].strip()
    return None

# outfits: (parent-key path, key) subtrees to relocate
OUTFIT_MOVES = [
    (["female"], "bare"), (["female"], "bdsm"),
    (["male"], "bare"), (["male", "fantasy"], "bdsm"),
    (["androgynous"], "bare"),
]

# ControlNet pose library files (+ their keypoints json) to relocate
CN_MOVES = ["cowgirl", "doggystyle", "kneeling_before", "missionary",
            "standing_from_behind", "arms_bound_overhead", "laying_on_back"]
# laying_on_back also has a working-tree explicit edit; its tracked copy is
# restored to the committed SFW version after the move.
CN_RESTORE_SFW = ["laying_on_back"]


def _clean(val):
    """Trim whitespace from a folded-scalar value (str, or {pos,neg} dict)."""
    if isinstance(val, dict):
        return {k: (v.strip() if isinstance(v, str) else v) for k, v in val.items()}
    return val.strip() if isinstance(val, str) else val


def _entries(path):
    return data._load_yaml(path).get("entries") or {}


def _has(path, parent, key):
    node = data._load_yaml(path)
    for seg in list(parent) + [key]:
        if not isinstance(node, dict) or seg not in node:
            return False
        node = node[seg]
    return True


def _resolve(tree, path):
    node = tree
    for seg in path:
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node


def move_flat():
    for pool, keys in FLAT_MOVES.items():
        base = os.path.join(DATA, pool + ".yaml")
        over = os.path.join(NSFW, pool + ".yaml")
        src = _entries(base)
        for k in keys:
            if k not in src:
                continue
            if not _has(over, ["entries"], k):
                data._ensure_overlay(over, pool)
                yaml_insert.insert_entry(over, ["entries"], k, _clean(src[k]))
                print(f"  + nsfw/{pool}.yaml :: {k}")
            yaml_insert.delete_entry(base, ["entries"], k)
            print(f"  - {pool}.yaml :: {k}")


def expr_overlay():
    over = os.path.join(NSFW, "expressions.yaml")
    data._ensure_overlay(over, "expressions")
    for k in EXPR_OVERLAY_KEYS:
        if _has(over, ["entries"], k):
            print(f"  = nsfw/expressions.yaml :: {k} already set, leaving as-is")
            continue
        v = _explicit_from_stash(k)
        if not v:
            print(f"  ! nsfw/expressions.yaml :: {k} — no stashed value found; "
                  f"add it by hand to data/nsfw/expressions.yaml")
            continue
        yaml_insert.insert_entry(over, ["entries"], k, v)
        print(f"  ~ nsfw/expressions.yaml :: {k} (explicit override, from stash)")


def move_outfits():
    base = os.path.join(DATA, "outfits.yaml")
    over = os.path.join(NSFW, "outfits.yaml")
    for parent, key in OUTFIT_MOVES:
        node = _resolve(data._load_yaml(base), parent + [key])
        if node is None:
            continue
        if not _has(over, parent, key):
            data._ensure_overlay(over, "outfits")
            yaml_insert.insert_entry(over, parent, key, node)
            print(f"  + nsfw/outfits.yaml :: {'/'.join(parent + [key])}")
        yaml_insert.delete_entry(base, parent, key)
        print(f"  - outfits.yaml :: {'/'.join(parent + [key])}")


def move_cn():
    os.makedirs(data.NSFW_CN_KP_DIR, exist_ok=True)
    for name in CN_MOVES:
        moved = False
        for ext in (".yaml", ".yml"):
            s = os.path.join(data.CN_DIR, name + ext)
            d = os.path.join(data.NSFW_CN_DIR, name + ext)
            if not os.path.isfile(s):
                continue
            if os.path.exists(d):
                print(f"  = nsfw/cn_positions/{name}{ext} already present, skipping")
                continue
            skel = data._load_yaml(s).get("skeleton")
            shutil.move(s, d)
            print(f"  -> nsfw/cn_positions/{name}{ext}")
            moved = True
            if skel:
                sk = os.path.join(data.CN_KP_DIR, skel)
                dk = os.path.join(data.NSFW_CN_KP_DIR, skel)
                if os.path.isfile(sk) and not os.path.exists(dk):
                    shutil.move(sk, dk)
                    print(f"  -> nsfw/cn_positions/keypoints/{skel}")
        if not moved:
            continue
        if name in CN_RESTORE_SFW:
            paths = [f"data/cn_positions/{name}.yaml",
                     f"data/cn_positions/keypoints/{name}.json"]
            subprocess.run(["git", "checkout", "--", *paths], cwd=ROOT, check=False)
            print(f"  restored SFW {name} in the tracked tree")


def main():
    print("flat pools:")
    move_flat()
    print("expressions overlay:")
    expr_overlay()
    print("outfits:")
    move_outfits()
    print("cn_positions:")
    move_cn()
    print("\ndone. `git status`: base data/*.yaml scrubbed, data/nsfw/ ignored.")


if __name__ == "__main__":
    main()
