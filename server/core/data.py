"""Preset data loading + keyword helpers.

Ported from ComfyUI-PromptForge/promptforge/data.py. All presets are YAML under
``<repo>/data`` (override with ``PROMPTFORGE_DATA_DIR``). No mtime cache here —
the files are tiny and the server reloads them per request.
"""

import json
import os
import re

import yaml

from . import cf_characters

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
DATA_DIR = os.environ.get("PROMPTFORGE_DATA_DIR") or os.path.join(_REPO, "data")
CHAR_DIR = os.path.join(DATA_DIR, "characters")
RAND_DIR = os.path.join(DATA_DIR, "randomizer")
CN_DIR = os.path.join(DATA_DIR, "cn_positions")
CN_KP_DIR = os.path.join(CN_DIR, "keypoints")

# Gitignored overlay of explicit content. Mirrors DATA_DIR: flat pools as
# `entries:` maps, outfits as the nested tree, themes as `themes:`, plus a
# cn_positions/ (+ keypoints/) sub-tree. Merged into every read only while the
# NSFW toggle is on (presence of NSFW_DIR/enabled). Absent on a fresh clone of
# the public repo -> every overlay read is a harmless no-op.
NSFW_DIR = os.path.join(DATA_DIR, "nsfw")
NSFW_CN_DIR = os.path.join(NSFW_DIR, "cn_positions")
NSFW_CN_KP_DIR = os.path.join(NSFW_CN_DIR, "keypoints")
_NSFW_FLAG = os.path.join(NSFW_DIR, "enabled")

CATEGORIES = ("poses", "positions", "styles", "backgrounds", "supporting",
              "expressions", "colors")

# Targets the UI's inline "add entry" boxes may write to (see add_entry).
ALLOWED_ENTRY_TARGETS = frozenset(CATEGORIES) | {"outfits", "themes"}


# ------------------------------------------------------------------- NSFW toggle

def nsfw_enabled():
    """True while the NSFW overlay should be merged into every data read."""
    return os.path.isfile(_NSFW_FLAG)


def set_nsfw(on):
    """Flip the process-global NSFW toggle (a gitignored marker file)."""
    if on:
        os.makedirs(NSFW_DIR, exist_ok=True)
        with open(_NSFW_FLAG, "w", encoding="utf-8") as fh:
            fh.write("")
    elif os.path.isfile(_NSFW_FLAG):
        os.remove(_NSFW_FLAG)
    return nsfw_enabled()


def _load_overlay(rel):
    """Parsed `data/nsfw/<rel>` (dict) or {} when NSFW is off / the file is absent."""
    if not nsfw_enabled():
        return {}
    return _load_yaml(os.path.join(NSFW_DIR, rel))


def _deep_merge(base, over):
    """Recursively merge `over` onto `base` (dicts merge, other values replace).
    Neither input is mutated."""
    if not isinstance(base, dict) or not isinstance(over, dict):
        return over if over is not None else base
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if k in out else v
    return out


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

_CF_OWNS = ("Characters are owned by CharacterForge: edit this character there "
            "(the local YAML presets were folded into it). Set PROMPTFORGE_CHARACTER_SOURCE=local "
            "to use the legacy local presets.")


def list_characters():
    if cf_characters.configured():
        return cf_characters.list_characters()
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
    if cf_characters.configured():
        return cf_characters.get_character(name)
    return _load_yaml(_char_path(name))


def raw_character(name):
    """Return the character YAML file's exact text (for the preset editor)."""
    if cf_characters.configured():
        raise ValueError(_CF_OWNS)
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
    if cf_characters.configured():
        raise ValueError(_CF_OWNS)
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


def _cat_entries(category):
    """Base `entries:` map, with the NSFW overlay merged on top when enabled
    (base key order preserved, overlay keys appended / overriding)."""
    base = _load_yaml(_cat_path(category)).get("entries") or {}
    over = _load_overlay(category + ".yaml").get("entries") or {}
    if not over:
        return base
    merged = dict(base)
    for k, v in over.items():
        merged[k] = v
    return merged


def list_entries(category):
    entries = _cat_entries(category)
    keys = list(entries.keys())
    if "none" in keys:
        keys.remove("none")
        keys.insert(0, "none")
    return keys or ["none"]


