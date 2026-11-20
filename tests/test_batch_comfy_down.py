"""POST /api/jobs/run when ComfyUI is down: refuse with a 409 if it cannot be started, start it once if it can, and do nothing if it is already up.
comfy_up and ensure_comfyui_ready are patched, spawn/kill are injected and time.sleep raises: no ComfyUI, no process, no network, no sleeping.
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -p 'test_batch_comfy_down.py'"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "test"))
from flask import Flask  # noqa: E402

from core import comfy_proc, jobs_routes  # noqa: E402
import test_jobs_routes  # noqa: E402


class BatchComfyDownTest(test_jobs_routes.RoutesBase):
    def setUp(self):
        super().setUp()
        self.spawned, self.killed = [], []
        app = Flask(__name__)
        jobs_routes.register(app, lambda: self.store, spawn=lambda store, hours: self.spawned.append(hours) or 4242,
                             kill=lambda pid, sig: self.killed.append((pid, sig)))
        self.client = app.test_client()
        self.file_job()          # a queued job, so a 409 can only come from the ComfyUI gate and not from "nothing queued"
        p = mock.patch("time.sleep", side_effect=AssertionError("a test reached real time.sleep"))
        p.start()
        self.addCleanup(p.stop)

    def patch_comfy(self, up, ensure):
        for name, repl in (("comfy_up", mock.Mock(return_value=up)), ("ensure_comfyui_ready", ensure)):
            p = mock.patch.object(comfy_proc, name, repl)
            p.start()
            self.addCleanup(p.stop)

    def test_start_fails_is_409_with_reason_and_nothing_spawns(self):
        exc = comfy_proc.ComfyUnavailable("boom")
        exc.reason, exc.log_tail = "boom", ["line1", "line2"]
        ensure = mock.Mock(side_effect=exc)
        self.patch_comfy(False, ensure)
        r = self.post("/api/jobs/run", {"hours": 2})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"], "ComfyUI is down and could not be started: boom")
        self.assertEqual(r.get_json()["log_tail"], ["line1", "line2"])
        self.assertEqual(self.spawned, [])
        ensure.assert_called_once()

    def test_start_succeeds_calls_ensure_once_and_is_202(self):
        ensure = mock.Mock(return_value={"started": True})
        self.patch_comfy(False, ensure)
        r = self.post("/api/jobs/run", {"hours": 2})
        self.assertEqual(r.status_code, 202)
        ensure.assert_called_once()
        self.assertEqual(self.spawned, [2])

    def test_already_up_does_not_call_ensure(self):
        ensure = mock.Mock()
        self.patch_comfy(True, ensure)
        r = self.post("/api/jobs/run", {"hours": 2})
        self.assertEqual(r.status_code, 202)
        ensure.assert_not_called()
        self.assertEqual(self.spawned, [2])


if __name__ == "__main__":
    unittest.main()
