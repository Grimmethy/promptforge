"""Tests for data.load_themes() (python3 -m unittest discover -s test -p 'test_*.py'; no GPU, no ComfyUI).
Covers the shallow-merge contract: base-only, overlay-add, overlay-replace
(base keys not retained), and missing-file cases."""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
from core import data  # noqa: E402


class LoadThemesTest(unittest.TestCase):
    def test_returns_base_themes_when_no_overlay(self):
        with mock.patch.object(data, "_load_overlay", return_value={}):
            result = data.load_themes()
        # data/themes.yaml ships a `themes:` key with several entries.
        self.assertIsInstance(result, dict)
        self.assertIn("none", result)
        self.assertIn("grimdark", result)
        self.assertIn("style", result["none"])
        self.assertIn("setting_bias", result["none"])

    def test_overlay_adds_new_themes(self):
        overlay = {"themes": {"custom": {"style": "x", "palette": "y"}}}
        with mock.patch.object(data, "_load_overlay", return_value=overlay):
            result = data.load_themes()
        self.assertIn("custom", result)
        self.assertEqual(result["custom"]["style"], "x")

    def test_overlay_replaces_base_entries_wholesale(self):
        # Overlay replaces the base "none" entry entirely; base keys like
        # "setting_bias" must NOT be retained.
        overlay = {"themes": {"none": {"style": "OVERRIDDEN"}}}
        with mock.patch.object(data, "_load_overlay", return_value=overlay):
            result = data.load_themes()
        self.assertNotIn("setting_bias", result["none"])
        self.assertNotIn("palette", result["none"])
        self.assertEqual(result["none"]["style"], "OVERRIDDEN")

    def test_missing_file_returns_empty_dict(self):
        with mock.patch.object(data, "_load_yaml", return_value={}):
            with mock.patch.object(data, "_load_overlay", return_value={}):
                result = data.load_themes()
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