def get_entry(category, key):
    """Return (positive, negative) for a category entry."""
    entries = _cat_entries(category)
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
    """Raw nested outfit dict. Reloaded per request like the rest of the module.
    The NSFW overlay (data/nsfw/outfits.yaml) is deep-merged in when enabled."""
    base = _load_yaml(os.path.join(DATA_DIR, "outfits.yaml"))
    over = _load_overlay("outfits.yaml")
    return _deep_merge(base, over) if over else base


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


def outfit_in_overlay(sel):
    """True if an outfit selection ({gender,genre,subgenre,key} or 4-item list)
    resolves inside the gitignored NSFW overlay tree (data/nsfw/outfits.yaml)."""
    over = _load_overlay("outfits.yaml")
    if not over:
        return False
    if isinstance(sel, dict):
        path = [sel.get("gender"), sel.get("genre"), sel.get("subgenre"), sel.get("key")]
    else:
        path = list(sel or [])
    if len(path) != 4 or not all(path):
        return False
    node = over
    for step in path:
        if not isinstance(node, dict) or step not in node:
            return False
        node = node[step]
    return True


def entry_in_overlay(category, key):
    """True if a flat-pool entry (expressions/poses/...) lives in the NSFW
    overlay (data/nsfw/<category>.yaml)."""
    if not key:
        return False
    over = _load_overlay(category + ".yaml")
    entries = (over or {}).get("entries") or {}
    return key in entries


def cn_position_in_overlay(name):
    """True if a named ControlNet pose resolves from the NSFW overlay dir
    (data/nsfw/cn_positions/) rather than the tracked library."""
    if not name or not nsfw_enabled():
        return False
    for ext in (".yaml", ".yml"):
        if os.path.isfile(os.path.join(NSFW_CN_DIR, name + ext)):
            return True
    return False


def fill_outfit(template, colors):
    """Substitute {base|contrast|detail} in an outfit fragment from `colors`.

    Missing / empty slots collapse to "". A regex sub (not str.format) so literal
    punctuation and stray braces in a fragment can't raise. Any doubled commas or
    spaces left behind are cleaned by normalize_kw downstream.
    """
    colors = colors or {}
    return _OUTFIT_PLACEHOLDER.sub(
        lambda m: str(colors.get(m.group(1), "") or "").strip(), template or "")


# ---------------------------------------------- add / update / delete entry (UI)

def _overlay_root_line(target):
    """Seed text for a freshly created data/nsfw/<target>.yaml."""
    if target in CATEGORIES:
        return "entries:\n"
    if target == "themes":
        return "themes:\n"
    return ""   # outfits: nested tree starts at the document root


def _ensure_overlay(file_path, target):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    if not os.path.isfile(file_path):
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(_overlay_root_line(target))


def _key_in_file(file_path, parent_path, key):
    if not os.path.isfile(file_path):
        return False
    node = _load_yaml(file_path)
    for seg in list(parent_path) + [key]:
        if not isinstance(node, dict) or seg not in node:
            return False
        node = node[seg]
    return True


def _entry_target(target, path, value=None, fields=None, *, kind="add", key=None):
    """Resolve a UI entry write to (file_path, parent_path, node).

    target: one of ALLOWED_ENTRY_TARGETS.
      - a flat CATEGORIES pool -> `entries: {key: <tag string>}`.
      - "outfits" -> `path` is the ancestor keys; add: 1-3 deep (leaf `key: value` at
        3, empty branch `key: {}` shallower); update: leaf only (3 deep); delete: any.
      - "themes"  -> `themes: {key: {style,setting_bias,palette,lighting,negative_add}}`.
    `node` is unused for kind='delete'. Raises ValueError on bad input.

    The write lands in the gitignored NSFW overlay (data/nsfw/<target>.yaml) when:
      - kind='add' and the NSFW toggle is on (new explicit content stays out of the
        public repo), or
      - kind in ('update','delete') and the key currently lives in the overlay.
    Otherwise it lands in the tracked base file, exactly as before.
    """
    target = str(target or "").strip()
    if target not in ALLOWED_ENTRY_TARGETS:
        raise ValueError(f"unknown target '{target}'")
    base_fp = os.path.abspath(os.path.join(DATA_DIR, target + ".yaml"))
    over_fp = os.path.abspath(os.path.join(NSFW_DIR, target + ".yaml"))
    root = os.path.abspath(DATA_DIR)
    if (os.path.commonpath([base_fp, root]) != root
            or os.path.commonpath([over_fp, root]) != root):
        raise ValueError("path escapes data dir")
    path = [str(p).strip() for p in (path or [])]

    if target in CATEGORIES:
        parent_path = ["entries"] + path
        node = str(value or "").strip()
    elif target == "outfits":
        lo, hi = {"update": (3, 3), "delete": (0, 3)}.get(kind, (1, 3))
        if not lo <= len(path) <= hi:
            raise ValueError("bad outfits path for this operation")
        parent_path = path
        node = str(value or "").strip() if len(path) == 3 else {}
    else:  # themes
        from . import themes
        parent_path = ["themes"]
        node = {k: str((fields or {}).get(k, "")).strip() for k in themes._KEYS}

    if kind == "add":
        if nsfw_enabled():
            _ensure_overlay(over_fp, target)
            file_path = over_fp
        else:
            file_path = base_fp
    else:  # update / delete: follow the key to whichever file holds it
        file_path = over_fp if _key_in_file(over_fp, parent_path, key) else base_fp
    return file_path, parent_path, node


