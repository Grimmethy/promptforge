"""The Library (server/core/library.py and its /api/library routes): copy in, list, edit, remove to a one-day trash.

Run: .venv/bin/python -B -m unittest test.test_library   (also as a file path or with `discover -s test -p 'test_*.py'`).
Everything runs on temporary gallery and library folders: the real ones are never touched (a guard test proves it).
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from core import library  # noqa: E402

try:
    import app as pf  # noqa: E402
except ImportError:
    pf = None

REAL_GALLERY = library.GALLERY_DIR
REAL_LIBRARY = library.LIBRARY_DIR


def tree_snapshot(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = fh.read()
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gal = os.path.join(self.tmp.name, "gal")
        self.lib = os.path.join(self.tmp.name, "lib")
        os.makedirs(os.path.join(self.gal, "2026-11"))
        self.pic = os.path.join(self.gal, "2026-11", "abc12345_0.png")
        with open(self.pic, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\nPICTURE-BYTES")
        with open(os.path.join(self.gal, "2026-11", "abc12345.json"), "w") as fh:
            json.dump({"seed": 7, "prompt_used": {"positive": "a goat"}}, fh)
        self.patches = [mock.patch.object(library, "LIBRARY_DIR", self.lib), mock.patch.object(library, "GALLERY_DIR", self.gal)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def trash(self):
        t = os.path.join(self.lib, ".trash")
        return sorted(os.listdir(t)) if os.path.isdir(t) else []


class StoreTests(Base):
    def test_save_copies_picture_and_sidecar_and_leaves_the_original_untouched(self):
        before = tree_snapshot(self.gal)
        rec, created = library.save("2026-11/abc12345_0.png", "keeper", "Goat, Hero", "manual")
        self.assertTrue(created)
        self.assertEqual((rec["file"], rec["note"], rec["tags"], rec["reason"]), ("2026-11/abc12345_0.png", "keeper", ["goat", "hero"], "manual"))
        with open(library.picture_path(rec["id"]), "rb") as fh:
            self.assertEqual(fh.read(), before["2026-11/abc12345_0.png"])
        with open(os.path.join(self.lib, "pictures", rec["id"] + ".sidecar.json"), "rb") as fh:
            self.assertEqual(fh.read(), before["2026-11/abc12345.json"])
        self.assertEqual(tree_snapshot(self.gal), before)                       # copied, never moved or edited

    def test_saving_twice_is_one_item_and_updates_note_and_tags(self):
        a, c1 = library.save("2026-11/abc12345_0.png", "one", ["a"])
        b, c2 = library.save("2026-11/abc12345_0.png", "two", ["b", "c"])
        self.assertEqual((c1, c2, a["id"], b["note"], b["tags"]), (True, False, b["id"], "two", ["b", "c"]))
        self.assertEqual(len(library.list_items()), 1)

    def test_paths_outside_the_gallery_and_non_pictures_are_refused(self):
        outside = os.path.join(self.tmp.name, "secret.png")
        with open(outside, "wb") as fh:
            fh.write(b"x")
        for bad in ("../secret.png", "../../etc/passwd", os.path.abspath(outside), "2026-11/../../secret.png", "", None, "2026-11\\abc.png", "2026-11/abc12345.json"):
            with self.assertRaises(library.LibraryError, msg=str(bad)):
                library.save(bad)
        with self.assertRaises(library.NotFound):
            library.save("2026-11/missing_0.png")
        self.assertEqual(library.list_items(), [])

    def test_list_filters_by_text_and_tag_and_sorts_newest_first(self):
        for i, (note, tags) in enumerate((("red hat", ["hat"]), ("blue boots", ["boots", "blue"]), ("green hat", ["hat", "green"]))):
            p = os.path.join(self.gal, "2026-11", "p%d_0.png" % i)
            with open(p, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n%d" % i)
            with mock.patch.object(library.time, "time", return_value=1000.0 + i):
                library.save("2026-11/p%d_0.png" % i, note, tags)
        self.assertEqual([r["note"] for r in library.list_items()], ["green hat", "blue boots", "red hat"])
        self.assertEqual([r["note"] for r in library.list_items(query="HAT")], ["green hat", "red hat"])
        self.assertEqual([r["note"] for r in library.list_items(tag="blue")], ["blue boots"])
        self.assertEqual(library.list_items(query="hat", tag="boots"), [])

    def test_update_edits_note_and_tags_only(self):
        rec, _ = library.save("2026-11/abc12345_0.png", "n", ["a"])
        out = library.update(rec["id"], note="changed", tags="x, y ,,X")
        self.assertEqual((out["note"], out["tags"], out["file"], out["id"]), ("changed", ["x", "y"], rec["file"], rec["id"]))
        with self.assertRaises(library.NotFound):
            library.update("0" * 12, note="x")
        with self.assertRaises(library.LibraryError):
            library.update("not-an-id", note="x")
        self.assertEqual(len(library.clean_tags([str(i) for i in range(30)])), library.MAX_TAGS)

    def test_remove_moves_to_trash_and_purge_deletes_only_stamped_files_older_than_a_day(self):
        rec, _ = library.save("2026-11/abc12345_0.png", "n")
        T0 = 1794000000.0
        library.remove(rec["id"], now=T0)
        self.assertEqual(library.list_items(), [])
        names = self.trash()
        self.assertEqual(sorted(n.split("-", 1)[1] for n in names), sorted([rec["id"] + ".png", rec["id"] + ".sidecar.json", rec["id"] + ".json"]))
        self.assertTrue(all(n.startswith("1794000000000-") for n in names))
        with open(os.path.join(self.lib, ".trash", "notes.txt"), "w") as fh:         # an unstamped file is never ours to delete
            fh.write("n")
        self.assertEqual(library.purge_trash(now=T0 + 86399), [])
        gone = library.purge_trash(now=T0 + 86401)
        self.assertEqual(len(gone), 3)
        self.assertEqual(self.trash(), ["notes.txt"])
        self.assertGreater(sum(g["bytes"] for g in gone), 0)
        with self.assertRaises(library.NotFound):
            library.get(rec["id"])
        self.assertTrue(os.path.isfile(self.pic))                                   # the gallery original is still there

    def test_purge_never_touches_live_items(self):
        rec, _ = library.save("2026-11/abc12345_0.png", "n")
        library.purge_trash(now=10 ** 11)
        self.assertEqual([r["id"] for r in library.list_items()], [rec["id"]])
        self.assertTrue(os.path.isfile(library.picture_path(rec["id"])))

    def test_the_real_gallery_and_library_are_never_touched_by_these_tests(self):
        self.assertNotEqual(os.path.abspath(self.gal), REAL_GALLERY)
        self.assertNotEqual(os.path.abspath(self.lib), REAL_LIBRARY)
        self.assertEqual(library.LIBRARY_DIR, self.lib)


@unittest.skipIf(pf is None, "Flask not installed")
class RouteTests(Base):
    def setUp(self):
        super().setUp()
        self.p2 = mock.patch.object(pf, "GALLERY_DIR", self.gal)
        self.p2.start()
        self.c = pf.app.test_client()

    def tearDown(self):
        self.p2.stop()
        super().tearDown()

    def test_post_get_patch_image_delete_round_trip(self):
        r = self.c.post("/api/library", json={"file": "2026-11/abc12345_0.png", "note": "first", "tags": "a, b"})
        self.assertEqual(r.status_code, 201)
        rec = r.get_json()
        self.assertEqual(self.c.post("/api/library", json={"file": "2026-11/abc12345_0.png", "note": "second"}).status_code, 200)
        items = self.c.get("/api/library").get_json()["items"]
        self.assertEqual([(i["id"], i["note"]) for i in items], [(rec["id"], "second")])
        self.assertEqual(self.c.get("/api/library?q=SECOND").get_json()["items"][0]["id"], rec["id"])
        self.assertEqual(self.c.get("/api/library?tag=zzz").get_json()["items"], [])
        img = self.c.get("/api/library/%s/image" % rec["id"])
        self.assertEqual((img.status_code, img.data), (200, b"\x89PNG\r\n\x1a\nPICTURE-BYTES"))
        p = self.c.patch("/api/library/%s" % rec["id"], json={"note": "edited", "tags": ["x"]})
        self.assertEqual((p.status_code, p.get_json()["note"], p.get_json()["tags"]), (200, "edited", ["x"]))
        self.assertEqual(self.c.get("/api/library/%s" % rec["id"]).get_json()["note"], "edited")
        d = self.c.delete("/api/library/%s" % rec["id"])
        self.assertEqual((d.status_code, d.get_json()["removed"]), (200, rec["id"]))
        self.assertEqual(self.c.get("/api/library/%s/image" % rec["id"]).status_code, 404)
        self.assertEqual(self.c.get("/api/library").get_json()["items"], [])
        self.assertTrue(os.path.isfile(self.pic))

    def test_errors_are_400_and_404(self):
        self.assertEqual(self.c.post("/api/library", json={"file": "../x.png"}).status_code, 400)
        self.assertEqual(self.c.post("/api/library", json={"file": "2026-11/abc12345.json"}).status_code, 400)
        self.assertEqual(self.c.post("/api/library", json={"file": "2026-11/none_0.png"}).status_code, 404)
        self.assertEqual(self.c.post("/api/library", json={}).status_code, 400)
        self.assertEqual(self.c.get("/api/library/" + "0" * 12).status_code, 404)
        self.assertEqual(self.c.get("/api/library/not-an-id").status_code, 400)
        self.assertEqual(self.c.delete("/api/library/" + "0" * 12).status_code, 404)


if __name__ == "__main__":
    unittest.main()
