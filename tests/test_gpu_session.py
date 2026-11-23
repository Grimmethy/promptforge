"""Forge GPU session (server/core/gpu_session.py): the Forge family and the pipeline never share the GPU.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from core import comfy_proc as cp  # noqa: E402
from core import gpu_session as gs  # noqa: E402

QWEN = [{"name": "qwen3.8:27b", "size_vram_mb": 16000}]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lease = os.path.join(self.tmp, "lease.json")
        self.locks = os.path.join(self.tmp, "locks")
        os.makedirs(self.locks)
        self.calls = []
        self.resident = list(QWEN)
        self.comfy_up = False
        self.busy = False
        self.queue_busy = False
        ps = [
            mock.patch.object(cp, "COMFY_LEASE_PATH", self.lease),
            mock.patch.object(gs, "LOCKS_DIR", self.locks),
            mock.patch.object(cp, "ollama_models_on_gpu", lambda: list(self.resident)),
            mock.patch.object(cp, "evict_ollama_models", self._evict),
            mock.patch.object(cp, "comfy_up", lambda timeout=2: self.comfy_up),
            mock.patch.object(cp, "ensure_comfyui_ready", self._start_comfy),
            mock.patch.object(cp, "mem_available_mb", lambda: 12000),
            mock.patch.object(cp, "gpu_inventory", lambda: [{"index": 0, "free_mb": 20000, "total_mb": 24000}]),
            mock.patch.object(gs, "stop_comfyui_anywhere", self._stop),
            mock.patch.object(gs, "_ensure_keeper", lambda: self.calls.append(("keeper",))),
            mock.patch.object(gs, "comfy_queue_busy", lambda: self.queue_busy),
        ]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        gs.set_activity_probe(lambda: self.busy)
        gs._session = None
        self.addCleanup(lambda: setattr(gs, "_session", None))

    def _evict(self, names):
        self.calls.append(("evict", list(names)))
        self.resident = []

    def _start_comfy(self, set_step=None, job_id=None):
        self.calls.append(("start_comfy",))
        self.comfy_up = True
        return {"started": True}

    def _stop(self, *a, **k):
        self.calls.append(("stop_comfy",))
        self.comfy_up = False
        return True

    def begin(self, owner="promptforge"):
        return gs.begin(owner, sleep=lambda s: None)


class BeginTests(Base):
    def test_begin_writes_lease_evicts_everything_and_starts_comfy(self):
        r = self.begin()
        self.assertTrue(r["started_session"])
        self.assertEqual(r["evicted"], ["qwen3.8:27b"])
        self.assertEqual(self.calls, [("keeper",), ("evict", ["qwen3.8:27b"]), ("start_comfy",)])
        d = json.load(open(self.lease))
        self.assertEqual((d["holder"], d["family"], d["owner"]), ("promptforge", "forge", "promptforge"))
        self.assertTrue(gs.lease_intact())

    def test_evicts_even_when_comfy_would_fit_beside_the_model(self):
        # old rule only evicted on a tight GPU; the family rule evicts always
        self.resident = [{"name": "tiny:1b", "size_vram_mb": 1500}]
        self.assertEqual(self.begin()["evicted"], ["tiny:1b"])

    def test_second_begin_is_a_heartbeat_not_a_new_session(self):
        self.begin()
        self.calls.clear()
        r = self.begin("videoforge")
        self.assertFalse(r["started_session"])
        self.assertEqual(self.calls, [])
        self.assertEqual(gs._session["owners"], ["promptforge", "videoforge"])

    def test_keeper_starts_before_the_slow_part_so_the_lease_cannot_go_stale(self):
        """The pipeline treats a lease not refreshed for 90 s as released. begin() waits up to 90 s for an in-flight call,
        evicts, and may cold-start ComfyUI for minutes, so the keeper has to be running before all of that."""
        self.begin()
        names = [c[0] for c in self.calls]
        self.assertEqual(names.count("keeper"), 1)
        self.assertLess(names.index("keeper"), names.index("evict"))
        self.assertLess(names.index("keeper"), names.index("start_comfy"))

    def test_a_failed_begin_still_clears_the_lease_with_the_early_keeper(self):
        with mock.patch.object(cp, "mem_available_mb", lambda: 100), \
                mock.patch.object(cp, "COMFY_MIN_AVAILABLE_RAM_MB", 8000), \
                mock.patch.object(cp, "COMFY_RAM_WAIT_S", 0):
            with self.assertRaises(cp.ComfyUnavailable):
                self.begin()
        self.assertFalse(os.path.exists(self.lease))
        self.assertIsNone(gs._session)

    def _two_jobs(self, fail_after_start=False):
        """Job A starts the session and blocks inside the ComfyUI start; job B arrives meanwhile. Returns (results, errors)."""
        gate, entered = threading.Event(), threading.Event()

        def slow_start(set_step=None, job_id=None):
            self.calls.append(("start_comfy",))
            entered.set()
            gate.wait(5)
            self.comfy_up = True
            return {"started": True}
        results, errors = {}, {}

        def run(name, sleep):
            try:
                results[name] = gs.begin(name, sleep=sleep)
            except Exception as exc:  # noqa: BLE001
                errors[name] = exc
        patches = [mock.patch.object(cp, "ensure_comfyui_ready", slow_start)]
        if fail_after_start:
            patches += [mock.patch.object(cp, "mem_available_mb", lambda: 100), mock.patch.object(cp, "COMFY_MIN_AVAILABLE_RAM_MB", 8000),
                        mock.patch.object(cp, "COMFY_RAM_WAIT_S", 0)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        a = threading.Thread(target=run, args=("a", lambda s: None))
        a.start()
        self.assertTrue(entered.wait(5))
        b = threading.Thread(target=run, args=("b", lambda s: time.sleep(0.01)))
        b.start()
        time.sleep(0.2)
        self.assertNotIn("b", results)           # B must NOT come back while ComfyUI is still starting
        self.assertNotIn("b", errors)
        gate.set()
        a.join(5)
        b.join(5)
        return results, errors

    def test_a_second_job_waits_for_the_first_to_finish_starting_comfyui(self):
        """Seen live 2026-10-16: four Regenerate presses while ComfyUI was cold; jobs 2-4 took the 'live session' path, skipped the
        wait and failed their first upload with Connection refused."""
        results, errors = self._two_jobs()
        self.assertEqual(errors, {})
        self.assertTrue(results["a"]["started_session"])
        self.assertFalse(results["b"]["started_session"])
        self.assertEqual([c for c in self.calls if c[0] == "start_comfy"], [("start_comfy",)])     # ComfyUI started once
        self.assertTrue(self.comfy_up)

    def test_a_waiting_job_reports_the_failure_when_the_session_fails_to_start(self):
        results, errors = self._two_jobs(fail_after_start=True)
        self.assertIsInstance(errors["a"], cp.ComfyUnavailable)
        self.assertIsInstance(errors["b"], gs.SessionError)
        self.assertIn("did not start", str(errors["b"]))
        self.assertIsNone(gs._session)

    def test_a_waiting_job_says_interrupted_when_the_pipeline_reclaims_mid_start(self):
        """The session was starting and got reclaimed (lease stomped) -- it was interrupted during start, not a start failure."""
        gate, entered = threading.Event(), threading.Event()

        def slow_start(set_step=None, job_id=None):
            self.calls.append(("start_comfy",))
            entered.set()
            gate.wait(5)
            self.comfy_up = True
            return {"started": True}
        with mock.patch.object(cp, "ensure_comfyui_ready", slow_start):
            errors = {}

            def run(name):
                try:
                    gs.begin(name, sleep=lambda s: None)
                except Exception as exc:  # noqa: BLE001
                    errors[name] = exc
            a = threading.Thread(target=run, args=("a",))
            a.start()
            self.assertTrue(entered.wait(5))
            b = threading.Thread(target=run, args=("b",))
            b.start()
            time.sleep(0.2)
            self.assertNotIn("b", errors)
            os.remove(self.lease)      # the pipeline starts: it stomps the lease while the session is still starting
            self.assertEqual(gs.keeper_tick(), "ended:pipeline-reclaimed")
            gate.set()
            a.join(5)
            b.join(5)
        self.assertIsInstance(errors.get("b"), gs.SessionError)
        self.assertIn("interrupted during start", str(errors["b"]))
        self.assertIn("pipeline-reclaimed", str(errors["b"]))
        self.assertNotIn("did not start", str(errors["b"]))

    def test_join_starting_message_distinguishes_interruption_from_failed_start(self):
        sess = {"ready": threading.Event(), "failed": "the session ended while starting (pipeline-reclaimed)"}
        sess["ready"].set()
        with self.assertRaises(gs.SessionError) as cm:
            gs._join_starting(sess, lambda m: None, lambda s: None, time.time)
        self.assertIn("interrupted during start", str(cm.exception))
        self.assertNotIn("did not start", str(cm.exception))
        sess2 = {"ready": threading.Event(), "failed": "host memory is too low"}
        sess2["ready"].set()
        with self.assertRaises(gs.SessionError) as cm:
            gs._join_starting(sess2, lambda m: None, lambda s: None, time.time)
        self.assertIn("did not start", str(cm.exception))

    def test_status_is_json_serialisable_while_a_session_is_starting_and_after(self):
        """The ready Event in the session dict made GET /api/gpu/status answer HTTP 500 ("Event is not JSON serializable")."""
        self.assertIsNone(gs.status()["session"])
        gs._session = {"owners": ["x"], "started": time.time(), "started_iso": gs._iso(), "last_activity": time.time(),
                       "max_s": 99999, "idle_s": 99999, "ready": threading.Event(), "failed": None}
        starting = json.loads(json.dumps(gs.status()))
        self.assertIs(starting["session"]["ready"], False)
        gs._session["ready"].set()
        ready = json.loads(json.dumps(gs.status()))
        self.assertIs(ready["session"]["ready"], True)
        self.assertTrue(ready["active"])

    def test_a_late_job_after_the_session_is_ready_is_still_a_heartbeat(self):
        self.begin()
        self.calls.clear()
        r = self.begin("another")
        self.assertFalse(r["started_session"])
        self.assertEqual(self.calls, [])

    def test_waiting_gives_up_after_the_start_window(self):
        gs._session = {"owners": ["x"], "started": time.time(), "started_iso": gs._iso(), "last_activity": time.time(),
                       "max_s": 99999, "idle_s": 99999, "ready": threading.Event(), "failed": None}
        gs.write_lease("x", keep_acquired=gs._iso())
        with mock.patch.object(gs, "START_WAIT_S", 0):
            with self.assertRaises(gs.SessionError):
                gs.begin("late", sleep=lambda s: None)

    def test_waits_for_pipeline_inflight_call_then_proceeds(self):
        lock = os.path.join(self.locks, "1-a.json")
        json.dump({"model": "qwen", "pid": os.getpid()}, open(lock, "w"))
        seen = {"n": 0}

        def sleeper(s):
            seen["n"] += 1
            if seen["n"] == 2:
                os.unlink(lock)          # the call finished
        gs.begin("promptforge", sleep=sleeper)
        self.assertGreaterEqual(seen["n"], 2)
        self.assertEqual(self.calls[1][0], "evict")   # (the keeper starts first) eviction only after the call drained

    def test_stale_or_dead_pid_locks_do_not_block(self):
        old = os.path.join(self.locks, "old.json")
        json.dump({"model": "q", "pid": os.getpid()}, open(old, "w"))
        os.utime(old, (time.time() - 600, time.time() - 600))
        dead = os.path.join(self.locks, "dead.json")
        json.dump({"model": "q", "pid": 2 ** 22 + 12345}, open(dead, "w"))
        self.assertEqual(gs.inflight_pipeline_calls(self.locks), [])

    def test_low_ram_after_eviction_fails_and_clears_the_lease(self):
        with mock.patch.object(cp, "mem_available_mb", lambda: 500), mock.patch.object(cp, "COMFY_RAM_WAIT_S", 0):
            with self.assertRaises(cp.ComfyUnavailable):
                self.begin()
        self.assertFalse(os.path.exists(self.lease))
        self.assertIsNone(gs._session)


class KeeperTests(Base):
    def test_keeper_refreshes_and_renews_acquired_at(self):
        self.begin()
        first = json.load(open(self.lease))
        time.sleep(1.1)
        self.assertEqual(gs.keeper_tick(), "refreshed")
        second = json.load(open(self.lease))
        self.assertGreater(second["refreshedAt"], first["refreshedAt"])
        self.assertGreater(second["acquiredAt"], first["acquiredAt"])   # defeats the pipeline's 15-min ceiling
        self.assertEqual(second["sessionStartedAt"], first["sessionStartedAt"])

    def test_pipeline_start_stomping_the_lease_ends_the_session_and_stops_comfy(self):
        self.begin()
        os.unlink(self.lease)            # scripts/launch.sh: "explicit pipeline start takes the GPU"
        self.calls.clear()
        self.assertEqual(gs.keeper_tick(), "ended:pipeline-reclaimed")
        self.assertEqual(self.calls, [("stop_comfy",)])
        self.assertIsNone(gs._session)
        self.assertFalse(os.path.exists(self.lease))   # not recreated
        self.assertEqual(gs.last_end_reason, "pipeline-reclaimed")

    def test_idle_session_releases_and_stops_comfy(self):
        self.begin()
        gs._session["last_activity"] -= gs.SESSION_IDLE_S + 5
        self.assertEqual(gs.keeper_tick(), "ended:idle")
        self.assertFalse(os.path.exists(self.lease))
        self.assertIn(("stop_comfy",), self.calls)

    def test_a_running_job_keeps_the_session_alive_past_the_idle_limit(self):
        self.begin()
        gs._session["last_activity"] -= gs.SESSION_IDLE_S + 5
        self.busy = True
        self.assertEqual(gs.keeper_tick(), "refreshed")

    def test_max_duration_ends_even_a_busy_session(self):
        self.begin()
        gs._session["started"] -= gs.SESSION_MAX_S + 5
        self.busy = True
        self.assertEqual(gs.keeper_tick(), "ended:max-duration")


class YieldAndEndTests(Base):
    def test_yield_is_refused_while_a_job_runs_and_granted_when_idle(self):
        self.begin()
        self.busy = True
        self.assertFalse(gs.yield_to_pipeline()["yielded"])
        self.assertTrue(os.path.exists(self.lease))
        self.busy = False
        self.calls.clear()
        self.assertTrue(gs.yield_to_pipeline()["yielded"])
        self.assertEqual(self.calls, [("stop_comfy",)])
        self.assertFalse(os.path.exists(self.lease))

    def test_yield_is_refused_while_comfyui_itself_is_rendering_even_with_no_session(self):
        self.comfy_up = True
        self.queue_busy = True           # e.g. ComfyUI launched by hand with a job in its queue
        r = gs.yield_to_pipeline()
        self.assertFalse(r["yielded"])
        self.assertEqual(self.calls, [])

    def test_yield_without_a_session_still_stops_a_stray_comfyui(self):
        self.comfy_up = True
        r = gs.yield_to_pipeline()
        self.assertTrue(r["yielded"])
        self.assertEqual(self.calls, [("stop_comfy",)])

    def _started(self, age_s):
        p = mock.patch.object(cp, "comfy_started_at", lambda: time.time() - age_s, create=True)
        p.start()
        self.addCleanup(p.stop)

    def test_grace_constant_exceeds_start_timeout(self):
        self.assertGreater(gs.COMFY_START_GRACE_S, cp.COMFY_START_TIMEOUT_S)

    def test_yield_is_refused_within_start_grace_and_comfy_is_not_stopped(self):
        self.comfy_up = True
        self._started(10)
        r = gs.yield_to_pipeline()
        self.assertFalse(r["yielded"])
        self.assertIn("grace", r["reason"])
        self.assertEqual(self.calls, [])
        ev = gs.get_last_guard_event()
        self.assertEqual(ev["action"], "refused")
        self.assertEqual(set(ev), {"actor", "reason", "at", "action"})

    def test_yield_is_granted_outside_grace_when_idle_and_records_the_stop(self):
        self.comfy_up = True
        self._started(gs.COMFY_START_GRACE_S + 5)
        r = gs.yield_to_pipeline()
        self.assertTrue(r["yielded"])
        self.assertEqual(self.calls, [("stop_comfy",)])
        ev = gs.get_last_guard_event()
        self.assertEqual(ev["action"], "stopped")
        self.assertIsNotNone(ev["at"])

    def test_yield_refusal_for_a_running_job_is_recorded(self):
        self.comfy_up = True
        self.queue_busy = True
        self.assertFalse(gs.yield_to_pipeline()["yielded"])
        self.assertEqual(gs.get_last_guard_event()["action"], "refused")
        self.assertEqual(self.calls, [])

    def test_end_and_touch(self):
        self.assertFalse(gs.touch("x"))
        self.begin()
        self.assertTrue(gs.touch("videoforge"))
        self.assertTrue(gs.end("released"))
        self.assertFalse(gs.end("again"))

    def test_begin_after_a_stomp_starts_a_fresh_session(self):
        self.begin()
        os.unlink(self.lease)
        self.calls.clear()
        self.resident = list(QWEN)       # pipeline came back and loaded its model
        r = self.begin()
        self.assertTrue(r["started_session"])
        self.assertEqual(self.calls[0], ("stop_comfy",))
        self.assertIn(("evict", ["qwen3.8:27b"]), self.calls)


class SafetyTests(unittest.TestCase):
    def test_never_kills_a_listener_that_is_not_comfyui(self):
        with mock.patch("subprocess.run") as run, mock.patch("builtins.open", mock.mock_open(read_data=b"nginx\0-g\0daemon off\0")):
            run.return_value.stdout = 'LISTEN 0 128 127.0.0.1:8188 0.0.0.0:* users:(("nginx",pid=4242,fd=6))\n'
            self.assertIsNone(gs.comfy_listener_pid(8188))

    def test_recognises_comfyui_main_py(self):
        cmd = b".venv/bin/python\0main.py\0--listen\0127.0.0.1\0--port\0" + b"8188\0"
        with mock.patch("subprocess.run") as run, mock.patch("builtins.open", mock.mock_open(read_data=cmd)):
            run.return_value.stdout = 'LISTEN 0 128 127.0.0.1:8188 0.0.0.0:* users:(("python",pid=4242,fd=6))\n'
            self.assertEqual(gs.comfy_listener_pid(8188), 4242)


if __name__ == "__main__":
    unittest.main()
