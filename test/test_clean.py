"""Clean copies: server/core/clean.py strips embedded generation metadata into NEW files, the routes serve them, the CLI writes them.
Synthetic PIL images with a fake ComfyUI graph in their metadata, temp folders only, no ComfyUI, no GPU, no network
(python3 -m unittest test.test_clean)."""
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from PIL import Image, PngImagePlugin  # noqa: E402

import app as pf  # noqa: E402
import clean_images  # noqa: E402
from core import clean  # noqa: E402

SECRETS = (b"LoraLoader", b"ckpt_name", b"SECRET PROMPT TEXT", b"hidden-lora-v1")
GRAPH = json.dumps({"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "hidden-ckpt.safetensors"}},
                    "2": {"class_type": "LoraLoader", "inputs": {"lora_name": "hidden-lora-v1.safetensors", "strength_model": 0.5}},
                    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "SECRET PROMPT TEXT"}}})


def pixels():
    im = Image.new("RGBA", (16, 12))
    im.putdata([(x * 15 % 256, y * 20 % 256, (x + y) * 9 % 256, 255 if (x + y) % 5 else 90) for y in range(12) for x in range(16)])
    return im


def png_bytes(mode="RGBA"):
    im = pixels().convert(mode)
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", GRAPH)
    info.add_itxt("workflow", GRAPH)
    buf = io.BytesIO()
    im.save(buf, "PNG", pnginfo=info, dpi=(300, 300), icc_profile=b"\x00" * 20 if False else None)
    return buf.getvalue()


def webp_bytes():
    exif = Image.Exif()
    exif[0x9286] = b"UNICODE\x00" + GRAPH.encode("utf-16-be")
    buf = io.BytesIO()
    pixels().save(buf, "WEBP", lossless=True, exif=exif.tobytes())
    return buf.getvalue()


def jpeg_bytes():
    exif = Image.Exif()
    exif[0x9286] = b"ASCII\x00\x00\x00" + GRAPH.encode()
    buf = io.BytesIO()
    pixels().convert("RGB").save(buf, "JPEG", quality=90, exif=exif.tobytes(), comment=GRAPH.encode())
    return buf.getvalue()


def has_secret(data):
    return [s for s in SECRETS if s in data]


class StripTest(unittest.TestCase):
    def test_the_sources_really_carry_the_trail(self):
        for data in (png_bytes(), webp_bytes(), jpeg_bytes()):
            self.assertTrue(has_secret(data) or b"UNICODE" in data)
            self.assertTrue(clean.leftover_metadata(data))

    def test_png_loses_every_trace_and_keeps_every_pixel(self):
        src = png_bytes()
        out, rep = clean.strip_image(src)
        self.assertEqual(has_secret(out), [])
        self.assertEqual(clean.leftover_metadata(out), [])
        self.assertEqual(Image.open(io.BytesIO(out)).tobytes(), Image.open(io.BytesIO(src)).tobytes())
        self.assertEqual((rep["format"], Image.open(io.BytesIO(out)).mode), ("PNG", "RGBA"))
        self.assertIn("tEXt:prompt", rep["removed"])
        self.assertIn("iTXt:workflow", rep["removed"])
        self.assertEqual((rep["size_before"], rep["size_after"]), (len(src), len(out)))

    def test_rgb_and_palette_png_keep_mode_and_pixels(self):
        for mode in ("RGB", "L"):
            out, _ = clean.strip_image(png_bytes(mode))
            self.assertEqual(Image.open(io.BytesIO(out)).mode, mode)
            self.assertEqual(has_secret(out), [])
        pal = Image.new("P", (4, 4), 3)
        pal.putpalette(list(range(256)) * 3)
        pal.info["transparency"] = 3
        buf = io.BytesIO()
        pal.save(buf, "PNG", transparency=3)
        out, _ = clean.strip_image(buf.getvalue())
        back = Image.open(io.BytesIO(out))
        self.assertEqual((back.mode, back.info.get("transparency")), ("P", 3))

    def test_webp_exif_is_removed_and_stays_lossless(self):
        src = webp_bytes()
        out, rep = clean.strip_image(src)
        self.assertEqual(has_secret(out), [])
        self.assertNotIn(b"UNICODE", out)
        self.assertEqual(clean.leftover_metadata(out), [])
        self.assertEqual(Image.open(io.BytesIO(out)).tobytes(), Image.open(io.BytesIO(src)).tobytes())
        self.assertEqual(rep["format"], "WEBP")
        self.assertIn("EXIF", rep["removed"])

    def test_jpeg_loses_exif_and_comment_and_stays_decodable(self):
        src = jpeg_bytes()
        out, rep = clean.strip_image(src)
        self.assertEqual(has_secret(out), [])
        self.assertEqual(clean.leftover_metadata(out), [])
        back = Image.open(io.BytesIO(out))
        self.assertEqual((back.format, back.size), ("JPEG", (16, 12)))
        self.assertTrue({"APP1", "COM"} <= set(rep["removed"]))

    def test_a_file_with_nothing_to_remove_succeeds(self):
        buf = io.BytesIO()
        pixels().save(buf, "PNG")
        out, rep = clean.strip_image(buf.getvalue())
        self.assertEqual(rep["removed"], [])
        self.assertEqual(Image.open(io.BytesIO(out)).tobytes(), pixels().tobytes())

    def test_animated_and_unsupported_files_are_refused(self):
        frames = [pixels(), pixels().transpose(Image.FLIP_LEFT_RIGHT)]
        buf = io.BytesIO()
        frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], lossless=True)
        with self.assertRaises(clean.CleanError):
            clean.strip_image(buf.getvalue())
        for bad in (b"", b"GIF89a....", b"not an image at all", b"\x89PNG\r\n\x1a\nbroken"):
            with self.assertRaises(clean.CleanError):
                clean.strip_image(bad)

    def test_webp_private_chunks_are_reported_and_a_leak_after_saving_is_refused(self):
        data = webp_bytes()
        chunk = b"ABCD" + struct.pack("<I", 3) + b"xyz" + b"\x00"
        riff = data[:4] + struct.pack("<I", len(data) - 8 + len(chunk)) + data[8:] + chunk
        self.assertIn("ABCD", clean.leftover_metadata(riff))
        with mock.patch.object(clean, "leftover_metadata", side_effect=[[], ["tEXt:evil"]]):
            with self.assertRaises(clean.CleanError):
                clean.strip_image(png_bytes())

    def test_leftover_metadata_walks_the_bytes_not_pil(self):
        data = png_bytes() + b""
        extra = struct.pack(">I", 3) + b"zzZz" + b"abc" + struct.pack(">I", 0)
        # insert a private chunk before IEND
        iend = data.rindex(b"IEND") - 4
        tampered = data[:iend] + extra + data[iend:]
        self.assertIn("zzZz", clean.leftover_metadata(tampered))


