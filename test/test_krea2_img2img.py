"""Krea 2 image-to-image and inpaint (2026-11-08): /api/regenerate and /api/repair accept a krea2:<file> model and build a LoadImage ->
VAEEncode -> KSampler graph (no ControlNet; a mask goes through SetLatentNoiseMask), with Krea defaults, 16-multiple sizes, an optional
background colour for transparent art, and the denoise / source / background recorded in the job spec. No ComfyUI, no GPU, comfy.start is mocked
(python3 -m unittest discover -s test -p 'test_*.py')."""
import io
import os
import sys
import unittest
from unittest import mock

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import app as pf  # noqa: E402
from core import comfy, repair  # noqa: E402

KREA = "krea2:krea2_turbo_int8_convrot.safetensors"
SDXL = "novaFurryXL_ilV140.safetensors"


def job(**kw):
    j = {"id": "abcdef0123456789", "model": KREA, "positive": "a gnome in a purple nightgown", "negative": "", "image_name": "in.png",
         "seed": 5, "steps": 8, "cfg": 1.0, "sampler": "euler", "scheduler": "simple", "denoise": 0.7}
    j.update(kw)
    return j


def classes(g):
    return [v["class_type"] for v in g.values()]


class GraphTest(unittest.TestCase):
    def test_krea2_regenerate_graph_encodes_the_picture_and_samples_it_at_the_job_denoise(self):
        g = comfy.build_repair_graph(job())
        cl = classes(g)
        for need in ("UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "VAEEncode", "KSampler", "VAEDecode", "SaveImage"):
            self.assertIn(need, cl)
        for banned in ("EmptyLatentImage", "CheckpointLoaderSimple", "ControlNetLoader", "ControlNetApplyAdvanced", "SetLatentNoiseMask"):
            self.assertNotIn(banned, cl)
        ks = g["11"]["inputs"]
        self.assertEqual(ks["denoise"], 0.7)
        self.assertEqual(ks["latent_image"], ["23", 0])
        self.assertEqual(g["23"]["inputs"]["vae"], ["3", 0])
        self.assertEqual(g["12"]["inputs"]["vae"], ["3", 0])
        self.assertEqual(g["20"]["inputs"]["image"], "in.png")
        self.assertEqual(g["1"]["inputs"]["unet_name"], "krea2_turbo_int8_convrot.safetensors")
        self.assertEqual(g["2"]["inputs"]["type"], "krea2")

    def test_a_mask_routes_the_latent_through_set_latent_noise_mask(self):
        g = comfy.build_repair_graph(job(mask_name="m.png", grow=4))
        self.assertEqual(g["11"]["inputs"]["latent_image"], ["24", 0])
        self.assertEqual(g["24"]["class_type"], "SetLatentNoiseMask")
        self.assertEqual(g["24"]["inputs"]["samples"], ["23", 0])
        self.assertEqual(g["22"]["inputs"]["expand"], 4)

    def test_pose_is_never_wired_for_krea2(self):
        g = comfy.build_repair_graph(job(pose=True))
        self.assertNotIn("ControlNetApplyAdvanced", classes(g))
        self.assertEqual(g["11"]["inputs"]["positive"], ["6", 0])

    def test_loras_chain_into_the_krea2_repair_graph(self):
        g = comfy.build_repair_graph(job(loras=[{"name": "style.safetensors", "weight": 0.8}]))
        self.assertEqual(g["100"]["class_type"], "LoraLoader")
        self.assertEqual(g["11"]["inputs"]["model"], ["100", 0])
        self.assertEqual(g["6"]["inputs"]["clip"], ["100", 1])

    def test_an_sdxl_repair_job_still_builds_the_checkpoint_graph(self):
        g = comfy.build_repair_graph(job(model=SDXL, steps=20, cfg=5, sampler="euler", scheduler="normal", pose=False))
        self.assertIn("CheckpointLoaderSimple", classes(g))
        self.assertNotIn("UNETLoader", classes(g))

    def test_the_text_to_image_krea2_graph_is_unchanged(self):
        g = comfy.build_krea2_graph({"id": "abcdef0123456789", "model": KREA, "width": 1024, "height": 1024, "batch": 1, "seed": 1,
                                     "steps": 8, "cfg": 1.0, "sampler": "euler", "scheduler": "simple", "positive": "x"})
        self.assertEqual(sorted(g), ["1", "10", "11", "12", "13", "2", "3", "6", "7"])
        self.assertEqual(g["11"]["inputs"]["denoise"], 1.0)
        self.assertEqual(g["11"]["inputs"]["latent_image"], ["10", 0])


class PrepareTest(unittest.TestCase):
    def test_default_prepare_still_snaps_to_8_and_flattens_onto_grey(self):
        img = Image.new("RGBA", (100, 150), (0, 0, 0, 0))
        png, _, size = repair.prepare(img, Image.new("L", img.size, 0))
        self.assertEqual(size[0] % 8, 0)
        self.assertEqual(Image.open(io.BytesIO(png)).getpixel((1, 1)), repair.GREY)

    def test_krea2_snaps_to_16_and_a_background_colour_replaces_the_grey(self):
        img = Image.new("RGBA", (101, 157), (0, 0, 0, 0))
        png, _, size = repair.prepare(img, Image.new("L", img.size, 0), snap=16, background=(244, 232, 205))
        self.assertEqual((size[0] % 16, size[1] % 16), (0, 0))
        self.assertEqual(Image.open(io.BytesIO(png)).getpixel((1, 1)), (244, 232, 205))


class BackgroundParseTest(unittest.TestCase):
    def test_hex_with_or_without_hash(self):
        self.assertEqual(pf._parse_background("#f4e8cd"), ((244, 232, 205), None))
        self.assertEqual(pf._parse_background("f4e8cd"), ((244, 232, 205), None))

    def test_blank_is_the_default(self):
        self.assertEqual(pf._parse_background(""), (None, None))
        self.assertEqual(pf._parse_background(None), (None, None))

    def test_garbage_is_an_error(self):
        rgb, err = pf._parse_background("not-a-colour")
        self.assertIsNone(rgb)
        self.assertTrue(err)


def png_bytes(size=(120, 180), rgba=(0, 0, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGBA", size, rgba).save(buf, "PNG")
    buf.seek(0)
    return buf


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.client = pf.app.test_client()
        self.started = []

        def fake_start(j, gallery_dir):
            self.started.append(j)
            return "jid123"
        p = mock.patch.object(pf.comfy, "start", side_effect=fake_start)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(pf, "list_checkpoints", return_value=[SDXL])
        q.start()
        self.addCleanup(q.stop)

    def post(self, route, **form):
        data = {"image": (png_bytes(), "lucky.png"), "prompt": "purple nightgown"}
        data.update(form)
        return self.client.post(route, data=data, content_type="multipart/form-data")

    def test_regenerate_with_krea2_gets_krea_defaults_and_16_multiple_size(self):
        r = self.post("/api/regenerate", model=KREA)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = self.started[0]
        self.assertEqual((j["steps"], j["cfg"], j["sampler"], j["scheduler"]), (8, 1.0, "euler", "simple"))
        self.assertEqual((j["width"] % 16, j["height"] % 16), (0, 0))
        self.assertFalse(j["pose"])
        self.assertEqual(j["warnings"], [])

    def test_a_form_supplied_steps_and_cfg_override_the_krea_defaults(self):
        self.post("/api/regenerate", model=KREA, steps="12", cfg="2.5")
        j = self.started[0]
        self.assertEqual((j["steps"], j["cfg"]), (12, 2.5))

    def test_an_explicit_pose_request_is_ignored_with_a_warning_for_krea2(self):
        self.post("/api/regenerate", model=KREA, pose="1")
        j = self.started[0]
        self.assertFalse(j["pose"])
        self.assertTrue(any("pose" in w.lower() for w in j["warnings"]))

    def test_the_sdxl_regenerate_defaults_are_unchanged(self):
        self.post("/api/regenerate", model=SDXL)
        j = self.started[0]
        self.assertEqual((j["steps"], j["cfg"]), (28, 5.0))
        self.assertTrue(j["pose"])
        self.assertEqual(j["sampler"], "dpmpp_2m_sde")

    def test_the_spec_records_denoise_source_and_background(self):
        self.post("/api/regenerate", model=KREA, denoise="0.8", background="#f4e8cd")
        spec = self.started[0]["spec"]
        self.assertEqual((spec["denoise"], spec["source"], spec["background"]), (0.8, "lucky.png", "#f4e8cd"))
        self.post("/api/regenerate", model=KREA)
        self.assertEqual(self.started[1]["spec"]["background"], "#808080")

    def test_an_invalid_background_is_a_400_and_starts_nothing(self):
        r = self.post("/api/regenerate", model=KREA, background="zzz")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.started, [])

    def test_repair_with_krea2_takes_the_mask_path_with_krea_defaults(self):
        r = self.post("/api/repair", model=KREA, mask=(png_bytes(rgba=(255, 255, 255, 255)), "m.png"))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = self.started[0]
        self.assertEqual((j["steps"], j["cfg"], j["sampler"]), (8, 1.0, "euler"))
        self.assertEqual((j["width"] % 16, j["height"] % 16), (0, 0))
        self.assertTrue(j["repair_mask_png"])
        self.assertEqual(j["spec"]["repair"], True)

    def test_repair_with_krea2_and_a_pose_field_records_the_warning(self):
        self.post("/api/repair", model=KREA, pose="1")
        self.assertTrue(any("pose" in w.lower() for w in self.started[0]["warnings"]))


if __name__ == "__main__":
    unittest.main()
