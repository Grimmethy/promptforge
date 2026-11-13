"""Delivering finished pictures to a job's destination (scripts/jobs/destinations.py): copy-dir files, CharacterForge posts and their errors. Temp folders and a
fake CharacterForge post; no network, no GPU (python3 -m unittest test.test_job_destinations)."""
import json
import os
import sys
import tempfile
import unittest
from collections import namedtuple

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "server"))
from scripts.jobs import destinations  # noqa: E402

Generated = namedtuple("Generated", "job name data")
JOB_ID = "job-20261113120000-abc123"


def job(dest, **kw):
    base = {"id": JOB_ID, "requester": "mapforge", "model": "NovaFlatILV9.safetensors", "positive": "a tiny cottage", "steps": 28, "cfg": 5.0, "destination": dest}
    base.update(kw)
    return base


def gen(name="2026-11/abc_0.png", data=b"PICTURE-BYTES"):
    return Generated({"status": "done"}, name, data)


class CopyNameTest(unittest.TestCase):
    def test_extensions(self):
        c = destinations.copy_name
        self.assertEqual(c(JOB_ID, 7, "2026-11/x_0.png"), JOB_ID + "_7.png")
        self.assertEqual(c(JOB_ID, 7, "x.jpg"), JOB_ID + "_7.jpg")
        self.assertEqual(c(JOB_ID, 7, "x.JPEG"), JOB_ID + "_7.jpeg")
        self.assertEqual(c(JOB_ID, 7, "x.webp"), JOB_ID + "_7.webp")
        self.assertEqual(c(JOB_ID, 7, "x.tiff"), JOB_ID + "_7.png")
        self.assertEqual(c(JOB_ID, 7, "noext"), JOB_ID + "_7.png")


class CopyDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = os.path.join(self.tmp.name, "art", "main-street")

    def test_the_picture_is_written_with_its_exact_bytes_and_the_path_is_returned(self):
        deliver = destinations.make_deliver()
        out = deliver(job({"kind": "copy-dir", "path": self.dest}), 12, gen(data=b"\x89PNG\x00\xff-exact"))
        self.assertEqual(out, os.path.join(self.dest, JOB_ID + "_12.png"))
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(), b"\x89PNG\x00\xff-exact")

    def test_no_temp_file_is_left_and_a_second_delivery_replaces_the_file(self):
        deliver = destinations.make_deliver()
        j = job({"kind": "copy-dir", "path": self.dest})
        deliver(j, 1, gen(data=b"first"))
        out = deliver(j, 1, gen(data=b"second"))
        self.assertEqual(os.listdir(self.dest), [JOB_ID + "_1.png"])
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(), b"second")

    def test_each_seed_gets_its_own_file_and_the_extension_follows_the_gallery_name(self):
        deliver = destinations.make_deliver()
        j = job({"kind": "copy-dir", "path": self.dest})
        deliver(j, 1, gen("a.png"))
        deliver(j, 2, gen("b.webp"))
        self.assertEqual(sorted(os.listdir(self.dest)), [JOB_ID + "_1.png", JOB_ID + "_2.webp"])

    def test_none_does_nothing(self):
        calls = []
        deliver = destinations.make_deliver(cf_post=lambda *a: calls.append(a))
        self.assertIsNone(deliver(job({"kind": "none"}), 1, gen()))
        self.assertIsNone(deliver(job(None), 1, gen()))             # a record with no destination counts as none
        self.assertEqual(calls, [])
        self.assertEqual(os.listdir(self.tmp.name), [])                  # no folder was created

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown destination kind ftp"):
            destinations.make_deliver()(job({"kind": "ftp"}), 1, gen())


class CharacterForgeTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.deliver = destinations.make_deliver(cf_url="http://cf.test:7433/", cf_post=lambda url, body, ctype: self.calls.append((url, body, ctype)) or {"ok": True})

    def dest(self, slug="cindy", role="portrait"):
        return job({"kind": "characterforge", "slug": slug, "role": role})

    def test_the_picture_is_posted_to_the_entrys_images_route(self):
        out = self.deliver(self.dest(), 5, gen("2026-11/x_0.png", b"IMG-BYTES"))
        self.assertEqual(out, "characterforge:cindy:portrait")
        (url, body, ctype), = self.calls
        self.assertEqual(url, "http://cf.test:7433/api/characters/cindy/images")
        self.assertTrue(ctype.startswith("multipart/form-data; boundary="))
        text = body.decode("latin-1")
        for needle in ('name="role"\r\n\r\nportrait', 'name="tag"\r\n\r\njob-%s-5' % JOB_ID, 'name="verdict"\r\n\r\nneeds-work',
                       'name="image"; filename="x_0.png"', "Content-Type: image/png", "IMG-BYTES"):
            self.assertIn(needle, text)

    def test_the_provenance_and_source_are_json_with_the_job_facts(self):
        self.deliver(self.dest(), 5, gen())
        text = self.calls[0][1].decode("latin-1")
        prov = json.loads(text.split('name="provenance"\r\n\r\n')[1].split("\r\n")[0])
        self.assertEqual((prov["job_id"], prov["seed"], prov["prompt"], prov["batch"], prov["model"], prov["steps"], prov["cfg"], prov["requester"]),
                         (JOB_ID, 5, "a tiny cottage", "job-queue", "NovaFlatILV9.safetensors", 28, 5.0, "mapforge"))
        src = json.loads(text.split('name="source"\r\n\r\n')[1].split("\r\n")[0])
        self.assertEqual(src, {"forge": "job-queue", "job": JOB_ID})

    def test_a_slug_with_a_space_and_a_slash_is_percent_encoded(self):
        self.deliver(self.dest(slug="the pig/king"), 1, gen())
        self.assertEqual(self.calls[0][0], "http://cf.test:7433/api/characters/the%20pig%2Fking/images")

    def test_the_mime_type_follows_the_file_name(self):
        for name, mime in (("a.webp", "image/webp"), ("a.jpg", "image/jpeg"), ("a.jpeg", "image/jpeg"), ("a.png", "image/png"), ("a.bmp", "image/png")):
            self.calls.clear()
            self.deliver(self.dest(), 1, gen(name))
            self.assertIn("Content-Type: " + mime, self.calls[0][1].decode("latin-1"), name)

    def test_errors_from_the_post_propagate(self):
        def refuse(url, body, ctype):
            raise RuntimeError("CharacterForge refused the picture: no such entry")
        with self.assertRaisesRegex(RuntimeError, "refused the picture: no such entry"):
            destinations.make_deliver(cf_url="http://x", cf_post=refuse)(self.dest(), 1, gen())

    def test_the_default_post_turns_failures_into_readable_errors(self):
        # nothing listens on this port, so the real default post must raise the 'not reachable' message
        with self.assertRaisesRegex(RuntimeError, r"CharacterForge not reachable at http://127\.0\.0\.1:9 "):
            destinations.make_deliver(cf_url="http://127.0.0.1:9")(self.dest(), 1, gen())

    def test_the_base_url_comes_from_the_environment_then_the_default(self):
        old = os.environ.get("CHARACTERFORGE_URL")
        self.addCleanup(lambda: os.environ.__setitem__("CHARACTERFORGE_URL", old) if old is not None else os.environ.pop("CHARACTERFORGE_URL", None))
        os.environ["CHARACTERFORGE_URL"] = "http://env.test:1/"
        calls = []
        destinations.make_deliver(cf_post=lambda url, b, c: calls.append(url) or {})(self.dest(), 1, gen())
        os.environ.pop("CHARACTERFORGE_URL")
        destinations.make_deliver(cf_post=lambda url, b, c: calls.append(url) or {})(self.dest(), 1, gen())
        self.assertEqual(calls, ["http://env.test:1/api/characters/cindy/images", "http://localhost:7433/api/characters/cindy/images"])


if __name__ == "__main__":
    unittest.main()
