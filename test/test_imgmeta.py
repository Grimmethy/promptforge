"""Reading a recipe out of an image: server/core/imgmeta.py (ComfyUI graph chunk, Tungsten EXIF JSON, A1111 parameters), the registry match and
GET /api/gallery-recipe/<name>. Synthetic PIL images built here, temp folders only, no ComfyUI, no GPU (python3 -m unittest test.test_imgmeta)."""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from PIL import Image, PngImagePlugin  # noqa: E402

import app as pf  # noqa: E402
from core import imgmeta, loras  # noqa: E402

REGISTRY = {"loras": [{"id": "ssb_il", "name": "SSB [Illustrious-XL]", "file": "SSB_Illustrious-XL_v1.safetensors", "base": "illustrious",
                       "aliases": ["[Style] SSB [Illustrious-XL] - v1"]},
                      {"id": "exaggerated_pose", "name": "Exaggerated, Dynamic Fashion Pose", "file": "Exaggerated_Dynamic_Fashion_Pose_v1.safetensors", "base": "illustrious"},
                      {"id": "huffslove_anima", "name": "Huffslove (Anima)", "file": "huffsloveAP3_v1.safetensors", "base": "anima"}],
            "checkpoints": {"novaFurryXL_ilV140.safetensors": "illustrious"}}


def graph(unet=False, loras_=(("a.safetensors", 0.5, 0.4), ("b.safetensors", 1.0, 1.0))):
    g = {"1": {"class_type": "UNETLoader" if unet else "CheckpointLoaderSimple", "inputs": {("unet_name" if unet else "ckpt_name"): "model-x.safetensors"}},
         "3": {"class_type": "VAELoader", "inputs": {"vae_name": "vae-y.safetensors"}},
         "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "1girl, goblin, embedding:goofy_style", "clip": ["50", 0]}},
         "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad hands", "clip": ["50", 0]}},
         "10": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
         "42": {"class_type": "ControlNetApplyAdvanced", "inputs": {"positive": ["6", 0], "negative": ["7", 0], "strength": 0.9}},
         "50": {"class_type": "CLIPSetLastLayer", "inputs": {"stop_at_clip_layer": -2, "clip": ["1", 1]}},
         "11": {"class_type": "KSampler", "inputs": {"seed": 77, "steps": 28, "cfg": 4.5, "sampler_name": "dpmpp_2m_sde", "scheduler": "karras",
                                                      "positive": ["42", 0], "negative": ["42", 1], "latent_image": ["10", 0], "model": ["1", 0]}}}
    for i, (name, w, cw) in enumerate(loras_):
        g[str(60 + i)] = {"class_type": "LoraLoader", "inputs": {"lora_name": name, "strength_model": w, "strength_clip": cw, "model": ["1", 0], "clip": ["1", 1]}}
    return g


def png(**chunks):
    info = PngImagePlugin.PngInfo()
    for k, v in chunks.items():
        info.add_text(k, v)
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (9, 9, 9)).save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


TUNGSTEN = {"type": "sdxl", "data": {"prompt": "masterpiece, goblin", "negative_prompt": "Nude, ground", "sampler": "dpmpp_2m_karras", "steps": 29,
                                     "cfg": 4.5, "clip_skip": 2, "seed": 3229962611, "width": 832, "height": 1216, "vae": "sdxl_vae.safetensors",
                                     "checkpoint": {"uuid": "u", "name": "Nova Furry XL - IL v18.0 B"},
                                     "loras": [{"uuid": "1", "name": "[Style] SSB [Illustrious-XL] - v1", "weight": 0.55},
                                               {"uuid": "2", "name": "Huffslove Style [Illustrious] - V1", "weight": 0.5}],
                                     "embeddings": [{"name": "emb1"}]}}


def webp(comment_bytes):
    ex = Image.Exif()
    ex[0x9286] = comment_bytes
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "WEBP", lossless=True, exif=ex.tobytes())
    return buf.getvalue()


A1111 = ("masterpiece, 1girl <lora:styleA:0.8> <lora:poseB:1:0.6>, embedding:ez\nNegative prompt: bad hands, blurry\n"
         "Steps: 25, Sampler: Euler a, CFG scale: 6.5, Seed: 123, Size: 512x768, Model hash: abc, Model: dreamX_v2, Clip skip: 2")


