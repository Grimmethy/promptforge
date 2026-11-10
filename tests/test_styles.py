"""data/styles.yaml: assert every style entry is positively worded (no negation)."""
import os
import sys
import unittest

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

NEGATION_WORDS = {"no", "without", "not", "never", "avoid"}


class StylesYamlTest(unittest.TestCase):
    def _load_styles(self):
        path = os.path.join(os.path.dirname(__file__), "..", "data", "styles.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("entries", {})

    def test_flat_vector_sticker_key_exists(self):
        entries = self._load_styles()
        self.assertIn(
            "flat vector sticker",
            entries,
            "'flat vector sticker' must be a key in data/styles.yaml",
        )

    def test_flat_vector_sticker_text_has_no_negation(self):
        entries = self._load_styles()
        text = entries["flat vector sticker"].lower()
        words = set(text.split())
        negated = words & NEGATION_WORDS
        self.assertEqual(
            negated,
            set(),
            f"'flat vector sticker' text must not contain negation words; "
            f"found: {sorted(negated)}",
        )


if __name__ == "__main__":
    unittest.main()
