"""Krea 2 Turbo engine (2026-10-14): a model named `krea2:<unet file>` gets a UNET + Qwen3-VL text encoder + VAE graph (8 steps, cfg 1, euler/simple,
a zeroed negative, no ControlNet) instead of the SDXL CheckpointLoader graph, and shows up in the model list when ComfyUI has the files.
No ComfyUI, no GPU (python3 -m unittest discover -s test -p 'test_*.py')."""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import app as pf  # noqa: E402
from core import comfy  # noqa: E402

KREA = "krea2:krea2_turbo_int8_convrot.safetensors"


def job(**kw):
    j = {"id": "abcdef0123456789", "model": KREA, "width": 1024, "height": 1024, "batch": 1, "seed": 123, "steps": 8, "cfg": 1.0,
         "sampler": "euler", "scheduler": "simple", "n_chars": 1, "positive": "a pig in a flat cap", "negative": "blurry"}
    j.update(kw)
    return j


def by_type(g, t):
    return [(k, v) for k, v in g.items() if v["class_type"] == t]


class GraphTest(unittest.TestCase):
    def test_a_krea2_model_builds_the_unet_encoder_vae_graph_not_the_checkpoint_graph(self):
        g = comfy.build_graph(job())
        types = {v["class_type"] for v in g.values()}
        self.assertEqual(types, {"UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "ConditioningZeroOut", "EmptyLatentImage",
                                 "KSampler", "VAEDecode", "SaveImage"})
        (_, unet), = by_type(g, "UNETLoader")
        self.assertEqual(unet["inputs"]["unet_name"], "krea2_turbo_int8_convrot.safetensors")      # the prefix is not part of the file name
        (_, clip), = by_type(g, "CLIPLoader")
        self.assertEqual(clip["inputs"]["type"], "krea2")
        self.assertEqual(clip["inputs"]["clip_name"], "qwen3vl_4b_fp8_scaled.safetensors")
        (_, vae), = by_type(g, "VAELoader")
        self.assertEqual(vae["inputs"]["vae_name"], "qwen_image_vae.safetensors")

    def test_the_sampler_gets_turbo_settings_and_the_links_line_up(self):
        g = comfy.build_graph(job(seed=777, width=896, height=1152, batch=2))
        (kid, ks), = by_type(g, "KSampler")
        i = ks["inputs"]
        self.assertEqual((i["seed"], i["steps"], i["cfg"], i["sampler_name"], i["scheduler"], i["denoise"]), (777, 8, 1.0, "euler", "simple", 1.0))
        pos, neg = g[i["positive"][0]], g[i["negative"][0]]
        self.assertEqual(pos["class_type"], "CLIPTextEncode")
        self.assertEqual(pos["inputs"]["text"], "a pig in a flat cap")
        self.assertEqual(neg["class_type"], "ConditioningZeroOut")                                  # the negative is a zeroed copy of the positive
        self.assertEqual(neg["inputs"]["conditioning"], i["positive"])
        self.assertEqual(g[i["model"][0]]["class_type"], "UNETLoader")
        (_, lat), = by_type(g, "EmptyLatentImage")
        self.assertEqual((lat["inputs"]["width"], lat["inputs"]["height"], lat["inputs"]["batch_size"]), (896, 1152, 2))
        (_, dec), = by_type(g, "VAEDecode")
        self.assertEqual(dec["inputs"]["samples"][0], kid)
        (_, save), = by_type(g, "SaveImage")
        self.assertEqual(save["inputs"]["filename_prefix"], "promptforge_abcdef01")

    def test_two_characters_become_one_text_prompt(self):
        g = comfy.build_graph(job(n_chars=2, positive=None, base="watercolor, granary", region_a="a pig, flat cap", region_b="a goat, tunic"))
        (_, enc), = by_type(g, "CLIPTextEncode")
        for part in ("watercolor, granary", "a pig, flat cap", "a goat, tunic"):
            self.assertIn(part, enc["inputs"]["text"])
        self.assertEqual(len(by_type(g, "ConditioningZeroOut")), 1)

    def test_the_encoder_and_vae_file_names_can_be_overridden_from_the_environment(self):
        with mock.patch.dict(os.environ, {"PROMPTFORGE_KREA2_TE": "qwen3vl_4b_bf16.safetensors", "PROMPTFORGE_KREA2_VAE": "other_vae.safetensors"}):
            g = comfy.build_graph(job())
        self.assertEqual(by_type(g, "CLIPLoader")[0][1]["inputs"]["clip_name"], "qwen3vl_4b_bf16.safetensors")
        self.assertEqual(by_type(g, "VAELoader")[0][1]["inputs"]["vae_name"], "other_vae.safetensors")

    def test_an_ordinary_sdxl_model_still_builds_the_checkpoint_graph(self):
        g = comfy.build_graph(job(model="novaFurryXL_ilV140.safetensors", steps=30, cfg=6.0, sampler="dpmpp_2m_sde", scheduler="karras"))
        self.assertEqual(len(by_type(g, "CheckpointLoaderSimple")), 1)
        self.assertEqual(len(by_type(g, "UNETLoader")), 0)