def add_entry(target, path, key, value=None, fields=None):
    """Append a new entry to a data YAML from the UI, preserving comments."""
    from . import yaml_insert
    fp, parent, node = _entry_target(target, path, value, fields, kind="add", key=key)
    return yaml_insert.insert_entry(fp, parent, key, node)


def update_entry(target, path, key, value=None, fields=None):
    """Replace an existing entry's value in place, preserving comments."""
    from . import yaml_insert
    fp, parent, node = _entry_target(target, path, value, fields, kind="update", key=key)
    return yaml_insert.update_entry(fp, parent, key, node)


def delete_entry(target, path, key):
    """Remove an entry (and its subtree) from a data YAML, preserving comments."""
    from . import yaml_insert
    fp, parent, _ = _entry_target(target, path, kind="delete", key=key)
    return yaml_insert.delete_entry(fp, parent, key)


# --------------------------------------------------------------- randomizer

def load_traits():
    return _load_yaml(os.path.join(RAND_DIR, "traits.yaml"))


def load_outfit_pools():
    return _load_yaml(os.path.join(RAND_DIR, "outfit_pools.yaml"))


# ----------------------------------------------------- ControlNet pose library

def load_view_tags():
    return _load_yaml(os.path.join(CN_DIR, "_view_tags.yaml")) or {}


def _cn_dirs():
    """Pose library dirs. NSFW overlay first so an overlay copy shadows a
    same-named tracked pose (e.g. a dashboard-saved explicit variant)."""
    return [NSFW_CN_DIR, CN_DIR] if nsfw_enabled() else [CN_DIR]


def list_cn_positions():
    names = set()
    for d in _cn_dirs():
        try:
            for f in os.listdir(d):
                if f.endswith((".yaml", ".yml")) and not f.startswith("_"):
                    names.add(os.path.splitext(f)[0])
        except OSError:
            pass
    return sorted(names)


def _cn_path(name):
    for d in _cn_dirs():
        for ext in (".yaml", ".yml"):
            p = os.path.join(d, name + ext)
            if os.path.isfile(p):
                return p
    return os.path.join(CN_DIR, name + ".yaml")


def _cn_kp_path(fname):
    """Resolve a skeleton keypoints file, NSFW overlay first (matches _cn_dirs)."""
    for d in ([NSFW_CN_KP_DIR, CN_KP_DIR] if nsfw_enabled() else [CN_KP_DIR]):
        p = os.path.join(d, fname)
        if os.path.isfile(p):
            return p
    return os.path.join(CN_KP_DIR, fname)


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
    path = _cn_kp_path(fname) if fname else None
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
        path = _cn_kp_path(fname)
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
    # NSFW-on -> the pose (and its keypoints) land in the gitignored overlay so
    # explicit poses saved from the dashboard never enter the public repo.
    cn_dir = NSFW_CN_DIR if nsfw_enabled() else CN_DIR
    kp_dir = NSFW_CN_KP_DIR if nsfw_enabled() else CN_KP_DIR
    os.makedirs(kp_dir, exist_ok=True)

    kp_path = os.path.abspath(os.path.join(kp_dir, f"{pos}.json"))
    if os.path.commonpath([kp_path, os.path.abspath(kp_dir)]) != os.path.abspath(kp_dir):
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
    if not os.path.isfile(yaml_path):
        yaml_path = os.path.join(cn_dir, f"{pos}.yaml")
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
