"""How PromptForge picks between its in-process Forge GPU session and the standalone broker (GPU_BROKER_URL), in comfy.py and the /api/gpu routes.
Everything that could reach the GPU, ComfyUI, the lease file or a real broker is patched.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import os
import shutil
import socket
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import app as pf_app  # noqa: E402
from core import comfy, comfy_proc, gpu_client, gpu_session  # noqa: E402


def broker_mode(on):
    return mock.patch.object(gpu_client, "enabled", return_value=on)


class SafeCase(unittest.TestCase):
    """Safety net for every test below: nothing here may reach the real GPU session, ComfyUI, Ollama, the lease file or a real broker, even when the
    code under test is broken (a mutated copy once reached the real gpu_session.begin and started ComfyUI). Dangerous calls raise by default and the
    paths and URLs point at nothing; a test that needs one of them patches it again on top."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        forbidden = AssertionError("a test reached the real GPU machinery")
        patches = [
            mock.patch.object(comfy_proc, "COMFY_LEASE_PATH", os.path.join(tmp, "lease.json")),
            mock.patch.object(comfy_proc, "COMFY_PID_PATH", os.path.join(tmp, "comfyui.pid")),
            mock.patch.object(comfy_proc, "COMFY_URL", "http://127.0.0.1:9"),
            mock.patch.object(comfy_proc, "OLLAMA_URL", "http://127.0.0.1:9"),
            mock.patch.dict(os.environ, {"GPU_BROKER_URL": "http://127.0.0.1:9"}),
        ]
        for owner, names in ((gpu_session, ("begin", "touch", "end", "yield_to_pipeline", "status")),
                             (comfy_proc, ("spawn_comfyui", "stop_comfyui", "evict_ollama_models", "ensure_comfyui_ready", "lease_acquire")),
                             (gpu_client, ("acquire", "heartbeat", "release", "yield_to_pipeline", "status", "job_started", "job_finished"))):
            patches += [mock.patch.object(owner, n, side_effect=forbidden) for n in names]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TakeGpuTest(SafeCase):
    def test_broker_mode_acquires_then_holds_and_never_begins_a_local_session(self):
        rec = {}
        calls = []
        with broker_mode(True), \
                mock.patch.object(gpu_client, "acquire", side_effect=lambda owner, on_step=None: (calls.append(("acquire", owner)), on_step("starting ComfyUI…"))) as acquire, \
                mock.patch.object(gpu_client, "job_started", side_effect=lambda owner: calls.append(("job_started", owner))), \
                mock.patch.object(gpu_session, "begin") as begin:
            comfy._take_gpu(rec)
        begin.assert_not_called()
        self.assertEqual(calls, [("acquire", "promptforge"), ("job_started", "promptforge")])
        self.assertTrue(callable(acquire.call_args.kwargs["on_step"]))
        self.assertEqual(rec["comfy_step"], "starting ComfyUI…")          # the broker's progress reaches the job record

    def test_in_process_mode_begins_a_local_session_with_the_same_progress_hook(self):
        rec = {}
        with broker_mode(False), mock.patch.object(gpu_client, "acquire") as acquire, \
                mock.patch.object(gpu_session, "begin", side_effect=lambda owner, set_step=None: set_step("clearing the pipeline's model from the GPU…")) as begin:
            comfy._take_gpu(rec)
        acquire.assert_not_called()
        begin.assert_called_once_with("promptforge", set_step=mock.ANY)
        self.assertEqual(rec["comfy_step"], "clearing the pipeline's model from the GPU…")

    def test_a_refused_acquire_propagates_and_no_hold_is_taken(self):
        with broker_mode(True), mock.patch.object(gpu_client, "acquire", side_effect=gpu_client.BrokerError("reserved", 503)), \
                mock.patch.object(gpu_client, "job_started") as job_started:
            with self.assertRaises(gpu_client.BrokerError):
                comfy._take_gpu({})
        job_started.assert_not_called()

    def test_gpu_errors_cover_all_three_failure_types(self):
        self.assertEqual(set(comfy._gpu_errors()), {comfy_proc.ComfyUnavailable, gpu_session.SessionError, gpu_client.BrokerError})


