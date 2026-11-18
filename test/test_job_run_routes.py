"""The Run queue routes (/api/jobs/run, /api/jobs/run/stop) and their helpers (server/core/job_run.py). A fresh Flask app on a temp job folder with an injected
spawner and signal sender: no process is started, nothing is signalled, no GPU. Needs flask, so use the repo venv:  .venv/bin/python -m unittest test.test_job_run_routes"""
import json
import os
import signal
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask  # noqa: E402

from core import job_run, jobs, jobs_routes  # noqa: E402
from test_jobs_routes import LOCAL, REMOTE, spec  # noqa: E402
import test_jobs_routes  # noqa: E402


class RunBase(test_jobs_routes.RoutesBase):
    def setUp(self):
        super().setUp()
        self.spawned, self.killed = [], []
        app = Flask(__name__)
        jobs_routes.register(app, lambda: self.store, spawn=lambda store, hours: self.spawned.append(hours) or 4242, kill=lambda pid, sig: self.killed.append((pid, sig)))
        self.client = app.test_client()
        self.run_dir = os.path.join(self.root, ".runner")

    def write(self, name, rec):
        os.makedirs(self.run_dir, exist_ok=True)
        with open(os.path.join(self.run_dir, name), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)


class StartTest(RunBase):
    def test_a_start_spawns_with_the_hours_and_answers_202(self):
        self.file_job()
        r = self.post("/api/jobs/run", {"hours": 3})
        self.assertEqual((r.status_code, r.get_json()), (202, {"started": True, "pid": 4242, "hours": 3}))
        self.assertEqual(self.spawned, [3])

    def test_no_body_means_two_hours(self):
        self.file_job()
        self.assertEqual(self.post("/api/jobs/run").status_code, 202)
        self.assertEqual(self.spawned, [2])

    def test_hours_out_of_range_are_refused_and_nothing_starts(self):
        self.file_job()
        for bad in (0, 0.2, 12.01, -3, "2", True, [1]):
            r = self.post("/api/jobs/run", {"hours": bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("hours must be a number from 0.25 to 12", r.get_json()["error"])
        self.assertEqual(self.post("/api/jobs/run", ["x"]).status_code, 400)
        self.assertEqual(self.spawned, [])

    def test_nothing_queued_is_a_409(self):
        r = self.post("/api/jobs/run", {"hours": 2})
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "nothing queued"))
        j = self.file_job()
        self.store.hold(j["id"])
        self.assertEqual(self.post("/api/jobs/run", {"hours": 2}).status_code, 409)
        self.assertEqual(self.spawned, [])

    def test_a_window_already_running_is_a_409(self):
        self.file_job()
        self.write("window-run.json", {"pid": os.getpid(), "phase": "rendering", "started_at": "2026-11-13T10:00:00Z"})
        r = self.post("/api/jobs/run", {"hours": 2})
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "a render window is already running"))
        self.assertEqual(self.spawned, [])

    def test_a_dead_or_finished_earlier_window_does_not_block(self):
        self.file_job()
        self.write("window-run.json", {"pid": 2 ** 22 + 99999, "phase": "rendering"})
        self.assertEqual(self.post("/api/jobs/run", {"hours": 2}).status_code, 202)
        self.write("window-run.json", {"pid": os.getpid(), "phase": "done"})
        self.assertEqual(self.post("/api/jobs/run", {"hours": 2}).status_code, 202)

    def test_only_this_machine_may_start_or_stop(self):
        self.file_job()
        self.assertEqual(self.post("/api/jobs/run", {"hours": 2}, env=REMOTE).status_code, 403)
        r = self.client.post("/api/jobs/run", json={"hours": 2}, environ_overrides=LOCAL, headers={"X-Forwarded-For": "100.64.0.1"})
        self.assertEqual(r.status_code, 403)
        self.write("window-run.json", {"pid": os.getpid(), "phase": "rendering"})
        self.assertEqual(self.post("/api/jobs/run/stop", env=REMOTE).status_code, 403)
        self.assertEqual((self.spawned, self.killed), ([], []))