class ComfyTest(unittest.TestCase):
    def test_checkpoint_two_loras_sampler_text_and_size(self):
        m = imgmeta.read(png(prompt=json.dumps(graph())))
        self.assertEqual(m["source"], "comfyui")
        self.assertEqual(m["checkpoint"], "model-x.safetensors")
        self.assertEqual(m["loras"], [{"name": "a.safetensors", "weight": 0.5, "clip_weight": 0.4}, {"name": "b.safetensors", "weight": 1, "clip_weight": 1}])
        self.assertEqual((m["seed"], m["steps"], m["cfg"], m["sampler"], m["scheduler"]), (77, 28, 4.5, "dpmpp_2m_sde", "karras"))
        self.assertEqual((m["width"], m["height"], m["clip_skip"], m["vae"]), (832, 1216, 2, "vae-y.safetensors"))
        self.assertEqual((m["prompt"], m["negative"]), ("1girl, goblin, embedding:goofy_style", "bad hands"))     # followed through the ControlNet node
        self.assertEqual(m["embeddings"], ["goofy_style"])
        self.assertIn("prompt", m["raw_keys"])

    def test_a_unet_graph_and_no_loras(self):
        m = imgmeta.read(png(prompt=json.dumps(graph(unet=True, loras_=()))))
        self.assertEqual((m["checkpoint"], m["loras"]), ("model-x.safetensors", []))

    def test_loras_come_in_graph_order_with_numeric_ids(self):
        g = graph(loras_=())
        for nid, name in (("9", "nine"), ("100", "hundred"), ("20", "twenty")):
            g[nid] = {"class_type": "LoraLoader", "inputs": {"lora_name": name, "strength_model": 1, "strength_clip": 1}}
        self.assertEqual([x["name"] for x in imgmeta.read(png(prompt=json.dumps(g)))["loras"]], ["nine", "twenty", "hundred"])

    def test_the_longest_text_is_the_fallback_when_the_links_do_not_lead_to_one(self):
        g = {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "short"}}, "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "a much longer prompt text"}}}
        self.assertEqual(imgmeta.read(png(prompt=json.dumps(g)))["prompt"], "a much longer prompt text")


class OtherShapesTest(unittest.TestCase):
    def test_tungsten_exif_in_both_byte_orders_and_plain_utf8(self):
        text = json.dumps(TUNGSTEN)
        for comment in (b"UNICODE\x00" + text.encode("utf-16-be"), b"UNICODE\x00" + text.encode("utf-16-le"), text.encode("utf-8")):
            m = imgmeta.read(webp(comment))
            self.assertEqual(m["source"], "tungsten")
            self.assertEqual(m["checkpoint"], "Nova Furry XL - IL v18.0 B")
            self.assertEqual([(x["name"], x["weight"]) for x in m["loras"]], [("[Style] SSB [Illustrious-XL] - v1", 0.55), ("Huffslove Style [Illustrious] - V1", 0.5)])
            self.assertEqual((m["steps"], m["cfg"], m["seed"], m["clip_skip"], m["sampler"], m["width"], m["height"]), (29, 4.5, 3229962611, 2, "dpmpp_2m_karras", 832, 1216))
            self.assertEqual((m["prompt"], m["negative"], m["embeddings"], m["vae"]), ("masterpiece, goblin", "Nude, ground", ["emb1"], "sdxl_vae.safetensors"))

    def test_a1111_parameters_chunk_with_lora_tags(self):
        m = imgmeta.read(png(parameters=A1111))
        self.assertEqual(m["source"], "a1111")
        self.assertEqual([(x["name"], x["weight"], x["clip_weight"]) for x in m["loras"]], [("styleA", 0.8, 0.8), ("poseB", 1, 0.6)])
        self.assertEqual((m["steps"], m["cfg"], m["seed"], m["sampler"], m["checkpoint"], m["clip_skip"], m["width"], m["height"]),
                         (25, 6.5, 123, "Euler a", "dreamX_v2", 2, 512, 768))
        self.assertEqual((m["negative"], m["embeddings"]), ("bad hands, blurry", ["ez"]))
        self.assertTrue(m["prompt"].startswith("masterpiece"))

    def test_a1111_text_in_the_exif_comment_too(self):
        m = imgmeta.read(webp(b"ASCII\x00\x00\x00" + A1111.encode()))
        self.assertEqual((m["source"], m["checkpoint"]), ("a1111", "dreamX_v2"))

    def test_a_plain_image_and_corrupt_chunks_never_raise(self):
        plain = imgmeta.read(png())
        self.assertEqual((plain["source"], plain["loras"], plain["checkpoint"], plain["warnings"]), ("none", [], None, []))
        bad = imgmeta.read(png(prompt="{not json"))
        self.assertEqual((bad["source"], bad["loras"]), ("none", []))
        self.assertTrue(bad["warnings"])
        notgraph = imgmeta.read(png(prompt='{"a": 1}'))
        self.assertEqual(notgraph["source"], "none")
        self.assertEqual(imgmeta.read(b"not an image")["source"], "none")
        self.assertTrue(imgmeta.read(b"not an image")["warnings"])
        self.assertEqual(imgmeta.read(webp(b"\xff\xfe\x00junk"))["source"], "none")

    def test_the_result_never_shares_lists_between_calls(self):
        a = imgmeta.read(png())
        a["loras"].append("x")
        self.assertEqual(imgmeta.read(png())["loras"], [])


