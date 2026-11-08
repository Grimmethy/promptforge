"""Image repair (inpaint): fix the masked part of a picture, leave the rest alone.

The picture is flattened onto a neutral grey, scaled so its long side is about SDXL's working size,
and sent to ComfyUI with a mask (white = repaint; only that is repainted, transparent background outside it is left alone). The result is scaled back and pasted only through
the feathered mask, so every unmasked pixel is exactly what came in.
"""

import io

from PIL import Image, ImageChops, ImageFilter

WORK = 1024          # long side sent to the model
GREY = (128, 128, 128)


def _snap(n, step=8):
    return max(64, int(round(n / float(step))) * step)


def work_size(w, h, work=WORK, snap=8):
    s = work / float(max(w, h))
    return _snap(w * s, snap), _snap(h * s, snap)


def prepare(img, mask, work=WORK, snap=8, background=GREY):
    """img RGBA/RGB, mask L (255 = repaint). Returns (rgb_png, mask_png, (w, h) sent).
    `snap` rounds the sent size to a multiple of it (8 for SDXL, 16 for Krea 2); `background` is the RGB colour transparent pixels are flattened onto."""
    img = img.convert("RGBA")
    mask = mask.convert("L").resize(img.size)
    flat = Image.new("RGB", img.size, tuple(background))
    flat.paste(img.convert("RGB"), mask=img.getchannel("A"))
    size = work_size(*img.size, work=work, snap=snap)
    out = []
    for im, mode in ((flat, Image.LANCZOS), (mask, Image.BILINEAR)):
        buf = io.BytesIO()
        im.resize(size, mode).save(buf, "PNG")
        out.append(buf.getvalue())
    return out[0], out[1], size


def finish(original, mask, generated, feather=3):
    """Paste `generated` into `original` through `mask`. Returns RGBA at the original size."""
    original = original.convert("RGBA")
    mask = mask.convert("L").resize(original.size)
    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(feather))
    gen = generated.convert("RGB").resize(original.size, Image.LANCZOS).convert("RGBA")
    out = Image.composite(gen, original, mask)
    # repainted pixels become opaque; everything outside the mask keeps its alpha (a transparent background stays so)
    alpha = ImageChops.lighter(original.getchannel("A"), mask)
    out.putalpha(alpha)
    return out
