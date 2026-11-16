"""server/core/civitai_session.py with a fake runner and fake Popen: no browser is ever opened, nothing is downloaded."""
import json
import os
import subprocess
import sys
import tempfile
import time
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
        def __init__(self, alive=True, rc=0):
            self.alive = alive
            self.returncode = None if alive else rc

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            if self.alive:
                raise subprocess.TimeoutExpired("fake", timeout)
            return self.returncode

    def test_starts_a_detached_login_and_refuses_a_second(self):
        made = []

        def popen(argv, **kw):
            made.append((argv, kw))
            return self.Proc()
        cs.start_login(popen)
        argv, kw = made[0]
        self.assertEqual(argv[1], "-c")
        self.assertIn("site_sessions.cli", argv[2])
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

    def test_a_sibling_console_script_is_used_when_present(self):
        script = os.path.join(self.tmp.name, "site-sessions")
        open(script, "w").close()
        os.chmod(script, 0o755)
        made = []

        def popen(argv, **kw):
            made.append(argv)
            return self.Proc()
        cs.start_login(popen)
        self.assertEqual(made[0][0], script)
        self.assertEqual(made[0][-2:], ["login", "civitai"])

    def test_a_child_that_exits_with_an_error_at_once_is_unavailable(self):
        with self.assertRaises(cs.SessionError) as cm:
            cs.start_login(lambda argv, **kw: self.Proc(alive=False, rc=1), wait=0.01)
        self.assertEqual(cm.exception.kind, "unavailable")
        self.assertIsNone(cs._login[0])

    def test_a_child_still_running_after_the_wait_is_accepted(self):
        cs.start_login(lambda argv, **kw: self.Proc(), wait=0.01)
        self.assertIsNotNone(cs._login[0])

    def test_busy_port_refuses(self):
        with mock.patch.object(cs, "port_busy", return_value=True):
            with self.assertRaises(cs.SessionError):
                cs.start_login(lambda *a, **k: self.fail("opened a window"))

    def test_sites_file_names_civitai_and_no_password(self):
        text = open(cs.SITES_FILE).read()
        self.assertIn("[site.civitai]", text)
        self.assertNotIn("password =", text.lower())


class RunTest(Base):
    """_run with real throwaway python children (no browser): SIGTERM first, SIGKILL only after the grace period."""

    def child(self, body):
        return [sys.executable, "-c", "import signal, sys, time\n" + body]

    def test_normal_answer_passes_through(self):
        rc, out = cs._run(self.child("print('{\"signed_in\": true}')"), 10)
        self.assertEqual((rc, out.strip()), (0, '{"signed_in": true}'))

    def test_timeout_sends_sigterm_so_the_child_can_clean_up(self):
        marker = os.path.join(self.tmp.name, "cleaned")
        body = ("def stop(*a):\n    open(%r, 'w').close(); sys.exit(0)\n"
                "signal.signal(signal.SIGTERM, stop)\nprint('ready', flush=True)\ntime.sleep(60)\n") % marker
        with self.assertRaises(subprocess.TimeoutExpired):
            cs._run(self.child(body), 1.5)
        self.assertTrue(os.path.exists(marker), "the child never got SIGTERM")

    def test_a_child_that_ignores_sigterm_is_killed_after_the_grace_period(self):
        pidfile = os.path.join(self.tmp.name, "pid")
        body = ("import os\nopen(%r, 'w').write(str(os.getpid()))\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n") % pidfile
        t0 = time.time()
        with mock.patch.object(cs, "TERM_GRACE", 0.5):
            with self.assertRaises(subprocess.TimeoutExpired):
                cs._run(self.child(body), 1.5)
        self.assertLess(time.time() - t0, 10)
        pid = int(open(pidfile).read())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_the_child_is_waited_for_after_sigterm(self):
        pidfile = os.path.join(self.tmp.name, "pid2")
        body = ("import os\nopen(%r, 'w').write(str(os.getpid()))\n"
                "def stop(*a):\n    time.sleep(0.5); open(%r, 'w').close(); sys.exit(0)\n"
                "signal.signal(signal.SIGTERM, stop)\ntime.sleep(60)\n") % (pidfile, pidfile + ".done")
        with self.assertRaises(subprocess.TimeoutExpired):
            cs._run(self.child(body), 1.5)
        self.assertTrue(os.path.exists(pidfile + ".done"), "the child was killed before its slow clean-up finished")
        with self.assertRaises(ProcessLookupError):      # reaped before _run returned: it did not leave a running (or zombie) child
            os.kill(int(open(pidfile).read()), 0)

    def test_the_default_status_timeout_is_ninety_seconds(self):
        seen = []
        cs.status(self.runner('{"signed_in": true}', seen=seen))
        self.assertEqual((cs.STATUS_TIMEOUT, seen[0][1]), (90, 90))


if __name__ == "__main__":
    unittest.main()
