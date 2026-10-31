"""Base class and fixtures for gpu_session tests."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core import comfy_proc as cp  # noqa: E402
from core import gpu_session as gs  # noqa: E402


class Base(unittest.TestCase):
    """Common fixtures for gpu_session tests."""

    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.mkdtemp()
        self.lease = os.path.join(self.tmpdir, "comfyui-lease.json")
        self.calls = []

        # Patch the lease path to use our temp directory
        self.lease_patch = mock.patch.object(cp, "COMFY_LEASE_PATH", self.lease)
        self.lease_patch.start()
        self.addCleanup(self.lease_patch.stop)



        # Mock inflight_pipeline_calls to return empty (no pipeline calls in progress)
        self.inflight_patch = mock.patch.object(
            gs, "inflight_pipeline_calls", return_value=[]
        )
        self.inflight_patch.start()
        self.addCleanup(self.inflight_patch.stop)

        # Mock lease_intact to return True
        self.lease_intact_patch = mock.patch.object(
            gs, "lease_intact", return_value=True
        )
        self.lease_intact_patch.start()
        self.addCleanup(self.lease_intact_patch.stop)

        # Mock ensure_comfyui_ready to avoid spawning real ComfyUI
        self.ensure_patch = mock.patch.object(
            cp, "ensure_comfyui_ready", return_value={"started": True}
        )
        self.ensure_patch.start()
        self.addCleanup(self.ensure_patch.stop)

        # Mock stop_comfyui_anywhere to avoid killing real ComfyUI
        self.stop_patch = mock.patch.object(
            gs, "stop_comfyui_anywhere", return_value=False
        )
        self.stop_patch.start()
        self.addCleanup(self.stop_patch.stop)

        # Mock _ensure_keeper to avoid starting keeper thread
        self.keeper_patch = mock.patch.object(gs, "_ensure_keeper")
        self.keeper_patch.start()
        self.addCleanup(self.keeper_patch.stop)

        # Mock _check_ram to avoid RAM checks
        self.ram_patch = mock.patch.object(gs, "_check_ram")
        self.ram_patch.start()
        self.addCleanup(self.ram_patch.stop)

        # Reset session state
        gs._session = None
        gs._keeper = None
        gs.last_end_reason = None

    def begin(self, owner="test-owner", **kwargs):
        """Helper to call begin() with common test parameters."""
        return gs.begin(owner, **kwargs)


class ComfyUpWaitTest(Base):
    """Tests for the wait-for-ComfyUI logic in begin()."""

    def test_waits_for_comfyui_when_not_yet_up(self):
        """The already-live path should wait for ComfyUI when it's not yet up."""
        call_count = [0]
        sleeps = []

        def fake_comfy_up(timeout=2):
            call_count[0] += 1
            if call_count[0] >= 3:
                return True
            return False

        def fake_sleep(duration):
            sleeps.append(duration)

        # First call: create a session (no session exists yet)
        # comfy_up returns True so we don't need to start ComfyUI
        with mock.patch.object(cp, "comfy_up", return_value=True):
            result1 = self.begin(sleep=fake_sleep)

        self.assertTrue(result1["started_session"])

        # Second call: should take the "already live" path and wait
        with mock.patch.object(cp, "comfy_up", side_effect=fake_comfy_up):
            result2 = self.begin(sleep=fake_sleep)

        self.assertEqual(call_count[0], 3)  # 3 calls in the wait loop
        self.assertEqual(sleeps, [1, 1])  # slept twice before third call succeeded
        self.assertFalse(result2["started_session"])
        self.assertFalse(result2["comfy_started"])

    def test_gives_up_after_timeout(self):
        """The wait should give up after START_WAIT_S with a clear error."""
        # First call: create a session
        with mock.patch.object(cp, "comfy_up", return_value=True):
            result1 = self.begin()
        self.assertTrue(result1["started_session"])

        # Second call: should timeout waiting for ComfyUI
        # Use a short "now" function to simulate time passing
        current_time = [0]

        def fake_now():
            current_time[0] += 100  # jump by 100 seconds each call
            return current_time[0]

        with mock.patch.object(cp, "comfy_up", return_value=False):
            with self.assertRaises(gs.SessionError) as cm:
                self.begin(now=fake_now)

        self.assertIn("timed out", str(cm.exception))
        self.assertIn("ComfyUI", str(cm.exception))

    def test_returns_immediately_when_already_up(self):
        """Should return immediately when ComfyUI is already up."""
        # First call: create a session
        with mock.patch.object(cp, "comfy_up", return_value=True):
            result1 = self.begin()
        self.assertTrue(result1["started_session"])

        # Second call: should return immediately
        with mock.patch.object(cp, "comfy_up", return_value=True):
            result2 = self.begin()

        self.assertFalse(result2["started_session"])
        self.assertFalse(result2["comfy_started"])

    def test_concurrent_heartbeat_completes_while_comfy_up_returns_false(self):
        """The wait for comfy_up happens OUTSIDE the lock, so a concurrent heartbeat/release is not blocked."""
        # First call: create a session
        with mock.patch.object(cp, "comfy_up", return_value=True):
            result1 = self.begin()
        self.assertTrue(result1["started_session"])

        # Second call: begin() will wait for comfy_up (returns False)
        # A concurrent touch() should complete quickly (not block on the lock)
        heartbeat_completed = threading.Event()
        heartbeat_result = [None]

        def do_heartbeat():
            heartbeat_result[0] = gs.touch("heartbeat-owner")
            heartbeat_completed.set()

        # Patch comfy_up to always return False (so begin() waits the full START_WAIT_S)
        with mock.patch.object(cp, "comfy_up", return_value=False):
            # Start heartbeat in a thread
            t = threading.Thread(target=do_heartbeat)
            t.start()

            # Wait for heartbeat to complete (should be fast)
            completed = heartbeat_completed.wait(timeout=2)

            self.assertTrue(completed, "heartbeat should complete within 2 seconds while comfy_up is False")
            self.assertTrue(heartbeat_result[0])  # touch returned True (session exists)
            t.join()

    def test_timeout_raises_session_error_with_correct_text(self):
        """After START_WAIT_S the wait should raise SessionError with the expected text."""
        # First call: create a session
        with mock.patch.object(cp, "comfy_up", return_value=True):
            result1 = self.begin()
        self.assertTrue(result1["started_session"])

        # Second call: should timeout
        current_time = [0]

        def fake_now():
            current_time[0] += 100  # jump by 100 seconds each call
            return current_time[0]

        with mock.patch.object(cp, "comfy_up", return_value=False), \
             self.assertRaises(gs.SessionError) as cm:
            self.begin(now=fake_now)

        self.assertIn("timed out", str(cm.exception))
        self.assertIn("ComfyUI", str(cm.exception))
