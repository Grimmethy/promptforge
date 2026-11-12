"""The CharacterForge adapter of the slot-fill engine (scripts/slot_fill/adapters/characterforge.py), against a fake CharacterForge over real HTTP.

Run: TMPDIR=/dev/shm/x .venv/bin/python -B -m unittest test.test_slot_fill_cf_adapter   (from the repo root)
"""
import email
import http.server
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from scripts.slot_fill import banks as banks_mod  # noqa: E402
from scripts.slot_fill.adapters.characterforge import CharacterForgeAdapter, outfit_of, strip_tokens  # noqa: E402
from scripts.slot_fill.errors import SlotFillError  # noqa: E402

NEUTRAL = {"style": "anime clean", "model": "m.safetensors", "cfg": 4.5, "steps": 8, "sampler": "euler", "negative": "bad hands", "width": 896, "height": 1152, "licence_note": "check"}
ROLES = ["neutral-reference", "pose", "action", "expression", "outfit", "cameo-variant", "storyboard-still", "other"]

DETAIL = {
    "Hooble": {"slug": "Hooble", "kind": "character", "keywords": "gnome, red hat, thoughtful expression, leather boots, round glasses", "default_outfit": ["m", "fantasy", "village", "tunic", "extra"],
               "neutral_text": "a small gnome", "neutral_negative": "tall", "negative_add": "blurry"},
    "Alpha": {"slug": "Alpha", "kind": "character", "keywords": "1girl, green eyes, long hair"},
    "Zap": {"slug": "Zap", "kind": "character", "keywords": "alien, blue skin, antennae"},
    "Zed": {"slug": "Zed", "kind": "character", "keywords": "dwarf, beard, wearing a heavy coat"},
    "Samp": {"slug": "Samp", "kind": "character", "keywords": "sample goat", "sample": True},
    "Old": {"slug": "Old", "kind": "character", "keywords": "retired thing", "status": "retired"},
    "Blank": {"slug": "Blank", "kind": "character", "keywords": "   "},
}
LIST = [{"slug": s, "kind": d.get("kind")} for s, d in DETAIL.items()] + [{"slug": "Arch", "kind": "archetype"}, {"slug": "Sq", "kind": "squad"}]


class FakeCF(http.server.BaseHTTPRequestHandler):
    requests = []
    images = {"Hooble": [{"role": "pose", "verdict": "rejected"}, {"role": "pose", "verdict": "needs-work"}, {"role": "outfit", "verdict": "needs-work"}]}
    post_status = 201
    post_error = None

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FakeCF.requests.append(("GET", self.path, None))
        if self.path == "/api/characters":
            return self._send(200, LIST)
        if self.path == "/api/link-target":
            return self._send(200, {"roles": ROLES, "categories": [{"id": r} for r in ROLES]})
        parts = self.path.split("/")
        if len(parts) == 4 and parts[2] == "characters" and parts[3] in DETAIL:
            return self._send(200, DETAIL[parts[3]])
        if len(parts) == 5 and parts[4] == "images":
            return self._send(200, {"images": FakeCF.images.get(parts[3], [])})
        self._send(404, {"error": "no"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        FakeCF.requests.append(("POST", self.path, (self.headers.get("Content-Type"), body)))
        if FakeCF.post_error:
            return self._send(FakeCF.post_status, {"error": FakeCF.post_error})
        self._send(201, {"id": "rec1", "file": "pose-03.png"})


def parse_multipart(ctype, body):
    msg = email.message_from_bytes(("Content-Type: %s\r\n\r\n" % ctype).encode() + body)
    out = {}
    for part in msg.get_payload():
        out[part.get_param("name", header="content-disposition")] = part.get_payload(decode=True)
    return out


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), FakeCF)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_port
        cls.banks = banks_mod.load("characters")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        FakeCF.requests, FakeCF.post_error, FakeCF.post_status = [], None, 201
        self.a = CharacterForgeAdapter(self.url, self.banks, dict(NEUTRAL))

    def spec(self, slug, role, rnd=1):
        return self.a.build_spec(DETAIL[slug], role, rnd)[1]

    def text(self, slug, role, rnd=1):
        return self.spec(slug, role, rnd)["characters"][0]["text"]


