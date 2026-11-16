"""The GPU acquire waits as long as the server's own start budget, and a server error reason reaches the run's reasons."""
import io
import shutil
import tempfile
import unittest
import urllib.error
from unittest import mock

from scripts.jobs import runner
from scripts.slot_fill import pf_client


class FakeStore:
    def __init__(self, root):
        self.root = root

    def list(self, state=None):
        return []

    def set_state(self, rec_id, state, extra=None):
        return {}


class SlowGoodClient:
    """A gpu client whose acquire takes longer than the old 180 s limit and still succeeds."""

    def __init__(self, clock):
        self.clock = clock
        self.released = False
        self.acquired = False

    def gpu_acquire(self, owner):
        self.clock[0] += 200
        self.acquired = True

    def gpu_release(self):
        self.released = True

    def models(self):
        return []


class FailingClient:
    def gpu_acquire(self, owner):
        raise pf_client.SlotFillError("PromptForge /api/gpu/acquire answered 500: ComfyUI did not become ready within 300s")

    def gpu_release(self):
        pass

    def models(self):
        return []


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


class GpuAcquireWaitTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = FakeStore(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_budget_is_grounded_and_above_the_old_limit(self):
        # 300 (comfy_proc.py:63) + 90 (gpu_session.py:47) + 45 (gpu_session.py:48) + 60 margin
        self.assertEqual(pf_client.GPU_ACQUIRE_WAIT_S, 495)
        self.assertGreater(pf_client.GPU_ACQUIRE_WAIT_S, 180)

    def test_the_acquire_request_uses_the_full_budget_as_its_timeout(self):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["timeout"] = timeout
            return _Resp()

        with mock.patch.object(pf_client.urllib.request, "urlopen", fake_urlopen):
            pf_client.PFClient(base_url="http://x").gpu_acquire("me")
        self.assertEqual(seen["timeout"], pf_client.GPU_ACQUIRE_WAIT_S)

    def test_a_slow_acquire_that_succeeds_lets_the_runner_proceed(self):
        clock = [0]
        pf = SlowGoodClient(clock)
        code = runner.run(self.store, pf, out=lambda m: None, sleep=lambda s: None, now=lambda: clock[0])
        self.assertTrue(pf.acquired)
        self.assertEqual(code, 0)
        self.assertTrue(pf.released)
        self.assertEqual(clock[0], 200)

    def test_a_server_error_reason_is_copied_into_the_run_reasons(self):
        reasons = []
        msgs = []

        def fake_urlopen(req, timeout=None):
            body = io.BytesIO(b'{"reason": "ComfyUI did not become ready within 300s"}')
            raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", None, body)

        pf = pf_client.PFClient(base_url="http://x")
        with mock.patch.object(pf_client.urllib.request, "urlopen", fake_urlopen):
            code = runner.run(self.store, pf, out=msgs.append, sleep=lambda s: None, reasons=reasons)
        self.assertEqual(code, 1)
        self.assertEqual(len(reasons), 1)
        self.assertIn("ComfyUI did not become ready within 300s", reasons[0])
        self.assertIn("could not take the GPU session", reasons[0])

    def test_a_failing_fake_client_reason_is_copied_into_the_run_reasons(self):
        reasons = []
        code = runner.run(self.store, FailingClient(), out=lambda m: None, sleep=lambda s: None, reasons=reasons)
        self.assertEqual(code, 1)
        self.assertIn("ComfyUI did not become ready within 300s", reasons[0])


if __name__ == "__main__":
    unittest.main()
