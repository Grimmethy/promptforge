"""Assign a gallery image to entries on a link target (2026-11-10): the descriptor and entries proxy, the assign route against a STUB target server
(success, role full 409, unreachable, partial failure), the sidecar's links appended once, the tile badge data, pre-selection and path safety.
A temp gallery and a local stub HTTP server: no CharacterForge, no ComfyUI, no GPU (python3 -m unittest discover -s test -p 'test_*.py')."""
import email.parser
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import app as pf  # noqa: E402
from core import gallery_chars as G, link_targets as L  # noqa: E402

DESC = {"forge": "characterforge", "label": "CharacterForge", "entries_path": "/api/characters", "images_path": "/api/characters/{slug}/images",
        "roles": ["neutral-reference", "pose", "action"], "verdicts": ["approved", "rejected", "needs-work"], "max_per_role": 10,
        "categories": [{"id": "neutral-reference", "label": "Neutral reference", "order": 1}, {"id": "pose", "label": "Pose", "order": 2}]}
ROWS = [{"slug": "Gobs", "name": "Gobs", "kind": "character", "group": "Goblin", "faction": "goblins", "image": 5, "primaryImage": "/api/characters/Gobs/image"},
        {"slug": "Goblin", "name": "Goblin", "kind": "group", "image": 0, "primaryImage": None},
        {"slug": "Hooble", "name": "Hooble", "kind": "character", "image": 0, "primaryImage": None},
        {"slug": "full", "name": "Full One", "kind": "character", "image": 0, "primaryImage": None},
        {"slug": "boom", "name": "Boom", "kind": "character", "image": 0, "primaryImage": None}]


