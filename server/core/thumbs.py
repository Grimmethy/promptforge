"""Cached WebP thumbnails for the gallery grid.

The grid shows every image at ~200 px but the originals are ~1.4 MB 1152x896 PNGs.
Thumbnails are written to a hidden ``.thumbs`` sub-dir inside each gallery folder
(dot-prefixed so the OS file manager ignores them; already covered by
``gallery/*`` in .gitignore). Generation is lazy — the first request for an image
creates its thumb, every later request serves the cached file.
"""

import os

from PIL import Image

THUMB_SUBDIR = ".thumbs"
_MAX = 512
_QUALITY = 78
_METHOD = 2   # webp encode effort; 2 is ~3x faster than the default 4, size ~same


def thumb_rel(png_rel):
    """Gallery-root-relative path of the thumbnail for a PNG relpath."""
    d, base = os.path.split(png_rel)
    return os.path.join(d, THUMB_SUBDIR, os.path.splitext(base)[0] + ".webp")


def _within(root, path):
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    return path == root or path.startswith(root + os.sep)


def ensure_thumb(gallery_dir, png_rel):
    """Return the absolute path of the (possibly just-created) thumbnail, or None
    if the source is missing / outside the gallery / can't be decoded."""
    src = os.path.abspath(os.path.join(gallery_dir, png_rel))
    if not (_within(gallery_dir, src) and os.path.isfile(src)):
        return None
    dst = os.path.join(gallery_dir, thumb_rel(png_rel))
    try:
        if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            return dst
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((_MAX, _MAX), Image.LANCZOS)
            tmp = dst + ".tmp"
            im.save(tmp, "WEBP", quality=_QUALITY, method=_METHOD)
        os.replace(tmp, dst)
        return dst
    except Exception as exc:  # noqa: BLE001
        print(f"[promptforge] thumb failed for {png_rel}: {exc}")
        return None
