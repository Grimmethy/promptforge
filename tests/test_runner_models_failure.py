"""scripts/jobs/runner.py: pf.models() failures must be logged and abort the run.

The previous silent-swallow (except Exception: available = set()) was changed to
log the error and return 1, consistent with the gpu_acquire() block above it.
This test pins that contract so the regression cannot come back.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.jobs import runner  # noqa: E402


class FakeStore:
    """Minimal store stand-in: list() returns what the test sets, set_state() records calls."""

    def __init__(self):
        self.root = "/tmp/fake-store"
        self.set_state_calls = []

    def list(self, state=None):
        return []

    def set_state(self, *a, **k):
        self.set_state_calls.append((a, k))
        return {"id": a[0] if a else k.get("id", "")}


class TestModelsFailure(unittest.TestCase):
    def test_models_failure_logs_and_returns_1(self):
        """When pf.models() raises, the runner must log the failure and return 1."""
        store = FakeStore()
        pf = mock.MagicMock()
        pf.models.side_effect = ConnectionError("ComfyUI not reachable")
        pf.gpu_acquire.return_value = True
        pf.gpu_release.return_value = None

        reasons = []
        logged = []

        def out(msg):
            logged.append(msg)

        code = runner.run(
            store,
            pf,
            limit=0,
            reasons=reasons,
            out=out,
            sleep=lambda s: None,
            now=lambda: 1_700_000_000,
            wait=lambda *a, **k: True,
        )

        self.assertEqual(
            code,
            1,
            "runner must return 1 when pf.models() fails",
        )
        self.assertTrue(
            any("could not list models from ComfyUI" in m for m in logged),
            "runner must log the models failure; logged: %r" % logged,
        )
        self.assertTrue(
            any("could not list models from ComfyUI" in r for r in reasons),
            "runner must append to reasons; reasons: %r" % reasons,
        )

    def test_models_failure_no_reasons_list_still_logs(self):
        """Even without a reasons list, the failure must still be logged."""
        store = FakeStore()
        pf = mock.MagicMock()
        pf.models.side_effect = ValueError("bad model name")
        pf.gpu_acquire.return_value = True
        pf.gpu_release.return_value = None

        logged = []

        def out(msg):
            logged.append(msg)

        code = runner.run(
            store,
            pf,
            limit=0,
            reasons=None,
            out=out,
            sleep=lambda s: None,
            now=lambda: 1_700_000_000,
            wait=lambda *a, **k: True,
        )

        self.assertEqual(code, 1)
        self.assertTrue(
            any("could not list models from ComfyUI" in m for m in logged),
            "runner must log the failure even without reasons list; logged: %r" % logged,
        )

    def test_models_success_continues_run(self):
        """When pf.models() succeeds, the run must proceed normally."""
        store = FakeStore()
        pf = mock.MagicMock()
        pf.models.return_value = ["model_a", "model_b"]
        pf.gpu_acquire.return_value = True
        pf.gpu_release.return_value = None

        logged = []

        def out(msg):
            logged.append(msg)

        code = runner.run(
            store,
            pf,
            limit=0,
            reasons=[],
            out=out,
            sleep=lambda s: None,
            now=lambda: 1_700_000_000,
            wait=lambda *a, **k: True,
        )

        self.assertEqual(code, 0, "runner must return 0 when models() succeeds")


if __name__ == "__main__":
    unittest.main()
