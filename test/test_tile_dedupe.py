"""One tile per generated image (2026-11-08, friction item 42): the server pushes gallery-new the moment an image is saved, which is before the page's
2-second job poll sees the job as done, so the event path added a tile and then pollJob replaced its placeholder with a SECOND tile for the same file.
Both paths now ask web/tile-dedupe.js which files still need a tile, and pollJob counts what it adds (and removes its placeholder when the push
already did the work). The helper is a pure function: these tests load it with node when node exists (skipped otherwise) and pin the wiring in app.js
as text; the real-page check of both arrival orders is recorded on the task. No ComfyUI, no GPU (python3 -m unittest discover -s test -p 'test_*.py')."""
import json
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "..", "web")


def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as fh:
        return fh.read()


def function_body(js, signature):
    start = js.index(signature)
    return js[start:js.index("\n}\n", start)]


def node_eval(expr):
    js = "const m = require(%s); console.log(JSON.stringify(%s));" % (json.dumps(os.path.join(WEB, "tile-dedupe.js")), expr)
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not installed: the JS logic tests are skipped")
class FreshFilesTest(unittest.TestCase):
    def test_files_without_a_tile_come_back_in_their_original_order(self):
        self.assertEqual(node_eval("m.freshFiles(new Set(), ['b.png', 'a.png', 'c.png'])"), ["b.png", "a.png", "c.png"])

    def test_files_that_already_have_a_tile_are_left_out(self):
        self.assertEqual(node_eval("m.freshFiles(new Set(['a.png']), ['b.png', 'a.png', 'c.png'])"), ["b.png", "c.png"])

    def test_when_every_file_has_a_tile_nothing_is_fresh(self):
        self.assertEqual(node_eval("m.freshFiles(new Set(['a.png', 'b.png']), ['a.png', 'b.png'])"), [])

    def test_the_shown_set_is_not_changed(self):
        self.assertEqual(node_eval("(() => { const s = new Set(['a.png']); m.freshFiles(s, ['a.png', 'b.png']); return [...s]; })()"), ["a.png"])

    def test_a_missing_or_empty_list_is_fine(self):
        self.assertEqual(node_eval("[m.freshFiles(new Set(), undefined), m.freshFiles(new Set(), null), m.freshFiles(new Set(['x']), [])]"), [[], [], []])


class WiringTest(unittest.TestCase):
    def test_the_page_loads_the_helper_before_app_js(self):
        html = read("index.html")
        a, b = html.find("/tile-dedupe.js"), html.find("/app.js")
        self.assertTrue(0 <= a < b, "tile-dedupe.js must be loaded before app.js")

    def test_the_push_path_uses_the_helper(self):
        self.assertIn("PFTileDedupe.freshFiles(_shown, files)", function_body(read("app.js"), "function addNewTiles("))

    def test_the_poll_path_only_adds_tiles_for_fresh_files_and_counts_them(self):
        body = function_body(read("app.js"), "async function pollJob(")
        self.assertIn("PFTileDedupe.freshFiles(_shown, j.images)", body)
        m = re.search(r"if \(fresh\.length\) \{(.*?)\} else tile\.remove\(\);", body, re.S)
        self.assertIsNotNone(m, "pollJob must replace the placeholder when there are fresh files and remove it otherwise")
        branch = m.group(1)
        self.assertIn("tile.replaceWith(...fresh.map(", branch)                  # tiles for the fresh files only
        self.assertIn("_galOffset += fresh.length", branch)                       # counted here, because the push did not add them
        self.assertIn("_galTotal += fresh.length", branch)
        self.assertEqual(body.count("_galTotal +="), 1)                           # and never when the push already counted them

    def test_no_timers_were_added_by_the_dedupe_helper_or_its_call_sites(self):
        self.assertNotRegex(read("tile-dedupe.js"), r"setInterval|setTimeout")
        # pollJob already waits with one setTimeout per round: the change must not add another
        self.assertEqual(function_body(read("app.js"), "async function pollJob(").count("setTimeout"), 1)


if __name__ == "__main__":
    unittest.main()
