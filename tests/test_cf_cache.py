"""Tests for the mtime-based index cache in server/core/cf_characters.py.

Builds are counted by wrapping yaml.safe_load (called once per parsed front-matter block).
Mtimes are changed deterministically with os.utime, never time.sleep.
"""

import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.core import cf_characters as cf  # noqa: E402

_REAL_SAFE_LOAD = yaml.safe_load


def _card(name):
    return "---\nname: %s\nweight: 1.0\n---\n\n## Keywords\n\n1girl, %s\n" % (name, name.lower())


class CfCacheTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.chars = os.path.join(self.root, "characters")
        os.makedirs(self.chars)
        self.paths = {}
        for n in ("Alice", "Bob", "Carol"):
            self.paths[n] = self._write(n)

        env = {"CHARACTERFORGE_ROOT": self.root, "CHARACTERFORGE_DIR": self.chars}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("PROMPTFORGE_CHARACTER_SOURCE", None)

        self.calls = 0

        def counting(*a, **kw):
            self.calls += 1
            return _REAL_SAFE_LOAD(*a, **kw)

        p2 = mock.patch.object(yaml, "safe_load", side_effect=counting)
        p2.start()
        self.addCleanup(p2.stop)

        saved = (cf._index_cache, cf._index_mtime)
        cf._index_cache, cf._index_mtime = {}, None
        self.addCleanup(lambda: (setattr(cf, "_index_cache", saved[0]), setattr(cf, "_index_mtime", saved[1])))

    def _write(self, name, display=None):
        path = os.path.join(self.chars, name.lower() + ".md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_card(display or name))
        return path

    def _bump(self, path, seconds=10):
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 10**9))

    def test_first_call_builds(self):
        idx = cf._get_index()
        self.assertEqual(self.calls, 3)
        self.assertEqual(sorted(idx), ["alice", "bob", "carol"])

    def test_unchanged_mtimes_return_cache(self):
        first = cf._get_index()
        n = self.calls
        self.assertEqual(cf._get_index(), first)
        self.assertEqual(cf._get_index(), first)
        self.assertEqual(self.calls, n)

    def test_file_mtime_change_rebuilds(self):
        cf._get_index()
        n = self.calls
        # Rewrite in place (directory mtime unchanged), then bump only the file's mtime.
        self._write("Bob", display="Robert")
        self._bump(self.paths["Bob"])
        idx = cf._get_index()
        self.assertGreater(self.calls, n)
        self.assertIn("robert", idx)
        self.assertNotIn("bob", idx)
        n = self.calls
        cf._get_index()
        self.assertEqual(self.calls, n)

    def test_directory_mtime_change_rebuilds(self):
        cf._get_index()
        n = self.calls
        self._bump(self.chars)
        cf._get_index()
        self.assertGreater(self.calls, n)
        n = self.calls
        cf._get_index()
        self.assertEqual(self.calls, n)

    def test_added_file_appears(self):
        cf._get_index()
        n = self.calls
        self._write("Dana")
        self._bump(self.chars)
        idx = cf._get_index()
        self.assertGreater(self.calls, n)
        self.assertIn("dana", idx)

    def test_removed_file_disappears(self):
        cf._get_index()
        n = self.calls
        os.remove(self.paths["Carol"])
        self._bump(self.chars)
        idx = cf._get_index()
        self.assertGreater(self.calls, n)
        self.assertNotIn("carol", idx)

    def test_get_character_case_insensitive_and_unknown(self):
        self.assertEqual(cf.get_character("ALICE")["name"], "Alice")
        self.assertEqual(cf.get_character("nobody"), {})

    def test_public_readers_share_cache(self):
        cf._get_index()
        n = self.calls
        self.assertEqual(cf.list_characters(), ["alice", "bob", "carol"])
        self.assertEqual(cf.character_names(), ["Alice", "Bob", "Carol"])
        cf.get_character("bob")
        self.assertEqual(self.calls, n)

    def test_concurrent_calls_build_once(self):
        results = []

        def worker():
            results.append(cf._get_index())

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 6)
        self.assertEqual(self.calls, 3)


class PresetsEndpointCacheTest(unittest.TestCase):
    """60 characters: /api/presets answers fast and builds the index exactly once.

    app.py imports the package as `core`, so this class uses `core.cf_characters` (a different
    module object from `server.core.cf_characters` used above) to reset and wrap the same cache."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
        try:
            import app as pf
        except ImportError as exc:  # flask etc. not installed in this environment
            self.skipTest("server.app not importable: %s" % exc)
        from core import cf_characters as cfc
        self.pf, self.cfc = pf, cfc

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        chars = os.path.join(tmp.name, "characters")
        os.makedirs(chars)
        for i in range(60):
            name = "Char%02d" % i
            with open(os.path.join(chars, name.lower() + ".md"), "w", encoding="utf-8") as fh:
                fh.write(_card(name))

        p = mock.patch.dict(os.environ, {"CHARACTERFORGE_ROOT": tmp.name, "CHARACTERFORGE_DIR": chars})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("PROMPTFORGE_CHARACTER_SOURCE", None)

        saved = (cfc._index_cache, cfc._index_mtime)
        cfc._index_cache, cfc._index_mtime = {}, None
        self.addCleanup(lambda: (setattr(cfc, "_index_cache", saved[0]), setattr(cfc, "_index_mtime", saved[1])))
        pf._PRESETS_CACHES.clear()
        self.addCleanup(pf._PRESETS_CACHES.clear)

        self.builds = 0
        real_build = cfc._build_index

        def counting():
            self.builds += 1
            return real_build()

        p2 = mock.patch.object(cfc, "_build_index", side_effect=counting)
        p2.start()
        self.addCleanup(p2.stop)
        p3 = mock.patch.object(pf, "list_models", return_value=[])
        p3.start()
        self.addCleanup(p3.stop)

    def test_presets_with_60_characters_builds_once_and_is_fast(self):
        import time
        t0 = time.perf_counter()
        resp = self.pf.app.test_client().get("/api/presets")
        elapsed = time.perf_counter() - t0
        self.assertEqual(resp.status_code, 200)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(self.builds, 1)
        self.assertEqual(len(self.cfc.list_characters()), 60)
        self.assertEqual(self.builds, 1)


if __name__ == "__main__":
    unittest.main()