class MatchTest(unittest.TestCase):
    def test_matching_ignores_case_punctuation_and_extension_but_nothing_looser(self):
        meta = {"checkpoint": "novaFurryXL_ilV140", "loras": [
            {"name": "ssb illustrious xl v1.SAFETENSORS", "weight": 0.5, "clip_weight": 0.5}, {"name": "Exaggerated_Dynamic Fashion-Pose_v1", "weight": 1, "clip_weight": 1},
            {"name": "Huffslove Style [Illustrious] - V1", "weight": 0.5, "clip_weight": 0.5}, {"name": "", "weight": 1, "clip_weight": 1}]}
        m = imgmeta.match_registry(meta, REGISTRY)
        self.assertEqual([r["registry_id"] for r in m["loras"]], ["ssb_il", "exaggerated_pose", None, None])
        self.assertEqual((m["known"], m["unknown"], m["checkpoint_base"]), (2, 2, "illustrious"))
        self.assertIsNone(m["loras"][2]["file"])                       # the Illustrious Huffslove is not the Anima file

    def test_an_alias_matches_the_display_name_another_site_uses(self):
        m = imgmeta.match_registry({"loras": [{"name": "[Style] SSB [Illustrious-XL] - v1", "weight": 0.55}]}, REGISTRY)
        self.assertEqual(m["loras"][0]["registry_id"], "ssb_il")

    def test_installed_is_reported_only_when_a_folder_listing_is_given(self):
        meta = {"loras": [{"name": "ssb_il"}, {"name": "exaggerated_pose"}, {"name": "unknown one"}]}
        self.assertEqual([r["installed"] for r in imgmeta.match_registry(meta, REGISTRY)["loras"]], [None, None, None])
        m = imgmeta.match_registry(meta, REGISTRY, ["SSB_Illustrious-XL_v1.safetensors"])
        self.assertEqual([r["installed"] for r in m["loras"]], [True, False, None])
        self.assertEqual(m["not_installed"], 1)

    def test_no_registry_and_no_checkpoint_are_fine(self):
        m = imgmeta.match_registry({"loras": [{"name": "x"}]}, None)
        self.assertEqual((m["known"], m["unknown"], m["checkpoint"], m["checkpoint_base"]), (0, 1, None, None))
        self.assertEqual(imgmeta.match_registry({}, REGISTRY)["loras"], [])


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "gal", "2026-11"))
        os.makedirs(os.path.join(self.tmp.name, "loras"))
        with open(os.path.join(self.tmp.name, "loras", "SSB_Illustrious-XL_v1.safetensors"), "wb") as fh:
            fh.write(b"x")
        for n, data in (("a_0.png", png(prompt=json.dumps(graph(loras_=(("SSB_Illustrious-XL_v1.safetensors", 0.55, 0.55),))))), ("b_0.png", png())):
            with open(os.path.join(self.tmp.name, "gal", "2026-11", n), "wb") as fh:
                fh.write(data)
        for p in (mock.patch.object(pf, "GALLERY_DIR", os.path.join(self.tmp.name, "gal")),
                  mock.patch.dict(os.environ, {"PROMPTFORGE_LORAS_DIR": os.path.join(self.tmp.name, "loras")})):
            p.start()
            self.addCleanup(p.stop)
        self.c = pf.app.test_client()

    def test_the_route_returns_the_recipe_and_the_registry_match(self):
        j = self.c.get("/api/gallery-recipe/2026-11/a_0.png").get_json()
        self.assertEqual(j["meta"]["source"], "comfyui")
        self.assertEqual(j["meta"]["loras"][0]["name"], "SSB_Illustrious-XL_v1.safetensors")
        row = j["match"]["loras"][0]
        self.assertEqual((row["registry_id"], row["known"], row["installed"]), ("ssb_il", True, True))      # the real seed registry knows SSB

    def test_a_plain_image_gives_source_none_and_unknown_or_unsafe_paths_404(self):
        self.assertEqual(self.c.get("/api/gallery-recipe/2026-11/b_0.png").get_json()["meta"]["source"], "none")
        self.assertEqual(self.c.get("/api/gallery-recipe/2026-11/nope.png").status_code, 404)
        self.assertEqual(self.c.get("/api/gallery-recipe/../../etc/passwd").status_code, 404)

    def test_the_lightbox_loads_the_recipe_script_and_section(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
        with open(os.path.join(root, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        with open(os.path.join(root, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("recipeBox(file)", js)
        self.assertIn("'Copy as spec'", js)
        self.assertIn("copyRecipeSpec(rec)", js)
        self.assertIn("PFRecipe.spec(rec)", js)
        self.assertIn("'/api/gallery-recipe/' + file", js)
        self.assertIn("/recipe.js", html)
        self.assertLess(html.index("/recipe.js"), html.index("/app.js"))


if __name__ == "__main__":
    unittest.main()