class Stub(BaseHTTPRequestHandler):
    posts = []

    def log_message(self, *a):
        pass

    def send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/api/link-target":
            return self.send(200, DESC)
        if self.path == "/api/characters":
            return self.send(200, ROWS)
        if self.path == "/api/characters/Gobs/images":
            return self.send(200, {"slug": "Gobs", "images": [{"role": "pose"}, {"role": "pose"}, {"role": "action"}]})
        self.send(404, {"error": "no"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        msg = email.parser.BytesParser().parsebytes(b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + raw)
        form = {p.get_param("name", header="content-disposition"): p.get_payload(decode=True) for p in msg.get_payload()}
        slug = self.path.split("/")[3]
        Stub.posts.append({"slug": slug, "form": {k: v.decode() for k, v in form.items() if k != "image"}, "image": form.get("image")})
        if slug == "full":
            return self.send(409, {"error": "role full", "role": form["role"].decode(), "count": 10, "entries": [{"id": "e1", "file": "pose-01.png"}]})
        if slug == "boom":
            return self.send(500, {"error": "disk on fire"})
        self.send(201, {"id": "%012x" % (len(Stub.posts)), "file": "%s-01.png" % form["role"].decode()})


class AssignBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        Stub.posts = []
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "2026-11")
        os.makedirs(self.dir)
        G.clear()
        self.env = mock.patch.dict(os.environ, {"PROMPTFORGE_LINK_TARGETS": "characterforge=" + self.url})
        self.patches = [self.env, mock.patch.object(pf, "GALLERY_DIR", self.tmp.name), mock.patch.object(pf.data, "nsfw_enabled", lambda: True),
                        mock.patch.object(pf, "_gallery_names", lambda: {G.norm("Gobs"): "Gobs", G.norm("Hooble"): "Hooble"})]
        for p in self.patches:
            p.start()
        self.c = pf.app.test_client()
        self.rel = "2026-11/bbbbbbb1_0.png"
        with open(os.path.join(self.tmp.name, self.rel), "wb") as fh:
            fh.write(b"\x89PNGgallery-bytes")
        self.side = os.path.join(self.dir, "bbbbbbb1.json")
        with open(self.side, "w") as fh:
            json.dump({"id": "bbbbbbb1", "seed": 7, "model": "m.safetensors", "spec": {"characters": [{"name": "Gobs"}]}}, fh)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        G.clear()
        self.tmp.cleanup()

    def assign(self, body, rel=None):
        return self.c.post("/api/gallery/%s/assign" % (rel or self.rel), json=body)

    def links(self):
        with open(self.side) as fh:
            return json.load(fh).get("links", [])

    def snapshot(self):
        out = {}
        for root, _, files in os.walk(self.tmp.name):
            for f in files:
                fp = os.path.join(root, f)
                with open(fp, "rb") as fh:
                    out[os.path.relpath(fp, self.tmp.name)] = hashlib.sha256(fh.read()).hexdigest()
        return out


class AssignRouteTest(AssignBase):
    def test_copies_the_image_with_the_contract_fields_and_records_the_link(self):
        r = self.assign({"targets": [{"slug": "Gobs", "role": "pose"}], "tag": "t", "note": "n", "names": {"Gobs": "Gobs"}})
        self.assertEqual(r.status_code, 200)
        res = r.get_json()["results"]
        self.assertEqual([(x["slug"], x["role"], x["ok"], x["file"]) for x in res], [("Gobs", "pose", True, "pose-01.png")])
        post = Stub.posts[0]
        self.assertEqual((post["form"]["role"], post["form"]["tag"], post["form"]["note"], post["form"]["verdict"]), ("pose", "t", "n", "needs-work"))
        self.assertEqual(json.loads(post["form"]["source"]), {"forge": "promptforge", "file": self.rel})
        prov = json.loads(post["form"]["provenance"])
        self.assertEqual((prov["seed"], prov["model"]), (7, "m.safetensors"))
        self.assertNotIn("links", prov)
        self.assertEqual(post["image"], b"\x89PNGgallery-bytes")
        (link,) = self.links()
        self.assertEqual((link["forge"], link["slug"], link["name"], link["role"], link["tag"], link["verdict"]), ("characterforge", "Gobs", "Gobs", "pose", "t", "needs-work"))
        self.assertEqual(link["imageId"], res[0]["imageId"])
        self.assertIsInstance(link["ts"], float)
        with open(os.path.join(self.tmp.name, self.rel), "rb") as fh:  # the gallery keeps its file untouched
            self.assertEqual(fh.read(), b"\x89PNGgallery-bytes")

    def test_provenance_never_carries_the_gallery_links_back_to_the_target(self):
        self.assign({"targets": [{"slug": "Gobs", "role": "pose"}]})
        self.assertEqual(len(self.links()), 1)  # the sidecar now has links; the next copy must not send them as provenance
        self.assign({"targets": [{"slug": "Hooble", "role": "action"}]})
        self.assertNotIn("links", json.loads(Stub.posts[-1]["form"]["provenance"]))
        self.assertEqual(json.loads(Stub.posts[-1]["form"]["provenance"])["seed"], 7)

    def test_one_image_to_several_entries_and_roles(self):
        r = self.assign({"targets": [{"slug": "Gobs", "role": "pose"}, {"slug": "Hooble", "role": "action"}, {"slug": "Gobs", "role": "action"}]})
        self.assertEqual([x["ok"] for x in r.get_json()["results"]], [True, True, True])
        self.assertEqual(sorted((l["slug"], l["role"]) for l in self.links()), [("Gobs", "action"), ("Gobs", "pose"), ("Hooble", "action")])

    def test_a_retry_of_the_same_target_and_role_is_not_sent_or_recorded_twice(self):
        body = {"targets": [{"slug": "Gobs", "role": "pose"}]}
        first = self.assign(body).get_json()["results"][0]
        again = self.assign(body).get_json()["results"][0]
        self.assertEqual((again["ok"], again.get("already"), again["imageId"]), (True, True, first["imageId"]))
        self.assertEqual((len(Stub.posts), len(self.links())), (1, 1))

    def test_role_full_is_passed_through_with_entries_and_replace_goes_through(self):
        r = self.assign({"targets": [{"slug": "full", "role": "pose"}]}).get_json()["results"][0]
        self.assertEqual((r["ok"], r["status"], r["error"], r["count"], r["entries"]), (False, 409, "role full", 10, [{"id": "e1", "file": "pose-01.png"}]))
        self.assertEqual(self.links(), [])
        r2 = self.assign({"targets": [{"slug": "full", "role": "pose"}], "replace": {"full": "e1"}}).get_json()["results"][0]
        self.assertEqual(Stub.posts[-1]["form"]["replace"], "e1")
        self.assertEqual(r2["status"], 409)  # the stub always answers 409 for this slug; the point is that replace was sent

    def test_partial_failure_reports_each_target_and_records_only_the_successes(self):
        r = self.assign({"targets": [{"slug": "Gobs", "role": "pose"}, {"slug": "boom", "role": "pose"}, {"slug": "full", "role": "pose"}]})
        self.assertEqual(r.status_code, 200)
        res = r.get_json()["results"]
        self.assertEqual([(x["slug"], x["ok"]) for x in res], [("Gobs", True), ("boom", False), ("full", False)])
        self.assertEqual(res[1]["error"], "disk on fire")
        self.assertEqual([l["slug"] for l in self.links()], ["Gobs"])

    def test_an_unreachable_target_is_a_clear_message_and_the_image_is_untouched(self):
        before = self.snapshot()  # image and sidecar, byte for byte
        with mock.patch.dict(os.environ, {"PROMPTFORGE_LINK_TARGETS": "characterforge=http://127.0.0.1:9"}):
            r = self.assign({"targets": [{"slug": "Gobs", "role": "pose"}]})
        self.assertEqual(r.status_code, 502)
        self.assertIn("not reachable", r.get_json()["error"])
        self.assertIn("not touched", r.get_json()["error"])
        self.assertEqual(self.links(), [])
        self.assertEqual(self.snapshot(), before)
        with open(os.path.join(self.tmp.name, self.rel), "rb") as fh:
            self.assertEqual(fh.read(), b"\x89PNGgallery-bytes")

    def test_a_target_that_dies_mid_batch_marks_that_target_unreachable_and_keeps_the_rest(self):
        real = L.post_image
        calls = []

        def flaky(*a, **kw):
            calls.append(1)
            if len(calls) == 2:
                raise L.TargetUnreachable("connection reset")
            return real(*a, **kw)
        with mock.patch.object(L, "post_image", flaky):
            res = self.assign({"targets": [{"slug": "Gobs", "role": "pose"}, {"slug": "Hooble", "role": "pose"}, {"slug": "Gobs", "role": "action"}]}).get_json()["results"]
        self.assertEqual([(x["slug"], x["ok"], x.get("unreachable", False)) for x in res], [("Gobs", True, False), ("Hooble", False, True), ("Gobs", True, False)])
        self.assertIn("not reachable", res[1]["error"])

    def test_bad_requests(self):
        self.assertEqual(self.assign({"targets": []}).status_code, 400)
        self.assertEqual(self.assign({}).status_code, 400)
        self.assertEqual(self.assign({"targets": [{"slug": "Gobs", "role": "pose"}], "target": "nope"}).status_code, 404)
        r = self.assign({"targets": [{"slug": "", "role": "pose"}, {"slug": "Gobs"}]}).get_json()["results"]
        self.assertEqual([x["ok"] for x in r], [False, False])
        self.assertEqual(Stub.posts, [])

    def test_path_safety_reuses_the_gallery_path_guard(self):
        before = self.snapshot()
        for rel in ("../../etc/passwd", "2026-11/../../outside.png", "2026-11/missing_0.png", "2026-11/bbbbbbb1.json"):
            r = self.assign({"targets": [{"slug": "Gobs", "role": "pose"}]}, rel=rel)
            self.assertTrue(400 <= r.status_code < 500, rel)
        self.assertEqual(self.snapshot(), before)  # no file in the gallery was created, changed or removed
        self.assertEqual(Stub.posts, [])
        self.assertIsNone(pf._safe_gallery_path("/etc/passwd"))  # an absolute relpath is refused by the guard itself


class TargetsAndBadgeTest(AssignBase):
    def test_link_targets_lists_descriptor_entries_and_preselects_only_certain_names(self):
        t = self.c.get("/api/link-targets?file=" + self.rel).get_json()["targets"][0]
        self.assertEqual((t["reachable"], t["label"], t["max_per_role"]), (True, "CharacterForge", 10))
        by = {e["slug"]: e for e in t["entries"]}
        self.assertEqual((by["Gobs"]["group"], by["Gobs"]["hasImage"], by["Goblin"]["hasImage"], by["Goblin"]["kind"]), ("Goblin", True, False, "group"))
        self.assertEqual(by["Gobs"]["thumb"], self.url + "/api/characters/Gobs/image")
        self.assertEqual(t["preselect"], ["Gobs"])
        with open(self.side, "w") as fh:  # a regenerate run only guessed from a file name: nothing is pre-selected
            json.dump({"id": "bbbbbbb1", "spec": {"regenerate": True, "source": "gobs-a-1.png"}}, fh)
        G.clear()
        self.assertEqual(self.c.get("/api/link-targets?file=" + self.rel).get_json()["targets"][0]["preselect"], [])
        self.assertEqual(self.c.get("/api/link-targets").get_json()["targets"][0]["preselect"], [])

    def test_preselect_is_all_named_entries_by_name_or_slug_and_never_a_guess(self):
        rows = [{"slug": "Gobs", "name": "Gobs"}, {"slug": "Lucky_the_Pocket_Gnome", "name": "Lucky the Pocket Gnome"}, {"slug": "x", "name": "X"}]
        chars = [{"name": "Lucky the Pocket Gnome", "guessed": False}, {"name": "gobs", "guessed": False}, {"name": "X", "guessed": True}]
        self.assertEqual(L.preselect(chars, rows), ["Gobs", "Lucky_the_Pocket_Gnome"])
        self.assertEqual(L.preselect([], rows), [])
        self.assertEqual(L.preselect(None, rows), [])

    def test_an_unreachable_target_is_listed_not_fatal(self):
        with mock.patch.dict(os.environ, {"PROMPTFORGE_LINK_TARGETS": "characterforge=http://127.0.0.1:9,other=" + self.url}):
            ts = self.c.get("/api/link-targets").get_json()["targets"]
        self.assertEqual([(t["name"], t["reachable"]) for t in ts], [("characterforge", False), ("other", True)])
        self.assertIn("not reachable", ts[0]["error"])

    def test_counts_per_role_and_target_names(self):
        self.assertEqual(self.c.get("/api/link-targets/characterforge/counts/Gobs").get_json()["counts"], {"pose": 2, "action": 1})
        self.assertEqual(self.c.get("/api/link-targets/nope/counts/Gobs").status_code, 404)
        self.assertEqual(L.targets(), {"characterforge": self.url})
        with mock.patch.dict(os.environ, {"PROMPTFORGE_LINK_TARGETS": "a=http://x:1/, bad, b=ftp://no, c=http://y:2"}):
            self.assertEqual(L.targets(), {"a": "http://x:1", "c": "http://y:2"})
        with mock.patch.dict(os.environ, {"PROMPTFORGE_LINK_TARGETS": ""}):
            self.assertEqual(L.targets(), {"characterforge": "http://localhost:7433"})

    def test_gallery_items_carry_the_tile_badge_data(self):
        self.assertIsNone(self.c.get("/api/gallery").get_json()["items"][0]["link"])
        self.assign({"targets": [{"slug": "Gobs", "role": "pose"}, {"slug": "Hooble", "role": "action"}], "names": {"Gobs": "Gobs", "Hooble": "Hooble"}})
        it = self.c.get("/api/gallery").get_json()["items"][0]
        self.assertEqual(it["link"], {"name": "Gobs", "role": "pose", "more": 1})
        self.assertEqual(self.c.get("/api/gallery?linked=any").get_json()["total"], 1)
        meta = self.c.get("/api/gallery-meta/" + self.rel).get_json()
        self.assertEqual(len(meta["links"]), 2)


if __name__ == "__main__":
    unittest.main()
