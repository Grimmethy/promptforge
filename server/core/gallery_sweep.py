"""The 7-day gallery sweep: the gallery is a working area, pictures worth keeping live in the Library (server/core/library.py).

A gallery picture older than 7 days (its sidecar ts, else the file time) is a CANDIDATE unless
  - it is already in the Library (its source file is a Library item), or
  - another forge's data names it (server/core/gallery_refs.py): such a picture is copied INTO the Library (reason 'referenced by <forge>:<record>')
    and then counts as saved, never deleted.
Everything else would go to <GALLERY_DIR>/.trash/<stamp>-<name> (picture and sidecar); purge_trash() deletes only stamped files in .trash older than a day.
The sweep is OFF: plan() is the default everywhere (it deletes nothing), and apply() refuses unless the owner created data/sweep-enabled.
Run: cd server && python -m core.gallery_sweep [--apply]
"""
import json
import os
import re
import shutil
import sys
import time

from . import gallery_refs, library

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
GALLERY_DIR = os.path.abspath(os.path.expanduser(os.environ.get("PROMPTFORGE_GALLERY_DIR") or os.path.join(REPO, "gallery")))
ENABLED_PATH = os.path.join(REPO, "data", "sweep-enabled")
LOG_PATH = os.path.join(REPO, "data", "sweep.log")
AGE_S = 7 * 86400
TRASH = ".trash"
TRASH_MAX_AGE_S = 86400
EXTS = library.EXTS
_STAMPED = re.compile(r"^(\d{13})-[A-Za-z0-9_.-]+$")


class SweepOff(PermissionError):
    """apply() was called while the owner's switch file is absent."""


def enabled():
    return os.path.isfile(ENABLED_PATH)


def set_enabled(on):
    if on:
        os.makedirs(os.path.dirname(ENABLED_PATH), exist_ok=True)
        with open(ENABLED_PATH, "w") as fh:
            fh.write("on\n")
    elif os.path.isfile(ENABLED_PATH):
        os.remove(ENABLED_PATH)
    return enabled()


def _sidecar(path):
    d, base = os.path.split(path)
    return os.path.join(d, base.split("_")[0] + ".json")


def _picture_time(path):
    try:
        with open(_sidecar(path), encoding="utf-8") as fh:
            ts = json.load(fh).get("ts")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            return float(ts)
    except (OSError, ValueError, AttributeError):
        pass
    return os.path.getmtime(path)


def _has_siblings(path):
    """Is another picture of the same job (same <id> prefix) still in the folder?"""
    d, base = os.path.split(path)
    jid = base.split("_")[0]
    return any(n != base and n.split("_")[0] == jid and os.path.splitext(n)[1].lower() in EXTS for n in os.listdir(d))


def _pictures(gallery_dir):
    """[(relpath, absolute path)] for every picture directly inside a YYYY-MM folder of the gallery (hidden folders such as .trash and .thumbs are skipped)."""
    out = []
    if os.path.isdir(gallery_dir):
        for month in sorted(os.listdir(gallery_dir)):
            d = os.path.join(gallery_dir, month)
            if month.startswith(".") or not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if os.path.splitext(name)[1].lower() in EXTS:
                    out.append((month + "/" + name, os.path.join(d, name)))
    return out


def plan(now=None, gallery_dir=None, library_files=None, refs=None):
    """What a sweep would do (deletes nothing): counts, bytes, per month, how many are protected, the oldest and newest candidate."""
    now = time.time() if now is None else now
    gdir = gallery_dir or GALLERY_DIR
    saved = set(library_files) if library_files is not None else {r["file"] for r in library.list_items()}
    refs = gallery_refs.referenced() if refs is None else refs
    cand, protect, saved_n, young, per_month = [], [], 0, 0, {}
    for rel, path in _pictures(gdir):
        try:
            ts = _picture_time(path)
        except OSError:
            continue
        if now - ts <= AGE_S:
            young += 1
        elif rel in saved:
            saved_n += 1
        elif rel in refs:
            protect.append({"file": rel, "holders": refs[rel]})
        else:
            size = os.path.getsize(path)
            cand.append({"file": rel, "bytes": size, "ts": ts})
            m = per_month.setdefault(rel.split("/")[0], {"files": 0, "bytes": 0})
            m["files"] += 1
            m["bytes"] += size
    cand.sort(key=lambda c: c["ts"])
    return {"enabled": enabled(), "now": now, "age_days": AGE_S // 86400, "younger_than_7_days": young, "already_in_library": saved_n,
            "protected_referenced": len(protect), "protected": protect[:50], "candidates": len(cand), "bytes": sum(c["bytes"] for c in cand),
            "per_month": per_month, "oldest": cand[0]["file"] if cand else None, "newest": cand[-1]["file"] if cand else None,
            "sample": [c["file"] for c in cand[:20]]}


