"""Model licence registry (2026-11-10): the shipped data/model-licenses.json, the lookup rules, the snapshot written into each sidecar, /api/presets, the
gallery's commercial-safe filter and badge field, and the badge labels in web/licences.js. No ComfyUI, no GPU (python3 -m unittest discover -s test -p 'test_*.py')."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import app as pf  # noqa: E402
from core import comfy, gallery_chars, licenses as L  # noqa: E402

WEB = os.path.join(HERE, "..", "web")
SHIPPED = os.path.join(HERE, "..", "data", "model-licenses.json")


class ShippedRegistry(unittest.TestCase):
    def test_the_shipped_file_loads_and_every_record_is_well_formed(self):
        reg = L.load(SHIPPED)
        self.assertGreaterEqual(len(reg), 5)
        for key, rec in reg.items():
            self.assertIn(rec["commercial_use"], L.VALUES, key)
            self.assertRegex(rec["checked"] or "", r"^\d{4}-\d{2}-\d{2}$", key)
            if rec["commercial_use"] in ("yes", "conditional"):
                self.assertTrue(rec["source_url"] and rec["licence"], key)         # a verdict that allows selling must say where it comes from

    def test_the_verdicts_researched_on_2026_11_09(self):
        got = lambda m: L.lookup(m, SHIPPED)["commercial_use"]
        self.assertEqual(got("WAI-Nsfw-Illustrious-17.safetensors"), "yes")
        self.assertEqual(got("novaFurryXL_ilV140.safetensors"), "yes")
        self.assertEqual(got("divingIllustrious_nijiMutedColorVAE.safetensors"), "unverified")
        self.assertEqual(got("krea2:krea2_turbo_int8_convrot.safetensors"), "conditional")
        self.assertEqual(got("wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"), "conditional")
        self.assertEqual(got("a_model_nobody_checked.safetensors"), "unverified")


class Rules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "reg.json")
        self.write({"_readme": "x", "Alpha.safetensors": {"source_url": "https://e/x", "licence": "L", "commercial_use": "yes", "note": "n", "checked": "2026-11-09"},
                    "beta_*": {"commercial_use": "no"}, "Weird.safetensors": {"commercial_use": "maybe"}, "NotAnObject.safetensors": "yes", "7": {"commercial_use": "yes"}})

    def tearDown(self):
        L.clear(); self.tmp.cleanup()

    def write(self, obj, raw=None):
        with open(self.path, "w") as fh:
            fh.write(raw if raw is not None else json.dumps(obj))
        os.utime(self.path, (os.path.getmtime(self.path) + 5, os.path.getmtime(self.path) + 5))

    def test_exact_prefix_case_and_glob_matches(self):
        for name in ("Alpha.safetensors", "krea2:Alpha.safetensors", "alpha.SAFETENSORS"):
            self.assertEqual(L.lookup(name, self.path)["commercial_use"], "yes", name)
        self.assertEqual(L.lookup("beta_v2.safetensors", self.path)["commercial_use"], "no")
        self.assertEqual(L.lookup("BETA_x", self.path)["commercial_use"], "no")
        self.assertEqual(L.lookup("gamma.safetensors", self.path), L.UNVERIFIED)

    def test_bad_input_and_bad_records_are_unverified_never_errors(self):
        for bad in (None, "", 5, ["x"], "krea2:"):
            self.assertEqual(L.lookup(bad, self.path)["commercial_use"], "unverified", repr(bad))
        self.assertEqual(L.lookup("Weird.safetensors", self.path)["commercial_use"], "unverified")      # unknown value
        self.assertNotIn("NotAnObject.safetensors", L.load(self.path)); self.assertNotIn("_readme", L.load(self.path))

    def test_missing_and_malformed_files_give_an_empty_registry(self):
        self.assertEqual(L.load(os.path.join(self.tmp.name, "nope.json")), {})
        for raw in ("{not json", "[]", '"text"', ""):
            self.write(None, raw=raw)
            self.assertEqual(L.load(self.path), {}, raw)
            self.assertEqual(L.lookup("Alpha.safetensors", self.path), L.UNVERIFIED)

    def test_the_answer_is_a_copy_and_the_cache_follows_the_file(self):
        rec = L.lookup("Alpha.safetensors", self.path); rec["commercial_use"] = "no"
        self.assertEqual(L.lookup("Alpha.safetensors", self.path)["commercial_use"], "yes")
        self.write({"Alpha.safetensors": {"commercial_use": "no"}})
        self.assertEqual(L.lookup("Alpha.safetensors", self.path)["commercial_use"], "no")

    def test_snapshot_has_only_the_two_sidecar_fields(self):
        self.assertEqual(L.snapshot("Alpha.safetensors", self.path), {"commercial_use": "yes", "licence": "L"})
        self.assertEqual(L.snapshot("nothing", self.path), {"commercial_use": "unverified", "licence": None})


class Sidecar(unittest.TestCase):
    JOB = {"id": "abcd1234efgh", "seed": 5, "model": "krea2:krea2_turbo_int8_convrot.safetensors", "spec": {"a": 1}, "prompt_used": {"positive": "p"}, "nsfw": False}

    def test_the_sidecar_records_a_snapshot_next_to_the_existing_fields(self):
        sc = comfy.build_sidecar(self.JOB, ["2026-11/abcd1234_0.png"], 123.0)
        self.assertEqual((sc["commercial_use"], sc["licence"]), ("conditional", "Krea 2 Community License"))
        for k, v in (("id", "abcd1234efgh"), ("seed", 5), ("model", self.JOB["model"]), ("spec", {"a": 1}), ("images", ["2026-11/abcd1234_0.png"]), ("ts", 123.0), ("nsfw", False)):
            self.assertEqual(sc[k], v)

    def test_a_registry_failure_never_loses_the_image(self):
        with mock.patch.object(L, "snapshot", side_effect=RuntimeError("boom")):
            sc = comfy.build_sidecar(self.JOB, ["x.png"], 1.0)
        self.assertEqual((sc["commercial_use"], sc["licence"]), ("unverified", None))

    def test_the_snapshot_is_not_a_live_reference(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.json")
            with open(p, "w") as fh:
                json.dump(comfy.build_sidecar(self.JOB, [], 1.0), fh)
            before = L.sidecar_info(p)
            with mock.patch.object(L, "PATH", os.path.join(d, "other.json")):
                L.clear()
                self.assertEqual(L.sidecar_info(p), before)           # the sidecar says what it said at generation time
            self.assertEqual(before[0], "conditional")
            L.clear()


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "2026-11"); os.makedirs(self.dir)
        gallery_chars.clear(); L.clear()
        self.patches = [mock.patch.object(pf, "GALLERY_DIR", self.tmp.name), mock.patch.object(pf, "_gallery_names", lambda: {}), mock.patch.object(pf.data, "nsfw_enabled", lambda: True)]
        for p in self.patches: p.start()
        self.c = pf.app.test_client(); self.t = 1_700_000_000
        self.add("aaaaaaa1", {"commercial_use": "yes", "licence": "L"}, "Ettercap")
        self.add("aaaaaaa2", {"commercial_use": "conditional"}, "Ettercap")
        self.add("aaaaaaa3", {"commercial_use": "no"}, "Drider")
        self.add("aaaaaaa4", {}, "Drider")                                  # an older image with no snapshot
        self.add("aaaaaaa5", {"commercial_use": "bogus"}, "Ettercap")
        self.add("aaaaaaa6", {"commercial_use": "yes"}, "Drider")

    def tearDown(self):
        for p in self.patches: p.stop()
        gallery_chars.clear(); L.clear(); self.tmp.cleanup()

    def add(self, name, extra, who):
        self.t += 10
        png = os.path.join(self.dir, name + "_0.png")
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG")
        os.utime(png, (self.t, self.t))
        with open(os.path.join(self.dir, name + ".json"), "w") as fh:
            json.dump({"id": name, "spec": {"characters": [{"name": who}]}, **extra}, fh)

    def get(self, q=""):
        return json.loads(self.c.get("/api/gallery" + q).data)

    def test_every_item_carries_its_recorded_commercial_use(self):
        got = {it["file"].split("/")[1][:8]: it["commercial_use"] for it in self.get()["items"]}
        self.assertEqual(got, {"aaaaaaa1": "yes", "aaaaaaa2": "conditional", "aaaaaaa3": "no", "aaaaaaa4": "unverified", "aaaaaaa5": "unverified", "aaaaaaa6": "yes"})

    def test_the_commercial_filter_keeps_only_yes_and_counts_correctly(self):
        d = self.get("?commercial=1")
        self.assertEqual(sorted(it["file"].split("/")[1][:8] for it in d["items"]), ["aaaaaaa1", "aaaaaaa6"])
        self.assertEqual(d["total"], 2)
        self.assertEqual(self.get("?commercial=0")["total"], 6); self.assertEqual(self.get()["total"], 6)
        paged = self.get("?commercial=1&limit=1&offset=1")
        self.assertEqual((paged["total"], len(paged["items"])), (2, 1))

    def test_the_commercial_filter_combines_with_the_character_filter(self):
        d = self.get("?commercial=1&character=Drider")
        self.assertEqual([it["file"].split("/")[1][:8] for it in d["items"]], ["aaaaaaa6"])

    def test_presets_list_a_licence_for_every_model(self):
        with mock.patch.object(pf, "list_models", lambda: ["WAI-Nsfw-Illustrious-17.safetensors", "krea2:krea2_turbo_int8_convrot.safetensors", "mystery.safetensors"]):
            d = json.loads(self.c.get("/api/presets").data)
        self.assertEqual(d["checkpoints"][0], "WAI-Nsfw-Illustrious-17.safetensors")            # the list itself is unchanged
        lic = d["checkpoint_licenses"]
        self.assertEqual([lic[m]["commercial_use"] for m in d["checkpoints"]], ["yes", "conditional", "unverified"])
        self.assertEqual(set(lic["mystery.safetensors"]), {"source_url", "licence", "commercial_use", "note", "checked"})

    def test_presets_with_no_models_still_answer(self):
        with mock.patch.object(pf, "list_models", lambda: []):
            d = json.loads(self.c.get("/api/presets").data)
        self.assertEqual((d["checkpoints"], d["checkpoint_licenses"]), ([], {}))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Badges(unittest.TestCase):
    def js(self, expr):
        code = "const m = require(%s); console.log(JSON.stringify(%s));" % (json.dumps(os.path.join(WEB, "licences.js")), expr)
        out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_each_verdict_has_its_own_label_and_colour_and_unknown_is_unverified(self):
        labels = {v: self.js("m.badge(%s)" % json.dumps(v)) for v in ("yes", "conditional", "unverified", "no")}
        self.assertEqual([labels[v]["short"] for v in ("yes", "conditional", "unverified", "no")], ["✔", "⚠", "?", "✖"])
        self.assertEqual(len({b["color"] for b in labels.values()}), 4)
        self.assertEqual(self.js("m.badge('weird')"), labels["unverified"]); self.assertEqual(self.js("m.badge(undefined)"), labels["unverified"])

    def test_option_text_and_tooltip(self):
        self.assertEqual(self.js("m.optionText('x.safetensors', {commercial_use: 'yes'})"), "x.safetensors   [✔ can sell]")
        self.assertEqual(self.js("m.optionText('x.safetensors')"), "x.safetensors   [? unverified]")
        tip = self.js("m.tooltip({commercial_use: 'conditional', licence: 'Lic', note: 'Read it', checked: '2026-11-09'})")
        self.assertEqual(tip, "⚠ conditional · Lic · Read it · checked 2026-11-09")
        self.assertEqual(self.js("m.tooltip(null)"), "? unverified")


class PageWiring(unittest.TestCase):
    def test_the_page_loads_the_badge_script_before_the_app_and_the_app_uses_it(self):
        html = open(os.path.join(WEB, "index.html")).read()
        self.assertLess(html.index("/licences.js"), html.index("/app.js"))
        app = open(os.path.join(WEB, "app.js")).read()
        for needle in ("decorateModels", "checkpoint_licenses", "commercial=1", "galCommercial", "PFLicences.badge", "o.value = name"):
            self.assertIn(needle, app, needle)
        self.assertTrue(re.search(r"fillSelect\('#model'[^\n]*\n\s*decorateModels\(\)", app))


if __name__ == "__main__":
    unittest.main()
