"""The slot-fill variation banks (scripts/slot_fill/banks.py and data/slot-banks/*.json).

Run: TMPDIR=/dev/shm/x .venv/bin/python -B -m unittest test.test_slot_fill_banks   (from the repo root)
"""
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from scripts.slot_fill import banks  # noqa: E402
from scripts.slot_fill.errors import SlotFillError  # noqa: E402

REFERENCE = "/media/model-cache/forge-batches/fill_character_slots.py"
ROLES = ["neutral-reference", "pose", "action", "expression", "outfit", "cameo-variant", "storyboard-still", "other"]


def offset(slug):
    return int(hashlib.sha256(slug.encode()).hexdigest()[:4], 16)


def put(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh)


def temp_banks(roles=None, defaults=None, overrides=None, extra_files=None):
    d = tempfile.mkdtemp(prefix="banks-")
    os.makedirs(os.path.join(d, "data", "slot-banks"))
    base = os.path.join(d, "data", "slot-banks")
    put(os.path.join(base, "characters.json"), {"roles": roles if roles is not None else {"pose": [{"tag": "a"}, {"tag": "b"}, {"tag": "c"}]}})
    put(os.path.join(base, "defaults.json"), defaults if defaults is not None else {"target": 10, "sizes": {}, "priority": [], "negative_extra": ""})
    put(os.path.join(base, "overrides.json"), overrides if overrides is not None else {"entries": {}})
    for name, obj in (extra_files or {}).items():
        put(os.path.join(base, name), obj)
    return d


class ShippedFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = banks.load("characters")

    def test_eight_roles_of_ten_unique_tags(self):
        self.assertEqual(list(self.b.roles), ROLES)
        for role, bank in self.b.roles.items():
            self.assertEqual(len(bank), 10, role)
            self.assertEqual(len({v["tag"] for v in bank}), 10, role)

    def test_defaults_and_overrides_match_the_reference_batch(self):
        self.assertEqual(self.b.priority, ["Hooble", "Lucky_the_Pocket_Gnome"])
        self.assertEqual(self.b.size("storyboard-still"), (1152, 896))
        self.assertEqual(self.b.size("other"), (1152, 896))
        self.assertIsNone(self.b.size("pose"))
        self.assertIn("nude", self.b.negative_extra)
        self.assertTrue(self.b.negative_extra.endswith("cropped"))
        self.assertEqual(self.b.override("Zap"), {"always_clothed": True})
        self.assertEqual(self.b.override("Zip"), {"always_clothed": True})
        self.assertEqual(self.b.override("Nobody"), {})
        self.assertEqual(self.b.target("Hooble", "pose"), 10)

    @unittest.skipUnless(os.path.exists(REFERENCE), "the reference batch script is not on this machine")
    def test_the_shipped_banks_are_the_reference_banks_faithfully(self):
        spec = importlib.util.spec_from_file_location("ref_batch", REFERENCE)
        ref = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ref)
        for role, bank in ref.BANKS.items():
            got = [(v["tag"], v["pose"], v["setting"], v["style"], v["extra"]) for v in self.b.roles[role]]
            self.assertEqual(got, [tuple(t) for t in bank], role)
        for slug in ("Hooble", "Zap", "Gobs", "Cindy"):
            for role in ROLES:
                for rnd in (1, 4, 10):
                    self.assertEqual(self.b.variation(slug, role, rnd)["tag"], ref.pick(slug, role, rnd)[0])


class Rotation(unittest.TestCase):
    def setUp(self):
        self.b = banks.load("characters")

    def test_the_first_variation_follows_the_hash_offset(self):
        for slug in ("Hooble", "Gobs", "a", "b"):
            expect = self.b.roles["pose"][offset(slug) % 10]
            self.assertEqual(self.b.variation(slug, "pose", 1), expect)

    def test_rounds_walk_the_bank_and_wrap_around(self):
        slug = "Gobs"
        seen = [self.b.variation(slug, "pose", r)["tag"] for r in range(1, 11)]
        self.assertEqual(sorted(seen), sorted(v["tag"] for v in self.b.roles["pose"]))       # ten rounds visit all ten variations
        self.assertEqual(self.b.variation(slug, "pose", 11), self.b.variation(slug, "pose", 1))
        self.assertEqual(self.b.variation(slug, "pose", 12)["tag"], seen[1])

    def test_slugs_start_at_different_places_and_the_choice_is_stable(self):
        firsts = {self.b.variation(s, "pose", 1)["tag"] for s in ("Hooble", "Gobs", "Zap", "Zip", "Cindy", "Lucky", "Marcus", "Bard")}
        self.assertGreater(len(firsts), 3)
        self.assertEqual(self.b.variation("Gobs", "action", 3), self.b.variation("Gobs", "action", 3))

    def test_a_returned_variation_is_a_copy(self):
        v = self.b.variation("Gobs", "pose", 1)
        v["tag"] = "changed"
        self.assertNotEqual(self.b.variation("Gobs", "pose", 1)["tag"], "changed")

    def test_unknown_role_is_an_error(self):
        with self.assertRaises(SlotFillError):
            self.b.variation("Gobs", "nonsense", 1)


