"""The slot-fill commands: `python -m scripts.slot_fill run|status|report --forge characters|objects ...` (run it from the repo root with the repo's .venv).

run     fill the slots breadth first (see engine.run). It asks forge-mode to pause the pipeline first and to restore it last when that command exists, takes the PromptForge GPU
        session before the engine and releases it after (also on an error). --dry-run only prints the plan and changes nothing.
status  slots filled and open per entry and role, and the disk used by the pictures the manifest names.
report  write data/slot-fill-runs/<stamp>/summary.md and one contact sheet per entry for the last run (--since ISO picks the rows by time) and print the summary path.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

from . import engine, headroom, report
from .adapters.characterforge import CharacterForgeAdapter
from .adapters.objectforge import ObjectForgeAdapter
from .adapter import Adapter  # noqa: F401  (documented interface)
from .errors import SlotFillError
from .pf_client import PFClient

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
NO_FORGE_MODE = "forge-mode command not found: stop the pipeline, its drains and the P40 VM yourself, then continue"


def forge_mode(arg, which=shutil.which, runner=subprocess.run, out=print, check=headroom.check):
    """Pause (on) or restore (off) the pipeline through the `forge-mode` command. True to go on. Without the command: say what to stop by hand and check headroom once."""
    if which("forge-mode") is None:
        if arg == "on":
            out(NO_FORGE_MODE)
            ok, why = check("light")
            if not ok:
                out("headroom check (light): not enough room right now (%s); the engine will wait for it before every picture" % "; ".join(why))
        return True
    r = runner(["forge-mode", arg])
    if r.returncode != 0:
        out("forge-mode %s failed (exit %s)" % (arg, r.returncode))
        return False
    return True


def _gallery_dir(gallery_dir):
    return gallery_dir or os.environ.get("PROMPTFORGE_GALLERY_DIR") or os.path.join(REPO, "gallery")


def disk_used_mb(manifest, gallery_dir):
    """Megabytes the manifest's pictures take in the PromptForge gallery (a missing file counts 0)."""
    total = 0
    try:
        with open(manifest) as fh:
            for line in fh:
                try:
                    g = json.loads(line).get("gallery")
                except ValueError:
                    continue
                if g:
                    try:
                        total += os.path.getsize(os.path.join(gallery_dir, g))
                    except OSError:
                        pass
    except OSError:
        pass
    return total / (1024.0 * 1024.0)


def status(adapter, state_dir, gallery_dir, out=print):
    roles = adapter.roles()
    filled = wanted = 0
    for e in adapter.entries():
        counts = adapter.counts(e["slug"])
        cells = []
        for r in roles:
            t = adapter.target(e, r)
            n = counts.get(r, 0)
            filled += min(n, t)
            wanted += t
            cells.append("%s %d/%d" % (r, n, t))
        out("%s: %s" % (e["slug"], ", ".join(cells)))
    out("total: %d filled, %d open" % (filled, wanted - filled))
    out("disk used: %.1f MB" % disk_used_mb(os.path.join(state_dir, adapter.name + ".jsonl"), _gallery_dir(gallery_dir)))
    return 0


def main(argv=None, adapters=None, pf=None, run=engine.run, forge_mode_fn=forge_mode, state_dir=None, gallery_dir=None, out=print, err=None, report_fn=report.make_report, out_root=None):
    err = err or (lambda m: print(m, file=sys.stderr))
    adapters = adapters or {"characters": CharacterForgeAdapter, "objects": ObjectForgeAdapter}
    ap = argparse.ArgumentParser(prog="python -m scripts.slot_fill")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("run", "status", "report"):
        p = sub.add_parser(name)
        p.add_argument("--forge", default="characters", choices=sorted(adapters))
        if name == "report":
            p.add_argument("--since", default=None)
        if name == "run":
            p.add_argument("--slugs", default="")
            p.add_argument("--round-from", type=int, default=1)
            p.add_argument("--round-to", type=int, default=None)
            p.add_argument("--limit", type=int, default=0)
            p.add_argument("--dry-run", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    if a.cmd not in ("run", "status", "report"):
        err("usage: python -m scripts.slot_fill run|status|report --forge characters|objects [--slugs a,b] [--round-from N] [--round-to M] [--limit N] [--dry-run]")
        return 2
    sd = state_dir or engine.STATE_DIR
    try:
        adapter = adapters[a.forge]()
        if a.cmd == "status":
            return status(adapter, sd, gallery_dir, out)
        if a.cmd == "report":
            out(report_fn(adapter.name, adapter=adapter, state_dir=state_dir, gallery_dir=gallery_dir, out_root=out_root, since=a.since))
            return 0
        pf = pf or PFClient()
        kw = dict(slugs=[s for s in a.slugs.split(",") if s], round_from=a.round_from, round_to=a.round_to, limit=a.limit, state_dir=state_dir)
        if a.dry_run:
            try:
                return run(adapter, pf, dry_run=True, out=out, **kw)
            except NotImplementedError:
                for e in adapter.entries():                       # a forge without linking can still list what it wants
                    out("%s\t%s\t%s" % (e["slug"], e.get("category", ""), e.get("name", "")))
                return 0
        if not getattr(adapter, "linking", True):
            err("%s has no linking yet: only --dry-run works for it (a separate task adds linking)" % a.forge)
            return 3
        if not forge_mode_fn("on"):
            return 2
        acquired = False
        try:
            pf.gpu_acquire("slot-fill")
            acquired = True
            return run(adapter, pf, out=out, **kw)
        finally:
            if acquired:
                try:
                    pf.gpu_release()
                except SlotFillError as e:
                    err("could not release the GPU session: %s" % e)
            forge_mode_fn("off")
    except NotImplementedError as e:
        err(str(e))
        return 3
    except SlotFillError as e:
        err(str(e))
        return 2
