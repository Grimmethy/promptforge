"""Which character(s) a gallery image shows (2026-11-10): the resolver (sidecar names, a guess from a regenerate run's source file name), the
mtime cache, and the gallery routes' ?character= / ?linked= filters and counts. A temp gallery and a fixed name list: no ComfyUI, no GPU, no
CharacterForge needed (python3 -m unittest discover -s test -p 'test_*.py')."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import app as pf  # noqa: E402
from core import gallery_chars as G  # noqa: E402

NAMES = {G.norm(n): n for n in ("Ettercap", "Drider", "Lucky the Pocket Gnome", "Peasant A (Goat)", "Peasant B (Goat)", "Gobs", "Goblin")}


class ResolveTest(unittest.TestCase):
    def test_sidecar_names_win_and_are_not_guessed(self):
        out = G.resolve({"names": ["Lucky_the_Pocket_Gnome", "Ettercap"], "source": "drider-a-1.png"}, NAMES)
        self.assertEqual(out, [{"name": "Lucky the Pocket Gnome", "guessed": False}, {"name": "Ettercap", "guessed": False}])

    def test_unknown_sidecar_name_is_kept_readable(self):
        self.assertEqual(G.resolve({"names": ["Some_Old_Preset"]}, NAMES), [{"name": "Some Old Preset", "guessed": False}])

    def test_regenerate_run_is_guessed_from_the_source_file_name(self):
        for src in ("ettercap-a-714aad.png", "ettercap-b-2afb36.png", "ettercap-a-colour-9ed2da.png", "Ettercap-A-714AAD.PNG"):
            self.assertEqual(G.resolve({"names": [], "source": src}, NAMES), [{"name": "Ettercap", "guessed": True}], src)

    def test_a_unique_name_prefix_of_four_or_more_letters_matches(self):
        self.assertEqual(G.resolve({"source": "lucky-a-1.png"}, NAMES)[0]["name"], "Lucky the Pocket Gnome")
        self.assertEqual(G.resolve({"source": "lucky-the-pocket-gnome-a-1.png"}, NAMES)[0]["name"], "Lucky the Pocket Gnome")

    def test_an_ambiguous_or_short_prefix_and_unrelated_names_give_nothing(self):
        self.assertEqual(G.resolve({"source": "peasant-a-1.png"}, NAMES), [])     # 'peasant' starts two names, and 'a' stops the prefix
        self.assertEqual(G.resolve({"source": "go-a-1.png"}, NAMES), [])           # under four letters (and ambiguous)
        self.assertEqual(G.resolve({"source": "dri-a-1.png"}, NAMES), [])          # unique, but only three letters: too short to trust
        self.assertEqual(G.resolve({"source": "profile-portrait-casual-c764743e.png"}, NAMES), [])
        self.assertEqual(G.resolve({"source": "c764743e.png"}, NAMES), [])
        self.assertEqual(G.resolve({}, NAMES), [])

    def test_spaces_underscores_and_case_do_not_matter(self):
        self.assertEqual(G.norm("Lucky_the Pocket-Gnome"), G.norm("lucky THE pocket gnome"))


class GalleryRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "2026-11")
        os.makedirs(self.dir)
        G.clear()
        self.patches = [mock.patch.object(pf, "GALLERY_DIR", self.tmp.name), mock.patch.object(pf, "_gallery_names", lambda: NAMES),
                        mock.patch.object(pf.data, "nsfw_enabled", lambda: True)]
        for p in self.patches:
            p.start()
        self.c = pf.app.test_client()
        self.mtime = 1_700_000_000
        self.add("aaaaaaa1", {"characters": [{"name": "Ettercap"}]})
        self.add("aaaaaaa2", {"characters": [{"name": "Lucky_the_Pocket_Gnome"}, {"name": "Ettercap"}]})
        self.add("aaaaaaa3", {"regenerate": True, "source": "drider-a-9257c7.png"})
        self.add("aaaaaaa4", {"regenerate": True, "source": "profile-portrait-casual-1.png"}, links=[{"forge": "characterforge", "slug": "Ettercap"}])
        self.add("aaaaaaa5", {"characters": []})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        G.clear()
        self.tmp.cleanup()

    def add(self, name, spec, links=None):
        self.mtime += 10
        png = os.path.join(self.dir, name + "_0.png")
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG")
        os.utime(png, (self.mtime, self.mtime))
        meta = {"id": name, "spec": spec}
        if links is not None:
            meta["links"] = links
        with open(os.path.join(self.dir, name + ".json"), "w") as fh:
            json.dump(meta, fh)

    def files(self, query=""):
        r = self.c.get("/api/gallery" + query).get_json()
        return [i["file"].split("/")[1][:8] for i in r["items"]], r

    def test_every_item_carries_its_characters(self):
        _, r = self.files()
        by = {i["file"].split("/")[1][:8]: i["characters"] for i in r["items"]}
        self.assertEqual(by["aaaaaaa1"], [{"name": "Ettercap", "guessed": False}])
        self.assertEqual([c["name"] for c in by["aaaaaaa2"]], ["Lucky the Pocket Gnome", "Ettercap"])
        self.assertEqual(by["aaaaaaa3"], [{"name": "Drider", "guessed": True}])
        self.assertEqual((by["aaaaaaa4"], by["aaaaaaa5"]), ([], []))

    def test_character_filter_matches_by_name_or_slug_and_includes_guesses(self):
        for q in ("Ettercap", "ettercap", "Lucky_the_Pocket_Gnome", "Drider"):
            files, r = self.files("?character=" + q)
            self.assertEqual(r["total"], len(files))
        self.assertEqual(sorted(self.files("?character=ettercap")[0]), ["aaaaaaa1", "aaaaaaa2"])
        self.assertEqual(self.files("?character=Drider")[0], ["aaaaaaa3"])
        self.assertEqual(sorted(self.files("?character=none")[0]), ["aaaaaaa4", "aaaaaaa5"])
        self.assertEqual(self.files("?character=Nobody")[0], [])

    def test_linked_filter_uses_the_sidecar_links(self):
        self.assertEqual(self.files("?linked=any")[0], ["aaaaaaa4"])
        self.assertEqual(sorted(self.files("?linked=none")[0]), ["aaaaaaa1", "aaaaaaa2", "aaaaaaa3", "aaaaaaa5"])
        self.assertEqual(self.files("?character=none&linked=any")[0], ["aaaaaaa4"])

    def test_paging_applies_after_the_filter(self):
        files, r = self.files("?character=ettercap&limit=1&offset=1")
        self.assertEqual((len(files), r["total"]), (1, 2))

    def test_no_filter_keeps_the_old_shape_and_order(self):
        files, r = self.files()
        self.assertEqual((files, r["total"]), (["aaaaaaa5", "aaaaaaa4", "aaaaaaa3", "aaaaaaa2", "aaaaaaa1"], 5))

    def test_counts_route_lists_characters_by_image_count(self):
        r = self.c.get("/api/gallery-characters").get_json()
        self.assertEqual([(x["name"], x["count"], x["guessed"]) for x in r["characters"]],
                         [("Ettercap", 2, 0), ("Drider", 1, 1), ("Lucky the Pocket Gnome", 1, 0)])
        self.assertEqual((r["none"], r["total"]), (2, 5))

    def test_gallery_meta_names_the_characters(self):
        m = self.c.get("/api/gallery-meta/2026-11/aaaaaaa3_0.png").get_json()
        self.assertEqual(m["characters"], [{"name": "Drider", "guessed": True}])
        self.assertEqual(m["id"], "aaaaaaa3")


class CacheTest(unittest.TestCase):
    def test_a_sidecar_is_read_once_until_its_mtime_changes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.json")
            with open(p, "w") as fh:
                json.dump({"spec": {"characters": [{"name": "A"}]}}, fh)
            os.utime(p, (1_700_000_000, 1_700_000_000))
            G.clear()
            self.assertEqual(G.raw_for(p)["names"], ["A"])
            with open(p, "w") as fh:
                json.dump({"spec": {"characters": [{"name": "B"}]}}, fh)
            os.utime(p, (1_700_000_000, 1_700_000_000))                 # same mtime: the cached copy is served, no re-read
            self.assertEqual(G.raw_for(p)["names"], ["A"])
            os.utime(p, (1_700_000_100, 1_700_000_100))                 # new mtime: re-read
            self.assertEqual(G.raw_for(p)["names"], ["B"])

    def test_missing_or_broken_sidecars_give_empty_values(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.json")
            with open(bad, "w") as fh:
                fh.write("{not json")
            G.clear()
            self.assertEqual(G.raw_for(os.path.join(d, "none.json")), {"names": [], "source": "", "links": 0})
            self.assertEqual(G.raw_for(bad), {"names": [], "source": "", "links": 0})


if __name__ == "__main__":
    unittest.main()
