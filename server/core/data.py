"""Preset data loading + keyword helpers.

Ported from ComfyUI-PromptForge/promptforge/data.py. All presets are YAML under
``<repo>/data`` (override with ``PROMPTFORGE_DATA_DIR``). No mtime cache here —
the files are tiny and the server reloads them per request.
"""

import json
import os
import re

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
DATA_DIR = os.environ.get("PROMPTFORGE_DATA_DIR") or os.path.join(_REPO, "data")
CHAR_DIR = os.path.join(DATA_DIR, "characters")
RAND_DIR = os.path.join(DATA_DIR, "randomizer")
CN_DIR = os.path.join(DATA_DIR, "cn_positions")
CN_KP_DIR = os.path.join(CN_DIR, "keypoints")

CATEGORIES = ("poses", "positions", "styles", "backgrounds", "supporting",
              "expressions", "colors")


# --------------------------------------------------------------------------- io

def _load_yaml(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        print(f"[promptforge] failed to parse {path}: {exc}")
        return {}


# ------------------------------------------------------------------- keywords

def normalize_kw(*parts):
    """Join fragments into one clean comma-separated tag string.

    Collapses whitespace/newlines, trims stray commas, drops empty and duplicate
    tokens (first occurrence wins, case-insensitive).
    """
    merged = ", ".join(p for p in parts if p and str(p).strip())
    merged = merged.replace("\n", " ").replace("\r", " ")
    merged = re.sub(r"\s+", " ", merged)
    out, seen = [], set()
    for tok in merged.split(","):
        tok = tok.strip().strip(",").strip()
        if not tok:
            continue
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
    return ", ".join(out)


def dedupe_across(text):
    """Drop repeated comma tokens across an already-built prompt, keeping BREAKs."""
    seen = set()
    out_lines = []
    for line in text.split("\n"):
        if line.strip() == "BREAK":
            out_lines.append(line)
            continue
        kept = []
        for tok in line.split(","):
            t = tok.strip()
            if not t:
                continue
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            kept.append(t)
        out_lines.append(", ".join(kept))
    return "\n".join(l for l in out_lines if l.strip())


def apply_weight(text, weight):
    """Wrap a whole segment in A1111 weight syntax when weight != 1.0."""
    if not text:
        return ""
    try:
        w = round(float(weight), 3)
    except (TypeError, ValueError):
        return text
    if abs(w - 1.0) < 1e-3:
        return text
    return f"({text}:{w})"


# ------------------------------------------------------------- characters

def list_characters():
    try:
        names = [
            os.path.splitext(f)[0]
            for f in os.listdir(CHAR_DIR)
            if f.endswith((".yaml", ".yml")) and not f.startswith("_")
        ]
    except OSError:
        names = []
    return sorted(names)


def _char_path(name):
    for ext in (".yaml", ".yml"):
        p = os.path.join(CHAR_DIR, name + ext)
        if os.path.isfile(p):
            return p
    return os.path.join(CHAR_DIR, name + ".yaml")


def get_character(name):
    return _load_yaml(_char_path(name))


def raw_character(name):
    """Return the character YAML file's exact text (for the preset editor)."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.splitext(str(name))[0]).strip("_")
    if not safe:
        raise ValueError("empty name")
    path = _char_path(safe)
    if not os.path.isfile(path):
        raise FileNotFoundError(name)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def save_character(filename, spec_yaml):
    """Write a character YAML. Returns the path. Guards against path escape."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.splitext(str(filename))[0]).strip("_")
    if not safe:
        raise ValueError("empty filename")
    os.makedirs(CHAR_DIR, exist_ok=True)
    path = os.path.abspath(os.path.join(CHAR_DIR, safe + ".yaml"))
    if os.path.commonpath([path, os.path.abspath(CHAR_DIR)]) != os.path.abspath(CHAR_DIR):
        raise ValueError("path escapes character dir")
    parsed = yaml.safe_load(spec_yaml)
    if not isinstance(parsed, dict) or "base" not in parsed:
        raise ValueError("spec_yaml must be a mapping with at least a 'base:' key")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(spec_yaml.rstrip() + "\n")
    return path


# --------------------------------------------------------------- categories

def _cat_path(category):
    return os.path.join(DATA_DIR, category + ".yaml")


def list_entries(category):
    entries = _load_yaml(_cat_path(category)).get("entries") or {}
    keys = list(entries.keys())
    if "none" in keys:
        keys.remove("none")
        keys.insert(0, "none")
    return keys or ["none"]


def get_entry(category, key):
    """Return (positive, negative) for a category entry."""
    entries = _load_yaml(_cat_path(category)).get("entries") or {}
    val = entries.get(key, "")
    if isinstance(val, dict):
        return str(val.get("pos", "")), str(val.get("neg", ""))
    return str(val or ""), ""


# ------------------------------------------------------------------- outfits

# Shared outfit pool: a nested gender -> genre -> subgenre -> key -> fragment
# tree in data/outfits.yaml. Fragments may carry {base}/{contrast}/{detail}
# placeholders for the per-character clothing-colour slots (see fill_outfit).
_OUTFIT_PLACEHOLDER = re.compile(r"\{(base|contrast|detail)\}")


def load_outfit_tree():
    """Raw nested outfit dict. Reloaded per request like the rest of the module."""
    return _load_yaml(os.path.join(DATA_DIR, "outfits.yaml"))


def outfit_fragment(sel):
    """sel: {'gender','genre','subgenre','key'} or a 4-item list.

    Return the leaf fragment string, or "" for any incomplete or unknown path.
    """
    if isinstance(sel, dict):
        path = [sel.get("gender"), sel.get("genre"),
                sel.get("subgenre"), sel.get("key")]
    else:
        path = list(sel or [])
    if len(path) != 4 or not all(path):
        return ""
    node = load_outfit_tree()
    for step in path:
        if not isinstance(node, dict) or step not in node:
            return ""
        node = node[step]
    return str(node or "")


def fill_outfit(template, colors):
    """Substitute {base|contrast|detail} in an outfit fragment from `colors`.

    Missing / empty slots collapse to "". A regex sub (not str.format) so literal
    punctuation and stray braces in a fragment can't raise. Any doubled commas or
    spaces left behind are cleaned by normalize_kw downstream.
    """
    colors = colors or {}
    return _OUTFIT_PLACEHOLDER.sub(
        lambda m: str(colors.get(m.group(1), "") or "").strip(), template or "")


# --------------------------------------------------------------- randomizer

def load_traits():
    return _load_yaml(os.path.join(RAND_DIR, "traits.yaml"))


def load_outfit_pools():
    return _load_yaml(os.path.join(RAND_DIR, "outfit_pools.yaml"))


# ----------------------------------------------------- ControlNet pose library

def load_view_tags():
    return _load_yaml(os.path.join(CN_DIR, "_view_tags.yaml")) or {}


def list_cn_positions():
    try:
        names = [
            os.path.splitext(f)[0]
            for f in os.listdir(CN_DIR)
            if f.endswith((".yaml", ".yml")) and not f.startswith("_")
        ]
    except OSError:
        names = []
    return sorted(names)


def _cn_path(name):
    for ext in (".yaml", ".yml"):
        p = os.path.join(CN_DIR, name + ext)
        if os.path.isfile(p):
            return p
    return os.path.join(CN_DIR, name + ".yaml")


def get_cn_position(name):
    return _load_yaml(_cn_path(name))


def cn_position_views(name):
    """That position's own views first, then any global view-tag keys it lacks."""
    pos = get_cn_position(name)
    views = list((pos.get("views") or {}).keys())
    default = pos.get("default_view")
    if default in views:
        views.remove(default)
        views.insert(0, default)
    for k in load_view_tags():
        if k not in views:
            views.append(k)
    return views or list(load_view_tags().keys()) or ["from_side"]


def cn_view_skeleton(name, view=None):
    """Legacy multi-view lookup. Return (abs_json_path_or_None, extra_view_tags).

    Kept for any old-format YAML that still carries a `views:` map; new poses
    store a single `skeleton:` file and facing is applied at render time — see
    cn_canonical_skeleton() and pose_ops.orient().
    """
    pos = get_cn_position(name)
    views = pos.get("views") or {}
    entry = views.get(view) if view else None
    if not entry and pos.get("default_view"):
        entry = views.get(pos["default_view"])
    if not entry and views:
        entry = next(iter(views.values()))
    if isinstance(entry, str):
        entry = {"file": entry, "tags": ""}
    entry = entry or {}
    fname = entry.get("file")
    path = os.path.join(CN_KP_DIR, fname) if fname else None
    if path and not os.path.isfile(path):
        path = None
    return path, str(entry.get("tags", ""))


def cn_canonical_skeleton(name):
    """Return (abs_json_path_or_None, pose_tags) for a position's single stored
    skeleton file. Facing is not baked in — the orbit camera synthesises it.
    Falls back to the old `views:`/`default_view:` shape."""
    pos = get_cn_position(name)
    fname = pos.get("skeleton")
    if fname:
        path = os.path.join(CN_KP_DIR, fname)
        if os.path.isfile(path):
            return path, str(pos.get("tags", ""))
    path, _ = cn_view_skeleton(name)
    return path, str(pos.get("tags", ""))


def cn_canonical_rig(name):
    """Return (rig_or_[rig,rig]_or_None, pose_tags). Reads the stored pose doc
    (3-D rig; legacy COCO-18 frames are lifted by rig.to_rigs)."""
    from . import rig as rigmod
    path, tags = cn_canonical_skeleton(name)
    if not path:
        return None, tags
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    rigs = rigmod.to_rigs(doc)
    return (rigs if len(rigs) > 1 else rigs[0]), tags


def orientation_tags(yaw=0.0, pitch=0.0):
    """Camera/facing prompt tags for the orbit camera. yaw & pitch are -1..1.

    Sign follows the projection: yaw > 0 turns the subject to face the viewer's
    left, yaw < 0 to the right; |yaw| -> 1 is the back. These carry the facing
    when the projected skeleton is ambiguous (an axial pose barely changes
    silhouette front-to-back), so keep them decisive.
    """
    y = max(-1.0, min(1.0, float(yaw or 0.0)))
    p = max(-1.0, min(1.0, float(pitch or 0.0)))
    ay = abs(y)
    out = []
    if ay <= 0.12:
        out.append("front view, facing viewer")
    elif ay >= 0.82:
        out.append("(from behind:1.3), (back view:1.2), view from behind, facing away")
    else:
        face = "facing left" if y > 0 else "facing right"
        if ay <= 0.4:
            out.append("three-quarter view, " + face)
        else:
            out.append("profile view, from the side, " + face)
    if p >= 0.3:
        out.append("high angle, from above, looking down"
                   + (", overhead shot, bird's-eye view" if p >= 0.7 else ""))
    elif p <= -0.3:
        out.append("low angle, from below, looking up"
                   + (", worm's-eye view" if p <= -0.7 else ""))
    return ", ".join(out)


def save_cn_pose(position, rig=None, rigs=None, position_tags="", people=1,
                 update_yaml=True, overwrite=False):
    """Write keypoints/<pos>.json (a 3-D rig doc) and upsert cn_positions/<pos>.yaml.

    The stored pose is a single facing-neutral rig; the name is the pose only
    (no camera angle)."""
    pos = re.sub(r"[^A-Za-z0-9_-]", "_", str(position).strip()).strip("_")
    if not pos:
        raise ValueError("position must be non-empty")
    os.makedirs(CN_KP_DIR, exist_ok=True)

    kp_path = os.path.abspath(os.path.join(CN_KP_DIR, f"{pos}.json"))
    if os.path.commonpath([kp_path, os.path.abspath(CN_KP_DIR)]) != os.path.abspath(CN_KP_DIR):
        raise ValueError("path escapes keypoints dir")
    if os.path.exists(kp_path) and not overwrite:
        raise ValueError(f"{pos}.json exists; pass overwrite=true")
    if rigs:
        doc = {"rigs": list(rigs)}
        people = len(rigs)
    elif rig is not None:
        doc = {"rig": rig}
    else:
        raise ValueError("rig or rigs required")
    with open(kp_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)

    yaml_path = _cn_path(pos)
    if update_yaml:
        doc = _load_yaml(yaml_path) if os.path.isfile(yaml_path) else {}
        doc.setdefault("name", pos.replace("_", " ").title())
        if "people" not in doc:
            doc["people"] = int(people)
        if position_tags or "tags" not in doc:
            doc["tags"] = str(position_tags or doc.get("tags", ""))
        doc["skeleton"] = f"{pos}.json"
        doc.pop("views", None)
        doc.pop("default_view", None)
        with open(yaml_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)
    return kp_path
