"""The 7-day gallery sweep and the reference scanner: a dry run by default, off until the owner switches it on.

Run: .venv/bin/python -B -m unittest test.test_gallery_sweep   (also as a file path or with `discover -s test -p 'test_*.py'`).
Everything runs on temporary gallery, library and forge folders; a guard proves the real gallery is only read, never changed.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from core import gallery_refs, gallery_sweep, library  # noqa: E402

try:
    import app as pf  # noqa: E402
except ImportError:
    pf = None

DAY = 86400
NOW = 1794500000.0
REAL_GALLERY = gallery_sweep.GALLERY_DIR


def snapshot(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = fh.read()
    return out


def stat_snapshot(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            st = os.stat(os.path.join(d, f))
            out[os.path.relpath(os.path.join(d, f), root)] = (st.st_size, st.st_mtime_ns)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.gal, self.lib, self.forges = os.path.join(t, "gal"), os.path.join(t, "lib"), os.path.join(t, "forges")
        self.enabled = os.path.join(t, "sweep-enabled")
        self.log = os.path.join(t, "sweep.log")
        os.makedirs(os.path.join(self.gal, "2026-10"))
        os.makedirs(os.path.join(self.gal, "2026-11"))
        # name -> age in days; each picture gets its own sidecar except the pair a1_0/a1_1, which share a1.json
        self.add("2026-10/a0000001_0.png", 30)             # candidate
        self.add("2026-10/a0000002_0.png", 20)             # candidate
        self.add("2026-10/c0000003_0.png", 25)             # already in the Library
        self.add("2026-10/d0000004_0.png", 25)             # referenced by a story
        self.add("2026-11/e0000005_0.png", 3)              # younger than 7 days
        self.add("2026-11/f0000006_0.png", 15)             # one of a pair sharing a sidecar: candidate
        self.add("2026-11/f0000006_1.png", 15, sidecar=False)  # its sibling is saved in the Library below, so it stays and the shared sidecar must stay
        os.makedirs(os.path.join(self.forges, "storyforge", "stories"))
        with open(os.path.join(self.forges, "storyforge", "stories", "tale.json"), "w") as fh:
            json.dump({"acts": [{"shots": [{"reference": {"gallery": "2026-10/d0000004_0.png"}}]}]}, fh)
        self.refs_src = [{"forge": "storyforge", "repo": "storyforge", "globs": ["stories/*.json"]}]
        self.patches = [mock.patch.object(library, "LIBRARY_DIR", self.lib), mock.patch.object(library, "GALLERY_DIR", self.gal),
                        mock.patch.object(gallery_sweep, "GALLERY_DIR", self.gal), mock.patch.object(gallery_sweep, "ENABLED_PATH", self.enabled),
                        mock.patch.object(gallery_sweep, "LOG_PATH", self.log), mock.patch.object(gallery_refs, "FORGES_ROOT", self.forges)]
        for p in self.patches:
            p.start()
        library.save("2026-10/c0000003_0.png", "mine", gallery_dir=self.gal)
        library.save("2026-11/f0000006_1.png", "pair", gallery_dir=self.gal)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def add(self, rel, age_days, sidecar=True):
        path = os.path.join(self.gal, rel)
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + rel.encode())
        side = os.path.join(os.path.dirname(path), os.path.basename(path).split("_")[0] + ".json")
        if sidecar:
            with open(side, "w") as fh:
                json.dump({"ts": NOW - age_days * DAY, "seed": 1}, fh)
        os.utime(path, (NOW, NOW))         # the file time is "now": the age must come from the sidecar ts

    def refs(self):
        return gallery_refs.referenced(self.refs_src, self.forges)


class RefsTests(Base):
    def test_referenced_finds_gallery_names_in_other_forges_data(self):
        r = self.refs()
        self.assertEqual(list(r), ["2026-10/d0000004_0.png"])
        self.assertEqual(r["2026-10/d0000004_0.png"], [{"forge": "storyforge", "record": os.path.join("stories", "tale.json")}])

    def test_the_scanner_ignores_node_modules_bad_json_names_inside_words_and_missing_repos(self):
        os.makedirs(os.path.join(self.forges, "storyforge", "stories", "sub"))
        with open(os.path.join(self.forges, "storyforge", "stories", "sub", "bad.json"), "w") as fh:
            fh.write("{ not json 2026-10/dead9999_0.png")                           # a malformed file is still scanned as text
        with open(os.path.join(self.forges, "storyforge", "stories", "word.json"), "w") as fh:
            fh.write('"x2026-10/aaaa1111_0.png" "path/2026-10/bbbb2222_0.png"')     # not at a word boundary: not a gallery name
        src = [{"forge": "storyforge", "repo": "storyforge", "globs": ["stories/**/*.json"]}, {"forge": "ghost", "repo": "nothere", "globs": ["**/*.json"]}]
        names = sorted(gallery_refs.referenced(src, self.forges))
        self.assertEqual(names, ["2026-10/d0000004_0.png", "2026-10/dead9999_0.png"])

    def test_load_sources_survives_a_missing_or_broken_file_and_the_shipped_list_is_valid(self):
        self.assertEqual(gallery_refs.load_sources(os.path.join(self.tmp.name, "none.json")), [])
        broken = os.path.join(self.tmp.name, "b.json")
        open(broken, "w").write("{")
        self.assertEqual(gallery_refs.load_sources(broken), [])
        shipped = gallery_refs.load_sources()
        self.assertTrue(shipped and all(s["globs"] for s in shipped))
        self.assertNotIn("characterforge", [s["forge"] for s in shipped])             # its linked pictures are copies

    def test_the_scanner_reads_only(self):
        before = snapshot(self.forges)
        self.refs()
        self.assertEqual(snapshot(self.forges), before)


class PlanTests(Base):
    def plan(self, now=NOW):
        return gallery_sweep.plan(now, self.gal, refs=self.refs())

    def test_plan_counts_and_protects(self):
        p = self.plan()
        self.assertEqual((p["candidates"], p["already_in_library"], p["protected_referenced"], p["younger_than_7_days"]), (3, 2, 1, 1))
        self.assertEqual(p["oldest"], "2026-10/a0000001_0.png")
        self.assertEqual(p["newest"], "2026-11/f0000006_0.png")
        self.assertEqual(sorted(p["per_month"]), ["2026-10", "2026-11"])
        self.assertEqual(p["per_month"]["2026-10"]["files"], 2)
        self.assertGreater(p["bytes"], 0)
        self.assertFalse(p["enabled"])

    def test_the_seven_day_boundary_uses_the_sidecar_time(self):
        self.add("2026-11/b0000007_0.png", 7)                                           # exactly 7 days: kept (not older than)
        before = self.plan()["candidates"]
        self.assertEqual(self.plan(NOW + 1)["candidates"], before + 1)                  # one second later it is a candidate

    def test_a_dry_run_changes_nothing(self):
        gal, lib = snapshot(self.gal), snapshot(self.lib)
        self.plan()
        self.assertEqual((snapshot(self.gal), snapshot(self.lib)), (gal, lib))

    def test_the_real_gallery_is_only_read(self):
        self.assertNotEqual(os.path.abspath(self.gal), REAL_GALLERY)
        if os.path.isdir(REAL_GALLERY):
            before = stat_snapshot(REAL_GALLERY)
            with mock.patch.object(gallery_sweep, "GALLERY_DIR", REAL_GALLERY):
                gallery_sweep.plan(NOW, REAL_GALLERY, library_files=set(), refs={})
            self.assertEqual(stat_snapshot(REAL_GALLERY), before)


class ApplyTests(Base):
    def test_apply_refuses_while_the_switch_is_off_and_touches_nothing(self):
        gal, lib = snapshot(self.gal), snapshot(self.lib)
        with self.assertRaises(gallery_sweep.SweepOff):
            gallery_sweep.apply(NOW, self.gal, refs=self.refs())
        self.assertEqual((snapshot(self.gal), snapshot(self.lib)), (gal, lib))
        self.assertFalse(os.path.exists(os.path.join(self.gal, ".trash")))

    def test_apply_saves_referenced_pictures_trashes_the_rest_and_keeps_library_items_and_young_ones(self):
        gallery_sweep.set_enabled(True)
        out = gallery_sweep.apply(NOW, self.gal, refs=self.refs())
        self.assertEqual((out["saved_referenced"], out["trashed"]), (1, 3))
        for kept in ("2026-10/c0000003_0.png", "2026-10/d0000004_0.png", "2026-11/e0000005_0.png", "2026-11/f0000006_1.png"):
            self.assertTrue(os.path.isfile(os.path.join(self.gal, kept)), kept)
        for gone in ("2026-10/a0000001_0.png", "2026-10/a0000002_0.png", "2026-11/f0000006_0.png"):
            self.assertFalse(os.path.exists(os.path.join(self.gal, gone)), gone)
        item = [r for r in library.list_items() if r["file"] == "2026-10/d0000004_0.png"][0]
        self.assertEqual(item["reason"], "referenced by storyforge:" + os.path.join("stories", "tale.json"))
        trash = sorted(os.listdir(os.path.join(self.gal, ".trash")))
        self.assertEqual(len(trash), 5)                                                 # three pictures and the two orphaned sidecars
        self.assertTrue(all(n.split("-", 1)[0].isdigit() and len(n.split("-", 1)[0]) == 13 for n in trash))
        self.assertTrue(os.path.isfile(os.path.join(self.gal, "2026-11", "f0000006.json")))   # the shared sidecar stayed with the sibling
        rows = [json.loads(l) for l in open(self.log).read().splitlines()]
        self.assertEqual(sorted(r["file"] for r in rows), ["2026-10/a0000001_0.png", "2026-10/a0000002_0.png", "2026-11/f0000006_0.png"])

    def test_the_trash_is_purged_after_a_day_by_the_stamp_and_never_touches_other_files(self):
        gallery_sweep.set_enabled(True)
        gallery_sweep.apply(NOW, self.gal, refs=self.refs())
        trash = os.path.join(self.gal, ".trash")
        open(os.path.join(trash, "notes.txt"), "w").write("n")                          # unstamped: never ours
        self.assertEqual(gallery_sweep.purge_trash(NOW + DAY - 1, self.gal), [])
        gone = gallery_sweep.purge_trash(NOW + DAY + 1, self.gal)
        self.assertEqual(len(gone), 5)
        self.assertEqual(os.listdir(trash), ["notes.txt"])
        self.assertTrue(os.path.isfile(os.path.join(self.gal, "2026-11", "e0000005_0.png")))

    def test_the_library_folder_is_never_swept(self):
        gallery_sweep.set_enabled(True)
        kept = snapshot(self.lib)
        gallery_sweep.apply(NOW, self.gal, refs=self.refs())
        after = snapshot(self.lib)
        for k, v in kept.items():
            self.assertEqual(after.get(k), v)

    def test_switch_file_toggles(self):
        self.assertFalse(gallery_sweep.enabled())
        self.assertTrue(gallery_sweep.set_enabled(True))
        self.assertTrue(os.path.isfile(self.enabled))
        self.assertFalse(gallery_sweep.set_enabled(False))
        self.assertFalse(os.path.exists(self.enabled))


@unittest.skipIf(pf is None, "Flask not installed")
class RouteTests(Base):
    def setUp(self):
        super().setUp()
        self.p2 = mock.patch.object(pf, "GALLERY_DIR", self.gal)
        self.p3 = mock.patch.object(gallery_refs, "load_sources", lambda *a, **k: self.refs_src)
        self.p4 = mock.patch.object(gallery_sweep, "time", mock.Mock(time=lambda: NOW, strftime=__import__("time").strftime, gmtime=__import__("time").gmtime))
        self.p2.start()
        self.p3.start()
        self.p4.start()
        self.c = pf.app.test_client()

    def tearDown(self):
        self.p4.stop()
        self.p3.stop()
        self.p2.stop()
        super().tearDown()

    def test_plan_is_200_run_is_403_while_off_and_the_switch_routes_work(self):
        before = snapshot(self.gal)
        r = self.c.get("/api/sweep/plan").get_json()
        self.assertEqual((r["candidates"], r["enabled"]), (3, False))
        self.assertEqual(self.c.post("/api/sweep/run").status_code, 403)
        self.assertEqual(snapshot(self.gal), before)
        self.assertTrue(self.c.post("/api/sweep/enable").get_json()["enabled"])
        self.assertTrue(self.c.get("/api/sweep/plan").get_json()["enabled"])
        out = self.c.post("/api/sweep/run")
        self.assertEqual((out.status_code, out.get_json()["trashed"]), (200, 3))
        self.assertFalse(self.c.post("/api/sweep/disable").get_json()["enabled"])

    def test_the_gallery_listing_never_shows_trashed_pictures(self):
        gallery_sweep.set_enabled(True)
        gallery_sweep.apply(NOW, self.gal, refs=self.refs())
        shown = [rel for rel, _ in pf._gallery_pngs()]
        self.assertTrue(shown and all(not rel.startswith(".trash") for rel in shown))
        self.assertTrue(any(n.endswith(".png") for n in os.listdir(os.path.join(self.gal, ".trash"))))


if __name__ == "__main__":
    unittest.main()
