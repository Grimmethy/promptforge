"""Clean copies of images and videos: new files with no embedded generation metadata (checkpoint, LoRAs, prompt, seed ...).

A generated PNG carries the whole ComfyUI graph in a text chunk, a Tungsten WebP carries it in EXIF, an A1111 file in a `parameters` chunk.
strip_image() decodes the pixels and writes a NEW image without any text chunk, EXIF, XMP, ICC profile or comment; the caller keeps the
original (the gallery is never edited) and every clean copy is recorded in data/clean-exports.jsonl, which names the SOURCE and never holds
prompt text, so a published file can always be traced back to its recipe.

What this does NOT do: it removes embedded metadata only. It does not change the visual style, remove an invisible watermark a generator
may add, or release anyone from a platform's AI-disclosure rules or a model's licence terms.
"""
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import time

from PIL import Image, JpegImagePlugin

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "clean-exports.jsonl")
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS"}
_PNG_TEXT = {b"tEXt", b"iTXt", b"zTXt"}
_WEBP_KEEP = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH"}
VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v")


class CleanError(ValueError):
    """The file cannot be cleaned (unsupported, animated, unreadable, or metadata would remain)."""


def _kind(data):
    if data[:8] == _PNG_SIG:
        return "PNG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data[:2] == b"\xff\xd8":
        return "JPEG"
    return None


def _png_blocks(data):
    out, i = [], 8
    while i + 8 <= len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + n]
        if typ in _PNG_TEXT:
            kw = body.split(b"\x00", 1)[0].decode("latin-1", "replace")
            out.append("%s:%s" % (typ.decode("latin-1"), kw))
        elif typ not in _PNG_KEEP:
            out.append(typ.decode("latin-1", "replace"))
        i += 12 + n
        if typ == b"IEND":
            break
    return out


def _webp_blocks(data):
    out, i = [], 12
    while i + 8 <= len(data):
        typ = data[i:i + 4]
        n = struct.unpack("<I", data[i + 4:i + 8])[0]
        if typ == b"VP8X":
            flags = data[i + 8]
            for bit, name in ((0x20, "ICCP"), (0x08, "EXIF"), (0x04, "XMP ")):
                if flags & bit:
                    out.append(name.strip())
        elif typ not in _WEBP_KEEP:
            out.append(typ.decode("latin-1", "replace").strip())
        i += 8 + n + (n & 1)
    return out


def _jpeg_blocks(data):
    out, i = [], 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        m = data[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        if m in (0xD9, 0xDA):
            break
        n = struct.unpack(">H", data[i + 2:i + 4])[0]
        body = data[i + 4:i + 2 + n]
        if m == 0xFE:
            out.append("COM")
        elif 0xE0 <= m <= 0xEF and not (m == 0xE0 and body[:5] == b"JFIF\x00"):
            out.append("APP%d" % (m - 0xE0))
        i += 2 + n
    return out


def leftover_metadata(data):
    """The metadata blocks present in the image bytes (names such as 'tEXt:prompt', 'EXIF', 'ICCP', 'APP1', 'COM'); [] when none.
    Read by walking the container bytes, not from PIL's .info."""
    kind = _kind(data)
    if kind == "PNG":
        return _png_blocks(data)
    if kind == "WEBP":
        return _webp_blocks(data)
    if kind == "JPEG":
        return _jpeg_blocks(data)
    raise CleanError("not a PNG, WebP or JPEG file")


def strip_image(data):
    """(clean_bytes, report) for image bytes. PNG stays PNG and WebP stays WebP, both lossless; a JPEG is RE-ENCODED at quality 95
    (JPEG has no lossless pixel copy through PIL). Raises CleanError for an unsupported or animated file, or if any metadata would remain.
    report = {format, size_before, size_after, removed: [block names found in the source]}."""
    kind = _kind(data)
    if kind is None:
        raise CleanError("unsupported file type: only PNG, WebP and JPEG images can be cleaned")
    removed = leftover_metadata(data)
    try:
        im = Image.open(io.BytesIO(data))
        if getattr(im, "n_frames", 1) > 1:
            raise CleanError("animated images are not supported")
        im.load()
    except CleanError:
        raise
    except Exception as e:
        raise CleanError("cannot decode the image: %s" % e)
    sampling = JpegImagePlugin.get_sampling(im) if kind == "JPEG" and im.mode in ("RGB", "L") else -1
    clean = im.copy()
    keep = {}
    if "transparency" in im.info and kind == "PNG":
        keep["transparency"] = im.info["transparency"]      # pixel data for palette/greyscale PNGs, not metadata
    clean.info = keep
    buf = io.BytesIO()
    if kind == "PNG":
        clean.save(buf, "PNG", optimize=True)
    elif kind == "WEBP":
        clean.save(buf, "WEBP", lossless=True, exact=True, method=4)
    else:
        clean.save(buf, "JPEG", quality=95, subsampling=sampling)
    out = buf.getvalue()
    left = leftover_metadata(out)
    if left:
        raise CleanError("metadata would remain after cleaning: %s" % ", ".join(left))
    return out, {"format": kind, "size_before": len(data), "size_after": len(out), "removed": removed}


def strip_video(src, dst):
    """Copy video `src` to `dst` with all container metadata and chapters dropped, no re-encode (needs ffmpeg on PATH)."""
    ff = shutil.which("ffmpeg")
    if not ff:
        raise CleanError("ffmpeg is not installed, so videos cannot be cleaned")
    if os.path.exists(dst):
        raise CleanError("refusing to overwrite %s" % dst)
    r = subprocess.run([ff, "-nostdin", "-v", "error", "-i", src, "-map", "0", "-map_metadata", "-1", "-map_chapters", "-1",
                        "-c", "copy", dst], capture_output=True, text=True)
    if r.returncode != 0:
        if os.path.exists(dst):
            os.remove(dst)
        raise CleanError("ffmpeg failed: %s" % (r.stderr.strip()[:300] or r.returncode))
    return {"format": os.path.splitext(dst)[1].lstrip(".").upper(), "size_before": os.path.getsize(src), "size_after": os.path.getsize(dst), "removed": ["container metadata"]}


def log_export(source, clean_bytes_or_path, removed, log_path=None):
    """Append one line to the internal export log: {at, source, sha256, bytes, removed}. Never any prompt text."""
    h = hashlib.sha256()
    if isinstance(clean_bytes_or_path, (bytes, bytearray)):
        h.update(clean_bytes_or_path)
        n = len(clean_bytes_or_path)
    else:
        n = os.path.getsize(clean_bytes_or_path)
        with open(clean_bytes_or_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    path = os.path.abspath(log_path or LOG_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": str(source), "sha256": h.hexdigest(),
                       "bytes": n, "removed": list(removed)}, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
