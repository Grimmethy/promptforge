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

CATEGORIES = ("poses", "positions", "styles", "backgrounds", "supporting")


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


def character_outfits(name):
    outfits = get_character(name).get("outfits") or {}
    keys = list(outfits.keys())
    if "default" in keys:
        keys.remove("default")
        keys.insert(0, "default")
    return keys or ["default"]


def character_expressions(name):
    expr = get_character(name).get("expressions") or {}
    keys = list(expr.keys())
    if "default" in keys:
        keys.remove("default")
        keys.insert(0, "default")
    return keys or ["default"]


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


def cn_view_skeleton(name, view):
    """Return (abs_json_path_or_None, extra_view_tags)."""
    pos = get_cn_position(name)
    views = pos.get("views") or {}
    entry = views.get(view)
    if not entry and pos.get("default_view"):
        entry = views.get(pos["default_view"])
    if isinstance(entry, str):
        entry = {"file": entry, "tags": ""}
    entry = entry or {}
    fname = entry.get("file")
    path = os.path.join(CN_KP_DIR, fname) if fname else None
    if path and not os.path.isfile(path):
        path = None
    return path, str(entry.get("tags", ""))


def save_cn_pose(position, view, keypoints_frame, position_tags="", people=1,
                 update_yaml=True, overwrite=False):
    """Write keypoints/<pos>__<view>.json and upsert cn_positions/<pos>.yaml."""
    pos = re.sub(r"[^A-Za-z0-9_-]", "_", str(position).strip()).strip("_")
    vw = re.sub(r"[^A-Za-z0-9_-]", "_", str(view).strip()).strip("_")
    if not pos or not vw:
        raise ValueError("position and view must be non-empty")
    os.makedirs(CN_KP_DIR, exist_ok=True)

    kp_path = os.path.abspath(os.path.join(CN_KP_DIR, f"{pos}__{vw}.json"))
    if os.path.commonpath([kp_path, os.path.abspath(CN_KP_DIR)]) != os.path.abspath(CN_KP_DIR):
        raise ValueError("path escapes keypoints dir")
    if os.path.exists(kp_path) and not overwrite:
        raise ValueError(f"{pos}__{vw}.json exists; pass overwrite=true")
    frame = keypoints_frame[0] if isinstance(keypoints_frame, list) else keypoints_frame
    with open(kp_path, "w", encoding="utf-8") as fh:
        json.dump(frame, fh)

    yaml_path = _cn_path(pos)
    if update_yaml:
        doc = _load_yaml(yaml_path) if os.path.isfile(yaml_path) else {}
        new = not doc
        doc.setdefault("name", pos.replace("_", " ").title())
        if new:
            doc["people"] = int(people)
            doc["tags"] = str(position_tags or "")
            doc["default_view"] = vw
        doc.setdefault("views", {})
        doc["views"][vw] = {"file": f"{pos}__{vw}.json", "tags": ""}
        with open(yaml_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)
    return kp_path