class Targets(unittest.TestCase):
    def test_override_then_defaults_then_argument(self):
        d = temp_banks(defaults={"target": 7}, overrides={"entries": {"x": {"targets": {"pose": 3}}}})
        b = banks.load("characters", d)
        self.assertEqual(b.target("x", "pose"), 3)
        self.assertEqual(b.target("x", "action"), 7)
        self.assertEqual(b.target("y", "pose"), 7)
        d2 = temp_banks(defaults={}, overrides={"entries": {}})
        self.assertEqual(banks.load("characters", d2).target("x", "pose", default=4), 4)

    def test_zero_and_skip_roles_mean_skip(self):
        d = temp_banks(defaults={"target": 10}, overrides={"entries": {"x": {"targets": {"pose": 0}, "skip_roles": ["action"]}}})
        b = banks.load("characters", d)
        self.assertEqual(b.target("x", "pose"), 0)
        self.assertEqual(b.target("x", "action"), 0)
        self.assertEqual(b.target("x", "other"), 10)

    def test_bad_target_values_fall_back(self):
        d = temp_banks(defaults={"target": 10}, overrides={"entries": {"x": {"targets": {"pose": -1, "action": True, "other": "3"}}}})
        b = banks.load("characters", d)
        self.assertEqual([b.target("x", r) for r in ("pose", "action", "other")], [10, 10, 10])


class Validation(unittest.TestCase):
    def fails(self, **kw):
        with self.assertRaises(SlotFillError) as cm:
            banks.load("characters", temp_banks(**kw))
        return str(cm.exception)

    def test_empty_role(self):
        self.assertIn("pose", self.fails(roles={"pose": []}))

    def test_duplicate_tag_names_the_file_and_the_tag(self):
        msg = self.fails(roles={"pose": [{"tag": "a"}, {"tag": "a"}]})
        self.assertIn("characters.json", msg)
        self.assertIn("'a'", msg)

    def test_variation_without_a_tag(self):
        self.assertIn("no tag", self.fails(roles={"pose": [{"pose": "x"}]}))

    def test_bad_size(self):
        self.assertIn("defaults.json", self.fails(defaults={"sizes": {"pose": [100]}}))
        self.assertIn("defaults.json", self.fails(defaults={"sizes": {"pose": [100, -5]}}))
        self.assertIn("defaults.json", self.fails(defaults={"sizes": {"pose": [0, 100]}}))       # zero is not a size
        self.assertIn("defaults.json", self.fails(defaults={"sizes": {"pose": [True, 100]}}))

    def test_missing_and_garbled_files(self):
        d = temp_banks()
        os.remove(os.path.join(d, "data", "slot-banks", "overrides.json"))
        with self.assertRaisesRegex(SlotFillError, "overrides.json"):
            banks.load("characters", d)
        d = temp_banks()
        with open(os.path.join(d, "data", "slot-banks", "defaults.json"), "w") as fh:
            fh.write("{not json")
        with self.assertRaisesRegex(SlotFillError, "not valid JSON"):
            banks.load("characters", d)

    def test_missing_fields_are_filled_with_defaults(self):
        b = banks.load("characters", temp_banks(roles={"pose": [{"tag": "a"}]}))
        self.assertEqual(b.variation("s", "pose", 1), {"tag": "a", "pose": "", "setting": None, "style": None, "extra": ""})


class ProposedIsIgnored(unittest.TestCase):
    def test_a_bogus_proposed_json_changes_nothing_and_is_never_read(self):
        d = temp_banks(extra_files={"proposed.json": {"roles": {"pose": [{"tag": "from-a-model"}]}}})
        base = os.path.join(d, "data", "slot-banks")
        with open(os.path.join(base, "proposed.json"), "w") as fh:
            fh.write("this is not even json")
        b = banks.load("characters", d)
        tags = {b.variation("s", "pose", r)["tag"] for r in range(1, 7)}
        self.assertEqual(tags, {"a", "b", "c"})


if __name__ == "__main__":
    unittest.main()