class ResolveTest(unittest.TestCase):
    def resolve(self, **spec):
        with mock.patch.object(pf, "list_checkpoints", return_value=["novaFurryXL_ilV140.safetensors"]):
            return pf.resolve_job(dict({"model": KREA, "characters": []}, **spec))

    def test_krea2_defaults_to_turbo_settings_not_the_sdxl_ui_defaults(self):
        j = self.resolve()
        self.assertEqual((j["steps"], j["cfg"], j["sampler"], j["scheduler"]), (8, 1.0, "euler", "simple"))
        s = self.resolve(steps=6, cfg=1.5, sampler="euler_ancestral", scheduler="beta")
        self.assertEqual((s["steps"], s["cfg"], s["sampler"], s["scheduler"]), (6, 1.5, "euler_ancestral", "beta"))

    def test_an_sdxl_job_keeps_the_sdxl_defaults(self):
        j = self.resolve(model="novaFurryXL_ilV140.safetensors")
        self.assertEqual((j["steps"], j["cfg"], j["sampler"], j["scheduler"]), (30, 6.0, "dpmpp_2m_sde", "karras"))

    def test_a_pose_skeleton_is_dropped_with_a_warning_because_krea2_has_no_controlnet_here(self):
        people = [{"pose_keypoints_2d": [0.5, 0.2, 1.0] * 18}]
        j = self.resolve(skeleton={"people": people})
        self.assertFalse(j.get("skeleton_png"))
        self.assertTrue(any("skeleton" in w.lower() for w in j["warnings"]))
        sdxl = self.resolve(model="novaFurryXL_ilV140.safetensors", skeleton={"people": people})
        self.assertTrue(sdxl.get("skeleton_png"))                                                   # SDXL still gets its ControlNet
        self.assertFalse(sdxl.get("warnings"))

    def test_a_prompt_text_overrides_the_assembled_tag_prompt(self):
        j = self.resolve(prompt_text="A weary goat carrying a sack of grain through a sunlit granary, watercolor.")
        self.assertEqual(j["positive"], "A weary goat carrying a sack of grain through a sunlit granary, watercolor.")
        self.assertEqual(j["prompt_used"]["positive"], j["positive"])


class ModelListTest(unittest.TestCase):
    def test_only_krea2_diffusion_models_are_added_and_prefixed(self):
        def fake(path, timeout=5):
            if "CheckpointLoaderSimple" in path:
                return {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors", "b.safetensors"]]}}}}
            return {"UNETLoader": {"input": {"required": {"unet_name": [["wan2.2_ti2v_5B_fp16.safetensors", "krea2_turbo_int8_convrot.safetensors",
                                                                         "krea2_turbo_fp8_scaled.safetensors", "notkrea.safetensors"]]}}}}
        with mock.patch.object(pf, "_comfy_get", side_effect=fake):
            self.assertEqual(pf.list_models(), ["a.safetensors", "b.safetensors", "krea2:krea2_turbo_fp8_scaled.safetensors",
                                                "krea2:krea2_turbo_int8_convrot.safetensors"])
            self.assertEqual(pf.list_checkpoints(), ["a.safetensors", "b.safetensors"])             # the SDXL-only list is unchanged

    def test_when_comfyui_is_down_the_list_is_just_empty_not_an_error(self):
        with mock.patch.object(pf, "_comfy_get", side_effect=OSError("down")):
            self.assertEqual(pf.list_models(), [])

    def test_when_only_the_unet_query_fails_the_checkpoints_still_list(self):
        def fake(path, timeout=5):
            if "UNETLoader" in path:
                raise OSError("no unet node")
            return {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors"]]}}}}
        with mock.patch.object(pf, "_comfy_get", side_effect=fake):
            self.assertEqual(pf.list_models(), ["a.safetensors"])


class JobRecordTest(unittest.TestCase):
    def test_warnings_reach_the_job_record_the_ui_polls(self):
        with mock.patch.object(comfy.threading, "Thread", return_value=mock.Mock(start=lambda: None)):
            jid = comfy.start(job(warnings=["pose skeleton ignored: Krea 2 has no ControlNet here"], seed=5), "/tmp")
        self.assertEqual(comfy.get_job(jid)["warnings"], ["pose skeleton ignored: Krea 2 has no ControlNet here"])


if __name__ == "__main__":
    unittest.main()
