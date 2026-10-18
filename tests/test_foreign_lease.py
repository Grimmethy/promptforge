"""PromptForge never writes over (or deletes) another holder's fresh GPU lease, e.g. a KaggleForge eval's.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from core import comfy_proc as cp  # noqa: E402
from core import gpu_session as gs  # noqa: E402
from test_gpu_session import Base  # noqa: E402

P40 = "http://192.168.122.29:11434"


def kaggle_lease(path, age_s=0):
    # KaggleForge's format (server/gpu_lease.py): milliseconds, a trailing Z, an endpoints array.
    t = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - age_s)) + ".123Z"
    with open(path, "w") as f:
        json.dump({"holder": "kaggleforge", "family": "forge", "owner": "KAG-gemma-dev-0009", "endpoints": [P40],
                   "acquiredAt": t, "refreshedAt": t}, f)


class ForeignLeaseTest(Base):
    def test_begin_refuses_while_another_holder_has_a_fresh_lease(self):
        kaggle_lease(self.lease)
        with self.assertRaises(gs.SessionError) as cm:
            self.begin()
        self.assertIn("kaggleforge", str(cm.exception))
        self.assertIn("KAG-gemma-dev-0009", str(cm.exception))
        self.assertEqual(json.load(open(self.lease))["holder"], "kaggleforge")
        self.assertEqual(self.calls, [])          # nothing evicted, ComfyUI not started
        self.assertIsNone(gs._session)

    def test_begin_takes_over_a_stale_foreign_lease(self):
        kaggle_lease(self.lease, age_s=120)
        self.begin()
        self.assertEqual(json.load(open(self.lease))["holder"], "promptforge")

    def test_release_and_refresh_leave_a_foreign_lease_alone(self):
        kaggle_lease(self.lease)
        before = open(self.lease).read()
        self.assertFalse(cp.lease_refresh("job-1"))
        cp.lease_release()
        self.assertEqual(open(self.lease).read(), before)

    def test_lease_acquire_refuses(self):
        kaggle_lease(self.lease)
        with self.assertRaises(cp.ComfyUnavailable):
            cp.lease_acquire("job-1")
        self.assertEqual(json.load(open(self.lease))["holder"], "kaggleforge")

    def test_own_lease_is_not_foreign(self):
        gs.write_lease("promptforge")
        self.assertIsNone(cp.foreign_lease())


class ComfyStartTest(unittest.TestCase):
    """ensure_comfyui_ready is the one place ComfyUI is spawned (begin() and /api/comfy/start both go through it)."""

    def test_does_not_start_comfyui_on_a_reserved_gpu_even_with_room(self):
        import tempfile
        d = tempfile.mkdtemp()
        lease = os.path.join(d, "lease.json")
        kaggle_lease(lease)
        spawned = []
        with mock.patch.object(cp, "COMFY_LEASE_PATH", lease), mock.patch.object(cp, "comfy_up", lambda timeout=2: False), \
                mock.patch.object(cp, "plan_comfyui_placement", lambda: mock.Mock(need_eviction=False, target_gpu=0, evict_names=[])), \
                mock.patch.object(cp, "spawn_comfyui", lambda gpu: spawned.append(gpu)):
            with self.assertRaises(cp.ComfyUnavailable):
                cp.ensure_comfyui_ready()
        self.assertEqual(spawned, [])

    def test_parses_both_stamp_formats(self):
        now = time.time()
        for s in (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + ".500Z"):
            self.assertLess(abs(cp._iso_ts(s) - now), 2, s)
        self.assertIsNone(cp._iso_ts("garbage"))
        self.assertIsNone(cp._iso_ts(None))


if __name__ == "__main__":
    unittest.main()
