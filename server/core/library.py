"""The Library: pictures the owner chose to keep, apart from the gallery (a working area that is swept after 7 days).

Storage (LIBRARY_DIR, from PROMPTFORGE_LIBRARY_DIR, default <repo>/library, git-ignored):
  items/<id>.json          the record {id, file, picture, saved_at, note, tags, reason}
  pictures/<id>.<ext>      a COPY of the gallery picture (the gallery is never edited or moved)
  pictures/<id>.sidecar.json   a copy of the gallery picture's sidecar (its recipe), when it had one
  .trash/<stamp>-<name>    removed items; purge_trash() deletes them a day later (by the stamp in the name, never the file time)
id is the first 12 hex digits of sha256 of the gallery relpath, so saving the same picture twice is one item.
Nothing here deletes from the gallery.
"""
import hashlib
import json
import os
import re
import shutil
import time

LIBRARY_DIR = os.path.abspath(os.path.expanduser(os.environ.get("PROMPTFORGE_LIBRARY_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "library")))
GALLERY_DIR = os.path.abspath(os.path.expanduser(os.environ.get("PROMPTFORGE_GALLERY_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "gallery")))
EXTS = (".png", ".jpg", ".jpeg", ".webp")
TRASH_MAX_AGE_S = 86400
MAX_TAGS = 12
_ID = re.compile(r"^[0-9a-f]{12}$")
_STAMPED = re.compile(r"^(\d{13})-[A-Za-z0-9_.-]+$")


class LibraryError(ValueError):
    """Bad input: a path outside the gallery, not a picture, a malformed id or tag list."""


class NotFound(LookupError):
    """No such library item, or the gallery picture is not there."""


def _dirs():
    return os.path.join(LIBRARY_DIR, "items"), os.path.join(LIBRARY_DIR, "pictures"), os.path.join(LIBRARY_DIR, ".trash")


def item_id(rel):
    return hashlib.sha256(rel.encode("utf-8")).hexdigest()[:12]


def clean_tags(tags):
    """Lowercase, trimmed, deduplicated tags (a list or a comma separated string), at most MAX_TAGS."""
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = tags.split(",")
    if not isinstance(tags, (list, tuple)) or not all(isinstance(t, str) for t in tags):
        raise LibraryError("tags must be a list of strings or a comma separated string")
    out = []
    for t in tags:
        t = t.strip().lower()[:40]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def _gallery_path(rel, gallery_dir):
    root = os.path.abspath(gallery_dir or GALLERY_DIR)
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or "\\" in rel:
        raise LibraryError("file must be a relative gallery path")
    p = os.path.abspath(os.path.join(root, rel))
    if not p.startswith(root + os.sep):
        raise LibraryError("file is outside the gallery")
    if os.path.splitext(p)[1].lower() not in EXTS:
        raise LibraryError("not a picture (png, jpg, jpeg, webp)")
    return p


def _sidecar(path):
    d, base = os.path.split(path)
    return os.path.join(d, base.split("_")[0] + ".json")


def _read(iid):
    if not isinstance(iid, str) or not _ID.match(iid):
        raise LibraryError("malformed id")
    try:
        with open(os.path.join(_dirs()[0], iid + ".json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        raise NotFound("no such library item")


def _write(rec):
    items = _dirs()[0]
    os.makedirs(items, exist_ok=True)
    tmp = os.path.join(items, rec["id"] + ".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2)
    os.replace(tmp, os.path.join(items, rec["id"] + ".json"))


def save(rel, note="", tags=(), reason="manual", gallery_dir=None):
    """Copy a gallery picture (and its sidecar) into the Library; returns (record, created). Saving the same picture again updates its note and tags."""
    src = _gallery_path(rel, gallery_dir)
    if not os.path.isfile(src):
        raise NotFound("the gallery picture is not there")
    if not isinstance(note, str):
        raise LibraryError("note must be text")
    iid = item_id(rel)
    tags = clean_tags(tags)
    try:
        rec = _read(iid)
        rec["note"], rec["tags"] = note, tags
        _write(rec)
        return rec, False
    except NotFound:
        pass
    pics = _dirs()[1]
    os.makedirs(pics, exist_ok=True)
    name = iid + os.path.splitext(src)[1].lower()
    shutil.copy2(src, os.path.join(pics, name))
    side = _sidecar(src)
    if os.path.isfile(side):
        try:
            shutil.copy2(side, os.path.join(pics, iid + ".sidecar.json"))
        except OSError:
            pass                                    # the recipe copy is best effort; the picture is what matters
    rec = {"id": iid, "file": rel, "picture": "pictures/" + name, "saved_at": time.time(), "note": note, "tags": tags, "reason": str(reason or "manual")[:200]}
    _write(rec)
    return rec, True


def get(iid):
    return _read(iid)


def list_items(query="", tag=""):
    """Every item, newest saved first, optionally filtered by text (in the note, tags and source name) and by one tag."""
    items = _dirs()[0]
    out = []
    if os.path.isdir(items):
        for n in os.listdir(items):
            if n.endswith(".json"):
                try:
                    out.append(_read(n[:-5]))
                except (NotFound, LibraryError):
                    continue
    q, t = (query or "").strip().lower(), (tag or "").strip().lower()
    if q:
        out = [r for r in out if q in (r.get("note", "") + " " + " ".join(r.get("tags", [])) + " " + r.get("file", "")).lower()]
    if t:
        out = [r for r in out if t in r.get("tags", [])]
    out.sort(key=lambda r: (r.get("saved_at") or 0, r["id"]), reverse=True)
    return out


def update(iid, note=None, tags=None):
    rec = _read(iid)
    if note is not None:
        if not isinstance(note, str):
            raise LibraryError("note must be text")
        rec["note"] = note
    if tags is not None:
        rec["tags"] = clean_tags(tags)
    _write(rec)
    return rec


def picture_path(iid):
    rec = _read(iid)
    p = os.path.join(LIBRARY_DIR, rec["picture"])
    if not os.path.isfile(p):
        raise NotFound("the saved picture file is missing")
    return p


def remove(iid, now=None):
    """Move an item (picture, sidecar copy and record) into .trash with a stamp prefix; purge_trash() deletes it a day later."""
    rec = _read(iid)
    items, pics, trash = _dirs()
    os.makedirs(trash, exist_ok=True)
    stamp = "%d" % int((time.time() if now is None else now) * 1000)
    for src in (os.path.join(LIBRARY_DIR, rec["picture"]), os.path.join(pics, iid + ".sidecar.json"), os.path.join(items, iid + ".json")):
        if os.path.isfile(src):
            shutil.move(src, os.path.join(trash, stamp + "-" + os.path.basename(src)))
    return iid


def purge_trash(now=None, max_age_s=TRASH_MAX_AGE_S):
    """Delete files directly inside .trash whose stamp prefix is older than max_age_s; returns [{file, bytes}]. Nothing else is ever touched."""
    trash = _dirs()[2]
    now_ms = (time.time() if now is None else now) * 1000
    gone = []
    if os.path.isdir(trash):
        for name in sorted(os.listdir(trash)):
            m = _STAMPED.match(name)
            path = os.path.join(trash, name)
            if m and os.path.isfile(path) and now_ms - int(m.group(1)) > max_age_s * 1000:
                size = os.path.getsize(path)
                os.remove(path)
                gone.append({"file": name, "bytes": size})
    return gone
