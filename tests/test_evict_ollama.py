"""comfy_proc.evict_ollama_models: asks Ollama to unload each model (keep_alive 0), concurrently, never raising.

SANDBOXED: urllib.request.urlopen is replaced for every test and OLLAMA_URL points at a closed port, so nothing here can reach a real Ollama or touch a GPU,
lease or ComfyUI.

Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from core import comfy_proc as cp  # noqa: E402


class FakeUrlopen:
    """Stands in for urllib.request.urlopen: records each request, tracks how many calls are in flight at once, and can fail chosen models."""

    def __init__(self, fail=(), delay=0.0):
        self.fail, self.delay = set(fail), delay
        self.requests = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode())
        with self._lock:
            self.requests.append((req.full_url, req.get_method(), body, timeout))
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(self.delay)
            if body["model"] in self.fail:
                raise OSError("simulated Ollama failure")
            resp = mock.MagicMock()
            resp.__enter__.return_value = resp
            resp.read.return_value = b"{}"
            return resp
        finally:
            with self._lock:
                self.in_flight -= 1


class EvictOllamaModelsTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(cp, "OLLAMA_URL", "http://127.0.0.1:9")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, names, **kw):
        fake = FakeUrlopen(**kw)
        with mock.patch.object(cp.urllib.request, "urlopen", fake):
            cp.evict_ollama_models(names)
        return fake

    def test_every_named_model_gets_one_unload_request(self):
        fake = self._run(["a:1", "b:2", "c:3"])
        self.assertEqual(sorted(r[2]["model"] for r in fake.requests), ["a:1", "b:2", "c:3"])
        for url, method, body, timeout in fake.requests:
            self.assertEqual(url, "http://127.0.0.1:9/api/generate")
            self.assertEqual(method, "POST")
            self.assertEqual((body["keep_alive"], body["prompt"]), (0, ""))
            self.assertEqual(timeout, 10)

    def test_empty_and_missing_names_are_skipped_and_an_empty_list_makes_no_request(self):
        self.assertEqual(len(self._run(["a:1", "", None, "b:2"]).requests), 2)
        self.assertEqual(self._run([]).requests, [])

    def test_the_requests_run_concurrently_but_never_more_than_five_at_once(self):
        fake = self._run([f"m{i}:1" for i in range(8)], delay=0.15)
        self.assertGreaterEqual(fake.max_in_flight, 2, "eight models took the serial path")
        self.assertLessEqual(fake.max_in_flight, 5)
        self.assertEqual(len(fake.requests), 8)

    def test_one_failing_model_does_not_stop_the_others_or_raise(self):
        fake = self._run(["a:1", "bad:2", "c:3"], fail={"bad:2"})
        self.assertEqual(sorted(r[2]["model"] for r in fake.requests), ["a:1", "bad:2", "c:3"])


if __name__ == "__main__":
    unittest.main()
