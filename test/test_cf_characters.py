"""CharacterForge-backed character reader (python3 -m unittest discover -s test -p 'test_*.py')."""
import os
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp(prefix="pf-cf-")
ROOT = os.path.join(TMP, "characterforge")
for d in ("characters", "samples"):
    os.makedirs(os.path.join(ROOT, d))
os.environ["CHARACTERFORGE_ROOT"] = ROOT
os.environ.pop("PROMPTFORGE_CHARACTER_SOURCE", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from core import cf_characters, data  # noqa: E402


def note(kind, slug, meta_json, keywords):
    with open(os.path.join(ROOT, kind, slug + ".md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\n{meta_json}\n---\n# {slug}\n\nprose\n\n## Keywords\n\n{keywords}\n\n## Backstory\n\nlong\n")


note("characters", "Gobs", '{"name": "Gobs", "weight": 1.2, "default_outfit": ["female","fantasy","scavenger","rags"], "negative_add": "green man"}', "1girl,\ngoblin, (green skin:1.3)")
note("samples", "Gobs", '{"name": "Sample Gobs"}', "SAMPLE SHOULD BE SHADOWED")
note("samples", "Bard_Goat", '{"name": "Bard Goat"}', "1other, anthro, goat")
note("characters", "NoKeywords", '{"name": "No Keywords"}', "")


class CfCharacterTests(unittest.TestCase):
    def test_configured_and_lists_only_characters_with_keywords(self):
        self.assertTrue(cf_characters.configured())
        self.assertEqual(cf_characters.list_characters(), ["Bard_Goat", "Gobs"])

    def test_returns_the_shape_the_preset_yaml_used_to_provide(self):
        c = cf_characters.get_character("Gobs")
        self.assertEqual(c, {"name": "Gobs", "weight": 1.2, "base": "1girl, goblin, (green skin:1.3)",
                             "default_outfit": ["female", "fantasy", "scavenger", "rags"], "negative_add": "green man"})

    def test_user_characters_shadow_samples(self):
        self.assertNotIn("SAMPLE", cf_characters.get_character("Gobs")["base"])

    def test_legacy_lowercase_and_display_names_resolve(self):
        self.assertEqual(cf_characters.get_character("gobs")["name"], "Gobs")
        self.assertEqual(cf_characters.get_character("bard goat")["name"], "Bard Goat")
        self.assertEqual(cf_characters.get_character("nobody"), {})
        self.assertEqual(cf_characters.get_character(""), {})

    def test_data_module_delegates_and_refuses_local_edits(self):
        self.assertEqual(data.list_characters(), ["Bard_Goat", "Gobs"])
        self.assertEqual(data.get_character("gobs")["weight"], 1.2)
        with self.assertRaisesRegex(ValueError, "owned by CharacterForge"):
            data.raw_character("Gobs")
        with self.assertRaisesRegex(ValueError, "owned by CharacterForge"):
            data.save_character("x", "base: a")

    def test_local_source_switch_restores_legacy_behaviour(self):
        os.environ["PROMPTFORGE_CHARACTER_SOURCE"] = "local"
        try:
            self.assertFalse(cf_characters.configured())
            self.assertIn("marcus", data.list_characters())  # the repo's own data/characters presets
        finally:
            del os.environ["PROMPTFORGE_CHARACTER_SOURCE"]


if __name__ == "__main__":
    unittest.main()
