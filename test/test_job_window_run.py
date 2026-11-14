"""The render window supervisor (scripts/jobs/window_run.py): Forge mode on, the runner, Forge mode off, whatever happens. Fake dashboard, fake runner, fake
clock, temp folders; it never starts a process, takes a GPU or touches Agent Manager (python3 -m unittest test.test_job_window_run)."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "server"))
from core import jobs  # noqa: E402
from scripts.jobs import window_run as wr  # noqa: E402


class FakeAM:
    """A dashboard double: POST on answers `on_code`, then GET reports paused after `ready_after` polls; POST off clears it."""

    def __init__(self, on_code=202, on_body=None, ready_after=1, never_ready=False, off_after=0, gpu_lag=0):
        self.posts, self.gets, self.on_code, self.on_body = [], 0, on_code, on_body or {}
        self.ready_after, self.never_ready, self.paused, self.polls_after_off, self.off_after = ready_after, never_ready, False, 0, off_after
        self.off_sent = False
        self.gpu_lag, self.paused_polls, self.gpu_active = gpu_lag, 0, False

    def post(self, path, body):
        self.posts.append((path, body))
        if body.get("on"):
            return self.on_code, self.on_body
        self.off_sent = True
        return 202, {"started": True}

    def get(self, path):
        self.gets += 1
        if self.off_sent:
            self.polls_after_off += 1
            self.paused = self.polls_after_off <= self.off_after
        elif not self.never_ready and self.gets > self.ready_after:
            self.paused = True
            self.paused_polls += 1
            self.gpu_active = self.paused_polls > self.gpu_lag
        if self.off_sent:
            self.gpu_active = False
        return 200, {"paused": self.paused, "gpuSession": {"active": self.gpu_active}}


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = jobs.JobStore(os.path.join(self.tmp.name, "jobs"))
        self.clock, self.out = Clock(), []
        self.runs = []

    def run_fn(self, code=0, boom=None):
        def fn(**kw):
            self.runs.append(dict(kw, phase=(wr.read_state(self.store) or {}).get("phase"), stop_exists=os.path.exists(kw["stop_file"])))
            if boom:
                raise boom
            return code
        return fn

    def supervise(self, am, hours=2, run_fn=None, **kw):
        return wr.supervise(self.store, hours, post=am.post, get=am.get, run_fn=run_fn or self.run_fn(), sleep=self.clock.sleep, now=self.clock, out=self.out.append, **kw)

    def state(self):
        return wr.read_state(self.store)


class HappyPathTest(Base):
    def test_forge_on_then_the_runner_then_forge_off_with_phases_in_order(self):
        am = FakeAM()
        self.assertEqual(self.supervise(am, hours=1.5), 0)
        self.assertEqual([(p, b) for p, b in am.posts], [("/api/pipeline/forge-mode", {"on": True, "vm": "keep"}), ("/api/pipeline/forge-mode", {"on": False})])
        (run,) = self.runs
        self.assertEqual(run["phase"], "rendering")
        st = self.state()
        self.assertEqual((st["phase"], st["exit_code"], st["hours"]), ("done", 0, 1.5))
        self.assertEqual(st["until"], wr._iso(1_800_000_000.0 + 5400))
        self.assertEqual(run["until"], st["until"])

    def test_the_stop_file_is_passed_and_cleared_before_and_after(self):
        os.makedirs(os.path.join(self.store.root, ".runner"))
        stale = os.path.join(self.store.root, ".runner", "stop")
        open(stale, "w").close()
        self.assertEqual(self.supervise(FakeAM()), 0)
        self.assertFalse(self.runs[0]["stop_exists"])               # a leftover stop request from an earlier window is removed first
        self.assertEqual(self.runs[0]["stop_file"], stale)
        self.assertFalse(os.path.exists(stale))

    def test_it_waits_for_the_gpu_session_not_just_the_pause_before_rendering(self):
        am = FakeAM(gpu_lag=3)                       # paused is true for three polls before the GPU session is active
        seen = []
        self.supervise(am, run_fn=lambda **kw: seen.append(am.gpu_active) or 0)
        self.assertEqual(seen, [True])

    def test_it_waits_until_forge_mode_reports_off(self):
        am = FakeAM(off_after=3)
        self.supervise(am)
        self.assertGreaterEqual(am.polls_after_off, 4)
        self.assertFalse(am.paused)

    def test_the_runner_exit_code_is_returned_and_recorded(self):
        self.assertEqual(self.supervise(FakeAM(), run_fn=self.run_fn(code=1)), 1)
        self.assertEqual(self.state()["exit_code"], 1)
        self.assertEqual(self.state()["phase"], "done")


class RefusalTest(Base):
    def test_a_refusal_changes_nothing_and_does_not_switch_forge_mode_off(self):
        am = FakeAM(on_code=409, on_body={"reasons": ["MemAvailable 5000 MB + 0 MB freed by the plan is under the 10240 MB needed"]})
        self.assertEqual(self.supervise(am), 4)
        self.assertEqual(am.posts, [("/api/pipeline/forge-mode", {"on": True, "vm": "keep"})])
        self.assertEqual(self.runs, [])
        st = self.state()
        self.assertEqual((st["phase"], st["exit_code"]), ("refused", 4))
        self.assertIn("10240 MB", st["reasons"][0])
        self.assertIn("Forge mode refused", self.out[0])

    def test_already_in_forge_mode_is_a_refusal_not_a_takeover(self):
        am = FakeAM(on_code=409, on_body={"reasons": ["already in forge mode"]})
        self.assertEqual(self.supervise(am), 4)
        self.assertEqual(len(am.posts), 1)

    def test_an_unreachable_dashboard_is_a_refusal_with_the_error(self):
        am = FakeAM(on_code=0, on_body={"error": "Connection refused"})
        self.assertEqual(self.supervise(am), 4)
        self.assertEqual(self.state()["reasons"], ["Connection refused"])
        self.assertEqual(self.supervise(FakeAM(on_code=500)), 4)
        self.assertEqual(self.state()["reasons"], ["the dashboard answered 500"])


class SafetyTest(Base):
    def test_forge_mode_that_never_finishes_switching_on_is_switched_off_and_returns_5(self):
        am = FakeAM(never_ready=True)
        self.assertEqual(self.supervise(am), 5)
        self.assertEqual(self.runs, [])
        self.assertEqual(am.posts[-1], ("/api/pipeline/forge-mode", {"on": False}))
        self.assertEqual(self.state()["phase"], "done")
        self.assertEqual(self.state()["exit_code"], 5)
        self.assertTrue(self.clock.t - 1_800_000_000.0 >= wr.ENTER_WAIT)

    def test_a_runner_that_raises_still_switches_forge_mode_off_exactly_once(self):
        am = FakeAM()
        with self.assertRaises(RuntimeError):
            self.supervise(am, run_fn=self.run_fn(boom=RuntimeError("comfy fell over")))
        self.assertEqual([b for _, b in am.posts].count({"on": False}), 1)
        st = self.state()
        self.assertEqual(st["phase"], "done")
        self.assertEqual(st["reasons"], ["comfy fell over"])

    def test_a_keyboard_interrupt_also_switches_forge_mode_off(self):
        am = FakeAM()
        with self.assertRaises(KeyboardInterrupt):
            self.supervise(am, run_fn=self.run_fn(boom=KeyboardInterrupt()))
        self.assertEqual(am.posts[-1], ("/api/pipeline/forge-mode", {"on": False}))

    def test_a_failing_off_call_is_recorded_and_the_state_is_still_done(self):
        am = FakeAM()
        original = am.post

        def post(path, body):
            if not body.get("on"):
                raise OSError("dashboard went away")
            return original(path, body)
        self.assertEqual(wr.supervise(self.store, 2, post=post, get=am.get, run_fn=self.run_fn(), sleep=self.clock.sleep, now=self.clock, out=self.out.append), 0)
        st = self.state()
        self.assertEqual(st["phase"], "done")
        self.assertIn("could not confirm Forge mode is off", st["reasons"][0])


class GuardTest(Base):
    def test_hours_must_be_in_range(self):
        for bad in (0, 0.1, 12.5, -1, None, "2", True):
            am = FakeAM()
            self.out.clear()
            self.assertEqual(self.supervise(am, hours=bad), 2, bad)
            self.assertEqual(am.posts, [])
            self.assertIn("hours must be from 0.25 to 12", self.out[0])
        for ok in (0.25, 12):
            self.assertEqual(self.supervise(FakeAM(), hours=ok), 0, ok)

    def test_a_live_window_run_refuses_a_second_one(self):
        os.makedirs(os.path.join(self.store.root, ".runner"))
        with open(os.path.join(self.store.root, ".runner", "window-run.json"), "w") as fh:
            json.dump({"pid": 1, "phase": "rendering"}, fh)          # pid 1 is always alive
        am = FakeAM()
        self.assertEqual(self.supervise(am), 3)
        self.assertEqual(am.posts, [])
        self.assertIn("another window run is running", self.out[0])

    def test_a_finished_or_dead_earlier_run_does_not_block(self):
        d = os.path.join(self.store.root, ".runner")
        os.makedirs(d)
        for rec in ({"pid": 1, "phase": "done"}, {"pid": 2 ** 22 + 12345, "phase": "rendering"}, {"pid": 1, "phase": "refused"}):
            with open(os.path.join(d, "window-run.json"), "w") as fh:
                json.dump(rec, fh)
            self.assertEqual(self.supervise(FakeAM()), 0, rec)


class CliTest(Base):
    def test_main_passes_hours_and_returns_the_exit_code(self):
        am = FakeAM()
        old = wr.signal.signal
        wr.signal.signal = lambda *a: None                           # the test must not install a real SIGTERM handler
        try:
            code = wr.main(["--hours", "1"], store=self.store, post=am.post, get=am.get, run_fn=self.run_fn(code=1), sleep=self.clock.sleep, now=self.clock, out=self.out.append)
        finally:
            wr.signal.signal = old
        self.assertEqual(code, 1)
        self.assertEqual(self.state()["hours"], 1.0)


class DefaultsTest(unittest.TestCase):
    def test_the_dashboard_address_comes_from_the_environment_then_the_default(self):
        seen = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = jobs.JobStore(os.path.join(tmp.name, "jobs"))
        old_http = wr.http_json
        wr.http_json = lambda method, url, body=None, timeout=30: (seen.append((method, url)) or (409, {"reasons": ["x"]}))
        old = os.environ.get("AGENT_MANAGER_URL")
        self.addCleanup(lambda: os.environ.__setitem__("AGENT_MANAGER_URL", old) if old is not None else os.environ.pop("AGENT_MANAGER_URL", None))
        try:
            os.environ["AGENT_MANAGER_URL"] = "http://am.test:1/"
            wr.supervise(store, 1, run_fn=lambda **k: 0, out=lambda m: None)
            os.environ.pop("AGENT_MANAGER_URL")
            wr.supervise(store, 1, run_fn=lambda **k: 0, out=lambda m: None)
        finally:
            wr.http_json = old_http
        self.assertEqual(seen, [("POST", "http://am.test:1/api/pipeline/forge-mode"), ("POST", "http://localhost:7420/api/pipeline/forge-mode")])

    def test_http_json_reports_a_connection_failure_instead_of_raising(self):
        code, body = wr.http_json("GET", "http://127.0.0.1:9/x", timeout=2)
        self.assertEqual(code, 0)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
