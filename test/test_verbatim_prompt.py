"""Verbatim prompt mode (the optional `verbatim` spec field {positive, negative}): a frozen job prompt reaches ComfyUI unchanged for SDXL/Illustrious and Krea 2
models. No ComfyUI, no GPU. Imports app.py, which needs Flask, so run with the repo venv:  .venv/bin/python -m unittest test.test_verbatim_prompt"""
import copy
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import app as pf  # noqa: E402
from core import assemble  # noqa: E402

SDXL = "novaFurryXL_ilV140.safetensors"
KREA = "krea2:krea2_turbo_int8_convrot.safetensors"
TEXT = 'a "tiny" cottage\nwith smoke, 🏠 BREAK (door:1.3) [x] {y}'
NEG = "blurry, text\nwatermark 🚫"


def spec(**kw):
    base = {"model": SDXL, "width": 512, "height": 768, "seed": 77, "verbatim": {"positive": TEXT, "negative": NEG}}
    base.update(kw)
    return base


class VerbatimTest(unittest.TestCase):
    def test_sdxl_gets_the_exact_positive_and_negative(self):
        j = pf.resolve_job(spec())
        self.assertEqual((j["positive"], j["negative"]), (TEXT, NEG))
        self.assertEqual(j["prompt_used"], {"positive": TEXT, "negative": NEG})
        self.assertNotIn(assemble.DEFAULT_NEGATIVE, j["negative"])
        self.assertNotIn(assemble.DEFAULT_QUALITY, j["positive"])
        self.assertEqual(j["n_chars"], 0)
        self.assertEqual(j["char_segments"], [])

    def test_without_verbatim_the_prompt_is_still_assembled(self):
        s = spec()
        del s["verbatim"]
        j = pf.resolve_job(s)
        self.assertIn(assemble.DEFAULT_NEGATIVE, j["negative"])
        self.assertNotEqual(j["positive"], TEXT)
        with mock.patch.object(pf, "_apply_verbatim", side_effect=AssertionError("must not run")):
            self.assertEqual(pf.resolve_job(s)["positive"], j["positive"])           # the helper is never entered without the field

    def test_a_job_without_verbatim_is_unchanged_by_the_new_code(self):
        s = spec()
        del s["verbatim"]
        s.update(theme=None, characters=[{"name": "Pip", "text": "a small pig"}], steps=12)
        before = copy.deepcopy(pf.resolve_job(copy.deepcopy(s)))
        with mock.patch.object(pf, "_apply_verbatim", side_effect=AssertionError("must not run")):
            after = pf.resolve_job(copy.deepcopy(s))
        self.assertEqual(before, after)
        self.assertEqual(after["n_chars"], 1)

    def test_a_negative_is_optional(self):
        j = pf.resolve_job(spec(verbatim={"positive": "x"}))
        self.assertEqual((j["positive"], j["negative"]), ("x", ""))

    def test_krea2_gets_the_exact_positive_and_an_empty_negative(self):
        j = pf.resolve_job(spec(model=KREA))
        self.assertEqual((j["positive"], j["negative"]), (TEXT, ""))
        self.assertEqual(j["prompt_used"], {"positive": TEXT, "negative": ""})
        self.assertEqual((j["steps"], j["cfg"]), (8, 1.0))

    def test_two_characters_are_ignored_and_the_regional_keys_removed(self):
        chars = [{"name": "A", "text": "a goat"}, {"name": "B", "text": "a pig"}]
        plain = pf.resolve_job({"model": SDXL, "width": 512, "height": 512, "characters": chars})
        self.assertIn("region_a", plain)                                    # the two-character path really builds them
        j = pf.resolve_job(spec(characters=chars))
        for k in ("region_a", "region_b", "base"):
            self.assertNotIn(k, j)
        self.assertEqual((j["n_chars"], j["positive"]), (0, TEXT))

    def test_a_pose_skeleton_is_dropped_with_a_warning(self):
        people = [{"pose_keypoints_2d": [0.5, 0.2, 1.0] * 18}]
        s = spec(skeleton={"people": people})
        del s["verbatim"]
        self.assertTrue(pf.resolve_job(s).get("skeleton_png"))               # SDXL normally keeps its ControlNet skeleton
        j = pf.resolve_job(spec(skeleton={"people": people}))
        self.assertFalse(j.get("skeleton_png"))
        self.assertIn("pose skeleton ignored: a verbatim prompt has no characters", j["warnings"])

    def test_everything_else_in_the_job_is_identical(self):
        s = spec(steps=14, cfg=4.5, sampler="euler", scheduler="normal")
        plain = copy.deepcopy(s)
        del plain["verbatim"]
        a, b = pf.resolve_job(plain), pf.resolve_job(s)
        for k in ("steps", "cfg", "sampler", "scheduler", "width", "height", "seed", "model", "nsfw"):
            self.assertEqual(a[k], b[k], k)
        self.assertEqual((b["steps"], b["cfg"], b["sampler"]), (14, 4.5, "euler"))

    def test_loras_are_still_validated_and_kept(self):
        stack = [{"file": "a.safetensors", "weight": 0.8}]
        with mock.patch.object(pf.loras_mod, "load_registry", return_value={}), \
                mock.patch.object(pf.loras_mod, "model_base", return_value="illustrious"), \
                mock.patch.object(pf.loras_mod, "validate_stack", return_value=(stack, [])) as vs:
            j = pf.resolve_job(spec(loras=stack))
        self.assertEqual(j["loras"], stack)
        vs.assert_called_once()
        with mock.patch.object(pf.loras_mod, "load_registry", return_value={}), \
                mock.patch.object(pf.loras_mod, "model_base", return_value="illustrious"), \
                mock.patch.object(pf.loras_mod, "validate_stack", side_effect=ValueError("no such lora")):
            with self.assertRaisesRegex(ValueError, "Invalid LoRA configuration"):
                pf.resolve_job(spec(loras=stack))

    def test_unusual_text_comes_back_unchanged(self):
        for text in ('She said "hi"', "line1\nline2\n", "😀🏠", "BREAK", "(word:1.3), [a|b], {c}", "  padded  "):
            j = pf.resolve_job(spec(verbatim={"positive": text, "negative": text}))
            self.assertEqual(j["positive"], text)
            self.assertEqual(j["negative"], text)

    def test_every_invalid_shape_is_refused_with_its_message(self):
        shape = "verbatim must be an object with a non-empty string positive and an optional string negative"
        for bad in (["a"], "text", 5, {}, {"negative": "x"}, {"positive": ""}, {"positive": "   "}, {"positive": 5}, {"positive": "x", "negative": 5}):
            with self.assertRaisesRegex(ValueError, "^" + shape + "$"):
                pf.resolve_job(spec(verbatim=bad))
        with self.assertRaisesRegex(ValueError, "verbatim.positive is longer than 8000 characters"):
            pf.resolve_job(spec(verbatim={"positive": "x" * 8001}))
        with self.assertRaisesRegex(ValueError, "verbatim.negative is longer than 4000 characters"):
            pf.resolve_job(spec(verbatim={"positive": "x", "negative": "y" * 4001}))
        pf.resolve_job(spec(verbatim={"positive": "x" * 8000, "negative": "y" * 4000}))     # the limits themselves are fine


class PreviewRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = pf.app.test_client()

    def test_preview_returns_the_exact_prompt(self):
        r = self.client.post("/api/preview", json=spec())
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["prompt_used"], {"positive": TEXT, "negative": NEG})
        self.assertEqual(body["n_chars"], 0)

    def test_preview_refuses_an_invalid_verbatim_with_400(self):
        r = self.client.post("/api/preview", json=spec(verbatim={"positive": ""}))
        self.assertEqual(r.status_code, 400)
        self.assertIn("verbatim must be an object", r.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
