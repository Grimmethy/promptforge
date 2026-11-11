"""LoRA stacks for the SDXL / Illustrious path: data/loras.json, server/core/loras.py, the LoraLoader chain in comfy.build_graph, the app's
validation and GET /api/loras. Temp folders only (PROMPTFORGE_LORAS_DIR), no ComfyUI, no GPU, no network (python3 -m unittest test.test_lora_stack)."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import app as pf  # noqa: E402
from core import comfy, loras  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sdxl_graphs_before_loras.json")
NOVA = "novaFurryXL_ilV140.safetensors"
BASES = {"illustrious", "pony", "noobai", "sdxl", "krea2", "anima"}


def folder_with(*files):
    d = tempfile.TemporaryDirectory()
    for f in files:
        with open(os.path.join(d.name, f), "wb") as fh:
            fh.write(b"x")
    return d


class RegistryTest(unittest.TestCase):
    def test_the_seed_registry_is_valid(self):
        reg = loras.load_registry()
        ids = [e["id"] for e in reg["loras"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {"ssb_il", "exaggerated_pose", "huffslove_anima"})
        for e in reg["loras"]:
            self.assertTrue(e["file"].endswith(".safetensors"))
            self.assertIn(e["base"], BASES)
            self.assertIsInstance(e["triggers"], list)
            self.assertTrue(-2 <= e["default_weight"] <= 2)
        self.assertEqual(loras.by_file(reg, "SSB_Illustrious-XL_v1.safetensors")["triggers"], ["SSB1L"])
        self.assertEqual(loras.by_id(reg, "huffslove_anima")["base"], "anima")
        self.assertTrue(all(b in BASES for b in reg["checkpoints"].values()))
        self.assertEqual(loras.model_base(reg, NOVA), "illustrious")
        self.assertIsNone(loras.model_base(reg, "unknown.safetensors"))
        self.assertIsNone(loras.model_base(reg, None))

    def test_an_absent_registry_is_empty_and_a_non_object_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(loras.load_registry(os.path.join(d, "nope.json")), {"loras": [], "checkpoints": {}})
            p = os.path.join(d, "bad.json")
            open(p, "w").write("[1]")
            with self.assertRaises(ValueError):
                loras.load_registry(p)

    def test_the_loras_folder_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {"PROMPTFORGE_LORAS_DIR": "/x/loras"}):
            self.assertEqual(loras.loras_dir(), "/x/loras")
        with mock.patch.dict(os.environ, {"COMFYUI_MODELS_DIR": "/m"}, clear=False):
            os.environ.pop("PROMPTFORGE_LORAS_DIR", None)
            self.assertEqual(loras.loras_dir(), "/m/loras")


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.dir = folder_with("SSB_Illustrious-XL_v1.safetensors", "Exaggerated_Dynamic_Fashion_Pose_v1.safetensors", "huffsloveAP3_v1.safetensors", "other.safetensors")
        self.addCleanup(self.dir.cleanup)

    def v(self, stack, base="illustrious"):
        return loras.validate_stack(stack, base, self.dir.name)

    def test_an_id_fills_the_file_weight_and_triggers(self):
        cleaned, warns = self.v([{"id": "exaggerated_pose"}])
        self.assertEqual(cleaned, [{"name": "Exaggerated_Dynamic_Fashion_Pose_v1.safetensors", "weight": 0.55,
                                    "trigger": "exagg3rat3dp0s3, dynamic pose, exaggerated pose"}])
        self.assertEqual(warns, [])

    def test_a_file_name_finds_its_registry_entry_and_explicit_values_win(self):
        cleaned, _ = self.v([{"name": "SSB_Illustrious-XL_v1.safetensors", "weight": 0.8, "trigger": "mine"}])
        self.assertEqual((cleaned[0]["weight"], cleaned[0]["trigger"]), (0.8, "mine"))
        cleaned, _ = self.v([{"name": "SSB_Illustrious-XL_v1.safetensors"}])
        self.assertEqual((cleaned[0]["weight"], cleaned[0]["trigger"]), (0.55, "SSB1L"))

    def test_a_file_the_registry_does_not_know_still_works_with_weight_one(self):
        cleaned, warns = self.v([{"name": "other.safetensors"}])
        self.assertEqual(cleaned, [{"name": "other.safetensors", "weight": 1.0, "trigger": ""}])
        self.assertEqual(warns, [])

    def test_refusals(self):
        for bad in ("x", None, {"a": 1}):
            with self.assertRaises(ValueError):
                self.v(bad)
        with self.assertRaises(FileNotFoundError):
            self.v([{"name": "missing.safetensors", "weight": 1}])
        for weight in (3, -2.5, "1", True):
            with self.assertRaises(ValueError, msg=repr(weight)):
                self.v([{"name": "other.safetensors", "weight": weight}])
        for item in ([5], [{}], [{"weight": 1}], [{"id": "nope"}], [{"name": 7}]):
            with self.assertRaises(ValueError, msg=repr(item)):
                self.v(item)
        with self.assertRaises(ValueError):
            self.v([{"name": "other.safetensors"}] * 7)
        self.assertEqual(len(self.v([{"name": "other.safetensors"}] * 6)[0]), 6)

    def test_a_base_mismatch_warns_and_does_not_fail(self):
        cleaned, warns = self.v([{"id": "huffslove_anima"}])
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(len(warns), 1)
        self.assertIn("anima", warns[0])
        self.assertEqual(self.v([{"id": "huffslove_anima"}], base=None)[1], [])          # an unknown model base gives no warning
        self.assertEqual(self.v([{"id": "ssb_il"}])[1], [])


class TriggerTest(unittest.TestCase):
    def test_triggers_go_in_front_once(self):
        stack = [{"trigger": "SSB1L"}, {"trigger": "dynamic pose, exaggerated pose"}]
        self.assertEqual(loras.with_triggers("1girl, goblin", stack), "SSB1L, dynamic pose, exaggerated pose, 1girl, goblin")
        self.assertEqual(loras.with_triggers("1girl, ssb1l, dynamic pose", stack), "exaggerated pose, 1girl, ssb1l, dynamic pose")
        self.assertEqual(loras.with_triggers("1girl", [{"trigger": "a"}, {"trigger": "a"}]), "a, 1girl")

    def test_no_triggers_leaves_the_text_alone(self):
        for stack in (None, [], [{"trigger": ""}], [{}]):
            self.assertEqual(loras.with_triggers("1girl", stack), "1girl")
        self.assertEqual(loras.with_triggers("", [{"trigger": "a"}]), "a")


class GraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding="utf-8") as fh:
            cls.fx = json.load(fh)

    def test_without_loras_the_graph_is_identical_to_the_one_before_this_change(self):
        for name, job in self.fx["jobs"].items():
            self.assertEqual(json.loads(json.dumps(comfy.build_graph(job))), self.fx["graphs"][name], name)
            self.assertEqual(json.loads(json.dumps(comfy.build_graph(dict(job, loras=[])))), self.fx["graphs"][name], name)
            self.assertEqual(json.loads(json.dumps(comfy.build_graph(dict(job, loras=None)))), self.fx["graphs"][name], name)

    def test_two_loras_are_chained_and_reach_every_consumer(self):
        stack = [{"name": "a.safetensors", "weight": 0.5, "trigger": "TRIG"}, {"name": "b.safetensors", "weight": -0.25, "trigger": ""}]
        g = comfy.build_graph(dict(self.fx["jobs"]["single"], loras=stack))
        self.assertEqual(g["60"]["inputs"], {"model": ["1", 0], "clip": ["1", 1], "lora_name": "a.safetensors", "strength_model": 0.5, "strength_clip": 0.5})
        self.assertEqual(g["61"]["inputs"]["model"], ["60", 0])
        self.assertEqual(g["61"]["inputs"]["clip"], ["60", 1])
        self.assertEqual(g["61"]["inputs"]["strength_model"], -0.25)
        self.assertEqual(g["50"]["inputs"]["clip"], ["61", 1])          # CLIPSetLastLayer feeds both text encoders
        self.assertEqual(g["11"]["inputs"]["model"], ["61", 0])
        self.assertEqual([k for k, v in g.items() if v["class_type"] == "LoraLoader"], ["60", "61"])
        self.assertEqual(g["12"]["inputs"]["vae"], ["1", 2])             # the VAE still comes from the checkpoint

    def test_the_trigger_is_in_the_positive_text_and_two_characters_use_the_shared_base(self):
        stack = [{"name": "a.safetensors", "weight": 1, "trigger": "TRIG"}]
        g1 = comfy.build_graph(dict(self.fx["jobs"]["single"], loras=stack))
        self.assertTrue(json.dumps(g1["6"]).count("TRIG, 1girl, goblin") == 1)
        self.assertNotIn("TRIG", json.dumps(g1["7"]))                    # never the negative
        g2 = comfy.build_graph(dict(self.fx["jobs"]["two"], loras=stack))
        self.assertIn("TRIG, masterpiece", json.dumps(g2["8"]))
        self.assertNotIn("TRIG", json.dumps(g2["6"]))
        self.assertNotIn("TRIG", json.dumps(g2["7"]))
        self.assertEqual(g2["50"]["inputs"]["clip"], ["60", 1])

    def test_the_controlnet_branch_still_connects(self):
        g = comfy.build_graph(dict(self.fx["jobs"]["skeleton"], loras=[{"name": "a.safetensors", "weight": 1, "trigger": ""}]))
        self.assertEqual(g["11"]["inputs"]["model"], ["60", 0])
        self.assertEqual(g["11"]["inputs"]["positive"], ["42", 0])
        self.assertEqual(g["42"]["inputs"]["positive"], ["6", 0])


class AppTest(unittest.TestCase):
    def setUp(self):
        self.dir = folder_with("SSB_Illustrious-XL_v1.safetensors", "huffsloveAP3_v1.safetensors")
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.dict(os.environ, {"PROMPTFORGE_LORAS_DIR": self.dir.name})
        p.start()
        self.addCleanup(p.stop)

    def resolve(self, **spec):
        with mock.patch.object(pf, "list_checkpoints", return_value=[NOVA]):
            return pf.resolve_job(dict({"model": NOVA, "characters": []}, **spec))

    def test_an_illustrious_job_keeps_its_validated_loras_and_warns_on_a_base_mismatch(self):
        j = self.resolve(loras=[{"id": "ssb_il"}, {"id": "huffslove_anima"}])
        self.assertEqual([x["name"] for x in j["loras"]], ["SSB_Illustrious-XL_v1.safetensors", "huffsloveAP3_v1.safetensors"])
        self.assertEqual(j["loras"][0]["weight"], 0.55)
        self.assertEqual(len(j["warnings"]), 1)
        self.assertIn("anima", j["warnings"][0])
        self.assertFalse(any("ignored" in w for w in j["warnings"]))
        self.assertIn("SSB1L", json.dumps(comfy.build_graph(dict(j, id="abcdef0123456789"))))

    def test_no_loras_means_no_loras_key_and_no_warning(self):
        j = self.resolve()
        self.assertNotIn("loras", j)
        self.assertFalse(j.get("warnings"))

    def test_a_missing_file_or_a_bad_weight_fails_the_job_with_a_clear_message(self):
        with self.assertRaisesRegex(ValueError, "Invalid LoRA configuration.*not found"):
            self.resolve(loras=[{"name": "nope.safetensors", "weight": 1}])
        with self.assertRaisesRegex(ValueError, "Invalid LoRA configuration.*out of bounds"):
            self.resolve(loras=[{"id": "ssb_il", "weight": 5}])

    def test_the_loras_route_lists_the_registry_with_present_and_fits(self):
        c = pf.app.test_client()
        rows = {r["id"]: r for r in c.get("/api/loras").get_json()["loras"]}
        self.assertTrue(rows["ssb_il"]["present"])
        self.assertTrue(rows["huffslove_anima"]["present"])
        self.assertFalse(rows["exaggerated_pose"]["present"])           # not in the temp folder
        self.assertIn(NOVA, rows["ssb_il"]["fits"])
        self.assertEqual(rows["huffslove_anima"]["fits"], [])


if __name__ == "__main__":
    unittest.main()