class EntriesAndCounts(Base):
    def test_entries_are_filtered_and_ordered_priority_first_then_alphabetical(self):
        self.assertEqual([e["slug"] for e in self.a.entries()], ["Hooble", "Alpha", "Zap", "Zed"])

    def test_archetypes_and_squads_are_never_fetched_in_detail(self):
        self.a.entries()
        fetched = [p for m, p, _ in FakeCF.requests if p.startswith("/api/characters/")]
        self.assertNotIn("/api/characters/Arch", fetched)
        self.assertNotIn("/api/characters/Sq", fetched)
        self.assertIn("/api/characters/Hooble", fetched)

    def test_an_override_can_skip_an_entry(self):
        b = banks_mod.Banks(self.banks.roles, self.banks.defaults, {"entries": {"Zed": {"skip": True}}})
        a = CharacterForgeAdapter(self.url, b, dict(NEUTRAL))
        self.assertNotIn("Zed", [e["slug"] for e in a.entries()])

    def test_roles_come_from_link_target(self):
        self.assertEqual(self.a.roles(), ROLES)

    def test_counts_include_rejected_pictures(self):
        c = self.a.counts("Hooble")
        self.assertEqual((c["pose"], c["outfit"], c["action"]), (2, 1, 0))
        self.assertEqual(set(c), set(ROLES))

    def test_target_comes_from_the_banks(self):
        self.assertEqual(self.a.target({"slug": "Hooble"}, "pose"), 10)

    def test_unreachable_forge_is_a_slotfill_error(self):
        with self.assertRaisesRegex(SlotFillError, "not reachable"):
            CharacterForgeAdapter("http://127.0.0.1:1", self.banks, dict(NEUTRAL)).roles()


class BuildSpec(Base):
    def test_outfit_role_has_no_canon_clothing_and_no_outfit_selection(self):
        s = self.spec("Hooble", "outfit")
        ch = s["characters"][0]
        for word in ("hat", "boots", "thoughtful expression"):
            self.assertNotIn(word, ch["text"].split("wearing")[0], word)
        self.assertIn("gnome", ch["text"])
        self.assertIn("round glasses", ch["text"])
        self.assertEqual(ch["outfit"], {})
        self.assertEqual(ch["source"], "custom")

    def test_expression_role_has_no_face_tags_and_expression_none(self):
        d = dict(DETAIL["Hooble"], keywords="gnome, calm face, big smile, red hat, open mouth")
        ch = self.a.build_spec(d, "expression", 1)[1]["characters"][0]
        self.assertEqual(ch["expression"], "none")
        for word in ("calm face", "smile", "open mouth"):
            self.assertNotIn(word, ch["text"])
        self.assertIn("red hat", ch["text"])

    def test_pose_action_expression_keywords_are_emphasised(self):
        for role in ("pose", "action", "expression"):
            kw = self.spec("Hooble", role)["pose_keywords"]
            self.assertTrue(kw.startswith("(") and kw.endswith(":1.3)"), role)
        self.assertFalse(self.spec("Hooble", "outfit")["pose_keywords"].startswith("("))

    def test_neutral_and_cameo_roles_use_the_preset_source_with_the_neutral_text(self):
        for role in ("neutral-reference", "cameo-variant"):
            ch = self.spec("Hooble", role)["characters"][0]
            self.assertEqual((ch["source"], ch["expression"]), ("preset", "flat"))
            self.assertIn("a small gnome", ch["text"])

    def test_a_five_item_default_outfit_is_accepted_and_the_first_four_select_it(self):
        self.assertEqual(outfit_of(DETAIL["Hooble"]), {"gender": "m", "genre": "fantasy", "subgenre": "village", "key": "tunic"})
        self.assertEqual(outfit_of({"default_outfit": ["a", "b", "c"]}), {})
        self.assertEqual(self.spec("Hooble", "pose")["characters"][0]["outfit"]["key"], "tunic")

    def test_negative_joins_neutral_entry_and_the_banks_extra(self):
        neg = self.spec("Hooble", "pose")["negative"]
        self.assertTrue(neg.startswith("bad hands, tall, blurry, "))
        self.assertTrue(neg.endswith(self.banks.negative_extra))

    def test_size_style_setting_and_model(self):
        wide = self.spec("Hooble", "storyboard-still")
        self.assertEqual((wide["width"], wide["height"]), (1152, 896))
        pose = self.spec("Hooble", "pose")
        self.assertEqual((pose["width"], pose["height"]), (896, 1152))
        self.assertEqual(pose["model"], "m.safetensors")
        self.assertEqual(pose["setting"], "plain pale parchment background, simple background")       # a null bank setting means the parchment default
        no_model = CharacterForgeAdapter(self.url, self.banks, {k: v for k, v in NEUTRAL.items() if k != "model"})
        self.assertNotIn("model", no_model.build_spec(DETAIL["Hooble"], "pose", 1)[1])

    def test_the_seed_is_stable_and_differs_by_round_and_role(self):
        self.assertEqual(self.spec("Zed", "pose", 2)["seed"], self.spec("Zed", "pose", 2)["seed"])
        self.assertNotEqual(self.spec("Zed", "pose", 2)["seed"], self.spec("Zed", "pose", 3)["seed"])
        self.assertNotEqual(self.spec("Zed", "pose", 2)["seed"], self.spec("Zed", "action", 2)["seed"])

    def test_a_human_with_no_clothing_and_no_outfit_gets_the_modest_backstop(self):
        t = self.text("Alpha", "pose")
        self.assertIn("wearing a simple modest tunic, trousers and shoes", t)
        self.assertTrue(t.endswith(", fully clothed"))

    def test_a_human_whose_text_names_clothing_gets_only_the_suffix(self):
        d = dict(DETAIL["Alpha"], keywords="1girl, red dress")
        t = self.a.build_spec(d, "pose", 1)[1]["characters"][0]["text"]
        self.assertNotIn("modest tunic", t)
        self.assertTrue(t.endswith(", fully clothed"))

    def test_a_human_with_an_outfit_selection_gets_no_backstop(self):
        d = dict(DETAIL["Alpha"], default_outfit=["f", "fantasy", "village", "dress"])
        t = self.a.build_spec(d, "pose", 1)[1]["characters"][0]["text"]
        self.assertNotIn("modest tunic", t)                                       # the selected outfit dresses her
        self.assertTrue(t.endswith(", fully clothed"))
        o = self.a.build_spec(d, "outfit", 1)[1]["characters"][0]["text"]            # the outfit role drops the selection, so the role's own text dresses her
        self.assertIn("wearing", o)

    def test_an_always_clothed_override_dresses_a_non_human(self):
        t = self.text("Zap", "pose")
        self.assertIn("modest tunic", t)
        self.assertTrue(t.endswith("fully clothed"))
        self.assertNotIn("fully clothed", self.text("Zed", "pose"))              # a plain dwarf is left alone

    def test_strip_tokens(self):
        self.assertEqual(strip_tokens("a, Red Hat, b, tall boots", ("hat", "boot")), "a, b")
        self.assertEqual(strip_tokens("", ("x",)), "")


