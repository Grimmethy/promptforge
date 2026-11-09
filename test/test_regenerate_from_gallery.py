"""Regenerate from a gallery image (2026-11-08): POST /api/regenerate accepts `from_gallery` (a gallery relpath) instead of an uploaded picture,
so the UI can redraw a tile without sending its bytes through the browser. The path must stay inside the gallery, an upload wins when both are sent,
prompt is still required, and the job spec records the relpath as the source. No ComfyUI, no GPU, comfy.start is mocked
(python3 -m unittest discover -s test -p 'test_*.py')."""
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import app as pf  # noqa: E402

KREA = "krea2:krea2_turbo_int8_convrot.safetensors"
SDXL = "novaFurryXL_ilV140.safetensors"
REL = "2026-11/abcd1234_0.png"


def png_bytes(size=(120, 180), rgba=(10, 20, 30, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", size, rgba).save(buf, "PNG")
    buf.seek(0)
    return buf


class FromGalleryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "2026-11"))
        Image.new("RGBA", (96, 160), (200, 50, 50, 255)).save(os.path.join(self.tmp.name, REL), "PNG")
        self.outside = os.path.join(os.path.dirname(self.tmp.name), "outside_secret.png")
        Image.new("RGBA", (64, 64), (1, 2, 3, 255)).save(self.outside, "PNG")
        self.addCleanup(lambda: os.path.exists(self.outside) and os.remove(self.outside))
        self.client = pf.app.test_client()
        self.started = []

        def fake_start(j, gallery_dir):
            self.started.append(j)
            return "jid123"
        for p in (mock.patch.object(pf, "GALLERY_DIR", self.tmp.name),
                  mock.patch.object(pf.comfy, "start", side_effect=fake_start),
                  mock.patch.object(pf, "list_checkpoints", return_value=[SDXL])):
            p.start()
            self.addCleanup(p.stop)

    def post(self, **form):
        data = {"prompt": "purple nightgown"}
        data.update(form)
        return self.client.post("/api/regenerate", data=data, content_type="multipart/form-data")

    def test_from_gallery_starts_a_job_from_that_file_and_records_the_relpath_as_the_source(self):
        r = self.post(from_gallery=REL, model=KREA, denoise="0.6")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = self.started[0]
        self.assertEqual(j["spec"]["source"], REL)
        self.assertEqual(j["spec"]["denoise"], 0.6)
        self.assertEqual(j["model"], KREA)
        sent = Image.open(io.BytesIO(j["repair_image_png"])).convert("RGB")
        self.assertEqual(sent.getpixel((3, 3)), (200, 50, 50))      # the gallery file's pixels, not a placeholder

    def test_a_path_outside_the_gallery_is_refused_and_starts_nothing(self):
        for bad in ("../outside_secret.png", "2026-11/../../outside_secret.png", self.outside):
            r = self.post(from_gallery=bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("inside the gallery", r.get_json()["error"])
        self.assertEqual(self.started, [])

    def test_a_missing_gallery_file_is_refused_and_starts_nothing(self):
        r = self.post(from_gallery="2026-11/nothing_0.png")
        self.assertEqual(r.status_code, 400)
        self.assertIn("not found", r.get_json()["error"])
        self.assertEqual(self.started, [])

    def test_an_uploaded_image_wins_over_from_gallery(self):
        r = self.post(from_gallery=REL, image=(png_bytes(rgba=(0, 255, 0, 255)), "up.png"))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = self.started[0]
        self.assertEqual(j["spec"]["source"], "up.png")
        sent = Image.open(io.BytesIO(j["repair_image_png"])).convert("RGB")
        self.assertEqual(sent.getpixel((3, 3)), (0, 255, 0))

    def test_prompt_is_still_required_and_so_is_some_picture(self):
        r = self.client.post("/api/regenerate", data={"from_gallery": REL}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/regenerate", data={"prompt": "x"}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.started, [])

    def test_krea2_and_sdxl_defaults_are_unchanged_for_a_gallery_source(self):
        self.post(from_gallery=REL, model=KREA)
        self.post(from_gallery=REL, model=SDXL)
        k, s = self.started
        self.assertEqual((k["steps"], k["cfg"], k["sampler"]), (8, 1.0, "euler"))
        self.assertFalse(k["pose"])
        self.assertEqual((s["steps"], s["cfg"], s["sampler"]), (28, 5.0, "dpmpp_2m_sde"))


if __name__ == "__main__":
    unittest.main()
