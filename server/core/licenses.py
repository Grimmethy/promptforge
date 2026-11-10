"""Model licence registry: what each model we run lets us sell (data/model-licenses.json).

lookup(model) answers for one model name as ComfyUI lists it (an optional "krea2:" prefix is ignored). Keys match exactly, then ignoring case, then as a
* pattern ("wan2.2_*"). A model with no entry, a missing or broken file, or a record with an unknown commercial_use is "unverified" (never an error, never
"yes"). The answer for a generation is written into its sidecar as a snapshot (commercial_use, licence), so a later change to the registry does not
rewrite what an old image was made with. Not legal advice; see styleforge docs/MODEL_LICENSES.md for the evidence.
"""
import fnmatch
import json
import os
import threading

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "model-licenses.json")
VALUES = ("yes", "conditional", "no", "unverified")
UNVERIFIED = {"source_url": None, "licence": None, "commercial_use": "unverified", "note": None, "checked": None}
_lock = threading.Lock()
_cache = {}                      # path -> (mtime, registry)
_side = {}                       # sidecar path -> (mtime, (commercial_use, licence))


def _clean(rec):
    out = dict(UNVERIFIED)
    if isinstance(rec, dict):
        for k in ("source_url", "licence", "note", "checked"):
            v = rec.get(k)
            out[k] = v if isinstance(v, str) and v.strip() else None
        cu = rec.get("commercial_use")
        out["commercial_use"] = cu if cu in VALUES else "unverified"
    return out


def load(path=None):
    """{model key: cleaned record}, cached by file mtime. A missing, unreadable or malformed file gives {}; keys starting with _ and non-object entries are skipped."""
    path = path or PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    reg = {}
    if isinstance(raw, dict):
        reg = {k: _clean(v) for k, v in raw.items() if isinstance(k, str) and not k.startswith("_") and isinstance(v, dict)}
    with _lock:
        _cache[path] = (mtime, reg)
    return reg


def lookup(model, path=None):
    """The licence record for a model name (a copy; "unverified" when nothing matches)."""
    name = model.split(":", 1)[1] if isinstance(model, str) and model.startswith("krea2:") else model
    if not isinstance(name, str) or not name:
        return dict(UNVERIFIED)
    reg = load(path)
    if name in reg:
        return dict(reg[name])
    low = name.lower()
    for key, rec in reg.items():
        if key.lower() == low:
            return dict(rec)
    for key, rec in reg.items():
        if "*" in key and fnmatch.fnmatchcase(low, key.lower()):
            return dict(rec)
    return dict(UNVERIFIED)


def snapshot(model, path=None):
    """The two fields a sidecar records at generation time."""
    rec = lookup(model, path)
    return {"commercial_use": rec["commercial_use"], "licence": rec["licence"]}


def sidecar_info(sidecar_path):
    """(commercial_use, licence) recorded in a gallery sidecar; ("unverified", None) when the sidecar is missing, unreadable or older than the snapshot. Cached by mtime."""
    try:
        mtime = os.path.getmtime(sidecar_path)
    except OSError:
        return "unverified", None
    with _lock:
        hit = _side.get(sidecar_path)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        with open(sidecar_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        meta = {}
    cu = meta.get("commercial_use") if isinstance(meta, dict) else None
    lic = meta.get("licence") if isinstance(meta, dict) else None
    info = (cu if cu in VALUES else "unverified", lic if isinstance(lic, str) else None)
    with _lock:
        _side[sidecar_path] = (mtime, info)
    return info


def clear():
    with _lock:
        _cache.clear()
        _side.clear()
