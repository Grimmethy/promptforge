"""Which character(s) a gallery image shows (2026-11-10).

A fresh generation records its characters in the sidecar (`spec.characters[].name`). A regenerate / image-to-image run does not: its spec
carries `regenerate: true` and `source: "<name>-a-colour-9ed2da.png"`, so the character is guessed from that file name and marked guessed.

  resolve(raw, names)      pure: raw = {"names": [...], "source": str}, names = {normalised name: display name} -> [{"name", "guessed"}]
  raw_for(sidecar_path)    reads one sidecar through a cache keyed by its mtime (a sidecar is read once until it changes; nothing scans the gallery)

Matching ignores case, spaces, dashes and underscores ("Lucky_the_Pocket_Gnome" = "lucky the pocket gnome"). A source prefix that is not a whole
character name matches when it is the start of exactly one character name and at least four letters long (the file name "lucky-a-1.png" is Lucky the Pocket Gnome).
"""

import json
import os
import re
import threading

_lock = threading.Lock()
_cache = {}                       # sidecar path -> (mtime, raw)
_SEGMENT_STOP = re.compile(r"^(?:[a-z]|[0-9a-f]{6,8})$")


def norm(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _source_prefixes(source):
    """Leading dash-joined parts of a source file name, longest first, stopping at a one-letter or hash part: ettercap-a-colour-9ed2da.png -> ["ettercap"]."""
    stem = os.path.splitext(os.path.basename(str(source or "")))[0]
    parts = []
    for part in stem.split("-"):
        if not part or _SEGMENT_STOP.match(part.lower()):
            break
        parts.append(part)
    return ["-".join(parts[:n]) for n in range(len(parts), 0, -1)]


def _guess(source, names):
    for cand in _source_prefixes(source):
        key = norm(cand)
        if key in names:
            return names[key]
        if len(key) >= 4:
            hits = {disp for k, disp in names.items() if k.startswith(key)}
            if len(hits) == 1:
                return hits.pop()
    return None


def resolve(raw, names):
    """Characters of one image: the sidecar's own names first (guessed False), else a guess from the source file name, else []."""
    out, seen = [], set()
    for n in raw.get("names") or []:
        disp = names.get(norm(n)) or str(n).replace("_", " ")
        if disp not in seen:
            seen.add(disp)
            out.append({"name": disp, "guessed": False})
    if out:
        return out
    g = _guess(raw.get("source"), names)
    return [{"name": g, "guessed": True}] if g else []


def raw_for(sidecar_path):
    """{"names": [...], "source": str, "links": int} for a sidecar file; empty values when it is missing or unreadable. Cached by mtime."""
    try:
        mtime = os.path.getmtime(sidecar_path)
    except OSError:
        return {"names": [], "source": "", "links": 0}
    with _lock:
        hit = _cache.get(sidecar_path)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        with open(sidecar_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        meta = {}
    spec = meta.get("spec") if isinstance(meta, dict) else None
    spec = spec if isinstance(spec, dict) else {}
    chars = spec.get("characters") if isinstance(spec.get("characters"), list) else []
    links = meta.get("links") if isinstance(meta, dict) else None
    raw = {"names": [c.get("name") for c in chars if isinstance(c, dict) and c.get("name")],
           "source": spec.get("source") or "", "links": len(links) if isinstance(links, list) else 0}
    with _lock:
        _cache[sidecar_path] = (mtime, raw)
    return raw


def clear():
    with _lock:
        _cache.clear()