class StatusTest(RunBase):
    def test_idle_when_nothing_ever_ran(self):
        body = self.client.get("/api/jobs/run").get_json()
        self.assertEqual((body["running"], body["phase"], body["pictures"], body["reasons"]), (False, "idle", 0, []))

    def test_a_running_window_with_a_fresh_heartbeat(self):
        self.write("window-run.json", {"pid": os.getpid(), "phase": "rendering", "started_at": "2026-11-13T10:00:00Z", "until": "2026-11-13T12:00:00Z", "reasons": []})
        self.write("heartbeat.json", {"pid": 1, "at": "2026-11-13T10:05:00Z", "pictures": 7, "jobs_done": 2, "jobs_failed": 1, "current": "job-x"})
        body = self.client.get("/api/jobs/run", environ_overrides=REMOTE).get_json()                # reads are open to anyone who can reach the service
        self.assertEqual((body["running"], body["phase"], body["pictures"], body["jobs_done"], body["jobs_failed"], body["current"], body["until"]),
                         (True, "rendering", 7, 2, 1, "job-x", "2026-11-13T12:00:00Z"))

    def test_a_heartbeat_from_before_this_window_is_ignored(self):
        self.write("window-run.json", {"pid": os.getpid(), "phase": "rendering", "started_at": "2026-11-13T10:00:00Z"})
        self.write("heartbeat.json", {"at": "2026-11-12T23:00:00Z", "pictures": 99, "jobs_done": 9, "jobs_failed": 9, "current": "old"})
        body = self.client.get("/api/jobs/run").get_json()
        self.assertEqual((body["pictures"], body["jobs_done"], body["current"]), (0, 0, None))

    def test_a_supervisor_that_died_is_reported_as_ended_not_running(self):
        self.write("window-run.json", {"pid": 2 ** 22 + 99999, "phase": "rendering", "started_at": "2026-11-13T10:00:00Z"})
        body = self.client.get("/api/jobs/run").get_json()
        self.assertEqual((body["running"], body["phase"]), (False, "ended"))

    def test_a_refused_window_shows_its_reasons_and_is_not_running(self):
        self.write("window-run.json", {"pid": os.getpid(), "phase": "refused", "reasons": ["MemAvailable low"], "exit_code": 4})
        body = self.client.get("/api/jobs/run").get_json()
        self.assertEqual((body["running"], body["phase"], body["reasons"], body["exit_code"]), (False, "refused", ["MemAvailable low"], 4))

    def test_junk_state_files_read_as_idle(self):
        os.makedirs(self.run_dir)
        for text in ("{broken", "[1]"):
            with open(os.path.join(self.run_dir, "window-run.json"), "w") as fh:
                fh.write(text)
            self.assertEqual(self.client.get("/api/jobs/run").get_json()["phase"], "idle", text)


class StopTest(RunBase):
    def test_stop_sends_sigterm_to_the_supervisor(self):
        self.write("window-run.json", {"pid": os.getpid(), "phase": "rendering"})
        r = self.post("/api/jobs/run/stop")
        self.assertEqual((r.status_code, r.get_json()), (200, {"stopping": True}))
        self.assertEqual(self.killed, [(os.getpid(), signal.SIGTERM)])

    def test_stop_with_nothing_running_is_a_409_and_signals_nothing(self):
        r = self.post("/api/jobs/run/stop")
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "no render window is running"))
        self.write("window-run.json", {"pid": os.getpid(), "phase": "done"})
        self.assertEqual(self.post("/api/jobs/run/stop").status_code, 409)
        self.write("window-run.json", {"pid": 2 ** 22 + 99999, "phase": "rendering"})
        self.assertEqual(self.post("/api/jobs/run/stop").status_code, 409)
        self.assertEqual(self.killed, [])


class HelperTest(unittest.TestCase):
    def test_parse_hours_bounds(self):
        for ok in (0.25, 1, 12, 7.5):
            self.assertEqual(job_run.parse_hours(ok), ok)
        self.assertEqual(job_run.parse_hours(None), 2)
        for bad in (0.24, 12.001, 0, "1", True, False):
            with self.assertRaises(job_run.RunError):
                job_run.parse_hours(bad)

    def test_nothing_in_the_module_starts_a_run_on_import(self):
        self.assertTrue(callable(job_run.default_spawn))      # defined, never called here
        with open(job_run.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(src.count("Popen("), 1)
        self.assertLess(src.index("def default_spawn"), src.index("Popen("))


if __name__ == "__main__":
    unittest.main()
