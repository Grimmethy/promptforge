"""A deleted image is counted once (2026-11-08, friction item 54): the delete button lowered the paging counters (_galOffset, _galTotal) by one after its
DELETE fetch resolved, and the gallery-removed push, which the server publishes BEFORE it answers the DELETE and which therefore usually arrives first, had
already removed the tile and lowered them as well: -2 for one delete, so infinite scroll could skip an item. Both paths now call one function,
dropTiles(file), that removes the file from the shown set and every tile for it and lowers the counters by the number of tiles it really removed, so a
second call for the same file changes nothing. These checks read web/app.js as text; the real-page check of both arrival orders is recorded on the task.
No browser, no ComfyUI, no GPU (python3 -m unittest discover -s test -p 'test_*.py')."""
import os
import re
import unittest

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")


def app_js():
    with open(os.path.join(WEB, "app.js"), encoding="utf-8") as fh:
        return fh.read()


def function_body(js, signature):
    start = js.index(signature)
    return js[start:js.index("\n}\n", start)]


class DeleteCountTest(unittest.TestCase):
    def test_the_delete_button_uses_dropTiles_and_does_no_counting_of_its_own(self):
        js = app_js()
        line = next(l for l in js.splitlines() if "mBtn('🗑'" in l)
        self.assertIn("dropTiles(file)", line)
        self.assertNotIn("_galOffset", line)
        self.assertNotIn("_galTotal", line)
        self.assertNotIn("tile.remove()", line)

    def test_the_gallery_removed_push_uses_dropTiles_and_does_no_counting_of_its_own(self):
        js = app_js()
        start = js.index("es.addEventListener('gallery-removed'")
        handler = js[start:js.index("\n", start)]
        self.assertIn("dropTiles(JSON.parse(e.data).file)", handler)
        self.assertNotIn("_galOffset", handler)
        self.assertNotIn("_galTotal", handler)

    def test_dropTiles_counts_only_the_tiles_it_really_removes(self):
        body = function_body(app_js(), "function dropTiles(")
        self.assertIn("_shown.delete(file)", body)
        self.assertIn("n.dataset.file === file", body)
        self.assertIn("gone.forEach(n => n.remove())", body)
        self.assertIn("_galOffset = Math.max(0, _galOffset - gone.length)", body)
        self.assertIn("_galTotal = Math.max(0, _galTotal - gone.length)", body)

    def test_the_counters_are_lowered_nowhere_else(self):
        js = app_js()
        self.assertEqual(len(re.findall(r"_galOffset\s*=\s*Math\.max\(", js)), 1)
        self.assertEqual(len(re.findall(r"_galTotal\s*=\s*Math\.max\(", js)), 1)
        self.assertNotRegex(js, r"_gal(Offset|Total)\s*-=")
        self.assertNotRegex(js, r"_gal(Offset|Total)--")

    def test_no_timer_was_added_for_it(self):
        self.assertNotRegex(function_body(app_js(), "function dropTiles("), r"setInterval|setTimeout")


if __name__ == "__main__":
    unittest.main()
