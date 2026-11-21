"""ComfyUI start bookkeeping (server/core/comfy_proc.py): comfy_started_at() is recorded on a
cold start, and a failed start raises ComfyUnavailable carrying a reason and the log tail.

Run: .venv/bin/python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import time
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from core import comfy_proc as cp  # noqa: E402

_NAMES = (
    "comfy_up", "spawn_comfyui", "refuse_if_foreign", "plan_comfyui_placement",
    "lease_acquire", "lease_release", "evict_ollama_models", "gpu_inventory",
    "COMFY_LOG", "COMFY_START_TIMEOUT_S", "_comfy_started_at",
)


class StartBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {n: getattr(cp, n) for n in _NAMES}
        self.saved_sleep = cp.time.sleep
        self.released = 0
        self.spawned = []
        self.up = [False]
        cp.COMFY_LOG = os.path.join(self.tmp.name, "comfy.log")
        cp.COMFY_START_TIMEOUT_S = 0          # a not-ready start fails immediately
        cp.time.sleep = lambda s: None
        cp._comfy_started_at = None
        cp.comfy_up = lambda timeout=2: self.up[0]
        cp.refuse_if_foreign = lambda: None
        cp.plan_comfyui_placement = lambda: types.SimpleNamespace(
            need_eviction=True, evict_names=["m"], target_gpu=0)
        cp.lease_acquire = lambda job_id=None: True
        cp.lease_release = self._release
        cp.evict_ollama_models = lambda names: None
        cp.gpu_inventory = lambda: [{"index": 0, "free_mb": 99999, "total_mb": 99999}]
        cp.spawn_comfyui = self._spawn

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(cp, n, v)
        cp.time.sleep = self.saved_sleep
        self.tmp.cleanup()

    def _release(self):
        self.released += 1

    def _spawn(self, gpu):
        self.spawned.append(gpu)

    def write_log(self, n):
        with open(cp.COMFY_LOG, "w") as f:
            for i in range(n):
                f.write(f"line {i}\n")


class StartedAt(StartBase):
    def test_none_before_any_start(self):
        self.assertIsNone(cp.comfy_started_at())

    def test_set_on_cold_start(self):
        cp.COMFY_START_TIMEOUT_S = 5
        cp.spawn_comfyui = lambda gpu: self.up.__setitem__(0, True)
        before = time.time()
        r = cp.ensure_comfyui_ready(job_id="j")
        self.assertTrue(r["started"])
        t = cp.comfy_started_at()
        self.assertIsInstance(t, float)
        self.assertGreaterEqual(t, before)

    def test_unchanged_when_already_up(self):
        cp._comfy_started_at = 123.0
        self.up[0] = True
        r = cp.ensure_comfyui_ready(job_id="j")
        self.assertFalse(r["started"])
        self.assertEqual(self.spawned, [])
        self.assertEqual(cp.comfy_started_at(), 123.0)

    def test_reset_on_failed_restart(self):
        cp._comfy_started_at = 123.0
        with self.assertRaises(cp.ComfyUnavailable):
            cp.ensure_comfyui_ready(job_id="j")
        self.assertIsNone(cp.comfy_started_at())


class FailureDetails(StartBase):
    def test_timeout_has_reason_log_tail_and_releases_lease(self):
        self.write_log(100)
        with self.assertRaises(cp.ComfyUnavailable) as cm:
            cp.ensure_comfyui_ready(job_id="j")
        exc = cm.exception
        self.assertIsInstance(exc.reason, str)
        self.assertTrue(exc.reason)
        self.assertIsInstance(exc.log_tail, list)
        self.assertTrue(all(isinstance(x, str) for x in exc.log_tail))
        self.assertTrue(0 < len(exc.log_tail) <= 40)
        self.assertEqual(exc.log_tail[-1].strip(), "line 99")
        self.assertEqual(self.released, 1)
        self.assertIn("did not become ready", str(exc))

    def test_oserror_is_wrapped(self):
        self.write_log(3)

        def boom(gpu):
            raise OSError("boom")
        cp.spawn_comfyui = boom
        with self.assertRaises(cp.ComfyUnavailable) as cm:
            cp.ensure_comfyui_ready(job_id="j")
        exc = cm.exception
        self.assertIsInstance(exc.__cause__, OSError)
        self.assertIn("boom", str(exc))
        self.assertTrue(exc.reason)
        self.assertEqual(exc.log_tail[-1].strip(), "line 2")
        self.assertEqual(self.released, 1)

    def test_missing_log_file_gives_empty_tail(self):
        self.assertFalse(os.path.exists(cp.COMFY_LOG))
        self.assertEqual(cp._log_tail_lines(), [])
        with self.assertRaises(cp.ComfyUnavailable) as cm:
            cp.ensure_comfyui_ready(job_id="j")
        self.assertEqual(cm.exception.log_tail, [])

    def test_log_tail_lines_caps_at_n(self):
        self.write_log(100)
        self.assertEqual(len(cp._log_tail_lines(5)), 5)
        self.assertEqual(len(cp._log_tail_lines()), 40)


class BackwardCompat(unittest.TestCase):
    def test_message_only_construction(self):
        exc = cp.ComfyUnavailable("x")
        self.assertEqual(str(exc), "x")
        self.assertIsInstance(exc, RuntimeError)
        self.assertEqual(exc.log_tail, [])


if __name__ == "__main__":
    unittest.main()