class JobDoneTest(SafeCase):
    def test_broker_mode_releases_the_hold_only_when_the_claim_succeeded(self):
        with broker_mode(True), mock.patch.object(gpu_client, "job_finished") as finished, mock.patch.object(gpu_session, "touch") as touch:
            comfy._gpu_job_done(True)
            finished.assert_called_once_with("promptforge")
            finished.reset_mock()
            comfy._gpu_job_done(False)
            finished.assert_not_called()
        touch.assert_not_called()

    def test_in_process_mode_touches_the_session_whatever_happened(self):
        with broker_mode(False), mock.patch.object(gpu_session, "touch") as touch, mock.patch.object(gpu_client, "job_finished") as finished:
            comfy._gpu_job_done(True)
            comfy._gpu_job_done(False)
        self.assertEqual(touch.call_count, 2)
        touch.assert_called_with("promptforge")
        finished.assert_not_called()


class ReclaimedTest(SafeCase):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(comfy, "_lease_stomped", False), mock.patch.object(gpu_session, "last_end_reason", None),
                        mock.patch.object(gpu_client, "reclaimed", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_nothing_reclaimed_by_default(self):
        with broker_mode(True):
            self.assertFalse(comfy._gpu_reclaimed())

    def test_the_stomped_flag_and_the_session_end_reason_still_count(self):
        with broker_mode(False):
            with mock.patch.object(comfy, "_lease_stomped", True):
                self.assertTrue(comfy._gpu_reclaimed())
            with mock.patch.object(gpu_session, "last_end_reason", "pipeline-reclaimed"):
                self.assertTrue(comfy._gpu_reclaimed())

    def test_the_client_flag_counts_only_in_broker_mode(self):
        with mock.patch.object(gpu_client, "reclaimed", return_value=True):
            with broker_mode(True):
                self.assertTrue(comfy._gpu_reclaimed())
            with broker_mode(False):
                self.assertFalse(comfy._gpu_reclaimed())


class ForwardTest(SafeCase):
    def test_forward_returns_the_brokers_status_and_json_and_uses_the_long_timeout_only_for_acquire(self):
        with mock.patch.object(gpu_client, "_call", return_value=(409, {"error": "no active session"})) as call:
            self.assertEqual(gpu_client.forward("POST", "/api/gpu/heartbeat", {"owner": "x"}), (409, {"error": "no active session"}))
            call.assert_called_with("POST", "/api/gpu/heartbeat", {"owner": "x"}, timeout=60)
            gpu_client.forward("POST", "/api/gpu/acquire", {"owner": "x"})
            call.assert_called_with("POST", "/api/gpu/acquire", {"owner": "x"}, timeout=gpu_client.ACQUIRE_TIMEOUT_S)

    def test_an_unreachable_broker_is_a_503_with_the_reason(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        url = "http://127.0.0.1:%d" % s.getsockname()[1]
        s.close()
        with mock.patch.dict(os.environ, {"GPU_BROKER_URL": url}):
            status, payload = gpu_client.forward("GET", "/api/gpu/status")
        self.assertEqual(status, 503)
        self.assertIn("not reachable", payload["error"])


class RunWiringTest(SafeCase):
    """comfy._run itself: the claim goes through _take_gpu and the end of the job through _gpu_job_done, with the right flag."""

    def test_a_refused_claim_ends_the_job_with_the_reason_and_takes_no_hold(self):
        rec = {}
        with mock.patch.object(comfy, "_take_gpu", side_effect=gpu_client.BrokerError("the GPU is reserved by kaggleforge", 503)), \
                mock.patch.object(comfy, "_gpu_job_done") as done:
            comfy._run({"id": "job-1"}, rec, "/nonexistent")
        self.assertEqual((rec["status"], rec["error"]), ("error", "the GPU is reserved by kaggleforge"))
        done.assert_called_once_with(False)

    def test_after_a_successful_claim_the_hold_is_released_even_when_the_job_then_fails(self):
        rec = {}
        with mock.patch.object(comfy, "_take_gpu") as take, mock.patch.object(comfy, "_gpu_job_done") as done, \
                mock.patch.object(comfy, "_gpu_reclaimed", return_value=False):
            comfy._run({"id": "job-2"}, rec, "/nonexistent")     # no graph inputs: the job fails right after the claim
        take.assert_called_once_with(rec)
        self.assertEqual(rec["status"], "error")
        done.assert_called_once_with(True)

    def test_a_reclaimed_gpu_is_reported_as_such(self):
        rec = {}
        with mock.patch.object(comfy, "_take_gpu"), mock.patch.object(comfy, "_gpu_job_done"), mock.patch.object(comfy, "_gpu_reclaimed", return_value=True):
            comfy._run({"id": "job-3"}, rec, "/nonexistent")
        self.assertIn("reclaimed the GPU", rec["error"])


ROUTES = (("GET", "status"), ("POST", "acquire"), ("POST", "heartbeat"), ("POST", "release"), ("POST", "yield"))


class RouteTest(SafeCase):
    def setUp(self):
        super().setUp()
        self.client = pf_app.app.test_client()

    def test_in_broker_mode_every_gpu_route_proxies_and_never_touches_the_local_session(self):
        for method, name in ROUTES:
            with broker_mode(True), mock.patch.object(gpu_client, "forward", return_value=(418, {"via": "broker", "route": name})) as forward, \
                    mock.patch.object(gpu_session, "status") as status, mock.patch.object(gpu_session, "begin") as begin, \
                    mock.patch.object(gpu_session, "touch") as touch, mock.patch.object(gpu_session, "end") as end, \
                    mock.patch.object(gpu_session, "yield_to_pipeline") as yield_:
                r = self.client.open("/api/gpu/" + name, method=method, json={"owner": "videoforge"} if method == "POST" else None)
            self.assertEqual((r.status_code, r.get_json()), (418, {"via": "broker", "route": name}), name)
            expected_body = {"owner": "videoforge"} if method == "POST" else None
            forward.assert_called_once_with(method, "/api/gpu/" + name, expected_body)
            for local in (status, begin, touch, end, yield_):
                local.assert_not_called()

    def test_in_process_mode_the_routes_use_the_local_session_as_before(self):
        with broker_mode(False), mock.patch.object(gpu_client, "forward") as forward, \
                mock.patch.object(gpu_session, "status", return_value={"active": False}), \
                mock.patch.object(gpu_session, "begin", return_value={"started_session": True}) as begin, \
                mock.patch.object(gpu_session, "touch", return_value=True) as touch, \
                mock.patch.object(gpu_session, "end", return_value=True) as end, \
                mock.patch.object(comfy_proc, "comfy_up", return_value=False), \
                mock.patch.object(gpu_session, "yield_to_pipeline", return_value={"yielded": True}) as yield_:
            self.assertEqual(self.client.get("/api/gpu/status").get_json(), {"active": False})
            self.assertEqual(self.client.post("/api/gpu/acquire", json={"owner": "x"}).get_json()["started_session"], True)
            self.assertEqual(self.client.post("/api/gpu/heartbeat", json={"owner": "x"}).get_json(), {"ok": True})
            self.assertEqual(self.client.post("/api/gpu/release").get_json(), {"ended": True, "comfy_up": False})
            self.assertEqual(self.client.post("/api/gpu/yield").get_json(), {"yielded": True})
        forward.assert_not_called()
        begin.assert_called_once_with("x")
        touch.assert_called_once_with("x")
        end.assert_called_once_with("released", stop_comfy=True)
        yield_.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
