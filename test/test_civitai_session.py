"""server/core/civitai_session.py with a fake runner and fake Popen: no browser is ever opened, nothing is downloaded."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from core import civitai_session as cs  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.py = os.path.join(self.tmp.name, "python")
        open(self.py, "w").close()
        for p in (mock.patch.dict(os.environ, {"PROMPTFORGE_SESSION_PYTHON": self.py, "PROMPTFORGE_BROWSER_PROFILE": os.path.join(self.tmp.name, "prof"),
                                               "PROMPTFORGE_BROWSER_PORT": "9399"}),
                  mock.patch.object(cs, "port_busy", return_value=False)):
            p.start()
            self.addCleanup(p.stop)
        cs._login[0] = None
        self.addCleanup(lambda: cs._login.__setitem__(0, None))

    @staticmethod
    def runner(out, rc=0, seen=None):
        def run(argv, timeout):
            if seen is not None:
                seen.append((argv, timeout))
            return rc, out
        return run


class ConfigTest(Base):
    def test_env_overrides_and_defaults(self):
        self.assertEqual(cs.session_python(), self.py)
        self.assertEqual(cs.port(), 9399)
        self.assertTrue(cs.profile_dir().endswith("prof"))
        with mock.patch.dict(os.environ, {"PROMPTFORGE_SESSION_PYTHON": os.path.join(self.tmp.name, "missing")}):
            self.assertIsNone(cs.session_python())
        with mock.patch.dict(os.environ, {"PROMPTFORGE_BROWSER_PORT": "abc"}):
            self.assertEqual(cs.port(), cs.DEFAULT_PORT)


class StatusTest(Base):
    def test_signed_in_and_out(self):
        seen = []
        self.assertEqual(cs.status(self.runner('noise\n{"signed_in": true}\n', seen=seen)), {"signed_in": True, "available": True, "error": None})
        self.assertEqual(cs.status(self.runner('{"signed_in": false}'))["signed_in"], False)
        argv = seen[0][0]
        self.assertEqual(argv[0], self.py)
        self.assertIn("--status", argv)
        self.assertEqual(argv[argv.index("--port") + 1], "9399")

    def test_missing_python_timeout_garbage_and_busy(self):
        with mock.patch.dict(os.environ, {"PROMPTFORGE_SESSION_PYTHON": "/nonexistent/python"}):
            self.assertEqual(cs.status(self.runner("{}")), {"signed_in": None, "available": False, "error": "session python not found"})

        def slow(argv, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)
        self.assertEqual(cs.status(slow)["error"], "timeout")
        self.assertIsNone(cs.status(self.runner("garbage"))["signed_in"])
        with mock.patch.object(cs, "port_busy", return_value=True):
            called = []
            self.assertEqual(cs.status(self.runner("{}", seen=called))["error"], "browser_busy")
            self.assertEqual(called, [])


class DownloadTest(Base):
    def test_success_returns_bytes_and_passes_url_and_dest(self):
        seen = []
        n = cs.download("https://civitai.com/api/download/models/1", "/x/y", self.runner('{"ok": true, "bytes": 42}', seen=seen), timeout=100)
        self.assertEqual(n, 42)
        argv, to = seen[0]
        self.assertEqual(argv[argv.index("--download") + 1], "https://civitai.com/api/download/models/1")
        self.assertEqual(argv[argv.index("--dest") + 1], "/x/y")
        self.assertGreater(to, 100)

    def test_error_kinds(self):
        for err, kind in (("signed_out", "signed_out"), ("refused", "refused"), ("timeout", "timeout"), ("browser_busy", "busy"), ("weird", "unavailable")):
            with self.assertRaises(cs.SessionError) as cm:
                cs.download("u", "d", self.runner(json.dumps({"ok": False, "error": err}), rc=1))
            self.assertEqual(cm.exception.kind, kind, err)

    def test_runner_timeout_and_no_python(self):
        def slow(argv, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)
        with self.assertRaises(cs.SessionError) as cm:
            cs.download("u", "d", slow)
        self.assertEqual(cm.exception.kind, "timeout")
        with mock.patch.dict(os.environ, {"PROMPTFORGE_SESSION_PYTHON": "/nonexistent/python"}):
            with self.assertRaises(cs.SessionError) as cm:
                cs.download("u", "d", self.runner("{}"))
        self.assertEqual(cm.exception.kind, "unavailable")


class LoginTest(Base):
    class Proc:
        def __init__(self, alive=True):
            self.alive = alive

        def poll(self):
            return None if self.alive else 0

    def test_starts_a_detached_login_and_refuses_a_second(self):
        made = []

        def popen(argv, **kw):
            made.append((argv, kw))
            return self.Proc()
        cs.start_login(popen)
        argv, kw = made[0]
        self.assertEqual(argv[1:3], ["-m", "site_sessions.cli"])
        self.assertEqual(argv[-2:], ["login", "civitai"])
        self.assertTrue(kw["start_new_session"])
        with self.assertRaises(cs.SessionError) as cm:
            cs.start_login(popen)
        self.assertEqual(cm.exception.kind, "busy")
        self.assertEqual(len(made), 1)

    def test_after_the_window_closed_a_new_one_may_open(self):
        cs.start_login(lambda argv, **kw: self.Proc(alive=False))
        made = []
        cs.start_login(lambda argv, **kw: made.append(1) or self.Proc())
        self.assertEqual(made, [1])

    def test_busy_port_refuses(self):
        with mock.patch.object(cs, "port_busy", return_value=True):
            with self.assertRaises(cs.SessionError):
                cs.start_login(lambda *a, **k: self.fail("opened a window"))

    def test_sites_file_names_civitai_and_no_password(self):
        text = open(cs.SITES_FILE).read()
        self.assertIn("[site.civitai]", text)
        self.assertNotIn("password =", text.lower())


if __name__ == "__main__":
    unittest.main()