class Link(Base):
    def test_link_posts_every_field_and_the_picture_as_needs_work(self):
        spec = self.spec("Hooble", "pose", 3)
        rec = self.a.link("Hooble", "pose", "waving", 3, spec, {"seed": 9, "prompt_used": {"positive": "the prompt"}}, "sub/img.png", b"\x89PNGdata")
        self.assertEqual(rec["file"], "pose-03.png")
        method, path, (ctype, body) = FakeCF.requests[-1]
        self.assertEqual((method, path), ("POST", "/api/characters/Hooble/images"))
        parts = parse_multipart(ctype, body)
        self.assertEqual(parts["role"], b"pose")
        self.assertEqual(parts["tag"], b"waving")
        self.assertEqual(parts["verdict"], b"needs-work")
        self.assertEqual(parts["note"], b"auto batch, round 3")
        self.assertEqual(parts["image"], b"\x89PNGdata")
        prov = json.loads(parts["provenance"])
        self.assertEqual((prov["model"], prov["seed"], prov["prompt"], prov["batch"], prov["round"], prov["licence_note"]), ("m.safetensors", 9, "the prompt", "slot-fill", 3, "check"))
        self.assertEqual(json.loads(parts["source"]), {"forge": "promptforge", "file": "sub/img.png"})

    def test_a_refusal_is_a_slotfill_error_with_the_message(self):
        FakeCF.post_error, FakeCF.post_status = "pose is full", 400
        with self.assertRaisesRegex(SlotFillError, "pose is full"):
            self.a.link("Hooble", "pose", "t", 1, self.spec("Hooble", "pose"), {}, "x.png", b"d")

    def test_nothing_is_ever_deleted_or_patched(self):
        self.a.entries()
        self.a.roles()
        self.a.counts("Hooble")
        self.a.link("Hooble", "pose", "t", 1, self.spec("Hooble", "pose"), {}, "x.png", b"d")
        self.assertTrue({m for m, _p, _b in FakeCF.requests} <= {"GET", "POST"})
        self.assertEqual([p for m, p, _ in FakeCF.requests if m == "POST"], ["/api/characters/Hooble/images"])


if __name__ == "__main__":
    unittest.main()
