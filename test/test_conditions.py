"""Condition levels from a CharacterForge ladder (python3 -m unittest discover -s test -p 'test_*.py')."""
import json
import os
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp(prefix="pf-cond-")
ROOT = os.path.join(TMP, "characterforge")
for d in ("characters", "samples"):
    os.makedirs(os.path.join(ROOT, d))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from core import assemble, cf_characters  # noqa: E402

LADDER = [{"id": "tired", "name": "Tired", "keywords": "dark circles, slumped shoulders", "negative": "smiling"},
          {"id": "Bruised", "name": "Bruised", "keywords": "black eye", "negative": ""},
          {"name": "no id, dropped", "keywords": "x"}]


def note(slug, meta, kw):
    with open(os.path.join(ROOT, "characters", slug + ".md"), "w", encoding="utf-8") as fh:
        fh.write("---\n%s\n---\n# %s\n\n## Keywords\n\n%s\n" % (json.dumps(meta), slug, kw))


note("Peasant_A_goat", {"name": "Peasant A (Goat)", "negative_add": "human face", "conditions": LADDER}, "anthro, goat, ragged tunic")
note("Plain", {"name": "Plain"}, "1girl, hat")


class Conditions(unittest.TestCase):
    # The reader looks the store up from the environment on every call, and the other test
    # modules point it at their own fixtures, so claim it for this class only.
    @classmethod
    def setUpClass(cls):
        cls._saved = {k: os.environ.get(k) for k in ("CHARACTERFORGE_ROOT", "PROMPTFORGE_CHARACTER_SOURCE", "CHARACTERFORGE_DIR")}
        os.environ["CHARACTERFORGE_ROOT"] = ROOT
        os.environ.pop("PROMPTFORGE_CHARACTER_SOURCE", None)
        os.environ.pop("CHARACTERFORGE_DIR", None)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_ladder_is_read_normalised_and_optional(self):
        c = cf_characters.get_character("Peasant_A_goat")
        self.assertEqual([r["id"] for r in c["conditions"]], ["tired", "bruised"])  # ids lowercased, id-less rung dropped
        self.assertNotIn("conditions", cf_characters.get_character("Plain"))

    def test_condition_adds_keywords_and_negatives_after_the_base(self):
        seg, neg = assemble.character_segment({"source": "preset", "name": "Peasant_A_goat", "condition": "tired", "expression": "flat"})
        self.assertIn("ragged tunic", seg)
        self.assertLess(seg.index("ragged tunic"), seg.index("dark circles"))
        self.assertIn("slumped shoulders", seg)
        self.assertEqual(neg, "human face, smiling")

    def test_condition_id_is_case_insensitive(self):
        seg, _ = assemble.character_segment({"source": "preset", "name": "Peasant_A_goat", "condition": "BRUISED"})
        self.assertIn("black eye", seg)

    def test_no_or_unknown_condition_is_the_base_look(self):
        base = assemble.character_segment({"source": "preset", "name": "Peasant_A_goat"})
        self.assertNotIn("dark circles", base[0])
        self.assertEqual(assemble.character_segment({"source": "preset", "name": "Peasant_A_goat", "condition": "dead"}), base)
        self.assertEqual(assemble.character_segment({"source": "preset", "name": "Plain", "condition": "tired"})[0],
                         assemble.character_segment({"source": "preset", "name": "Plain"})[0])

    def test_the_assembled_prompt_carries_it(self):
        out = assemble.assemble({"characters": [{"source": "preset", "name": "Peasant_A_goat", "condition": "tired"}]})
        self.assertIn("dark circles", out["positive"])
        self.assertIn("smiling", out["negative"])


if __name__ == "__main__":
    unittest.main()
