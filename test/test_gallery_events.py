"""Gallery events (2026-11-08): a finished generation or a deleted image is pushed to every open page over /api/events (server-sent events), so
a job started through the API shows up without a refresh. No ComfyUI, no GPU (python3 -m unittest discover -s test -p 'test_*.py')."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import app as pf  # noqa: E402
from core import comfy, events  # noqa: E402


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


class HubTest(unittest.TestCase):
    def setUp(self):
        for q in list(events._subs):
            events.unsubscribe(q)

    def test_publish_fans_out_to_every_subscriber(self):
        a, b = events.subscribe(), events.subscribe()
        self.assertEqual(events.publish("gallery-new", {"images": ["x.png"]}), 2)
        self.assertEqual(drain(a), [("gallery-new", '{"images": ["x.png"]}')])
        self.assertEqual(drain(b), [("gallery-new", '{"images": ["x.png"]}')])

    def test_a_full_queue_drops_only_that_subscriber_and_publish_never_blocks(self):
        slow, fast = events.subscribe(), events.subscribe()
        for i in range(events.QUEUE_SIZE):
            slow.put_nowait(("filler", str(i)))
        self.assertEqual(events.publish("gallery-new", {}), 1)         # only the fast one received it
        self.assertEqual(events.subscriber_count(), 1)
        self.assertEqual(len(drain(fast)), 1)

    def test_subscriptions_beyond_the_cap_are_refused(self):
        with mock.patch.object(events, "MAX_SUBSCRIBERS", 2):
            self.assertIsNotNone(events.subscribe())
            self.assertIsNotNone(events.subscribe())
            self.assertIsNone(events.subscribe())

    def test_unsubscribe_releases_the_slot(self):
        q = events.subscribe()
        events.unsubscribe(q)
        self.assertEqual(events.subscriber_count(), 0)
        events.unsubscribe(q)                                          # a second call is harmless

    def test_an_unserialisable_payload_is_dropped_not_raised(self):
        events.subscribe()
        self.assertEqual(events.publish("gallery-new", {"bad": object()}), 0)


class RouteTest(unittest.TestCase):
    def setUp(self):
        for q in list(events._subs):
            events.unsubscribe(q)
        self.client = pf.app.test_client()

    def test_the_stream_sends_retry_then_named_events_with_json(self):
        r = self.client.get("/api/events", buffered=False)
        self.assertTrue(r.mimetype.startswith("text/event-stream"))
        it = iter(r.response)
        first = next(it)
        first = first.decode() if isinstance(first, bytes) else first
        self.assertEqual(first, "retry: 3000\n\n")
        self.assertEqual(events.subscriber_count(), 1)
        events.publish("gallery-new", {"job": "j1", "images": ["2026-11/a_0.png"], "nsfw": False})
        frame = next(it)
        frame = frame.decode() if isinstance(frame, bytes) else frame
        self.assertTrue(frame.startswith("event: gallery-new\ndata: "))
        self.assertEqual(json.loads(frame.split("data: ", 1)[1].strip())["images"], ["2026-11/a_0.png"])
        r.close()
        self.assertEqual(events.subscriber_count(), 0)                 # disconnect releases the subscriber

    def test_an_idle_stream_sends_a_keep_alive_comment(self):
        with mock.patch.object(pf, "SSE_PING_S", 0.05):
            r = self.client.get("/api/events", buffered=False)
            it = iter(r.response)
            next(it)                                                   # retry line
            ping = next(it)
            ping = ping.decode() if isinstance(ping, bytes) else ping
            self.assertEqual(ping, ": ping\n\n")
            r.close()

    def test_a_stream_beyond_the_cap_is_a_503(self):
        with mock.patch.object(events, "MAX_SUBSCRIBERS", 0):
            self.assertEqual(self.client.get("/api/events").status_code, 503)

    def test_deleting_an_image_publishes_gallery_removed(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "2026-11"))
            with open(os.path.join(d, "2026-11", "abcd1234_0.png"), "wb") as fh:
                fh.write(b"png")
            q = events.subscribe()
            with mock.patch.object(pf, "GALLERY_DIR", d):
                r = self.client.delete("/api/image/2026-11/abcd1234_0.png")
            self.assertEqual(r.get_json()["deleted"], ["2026-11/abcd1234_0.png"])
            self.assertEqual(drain(q), [("gallery-removed", '{"file": "2026-11/abcd1234_0.png"}')])

    def test_deleting_a_missing_image_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            q = events.subscribe()
            with mock.patch.object(pf, "GALLERY_DIR", d):
                self.client.delete("/api/image/2026-11/nothing_0.png")
            self.assertEqual(drain(q), [])


class RunPublishTest(unittest.TestCase):
    """comfy._run on a finished (stubbed) ComfyUI round trip publishes gallery-new, and a failing publish does not fail the job."""

    def run_job(self, publish_side_effect=None):
        with tempfile.TemporaryDirectory() as gallery:
            job = {"id": "abcdef0123456789", "model": "m.safetensors", "seed": 1, "nsfw": True, "spec": {"regenerate": True}, "prompt_used": {}}
            rec = {"id": job["id"], "status": "starting", "images": [], "error": None, "warnings": []}
            history = {"p1": {"status": {"status_str": "success"}, "outputs": {"13": {"images": [{"filename": "o.png", "subfolder": "", "type": "output"}]}}}}
            patches = [
                mock.patch.object(comfy, "_take_gpu"),
                mock.patch.object(comfy, "build_graph", return_value={}),
                mock.patch.object(comfy, "_post", return_value={"prompt_id": "p1"}),
                mock.patch.object(comfy, "_get", return_value=history),
                mock.patch.object(comfy, "fetch_view", return_value=b"\x89PNG"),
                mock.patch.object(comfy.time, "sleep"),
                mock.patch.object(comfy, "_gpu_reclaimed", return_value=False),
            ]
            if publish_side_effect:
                patches.append(mock.patch.object(comfy.events, "publish", side_effect=publish_side_effect))
            for p in patches:
                p.start()
            try:
                q = events.subscribe()
                try:
                    comfy._run(job, rec, gallery)
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"_run raised {exc!r}")
                finally:
                    got = drain(q) if q else []
                    events.unsubscribe(q)
            finally:
                mock.patch.stopall()
            return rec, got

    def test_a_finished_job_publishes_gallery_new_with_the_nsfw_flag(self):
        rec, got = self.run_job()
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual(len(got), 1)
        name, payload = got[0]
        data = json.loads(payload)
        self.assertEqual((name, data["job"], data["nsfw"]), ("gallery-new", "abcdef0123456789", True))
        self.assertEqual(data["images"], rec["images"])

    def test_a_failing_publish_never_fails_the_job(self):
        rec, _ = self.run_job(publish_side_effect=RuntimeError("boom"))
        self.assertEqual(rec["status"], "done", rec)


if __name__ == "__main__":
    unittest.main()