def _log(row):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


def apply(now=None, gallery_dir=None, library_files=None, refs=None):
    """Run the sweep. SweepOff (nothing touched) unless the switch file exists. Referenced pictures are copied into the Library first and kept."""
    if not enabled():
        raise SweepOff("the sweep is off: the owner switches it on (data/sweep-enabled)")
    now = time.time() if now is None else now
    gdir = gallery_dir or GALLERY_DIR
    saved = set(library_files) if library_files is not None else {r["file"] for r in library.list_items()}
    refs = gallery_refs.referenced() if refs is None else refs
    saved_refs = 0
    for rel, path in _pictures(gdir):
        try:
            if now - _picture_time(path) > AGE_S and rel not in saved and rel in refs:
                h = refs[rel][0]
                library.save(rel, reason="referenced by %s:%s" % (h["forge"], h["record"]), gallery_dir=gdir)
                saved_refs += 1
        except OSError:
            continue
    trash = os.path.join(gdir, TRASH)
    stamp = "%d" % int(now * 1000)
    moved, freed = 0, 0
    for c in _candidates_full(now, gdir, saved, refs):
        src = os.path.join(gdir, c["file"])
        month = c["file"].split("/")[0]
        os.makedirs(trash, exist_ok=True)
        shutil.move(src, os.path.join(trash, "%s-%s__%s" % (stamp, month, os.path.basename(src))))
        side = _sidecar(src)
        if os.path.isfile(side) and not _has_siblings(src):                  # one sidecar serves every picture of a job (<id>_0, <id>_1 ...)
            shutil.move(side, os.path.join(trash, "%s-%s__%s" % (stamp, month, os.path.basename(side))))
        moved += 1
        freed += c["bytes"]
        _log({"at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)), "file": c["file"], "bytes": c["bytes"], "action": "trashed"})
    return {"saved_referenced": saved_refs, "trashed": moved, "bytes": freed, "purged": len(purge_trash(now, gdir))}


def _candidates_full(now, gdir, library_files, refs):
    """The complete candidate list (plan() only samples 20)."""
    saved = set(library_files) if library_files is not None else {r["file"] for r in library.list_items()}
    refs = gallery_refs.referenced() if refs is None else refs
    out = []
    for rel, path in _pictures(gdir):
        try:
            if now - _picture_time(path) <= AGE_S or rel in saved or rel in refs:
                continue
            out.append({"file": rel, "bytes": os.path.getsize(path)})
        except OSError:
            continue
    return out


def purge_trash(now=None, gallery_dir=None, max_age_s=TRASH_MAX_AGE_S):
    """Delete files directly inside <gallery>/.trash whose stamp prefix is older than max_age_s; returns [{file, bytes}]."""
    trash = os.path.join(gallery_dir or GALLERY_DIR, TRASH)
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--apply" in argv:
        try:
            print(json.dumps(apply(), indent=1))
        except SweepOff as e:
            print(str(e), file=sys.stderr)
            return 3
        return 0
    p = plan()
    print("sweep is %s (this is a dry run: nothing was changed)" % ("ON" if p["enabled"] else "OFF"))
    print("%d pictures older than %d days would be moved to .trash (%.2f GB); %d protected as referenced, %d already in the Library, %d younger" % (
        p["candidates"], p["age_days"], p["bytes"] / 1e9, p["protected_referenced"], p["already_in_library"], p["younger_than_7_days"]))
    for month, v in sorted(p["per_month"].items()):
        print("  %s: %d files, %.2f GB" % (month, v["files"], v["bytes"] / 1e9))
    return 0


if __name__ == "__main__":
    sys.exit(main())
