"""Sampler defaults follow the model family (2026-11-08, friction item 41): choosing a Krea 2 model must switch CFG / steps / sampler / scheduler to
Krea Turbo's values (1 / 8 / euler / simple) and choosing an SDXL model must bring back 6 / 30 / dpmpp_2m_sde / karras, because the UI always sends all four
fields and the server then treats them as explicit (the SDXL numbers made Krea 2 Turbo return colour noise). The logic is in web/model-defaults.js (pure
functions); these tests load it with node when node exists (skipped otherwise), and pin its numbers to the server's without node.
No ComfyUI, no GPU (python3 -m unittest discover -s test -p 'test_*.py')."""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "..", "web")
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import app as pf  # noqa: E402

KREA = "krea2:krea2_turbo_int8_convrot.safetensors"
SDXL = "novaFurryXL_ilV140.safetensors"
SDXL_VALUES = {"cfg": "6", "steps": "30", "sampler": "dpmpp_2m_sde", "scheduler": "karras"}
KREA_VALUES = {"cfg": 1, "steps": 8, "sampler": "euler", "scheduler": "simple"}


def node_eval(expr):
    """Evaluate a JS expression with the module loaded as `m`; returns the JSON-decoded result."""
    js = "const m = require(%s); console.log(JSON.stringify(%s));" % (json.dumps(os.path.join(WEB, "model-defaults.js")), expr)
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not installed: the JS logic tests are skipped")
class LogicTest(unittest.TestCase):
    def test_family_is_the_krea2_prefix_and_the_placeholder_counts_as_sdxl(self):
        self.assertEqual(node_eval("[m.family(%s), m.family(%s), m.family('(ComfyUI down)'), m.family(''), m.family(undefined)]"
                                   % (json.dumps(KREA), json.dumps(SDXL))), ["krea2", "sdxl", "sdxl", "sdxl", "sdxl"])

    def test_choosing_krea2_from_sdxl_returns_the_krea_defaults(self):
        r = node_eval("m.swap(%s, %s, %s, {})" % (json.dumps(SDXL), json.dumps(KREA), json.dumps(SDXL_VALUES)))
        self.assertEqual(r["values"], KREA_VALUES)
        self.assertEqual(r["stash"]["sdxl"], SDXL_VALUES)          # what the user had for SDXL is kept

    def test_choosing_sdxl_again_returns_the_sdxl_defaults_when_nothing_was_stashed(self):
        r = node_eval("m.swap(%s, %s, %s, {})" % (json.dumps(KREA), json.dumps(SDXL), json.dumps(KREA_VALUES)))
        self.assertEqual(r["values"], {"cfg": 6, "steps": 30, "sampler": "dpmpp_2m_sde", "scheduler": "karras"})

    def test_a_value_typed_for_one_family_comes_back_after_a_round_trip(self):
        typed = {"cfg": "2", "steps": "8", "sampler": "euler", "scheduler": "simple"}      # what the form fields hold: strings
        a = node_eval("m.swap(%s, %s, %s, {})" % (json.dumps(KREA), json.dumps(SDXL), json.dumps(typed)))
        b = node_eval("m.swap(%s, %s, %s, %s)" % (json.dumps(SDXL), json.dumps(KREA), json.dumps(a["values"]), json.dumps(a["stash"])))
        self.assertEqual(b["values"], typed)                                                # the user's own cfg 2 came back, not Krea's 1

    def test_no_change_inside_one_family(self):
        for a, b in ((SDXL, "otherCheckpoint.safetensors"), (KREA, "krea2:krea2_other.safetensors")):
            r = node_eval("m.swap(%s, %s, %s, {})" % (json.dumps(a), json.dumps(b), json.dumps(SDXL_VALUES)))
            self.assertIsNone(r["values"], (a, b))

    def test_a_saved_krea2_model_with_the_sdxl_numbers_needs_the_fix_and_nothing_else_does(self):
        cases = [(KREA, SDXL_VALUES, True),
                 (KREA, dict(SDXL_VALUES, cfg="6.0"), True),                      # the same number written another way
                 (KREA, dict(SDXL_VALUES, cfg="2"), False),                       # the user chose a value
                 (KREA, {k: str(v) for k, v in KREA_VALUES.items()}, False),      # already Krea's
                 (SDXL, SDXL_VALUES, False)]                                      # an SDXL model with SDXL numbers is right
        for model, vals, want in cases:
            self.assertEqual(node_eval("m.needsKreaFix(%s, %s)" % (json.dumps(model), json.dumps(vals))), want, (model, vals))


class PinnedToTheServerTest(unittest.TestCase):
    """No node needed: the numbers in model-defaults.js are read as text and compared with the server's."""

    @staticmethod
    def js_defaults():
        with open(os.path.join(WEB, "model-defaults.js"), encoding="utf-8") as fh:
            src = fh.read()
        out = {}
        for fam in ("sdxl", "krea2"):
            m = re.search(fam + r":\s*\{\s*cfg:\s*([\d.]+),\s*steps:\s*(\d+),\s*sampler:\s*'([^']+)',\s*scheduler:\s*'([^']+)'", src)
            assert m, "no %s defaults in model-defaults.js" % fam
            out[fam] = {"cfg": float(m.group(1)), "steps": int(m.group(2)), "sampler": m.group(3), "scheduler": m.group(4)}
        return out

    def test_the_sdxl_numbers_equal_the_servers_defaults(self):
        d = self.js_defaults()["sdxl"]
        self.assertEqual((d["cfg"], d["steps"], d["sampler"], d["scheduler"]),
                         (pf._DEFAULTS["cfg"], pf._DEFAULTS["steps"], pf._DEFAULTS["sampler"], pf._DEFAULTS["scheduler"]))

    def test_the_krea2_numbers_equal_what_the_server_uses_for_a_krea2_model(self):
        d = self.js_defaults()["krea2"]
        s = pf._repair_job_defaults({}, KREA)
        self.assertEqual((d["cfg"], d["steps"], d["sampler"], d["scheduler"]), (s["cfg"], s["steps"], s["sampler"], s["scheduler"]))

    def test_the_page_loads_the_helper_before_app_js(self):
        with open(os.path.join(WEB, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        a, b = html.find("/model-defaults.js"), html.find("/app.js")
        self.assertTrue(0 <= a < b, "model-defaults.js must be loaded before app.js")

    def test_app_js_wires_the_model_change_and_the_reload_repair(self):
        with open(os.path.join(WEB, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("$('#model').addEventListener('change', onModelChange)", js)
        self.assertIn("PFModelDefaults.needsKreaFix(", js)
        self.assertIn("PFModelDefaults.swap(", js)

    def test_reuse_settings_keeps_the_family_tracker_in_step_without_swapping_the_specs_numbers(self):
        with open(os.path.join(WEB, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        start = js.index("function applySpec(")
        body = js[start:js.index("\n}\n", start)]
        self.assertIn("_prevModel = $('#model').value", body)       # a programmatic model change fires no change event
        self.assertNotIn("PFModelDefaults.swap", body)              # and the saved numbers are not replaced by defaults


if __name__ == "__main__":
    unittest.main()
