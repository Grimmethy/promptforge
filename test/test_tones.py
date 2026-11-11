"""Tone: a prompt category beside styles and expressions (python3 -m unittest discover -s test -p 'test_*.py'; no GPU, no ComfyUI).
A tone is how a shot feels: expression, camera and lighting tags. data/tones.yaml mirrors the six tones of the shared action-catalog."""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import app as pf  # noqa: E402
from core import data, themes  # noqa: E402

TONES = ["serious", "comedic", "dramatic", "mischievous", "playful", "somber"]
BASE = {"characters": [], "theme": "", "style": "anime clean", "setting": "", "negative": "", "model": "x.safetensors"}


def job(**extra):
    return pf.resolve_job({**BASE, **extra})


def read(rel):
    with open(os.path.join(HERE, "..", rel), encoding="utf-8") as fh:
        return fh.read()


class ToneDataTest(unittest.TestCase):
    def test_the_six_tones_load_after_none(self):
        self.assertEqual(data.list_entries("tones"), ["none"] + TONES)

    def test_none_adds_nothing(self):
        self.assertEqual(data.get_entry("tones", "none"), ("", ""))

    def test_each_tone_is_built_from_its_expression_camera_and_lighting_tags(self):
        want = {"serious": ["stern", "slightly low angle", "dramatic lighting"], "comedic": ["wide eyes", "dutch angle", "bright even lighting"],
                "dramatic": ["determined", "low angle", "rim light"], "mischievous": ["smirk", "close-up", "warm spotlight"],
                "playful": ["bouncy pose", "medium shot", "bright flat lighting"], "somber": ["downcast eyes", "eye level", "overcast soft light"]}
        for tone, tags in want.items():
            text = data.get_entry("tones", tone)[0]
            for tag in tags:
                self.assertIn(tag, text, "%s lacks %s" % (tone, tag))

    def test_tones_is_an_entry_target_like_styles(self):
        self.assertIn("tones", data.CATEGORIES)
        self.assertIn("tones", data.ALLOWED_ENTRY_TARGETS)


class ToneAssemblyTest(unittest.TestCase):
    def test_a_spec_with_a_tone_adds_its_tags_after_the_style_tags(self):
        scene = job(tone="comedic")["scene_tags"]
        style = data.get_entry("styles", "anime clean")[0]
        tone = data.get_entry("tones", "comedic")[0]
        self.assertEqual(scene, data.normalize_kw(style, tone))
        self.assertLess(scene.index("flat colors"), scene.index("exaggerated expression"))
        self.assertIn("exaggerated expression", job(tone="comedic")["positive"])

    def test_every_tone_reaches_the_positive_prompt(self):
        for tone in TONES:
            first = data.get_entry("tones", tone)[0].split(",")[0].strip()
            self.assertIn(first, job(tone=tone)["positive"], tone)

    def test_a_spec_without_a_tone_assembles_exactly_as_before(self):
        th = themes.resolve(BASE["theme"])
        before = data.normalize_kw(pf._cat_tags("backgrounds", BASE["setting"]) or th["setting_bias"],
                                   pf._cat_tags("styles", BASE["style"]) or th["style"], th["palette"], th["lighting"])
        plain = job()
        self.assertEqual(plain["scene_tags"], before)
        for absent in ("none", "", None):
            other = job(tone=absent)
            for field in ("scene_tags", "positive", "negative", "prompt_used"):
                self.assertEqual(other[field], plain[field], "%r changed %s" % (absent, field))

    def test_a_tone_does_not_change_the_negative_prompt(self):
        self.assertEqual(job(tone="serious")["negative"], job()["negative"])

    def test_an_unknown_tone_is_passed_through_the_way_an_unknown_style_is(self):
        self.assertEqual(job(tone="grumpy cat")["scene_tags"], data.normalize_kw(data.get_entry("styles", "anime clean")[0], "grumpy cat"))
        self.assertEqual(job(style="grumpy cat")["scene_tags"], "grumpy cat")

    def test_a_theme_without_an_explicit_tone_is_unchanged(self):
        theme = themes.list_themes()[0]
        self.assertEqual(job(theme=theme, tone="none")["scene_tags"], job(theme=theme)["scene_tags"])


class ToneUiTest(unittest.TestCase):
    def test_the_presets_payload_lists_the_tones(self):
        with mock.patch.object(pf, "list_models", return_value=[]):
            body = pf.app.test_client().get("/api/presets").get_json()
        self.assertEqual(body["tones"], ["none"] + TONES)

    def test_the_builder_has_a_tone_selector_next_to_style(self):
        html, js = read("web/index.html"), read("web/app.js")
        self.assertIn('<select id="tone"></select>', html)
        self.assertLess(html.index('id="styleText"'), html.index('id="tone"'))
        self.assertEqual(js.count("fillSelect('#tone'"), js.count("fillSelect('#style'"))   # filled wherever the style select is
        self.assertIn("tone: $('#tone').value === 'none' ? undefined : $('#tone').value", js)  # sent with the spec, absent when none
        self.assertIn("'scheduler', 'tone']", js)                                               # restored with the rest of the spec


if __name__ == "__main__":
    unittest.main()
