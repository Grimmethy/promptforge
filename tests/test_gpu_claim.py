"""Generation GPU claim (server/core/comfy_proc.py): every generation takes the shared lease,
evicts the pipeline's resident model when ComfyUI would not fit beside it, and refuses when host
RAM is too low. Found 2026-10-12: with ComfyUI already up nothing was claimed, so the pipeline's
19.9 GB model left ComfyUI ~3.3 GB and the job crawled while the box thrashed.

Run: .venv/bin/python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from core import comfy_proc as cp  # noqa: E402

GPU_TIGHT = [{"index": 0, "free_mb": 3300, "total_mb": 24000}]
GPU_ROOMY = [{"index": 0, "free_mb": 15000, "total_mb": 24000}]
QWEN = [{"name": "qwen3.8:27b", "size_vram_mb": 19000}]


class ModelsToEvict(unittest.TestCase):
    def test_tight_gpu_evicts_resident_models(self):
        self.assertEqual(cp.models_to_evict(GPU_TIGHT, QWEN), ["qwen3.8:27b"])

    def test_roomy_gpu_evicts_nothing(self):
        self.assertEqual(cp.models_to_evict(GPU_ROOMY, QWEN), [])

    def test_nothing_resident_or_no_gpu_visibility(self):
        self.assertEqual(cp.models_to_evict(GPU_TIGHT, []), [])
        self.assertEqual(cp.models_to_evict([], QWEN), [])


class ClaimGpu(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (cp.COMFY_LEASE_PATH, cp.evict_ollama_models, cp.COMFY_MIN_AVAILABLE_RAM_MB, cp.COMFY_RAM_WAIT_S)
        cp.COMFY_LEASE_PATH = os.path.join(self.tmp.name, "lease.json")
        self.evicted = []
        cp.evict_ollama_models = lambda names: self.evicted.extend(names)
        cp.COMFY_MIN_AVAILABLE_RAM_MB, cp.COMFY_RAM_WAIT_S = 2048, 20
        self.clock = [0.0]

    def tearDown(self):
        cp.COMFY_LEASE_PATH, cp.evict_ollama_models, cp.COMFY_MIN_AVAILABLE_RAM_MB, cp.COMFY_RAM_WAIT_S = self.saved
        self.tmp.cleanup()

    def now(self): return self.clock[0]

    def sleep(self, s): self.clock[0] += s

    def claim(self, inv, models, mem):
        return cp.claim_gpu("job1", inventory=inv, ollama_models=models, mem_mb=mem, sleep=self.sleep, now=self.now)

    def test_tight_gpu_takes_lease_and_evicts(self):
        r = self.claim(GPU_TIGHT, QWEN, 8000)
        self.assertTrue(r["lease_taken"])
        self.assertEqual(r["evicted"], ["qwen3.8:27b"])
        self.assertEqual(self.evicted, ["qwen3.8:27b"])
        self.assertTrue(cp.lease_held())

    def test_roomy_gpu_still_takes_the_lease_but_evicts_nothing(self):
        r = self.claim(GPU_ROOMY, QWEN, 8000)
        self.assertTrue(r["lease_taken"])
        self.assertEqual(self.evicted, [])
        self.assertTrue(cp.lease_held())

    def test_low_host_ram_raises_and_releases_the_lease(self):
        with self.assertRaises(cp.ComfyUnavailable) as cm:
            self.claim(GPU_ROOMY, [], 500)
        self.assertIn("host memory is too low", str(cm.exception))
        self.assertFalse(cp.lease_held())
        self.assertGreaterEqual(self.clock[0], 20)       # waited the full window first

    def test_ram_that_recovers_within_the_wait_is_accepted(self):
        readings = iter([500, 900, 5000])
        def mem(): return next(readings)
        orig = cp.mem_available_mb
        cp.mem_available_mb = mem
        try:
            r = cp.claim_gpu("job1", inventory=GPU_ROOMY, ollama_models=[], sleep=self.sleep, now=self.now)
        finally:
            cp.mem_available_mb = orig
        self.assertTrue(r["lease_taken"])

    def test_ram_guard_can_be_disabled(self):
        cp.COMFY_MIN_AVAILABLE_RAM_MB = 0
        self.assertTrue(self.claim(GPU_ROOMY, [], 10)["lease_taken"])

    def test_existing_lease_is_refreshed_not_restarted(self):
        cp.lease_acquire("old")
        first = open(cp.COMFY_LEASE_PATH).read()
        self.claim(GPU_ROOMY, [], 8000)
        import json
        data = json.load(open(cp.COMFY_LEASE_PATH))
        self.assertEqual(data["acquiredAt"], json.loads(first)["acquiredAt"])
        self.assertEqual(data["jobId"], "job1")


class EnsureGpuForGeneration(unittest.TestCase):
    def setUp(self):
        self.saved = (cp.comfy_up, cp.ensure_comfyui_ready, cp.claim_gpu, cp.COMFY_AUTOSTART)
        self.calls = []
        cp.claim_gpu = lambda job_id, set_step=None: (self.calls.append("claim") or {"lease_taken": True, "evicted": []})
        cp.ensure_comfyui_ready = lambda set_step=None, job_id=None: (self.calls.append("start") or {"started": True})

    def tearDown(self):
        cp.comfy_up, cp.ensure_comfyui_ready, cp.claim_gpu, cp.COMFY_AUTOSTART = self.saved

    def test_comfy_already_up_still_claims_without_starting(self):
        cp.comfy_up = lambda timeout=2: True
        r = cp.ensure_gpu_for_generation(job_id="j")
        self.assertEqual(self.calls, ["claim"])
        self.assertEqual((r["started"], r["lease_taken"]), (False, True))

    def test_comfy_down_starts_then_claims(self):
        cp.comfy_up = lambda timeout=2: False
        cp.COMFY_AUTOSTART = True
        r = cp.ensure_gpu_for_generation(job_id="j")
        self.assertEqual(self.calls, ["start", "claim"])
        self.assertTrue(r["started"])

    def test_autostart_off_and_comfy_down_does_nothing(self):
        cp.comfy_up = lambda timeout=2: False
        cp.COMFY_AUTOSTART = False
        r = cp.ensure_gpu_for_generation(job_id="j")
        self.assertEqual(self.calls, [])
        self.assertFalse(r["lease_taken"])


if __name__ == "__main__":
    unittest.main()
