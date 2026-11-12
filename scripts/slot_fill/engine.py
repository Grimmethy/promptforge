"""The breadth-first run loop. For round r = 1..round_to, for every entry (the adapter's order), for each role whose count is below min(r, target): one picture.

Stopping at any moment leaves every entry balanced, and a re-run continues where it stopped (the counts come from the forge itself). Nothing is ever rejected, flagged,
held back or deleted: every picture that is not blank is linked with verdict needs-work for the owner to rank.
"""
import io
import json
import os
import time

from . import headroom
from .errors import SlotFillError

FAIL_STOP = 3                                       # failures in a row that stop the run
STATE_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "slot-fill")
HEARTBEAT_PATH = os.path.join(os.path.expanduser("~"), ".local", "state", "slot-fill", "heartbeat.json")
MIN_BYTES = 30000
MIN_STDDEV = 12


def good_image(data):
    """A blank or tiny picture is a failure (a technical check, not a judgement of the picture). Without Pillow only the size test applies."""
    if len(data) <= MIN_BYTES:
        return False
    try:
        from PIL import Image, ImageStat
        im = Image.open(io.BytesIO(data)).convert("L")
        return ImageStat.Stat(im).stddev[0] > MIN_STDDEV
    except Exception:  # noqa: BLE001 - Pillow missing or the file unreadable: the size test already passed
        return True


def _stamp(now):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now()))


def _append(path, row):
    with open(path, "a") as fh:
        fh.write(json.dumps(row) + "\n")


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w") as fh:
        fh.write(json.dumps(obj) + "\n")
    os.replace(tmp, path)


def _generate(pf, spec, owner):
    """One picture. A failure (a ended GPU session, a hiccup) takes the GPU session again once and retries this picture once."""
    try:
        return pf.generate(spec)
    except (SlotFillError, OSError):
        pf.gpu_acquire(owner)
        return pf.generate(spec)


def run(adapter, pf, *, slugs=None, round_from=1, round_to=None, limit=0, dry_run=False, state_dir=None, heartbeat_path=None, owner="slot-fill",
        sleep=time.sleep, now=time.time, wait=headroom.wait, out=print):
    """Run the loop; returns 0 (finished or limit reached), 1 (three failures in a row) or 2 (no headroom for 30 minutes)."""
    state_dir = state_dir or STATE_DIR
    heartbeat_path = heartbeat_path or HEARTBEAT_PATH
    want = set(slugs) if slugs else None
    entries = [e for e in adapter.entries() if want is None or e["slug"] in want]
    roles = adapter.roles()
    targets = {(e["slug"], r): adapter.target(e, r) for e in entries for r in roles}
    if round_to is None:
        round_to = max(targets.values(), default=0)
    manifest = os.path.join(state_dir, adapter.name + ".jsonl")
    pidfile = os.path.join(state_dir, adapter.name + ".pid")
    st = {"done": 0, "errors": 0, "last": None}

    def beat(running, exit_code=None):
        row = {"pid": os.getpid(), "forge": adapter.name, "at": _stamp(now), "done": st["done"], "errors": st["errors"], "last": st["last"], "running": running}
        if exit_code is not None:
            row["exit"] = exit_code
        _write_json(heartbeat_path, row)

    if not dry_run:
        os.makedirs(state_dir, exist_ok=True)
        _append(manifest, {"status": "run-start", "at": _stamp(now), "args": {"slugs": sorted(want) if want else None, "round_from": round_from, "round_to": round_to, "limit": limit}})
        with open(pidfile, "w") as fh:
            fh.write("%d\n" % os.getpid())
        beat(True)
    code = 0
    fails = 0
    try:
        for rnd in range(round_from, round_to + 1):
            for entry in entries:
                slug = entry["slug"]
                have = adapter.counts(slug)
                for role in roles:
                    if have.get(role, 0) >= min(rnd, targets[(slug, role)]):
                        continue
                    if dry_run:
                        out("%d %s %s %s" % (rnd, slug, role, adapter.build_spec(entry, role, rnd)[0]))
                        st["done"] += 1
                        if limit and st["done"] >= limit:
                            return 0
                        continue
                    if not wait("light", 1800, 30, sleep, time.monotonic):
                        out("no headroom for 30 minutes: stopping")
                        code = 2
                        return code
                    t0 = now()
                    try:
                        tag, spec = adapter.build_spec(entry, role, rnd)
                        gen = _generate(pf, spec, owner)
                        if not good_image(gen.data):
                            raise SlotFillError("the picture is blank or tiny; left in the PromptForge gallery, not linked")
                        rec = adapter.link(slug, role, tag, rnd, spec, gen.job, gen.name, gen.data)
                        row = {"slug": slug, "role": role, "round": rnd, "tag": tag, "status": "linked", "file": (rec or {}).get("file"), "gallery": gen.name}
                        fails = 0
                    except (SlotFillError, OSError, ValueError) as exc:
                        row = {"slug": slug, "role": role, "round": rnd, "status": "error", "error": str(exc)[:200]}
                        fails += 1
                        st["errors"] += 1
                    row["seconds"] = round(now() - t0)
                    row["at"] = _stamp(now)
                    _append(manifest, row)
                    st["done"] += 1
                    st["last"] = {k: row.get(k) for k in ("slug", "role", "round", "tag", "status")}
                    beat(True)
                    out("[r%d] %s %s %s %s %ds" % (rnd, slug, role, row.get("tag", ""), row["status"], row["seconds"]))
                    if fails >= FAIL_STOP:
                        out("three failures in a row: stopping")
                        code = 1
                        return code
                    if limit and st["done"] >= limit:
                        return 0
        return 0
    finally:
        if not dry_run:
            try:
                os.unlink(pidfile)
            except OSError:
                pass
            beat(False, code)