class LogTest(unittest.TestCase):
    def test_the_log_line_names_the_source_and_holds_no_prompt_text(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "sub", "clean-exports.jsonl")
            out, rep = clean.strip_image(png_bytes())
            clean.log_export("gallery:2026-11/x_0.png", out, rep["removed"], lp)
            clean.log_export("file:/a/b.png", out, [], lp)
            raw = open(lp, "rb").read()
            self.assertEqual(has_secret(raw), [])
            rows = [json.loads(l) for l in raw.decode().splitlines()]
            self.assertEqual([r["source"] for r in rows], ["gallery:2026-11/x_0.png", "file:/a/b.png"])
            self.assertEqual(rows[0]["bytes"], len(out))
            self.assertEqual(len(rows[0]["sha256"]), 64)
            self.assertIn("tEXt:prompt", rows[0]["removed"])


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "log", "clean-exports.jsonl")
        os.makedirs(os.path.join(self.tmp.name, "gal", "2026-11"))
        self.paths = {}
        for n, data in (("a_0.png", png_bytes()), ("b_0.webp", webp_bytes()), ("c_0.jpg", jpeg_bytes()), ("d_0.png", png_bytes("RGB"))):
            p = os.path.join(self.tmp.name, "gal", "2026-11", n)
            with open(p, "wb") as fh:
                fh.write(data)
            self.paths[n] = p
        open(os.path.join(self.tmp.name, "gal", "2026-11", "anim_0.webp"), "wb").write(b"RIFF\x00\x00\x00\x00WEBPVP8X")
        self.patches = [mock.patch.object(pf, "GALLERY_DIR", os.path.join(self.tmp.name, "gal")), mock.patch.object(clean, "LOG_PATH", self.log)]
        for p in self.patches:
            p.start()
        self.c = pf.app.test_client()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_single_clean_copy_is_a_download_and_the_original_is_untouched(self):
        p = self.paths["a_0.png"]
        before = (open(p, "rb").read(), os.stat(p).st_mtime_ns)
        r = self.c.get("/api/gallery-clean/2026-11/a_0.png")
        self.assertEqual((r.status_code, r.mimetype), (200, "image/png"))
        self.assertIn('filename="clean_a_0.png"', r.headers["Content-Disposition"])
        self.assertEqual(has_secret(r.data), [])
        self.assertEqual(clean.leftover_metadata(r.data), [])
        self.assertEqual((open(p, "rb").read(), os.stat(p).st_mtime_ns), before)       # the gallery file is byte- and mtime-identical
        self.assertTrue(has_secret(before[0]))                                          # and still carries its recipe
        rows = [json.loads(l) for l in open(self.log).read().splitlines()]
        self.assertEqual([r_["source"] for r_ in rows], ["gallery:2026-11/a_0.png"])

    def test_each_format_comes_back_clean(self):
        for n, mime in (("b_0.webp", "image/webp"), ("c_0.jpg", "image/jpeg")):
            r = self.c.get("/api/gallery-clean/2026-11/" + n)
            self.assertEqual((r.status_code, r.mimetype), (200, mime))
            self.assertEqual(has_secret(r.data), [])

    def test_unknown_unsafe_and_unsupported_paths(self):
        self.assertEqual(self.c.get("/api/gallery-clean/2026-11/nope.png").status_code, 404)
        self.assertEqual(self.c.get("/api/gallery-clean/../../etc/passwd").status_code, 404)
        self.assertEqual(self.c.get("/api/gallery-clean/2026-11/anim_0.webp").status_code, 415)

    def test_zip_of_several_and_its_limits(self):
        r = self.c.post("/api/gallery-clean", json={"names": ["2026-11/a_0.png", "2026-11/b_0.webp", "2026-11/d_0.png"]})
        self.assertEqual((r.status_code, r.mimetype), (200, "application/zip"))
        z = zipfile.ZipFile(io.BytesIO(r.data))
        self.assertEqual(sorted(z.namelist()), ["clean_a_0.png", "clean_b_0.webp", "clean_d_0.png"])
        for n in z.namelist():
            self.assertEqual(has_secret(z.read(n)), [])
        self.assertEqual(len(open(self.log).read().splitlines()), 3)
        self.assertEqual(self.c.post("/api/gallery-clean", json={"names": []}).status_code, 400)
        self.assertEqual(self.c.post("/api/gallery-clean", json={"names": ["x"] * 51}).status_code, 400)
        self.assertEqual(self.c.post("/api/gallery-clean", json={"names": ["2026-11/nope.png"]}).status_code, 404)
        self.assertEqual(self.c.post("/api/gallery-clean", json={"names": ["2026-11/anim_0.webp"]}).status_code, 415)

    def test_the_lightbox_has_the_button(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("'/api/gallery-clean/' + file", js)
        self.assertIn("Download clean copy", js)


class CliTest(unittest.TestCase):
    def test_writes_clean_copies_keeps_paths_and_never_edits_or_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            src, out, lp = os.path.join(d, "src"), os.path.join(d, "out"), os.path.join(d, "log.jsonl")
            os.makedirs(os.path.join(src, "sub"))
            files = {"a.png": png_bytes(), "sub/b.webp": webp_bytes(), "sub/c.jpg": jpeg_bytes()}
            for rel, data in files.items():
                open(os.path.join(src, rel), "wb").write(data)
            open(os.path.join(src, "notes.txt"), "w").write("ignored")
            self.assertEqual(clean_images.main([src, "--out", out], lp), 0)
            for rel in files:
                self.assertEqual(has_secret(open(os.path.join(out, rel), "rb").read()), [])
                self.assertEqual(open(os.path.join(src, rel), "rb").read(), files[rel])        # inputs untouched
            self.assertFalse(os.path.exists(os.path.join(out, "notes.txt")))
            self.assertEqual(len(open(lp).read().splitlines()), 3)
            self.assertEqual(clean_images.main([src, "--out", out], lp), 1)                  # second run: nothing is overwritten
            self.assertEqual(open(lp).read().count("\n"), 3)

    def test_a_file_that_appears_between_the_check_and_the_write_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as out:
            open(os.path.join(d, "a.png"), "wb").write(png_bytes())
            open(os.path.join(out, "a.png"), "wb").write(b"KEEP ME")
            with mock.patch.object(os.path, "exists", return_value=False):
                self.assertEqual(clean_images.main([d, "--out", out], os.path.join(out, "l.jsonl")), 1)
            self.assertEqual(open(os.path.join(out, "a.png"), "rb").read(), b"KEEP ME")

    def test_refuses_an_out_folder_inside_the_source_and_a_missing_input(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "a.png"), "wb").write(png_bytes())
            self.assertEqual(clean_images.main([d, "--out", os.path.join(d, "clean")], os.path.join(d, "l.jsonl")), 2)
            self.assertFalse(os.path.exists(os.path.join(d, "clean")))
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            with self.assertRaises(SystemExit):
                clean_images.main([os.path.join(d, "missing"), "--out", os.path.join(other, "o")])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_video_metadata_is_dropped(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "v.mp4")
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.5", "-metadata", "comment=SECRET PROMPT TEXT",
                            "-pix_fmt", "yuv420p", src], check=True)
            self.assertIn(b"SECRET PROMPT TEXT", open(src, "rb").read())
            dst = os.path.join(d, "o", "v.mp4")
            os.makedirs(os.path.dirname(dst))
            clean.strip_video(src, dst)
            self.assertNotIn(b"SECRET PROMPT TEXT", open(dst, "rb").read())
            with self.assertRaises(clean.CleanError):
                clean.strip_video(src, dst)

    def test_video_without_ffmpeg_is_a_clear_error(self):
        with mock.patch.object(shutil, "which", return_value=None):
            with self.assertRaises(clean.CleanError):
                clean.strip_video("a.mp4", "b.mp4")


if __name__ == "__main__":
    unittest.main()
