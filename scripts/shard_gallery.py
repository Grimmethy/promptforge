#!/usr/bin/env python3
"""One-off: move a flat gallery/ into gallery/<YYYY-MM>/ sub-folders by file mtime.

Keeps each folder small so the OS file manager's thumbnailer doesn't choke, and
matches what the server now writes. Idempotent — re-running only moves any new
loose files. Also warms the .thumbs cache for every image.

    python scripts/shard_gallery.py
"""

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
GALLERY = os.path.join(ROOT, "gallery")
sys.path.insert(0, os.path.join(ROOT, "server"))


def main():
    if not os.path.isdir(GALLERY):
        print("no gallery/ — nothing to do")
        return
    moved = 0
    for f in sorted(os.listdir(GALLERY)):
        src = os.path.join(GALLERY, f)
        if not os.path.isfile(src) or not (f.endswith(".png") or f.endswith(".json")):
            continue
        month = time.strftime("%Y-%m", time.localtime(os.path.getmtime(src)))
        dstdir = os.path.join(GALLERY, month)
        os.makedirs(dstdir, exist_ok=True)
        os.rename(src, os.path.join(dstdir, f))
        moved += 1
    print(f"moved {moved} file(s) into month folders")

    try:
        from core import thumbs
    except Exception as exc:  # noqa: BLE001
        print(f"(skipping thumb warm: {exc})")
        return
    made = 0
    for root, dirs, files in os.walk(GALLERY):
        dirs[:] = [d for d in dirs if d != thumbs.THUMB_SUBDIR]
        for f in files:
            if f.endswith(".png"):
                rel = os.path.relpath(os.path.join(root, f), GALLERY)
                if thumbs.ensure_thumb(GALLERY, rel):
                    made += 1
    print(f"thumbnails ready for {made} image(s)")


if __name__ == "__main__":
    main()
