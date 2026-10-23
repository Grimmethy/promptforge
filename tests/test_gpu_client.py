"""PromptForge's client of the standalone GPU broker (server/core/gpu_client.py), tested against a fake broker on a local port.
Nothing here touches a real broker, the GPU, ComfyUI or the lease file.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from core import gpu_client  # noqa: E402


class FakeBroker:
    """Records every request and answers the five /api/gpu routes from fields a test can set."""

    def __init__(self):
        self.requests = []
        self.acquire_answer = (200, {"started_session": True, "evicted": []})
        self.acquire_steps = []        # steps shown in status while the acquire is held, each for step_hold seconds
        self.step_hold = 0.2
        self.current_step = None
        self.heartbeat_status = 200
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, payload):
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}") if n else {}

            def do_GET(self):
                broker.requests.append(("GET", self.path, None))
                steps = {"promptforge": broker.current_step} if broker.current_step else {}
                self._reply(200, {"active": False, "acquire_steps": steps})

            def do_POST(self):
                body = self._body()
                broker.requests.append(("POST", self.path, body))
                if self.path == "/api/gpu/acquire":
                    for step in broker.acquire_steps:
                        broker.current_step = step
                        time.sleep(broker.step_hold)
                    broker.current_step = None
                    return self._reply(*broker.acquire_answer)
                if self.path == "/api/gpu/heartbeat":
                    return self._reply(broker.heartbeat_status, {"ok": True} if broker.heartbeat_status == 200 else {"error": "no active session"})
                if self.path == "/api/gpu/release":
                    return self._reply(200, {"ended": True, "comfy_up": False})
                if self.path == "/api/gpu/yield":
                    return self._reply(200, {"yielded": True, "reason": "forge session ended"})
                self._reply(404, {"error": "unknown"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def count(self, method, path):
        return sum(1 for m, p, _b in self.requests if (m, p) == (method, path))

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def closed_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    url = "http://127.0.0.1:%d" % s.getsockname()[1]
    s.close()
    return url


def beating_threads():
    return [t for t in threading.enumerate() if t.name == "gpu-broker-heartbeat" and t.is_alive()]


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.broker = FakeBroker()
        self.addCleanup(self.broker.stop)
        env = mock.patch.dict(os.environ, {"GPU_BROKER_URL": self.broker.url})
        env.start()
        self.addCleanup(env.stop)
        for name, value in (("STEP_POLL_S", 0.03), ("HEARTBEAT_S", 0.03)):
            p = mock.patch.object(gpu_client, name, value)
            p.start()
            self.addCleanup(p.stop)
        # the job hold is module state: start every test from nothing held
        gpu_client._holds, gpu_client._hold_stop, gpu_client._reclaimed = 0, None, False
        self.addCleanup(self._stop_beats)

    def _stop_beats(self):
        if gpu_client._hold_stop is not None:
            gpu_client._hold_stop.set()
        wait_for(lambda: not beating_threads())

    # ---- configuration
    def test_enabled_and_base_url(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("GPU_BROKER_URL", None)
            self.assertEqual((gpu_client.enabled(), gpu_client.base_url()), (False, ""))
            os.environ["GPU_BROKER_URL"] = "http://x:1/"
            self.assertEqual((gpu_client.enabled(), gpu_client.base_url()), (True, "http://x:1"))
            os.environ["GPU_BROKER_URL"] = "   "
            self.assertEqual((gpu_client.enabled(), gpu_client.base_url()), (False, ""))

    # ---- acquire
    def test_acquire_posts_the_owner_and_returns_the_payload(self):
        self.assertEqual(gpu_client.acquire("promptforge"), {"started_session": True, "evicted": []})
        self.assertEqual(self.broker.requests, [("POST", "/api/gpu/acquire", {"owner": "promptforge"})])

    def test_a_refusal_raises_with_the_brokers_reason_and_status(self):
        self.broker.acquire_answer = (503, {"error": "the GPU is reserved by kaggleforge"})
        with self.assertRaises(gpu_client.BrokerError) as c:
            gpu_client.acquire("promptforge")
        self.assertEqual((str(c.exception), c.exception.status), ("the GPU is reserved by kaggleforge", 503))

    def test_an_unreachable_broker_raises_not_reachable(self):
        with mock.patch.dict(os.environ, {"GPU_BROKER_URL": closed_port_url()}):
            with self.assertRaises(gpu_client.BrokerError) as c:
                gpu_client.acquire("promptforge")
        self.assertIn("not reachable", str(c.exception))

    def test_on_step_sees_each_distinct_step_in_order_once_and_the_poller_stops(self):
        self.broker.acquire_steps = ["clearing the pipeline's model from the GPU…", "starting ComfyUI…"]
        seen = []
        gpu_client.acquire("promptforge", on_step=seen.append)
        self.assertEqual(seen, ["clearing the pipeline's model from the GPU…", "starting ComfyUI…"])
        polls = self.broker.count("GET", "/api/gpu/status")
        self.assertGreaterEqual(polls, 2)
        time.sleep(0.2)
        self.assertEqual(self.broker.count("GET", "/api/gpu/status"), polls)

    def test_an_on_step_that_raises_does_not_break_the_acquire(self):
        self.broker.acquire_steps = ["starting ComfyUI…"]

        def boom(_text):
            raise RuntimeError("display bug")

        self.assertEqual(gpu_client.acquire("promptforge", on_step=boom)["started_session"], True)

    # ---- the other routes
    def test_heartbeat_is_true_on_200_and_false_on_409(self):
        self.assertTrue(gpu_client.heartbeat("promptforge"))
        self.broker.heartbeat_status = 409
        self.assertFalse(gpu_client.heartbeat("promptforge"))

    def test_release_yield_and_status_return_the_payloads(self):
        self.assertEqual(gpu_client.release(), {"ended": True, "comfy_up": False})
        self.assertEqual(gpu_client.yield_to_pipeline(), {"yielded": True, "reason": "forge session ended"})
        self.assertEqual(gpu_client.status()["active"], False)

    # ---- job hold
    def test_a_held_job_sends_heartbeats_and_finishing_sends_a_last_one(self):
        gpu_client.job_started("promptforge")
        self.assertTrue(wait_for(lambda: self.broker.count("POST", "/api/gpu/heartbeat") >= 2))
        before = self.broker.count("POST", "/api/gpu/heartbeat")
        gpu_client.job_finished("promptforge")
        self.assertTrue(wait_for(lambda: not beating_threads()))
        after = self.broker.count("POST", "/api/gpu/heartbeat")
        self.assertGreaterEqual(after, before + 1)          # the final heartbeat
        time.sleep(0.15)
        self.assertEqual(self.broker.count("POST", "/api/gpu/heartbeat"), after)   # and then silence

    def test_nested_holds_share_one_thread_until_the_last_job_finishes(self):
        gpu_client.job_started("promptforge")
        gpu_client.job_started("promptforge")
        self.assertEqual(len(beating_threads()), 1)
        gpu_client.job_finished("promptforge")
        time.sleep(0.1)
        self.assertEqual(len(beating_threads()), 1)
        gpu_client.job_finished("promptforge")
        self.assertTrue(wait_for(lambda: not beating_threads()))
        gpu_client.job_finished("promptforge")              # one too many never goes below zero or raises
        self.assertEqual(gpu_client._holds, 0)

    def test_a_409_sets_reclaimed_and_the_next_first_job_clears_it(self):
        self.broker.heartbeat_status = 409
        gpu_client.job_started("promptforge")
        self.assertTrue(wait_for(gpu_client.reclaimed))
        gpu_client.job_finished("promptforge")
        self.assertTrue(wait_for(lambda: not beating_threads()))
        self.assertTrue(gpu_client.reclaimed())             # still reported after the job, for the error message
        self.broker.heartbeat_status = 200
        gpu_client.job_started("promptforge")
        self.assertFalse(gpu_client.reclaimed())

    def test_a_broker_that_is_down_never_raises_from_the_hold(self):
        with mock.patch.dict(os.environ, {"GPU_BROKER_URL": closed_port_url()}):
            gpu_client.job_started("promptforge")
            time.sleep(0.1)
            gpu_client.job_finished("promptforge")
            self.assertTrue(wait_for(lambda: not beating_threads()))
            self.assertFalse(gpu_client.reclaimed())


if __name__ == "__main__":
    unittest.main()
