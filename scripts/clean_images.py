#!/usr/bin/env python3
"""Write clean copies (no embedded generation metadata) of images and videos into --out; never edits an input.

  python3 scripts/clean_images.py <file-or-folder>... --out <dir> [--video]

PNG, WebP and JPEG images are cleaned (see server/core/clean.py); --video also cleans .mp4/.webm/.mov/.m4v with ffmpeg (no re-encode).
Relative paths under a folder argument are kept. The out folder must not be inside a source folder, an existing file is never overwritten,
and each copy is logged in data/clean-exports.jsonl (source and hash, no prompt text). Exit status 1 when any file failed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from core import clean  # noqa: E402

IMG_EXT = (".png", ".webp", ".jpg", ".jpeg")


def _inside(child, parent):
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent + os.sep)


def collect(paths, video):
    """[(source file, relative path under its argument)] for the files named or found in the folders."""
    exts = IMG_EXT + (clean.VIDEO_EXT if video else ())
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in sorted(files):
                    if f.lower().endswith(exts):
                        full = os.path.join(root, f)
                        out.append((full, os.path.relpath(full, p)))
        elif os.path.isfile(p):
            out.append((p, os.path.basename(p)))
        else:
            raise SystemExit("not found: %s" % p)
    return out


def main(argv=None, log_path=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--video", action="store_true", help="also clean videos (needs ffmpeg)")
    a = ap.parse_args(argv)
    for p in a.paths:
        src_dir = p if os.path.isdir(p) else os.path.dirname(os.path.abspath(p))
        if _inside(a.out, src_dir):
            print("refusing: the out folder %s is inside the source folder %s" % (a.out, src_dir), file=sys.stderr)
            return 2
    failed = 0
    for src, rel in collect(a.paths, a.video):
        dst = os.path.join(a.out, rel)
        try:
            if os.path.exists(dst):
                raise clean.CleanError("refusing to overwrite %s" % dst)
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            if src.lower().endswith(clean.VIDEO_EXT):
                report = clean.strip_video(src, dst)
                clean.log_export("file:" + os.path.abspath(src), dst, report["removed"], log_path)
            else:
                with open(src, "rb") as fh:
                    out, report = clean.strip_image(fh.read())
                with open(dst, "xb") as fh:
                    fh.write(out)
                clean.log_export("file:" + os.path.abspath(src), out, report["removed"], log_path)
            print("%s: removed %s (%d -> %d bytes)" % (rel, ", ".join(report["removed"]) or "nothing", report["size_before"], report["size_after"]))
        except (clean.CleanError, OSError) as e:
            failed += 1
            print("%s: FAILED %s" % (rel, e), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
