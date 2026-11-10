"""POST /api/generate and /api/preview take an optional `steps` bounded to 1..60 (2026-11-10); absent keeps today's defaults (SDXL 30, Krea 2 8). No ComfyUI, no GPU."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import app as pf  # noqa: E402

SDXL = {"model": "sdxl.safetensors", "characters": []}
KREA = {"model": "krea2:k.safetensors", "prompt_text": "a goat", "characters": []}


class CheckSteps(unittest.TestCase):
    def test_in_range_whole_numbers_and_numeric_strings_are_accepted(self):
        for v, want in ((1, 1), (30, 30), (60, 60), ("45", 45), (12.0, 12), ("8", 8)):
            self.assertEqual(pf.check_steps(v), want, v)

    def test_everything_else_is_rejected_with_the_same_message(self):
        for v in (0, 61, -5, 30.5, "x", "", "30.5", "61", True, False, [], {}, "1e9", float("nan"), float("inf")):
            with self.assertRaises(ValueError, msg=repr(v)) as ctx:
                pf.check_steps(v)
            self.assertIn("1 to 60", str(ctx.exception), repr(v))


class ResolveJob(unittest.TestCase):
    def test_absent_steps_keep_the_defaults_for_both_families(self):
        self.assertEqual(pf.resolve_job(dict(SDXL))["steps"], 30)
        self.assertEqual(pf.resolve_job(dict(KREA))["steps"], 8)

    def test_a_valid_value_is_used_for_sdxl_and_krea_and_stored_as_an_int(self):
        self.assertEqual(pf.resolve_job({**SDXL, "steps": "42"})["steps"], 42)
        self.assertEqual(pf.resolve_job({**KREA, "steps": 20})["steps"], 20)

    def test_an_invalid_value_raises_before_anything_is_built(self):
        for bad in (0, 61, "abc", 2.5, True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                pf.resolve_job({**SDXL, "steps": bad})

    def test_the_callers_spec_is_not_changed(self):
        spec = {**SDXL, "steps": "42"}
        pf.resolve_job(spec)
        self.assertEqual(spec["steps"], "42")


class Routes(unittest.TestCase):
    def setUp(self):
        self.c = pf.app.test_client()
        self.p = [mock.patch.object(pf.comfy, "start", lambda job, gal: "job1"), mock.patch.object(pf, "list_checkpoints", lambda: ["sdxl.safetensors"])]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_generate_rejects_out_of_range_steps_with_a_400_and_accepts_in_range(self):
        for bad in (0, 61, "x", 1.5):
            r = self.c.post("/api/generate", json={**SDXL, "steps": bad})
            self.assertEqual(r.status_code, 400, bad); self.assertIn("1 to 60", r.get_json()["error"])
        for ok in (1, 60, "30"):
            r = self.c.post("/api/generate", json={**SDXL, "steps": ok})
            self.assertEqual((r.status_code, r.get_json()["job_id"]), (200, "job1"), ok)
        self.assertEqual(self.c.post("/api/generate", json=dict(SDXL)).status_code, 200)

    def test_preview_applies_the_same_rule(self):
        self.assertEqual(self.c.post("/api/preview", json={**SDXL, "steps": 99}).status_code, 400)
        self.assertEqual(self.c.post("/api/preview", json={**SDXL, "steps": 10}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
