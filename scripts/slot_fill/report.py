"""The run report: what the last run made, what failed, how long a picture took, which slots are still open, the disk used, and one contact sheet per entry for the owner's audit.

Reads the manifest the engine wrote (<state_dir>/<forge>.jsonl) and the pictures in the PromptForge gallery; writes only under data/slot-fill-runs/<stamp>/ (summary.md and
<slug>.jpg). Nothing else is ever created, changed or deleted. Pillow is used for the sheets.
"""
import json
import os
import statistics
import time

from . import engine
from .errors import SlotFillError

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
THUMB = 192
LABEL_H = 16
COLUMNS = 5


def last_run_rows(rows, since=None):
    """The rows of the last run: from the last 'run-start' row on, or every row at or after `since` (an ISO timestamp)."""
    if since:
        return [r for r in rows if str(r.get("at", "")) >= since]
    start = max((i for i, r in enumerate(rows) if r.get("status") == "run-start"), default=0)
    return rows[start:]


def read_rows(path):
    rows = []
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def contact_sheet(items, gallery_dir, dest):
    """items: [(file name in the gallery, label)]. Writes a JPEG of 192 px thumbnails, 5 columns, in the order given; returns (written?, [missing names])."""
    from PIL import Image, ImageDraw
    thumbs, missing = [], []
    for name, label in items:
        try:
            im = Image.open(os.path.join(gallery_dir, name)).convert("RGB")
        except (OSError, ValueError):
            missing.append(name)
            continue
        im.thumbnail((THUMB, THUMB))
        thumbs.append((im, label))
    if not thumbs:
        return False, missing
    rows = (len(thumbs) + COLUMNS - 1) // COLUMNS
    sheet = Image.new("RGB", (COLUMNS * THUMB, rows * (THUMB + LABEL_H)), (30, 30, 30))
    draw = ImageDraw.Draw(sheet)
    for i, (im, label) in enumerate(thumbs):
        x, y = (i % COLUMNS) * THUMB, (i // COLUMNS) * (THUMB + LABEL_H)
        sheet.paste(im, (x + (THUMB - im.width) // 2, y + (THUMB - im.height) // 2))
        draw.text((x + 3, y + THUMB + 2), label[:30], fill=(230, 230, 230))
    sheet.save(dest, "JPEG", quality=80)
    return True, missing


def make_report(adapter_name, adapter=None, state_dir=None, gallery_dir=None, out_root=None, since=None, now=time.time):
    """Write data/slot-fill-runs/<stamp>/summary.md (+ contact sheets) for the last run and return the summary path."""
    state_dir = state_dir or engine.STATE_DIR
    gallery_dir = gallery_dir or os.environ.get("PROMPTFORGE_GALLERY_DIR") or os.path.join(REPO, "gallery")
    out_root = out_root or os.path.join(REPO, "data", "slot-fill-runs")
    rows = last_run_rows(read_rows(os.path.join(state_dir, adapter_name + ".jsonl")), since)
    linked = [r for r in rows if r.get("status") == "linked"]
    errors = [r for r in rows if r.get("status") == "error"]
    secs = [r["seconds"] for r in linked if isinstance(r.get("seconds"), (int, float))]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now()))
    outdir = os.path.join(out_root, stamp)
    os.makedirs(outdir, exist_ok=True)
    disk = 0
    for r in linked:
        try:
            disk += os.path.getsize(os.path.join(gallery_dir, r.get("gallery") or ""))
        except OSError:
            pass
    by_slug = {}
    for r in linked:
        by_slug.setdefault(r["slug"], []).append(r)
    sheets, missing = {}, []
    for slug, rs in by_slug.items():
        ok, miss = contact_sheet([(r["gallery"], "%s/%s" % (r["role"], r.get("tag", ""))) for r in rs if r.get("gallery")], gallery_dir, os.path.join(outdir, slug + ".jpg"))
        missing += miss
        if ok:
            sheets[slug] = slug + ".jpg"
    lines = ["# Slot-fill run: %s" % adapter_name, ""]
    start = next((r.get("at") for r in rows if r.get("at")), "?")
    end = next((r.get("at") for r in reversed(rows) if r.get("at")), "?")
    lines += ["Window: %s to %s" % (start, end), "", "## Result", "",
              "- pictures made (linked): %d" % len(linked), "- errors: %d" % len(errors)]
    for msg in sorted({r.get("error", "") for r in errors}):
        lines.append("  - %s" % msg)
    if secs:
        lines.append("- seconds per picture: mean %.1f, median %.1f" % (statistics.mean(secs), statistics.median(secs)))
    lines.append("- disk used by the new pictures: %.1f MB" % (disk / (1024.0 * 1024.0)))
    lines += ["", "## Pictures per entry and role", ""]
    for slug in sorted(by_slug):
        per = {}
        for r in by_slug[slug]:
            per[r["role"]] = per.get(r["role"], 0) + 1
        lines.append("- %s: %s" % (slug, ", ".join("%s %d" % (k, per[k]) for k in sorted(per))))
    if not by_slug:
        lines.append("- none")
    lines += ["", "## Slots still open", ""]
    if adapter is None:
        lines.append("- not checked (no adapter given)")
    else:
        opened = 0
        try:
            for e in adapter.entries():
                have = adapter.counts(e["slug"])
                gaps = ["%s %d" % (role, adapter.target(e, role) - have.get(role, 0)) for role in adapter.roles() if adapter.target(e, role) - have.get(role, 0) > 0]
                if gaps:
                    opened += 1
                    lines.append("- %s: %s" % (e["slug"], ", ".join(gaps)))
            if not opened:
                lines.append("- none: every slot is at its target")
        except (SlotFillError, OSError, NotImplementedError) as exc:
            lines.append("- not available: %s" % exc)
    lines += ["", "## Contact sheets", ""]
    lines += ["- %s" % os.path.join(outdir, f) for f in sheets.values()] or ["- none"]
    if missing:
        lines += ["", "## Pictures missing from the gallery (skipped)", ""] + ["- %s" % m for m in missing]
    path = os.path.join(outdir, "summary.md")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path
