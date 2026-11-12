"""Hand-offs from the library forges: the store (one hour), the routes, and the empty-slot spec builder.

Run: .venv/bin/python -B -m unittest test.test_handoff   (also as a file path or with `discover -s test -p 'test_*.py'`).
Everything runs on a temporary hand-off folder; CharacterForge is faked by a callable, never contacted.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from core import handoff  # noqa: E402

try:
    import app as pf  # noqa: E402
except ImportError:
    pf = None

NOW = 1794500000.0
PNG = b"\x89PNG\r\n\x1a\nhandoff"
BANKS = handoff.load_role_keywords()
ENTRY = {"slug": "Hooble", "keywords": "1girl, solo, gnome, (floppy denim wizard hat:1.2), blue overalls, leather boots, mace at hip, thoughtful expression, closed mouth, big backpack",
         "default_outfit": ["female", "scavenger", "patched jacket", "utility belt", "goggles"], "negative_add": "armor"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "handoff")
        self.p = mock.patch.object(handoff, "HANDOFF_DIR", self.dir)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def files(self):
        return sorted(os.listdir(self.dir)) if os.path.isdir(self.dir) else []


class StoreTests(Base):
    def test_create_get_and_image_round_trip(self):
        hid = handoff.create({"kind": "image", "prompt": "a goat", "seed": 5, "source": {"forge": "characterforge", "slug": "Hooble", "role": "pose"}}, PNG, ".png", now=NOW)
        rec = handoff.get(hid, now=NOW + 10)
        self.assertEqual((rec["payload"]["kind"], rec["payload"]["prompt"], rec["payload"]["seed"]), ("image", "a goat", 5))
        with open(handoff.image_path(hid, now=NOW + 10), "rb") as fh:
            self.assertEqual(fh.read(), PNG)
        self.assertEqual(sorted(n.split(".")[1] for n in self.files()), ["json", "png"])

    def test_entries_expire_after_an_hour_by_their_created_time(self):
        hid = handoff.create({"kind": "image", "prompt": "x"}, PNG, ".png", now=NOW)
        handoff.get(hid, now=NOW + 3599)
        with self.assertRaises(handoff.NotFound):
            handoff.get(hid, now=NOW + 3601)
        self.assertEqual(self.files(), [])                                         # the picture went with the record

    def test_a_new_hand_off_purges_old_ones_and_never_touches_other_files(self):
        old = handoff.create({"kind": "image", "prompt": "old"}, PNG, ".png", now=NOW - 7200)
        with open(os.path.join(self.dir, "notes.txt"), "w") as fh:
            fh.write("keep")
        fresh = handoff.create({"kind": "image", "prompt": "new"}, None, now=NOW)
        self.assertEqual(sorted(self.files()), sorted(["notes.txt", fresh + ".json"]))
        with self.assertRaises(handoff.NotFound):
            handoff.get(old, now=NOW)

    def test_bad_input_is_refused_and_stores_nothing(self):
        for payload in ({"kind": "nope"}, {}, "text", {"kind": "image", "seed": "x"}, {"kind": "image", "spec": "no"}, {"kind": "slot"}, {"kind": "slot", "slot": {"forge": "f", "slug": "s"}},
                        {"kind": "slot", "slot": {"forge": "f", "slug": "s", "role": "pose", "have": "x"}}, {"kind": "image", "prompt": 5}):
            with self.assertRaises(handoff.HandoffError, msg=str(payload)):
                handoff.create(payload, None, now=NOW)
        with self.assertRaises(handoff.HandoffError):
            handoff.create({"kind": "image"}, b"x" * (handoff.MAX_IMAGE_BYTES + 1), ".png", now=NOW)
        with self.assertRaises(handoff.HandoffError):
            handoff.create({"kind": "image"}, PNG, ".gif", now=NOW)
        with self.assertRaises(handoff.HandoffError):
            handoff.create({"kind": "slot", "slot": {"forge": "f", "slug": "s", "role": "pose"}}, PNG, ".png", now=NOW)      # a slot carries no picture
        self.assertEqual(self.files(), [])

    def test_ids_are_checked_so_paths_cannot_escape(self):
        for bad in ("../../etc/passwd", "..", "abc", "G" * 32, None, ""):
            with self.assertRaises(handoff.HandoffError):
                handoff.get(bad)
        with self.assertRaises(handoff.NotFound):
            handoff.get("0" * 32)

    def test_an_image_less_hand_off_has_no_picture(self):
        hid = handoff.create({"kind": "image", "prompt": "x"}, None, now=NOW)
        with self.assertRaises(handoff.NotFound):
            handoff.image_path(hid, now=NOW)


class SlotSpecTests(Base):
    def test_role_keywords_file_has_ten_variations_for_each_of_the_eight_roles(self):
        self.assertEqual(sorted(BANKS["roles"]), sorted(["neutral-reference", "pose", "action", "expression", "outfit", "cameo-variant", "storyboard-still", "other"]))
        for role, vs in BANKS["roles"].items():
            self.assertEqual(len(vs), 10, role)
            self.assertEqual(len({v["tag"] for v in vs}), 10, role)

    def test_the_first_unused_variation_is_chosen_and_it_rotates(self):
        first = handoff.build_slot_spec(ENTRY, "pose", [], BANKS)
        tags = [v["tag"] for v in BANKS["roles"]["pose"]]
        self.assertEqual(first["slot_tag"], tags[0])
        second = handoff.build_slot_spec(ENTRY, "pose", [tags[0]], BANKS)
        self.assertEqual(second["slot_tag"], tags[1])
        self.assertEqual(handoff.build_slot_spec(ENTRY, "pose", tags, BANKS)["slot_tag"], tags[0])        # all used: back to the first

    def test_the_spec_carries_canon_text_role_words_setting_and_negative(self):
        s = handoff.build_slot_spec(ENTRY, "action", [], BANKS)
        ch = s["characters"][0]
        self.assertEqual((ch["source"], ch["name"], ch["expression"]), ("custom", "Hooble", "none"))
        self.assertIn("blue overalls", ch["text"])
        self.assertTrue(s["pose_keywords"].startswith("(") and s["pose_keywords"].endswith(":1.3)"))
        self.assertIn("armor", s["negative"])
        self.assertIn("nude", s["negative"])
        self.assertEqual(ch["outfit"], {"gender": "female", "genre": "scavenger", "subgenre": "patched jacket", "key": "utility belt"})
        self.assertEqual(handoff.build_slot_spec(ENTRY, "neutral-reference", [], BANKS)["characters"][0]["expression"], "flat")

    def test_the_outfit_role_drops_canon_clothing_and_the_expression_role_the_face_tags(self):
        o = handoff.build_slot_spec(ENTRY, "outfit", [], BANKS)
        text = o["characters"][0]["text"]
        for gone in ("overalls", "leather boots", "backpack", "denim wizard hat", "thoughtful expression"):
            self.assertNotIn(gone, text)
        self.assertIn("mace at hip", text)
        self.assertEqual(o["characters"][0]["outfit"], {})
        e = handoff.build_slot_spec(ENTRY, "expression", [], BANKS)["characters"][0]["text"]
        self.assertNotIn("thoughtful expression", e)
        self.assertNotIn("closed mouth", e)
        self.assertIn("blue overalls", e)

    def test_unknown_role_and_an_entry_without_keywords_are_errors(self):
        with self.assertRaises(handoff.HandoffError):
            handoff.build_slot_spec(ENTRY, "dance", [], BANKS)
        with self.assertRaises(handoff.HandoffError):
            handoff.build_slot_spec({"slug": "x", "keywords": "  "}, "pose", [], BANKS)

    def test_resolve_fills_the_spec_from_the_entry_and_never_raises(self):
        rec = {"payload": {"kind": "slot", "slot": {"forge": "characterforge", "slug": "Hooble", "role": "pose", "have": []}}}
        out = handoff.resolve(rec, BANKS, opener=lambda url: json.dumps(ENTRY))
        self.assertIn("spec", out)
        self.assertNotIn("warning", out)
        bad = handoff.resolve(rec, BANKS, opener=lambda url: (_ for _ in ()).throw(OSError("down")))
        self.assertIn("could not read the entry", bad["warning"])
        self.assertNotIn("spec", bad)
        other = handoff.resolve({"payload": {"kind": "slot", "slot": {"forge": "someforge", "slug": "x", "role": "pose"}}}, BANKS)
        self.assertIn("unknown forge", other["warning"])
        img = handoff.resolve({"payload": {"kind": "image", "prompt": "p"}}, BANKS)
        self.assertEqual(img, {"kind": "image", "prompt": "p"})


@unittest.skipIf(pf is None, "Flask not installed")
class RouteTests(Base):
    def setUp(self):
        super().setUp()
        self.c = pf.app.test_client()

    def test_post_get_and_image_with_a_picture(self):
        r = self.c.post("/api/handoff", data={"payload": json.dumps({"kind": "image", "prompt": "a goat", "source": {"forge": "characterforge", "slug": "Hooble", "role": "pose"}}),
                                              "image": (io.BytesIO(PNG), "pic.png", "image/png")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 201)
        hid = r.get_json()["id"]
        self.assertEqual(r.get_json()["url"], "/?handoff=" + hid)
        g = self.c.get("/api/handoff/" + hid).get_json()
        self.assertEqual((g["payload"]["prompt"], g["payload"]["image_url"]), ("a goat", "/api/handoff/%s/image" % hid))
        img = self.c.get("/api/handoff/%s/image" % hid)
        self.assertEqual((img.status_code, img.data), (200, PNG))

    def test_a_slot_hand_off_is_resolved_on_get(self):
        r = self.c.post("/api/handoff", json={"kind": "slot", "slot": {"forge": "characterforge", "slug": "Hooble", "role": "pose", "have": []}})
        self.assertEqual(r.status_code, 201)
        with mock.patch.object(handoff, "fetch_entry", lambda forge, slug, opener=None: ENTRY):
            g = self.c.get("/api/handoff/" + r.get_json()["id"]).get_json()
        self.assertIn("blue overalls", g["payload"]["spec"]["characters"][0]["text"])
        self.assertNotIn("image_url", g["payload"])

    def test_errors_are_400_and_404(self):
        self.assertEqual(self.c.post("/api/handoff", json={"kind": "nope"}).status_code, 400)
        self.assertEqual(self.c.post("/api/handoff", data={"payload": "{not json"}, content_type="multipart/form-data").status_code, 400)
        self.assertEqual(self.c.get("/api/handoff/" + "0" * 32).status_code, 404)
        self.assertEqual(self.c.get("/api/handoff/not-an-id").status_code, 400)
        hid = self.c.post("/api/handoff", json={"kind": "image", "prompt": "x"}).get_json()["id"]
        self.assertEqual(self.c.get("/api/handoff/%s/image" % hid).status_code, 404)


if __name__ == "__main__":
    unittest.main()
