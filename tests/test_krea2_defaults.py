"""Krea 2 sampler defaults in resolve_job: steps 8, cfg 1, euler/simple and an empty negative unless the request says otherwise; SDXL is unaffected."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import app as pf_app  # noqa: E402

KREA = {"model": "krea2:k.safetensors", "prompt_text": "a goat", "characters": []}
SDXL = {"model": "sdxl.safetensors", "characters": []}


class Krea2Defaults(unittest.TestCase):
    def test_unspecified_values_get_the_distilled_model_defaults(self):
        j = pf_app.resolve_job(dict(KREA))
        self.assertEqual((j["steps"], j["cfg"], j["sampler"], j["scheduler"]), (8, 1.0, "euler", "simple"))
        self.assertEqual(j["negative"], "")
        self.assertEqual(j["prompt_used"]["negative"], "")

    def test_explicit_values_win(self):
        j = pf_app.resolve_job({**KREA, "steps": 20, "cfg": 4.5, "sampler": "dpmpp_2m", "scheduler": "karras"})
        self.assertEqual((j["steps"], j["cfg"], j["sampler"], j["scheduler"]), (20, 4.5, "dpmpp_2m", "karras"))

    def test_an_explicit_negative_is_kept(self):
        j = pf_app.resolve_job({**KREA, "negative": "blurry"})
        self.assertIn("blurry", j["negative"])
        self.assertIn("blurry", j["prompt_used"]["negative"])

    def test_a_blank_negative_counts_as_unspecified(self):
        self.assertEqual(pf_app.resolve_job({**KREA, "negative": "   "})["negative"], "")

    def test_the_tag_path_also_records_an_empty_negative(self):
        j = pf_app.resolve_job({"model": "krea2:k.safetensors", "characters": []})        # no prompt_text: positive comes from the tag assembly
        self.assertEqual(j["negative"], "")
        self.assertEqual(j["prompt_used"]["negative"], "")

    def test_sdxl_checkpoints_are_unaffected(self):
        j = pf_app.resolve_job(dict(SDXL))
        self.assertEqual((j["steps"], j["cfg"], j["sampler"], j["scheduler"]), (30, 6.0, "dpmpp_2m_sde", "karras"))
        self.assertIn("worst quality", j["negative"])
        self.assertIn("worst quality", j["prompt_used"]["negative"])


if __name__ == "__main__":
    unittest.main()
