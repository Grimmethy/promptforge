import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import runner
except ImportError:  # package-style imports inside runner.py
    from scripts.jobs import runner


class _Gen:
    data = b"x"
    name = "pic.png"


class _PF:
    def generate(self, spec):
        return _Gen()

    def gpu_acquire(self, owner):
        pass


class _Store:
    def __init__(self):
        self.rec = None

    def set_state(self, job_id, state, fields=None):
        self.rec = dict(self.rec or {})
        self.rec.update(fields or {})
        self.rec["id"] = job_id
        self.rec["state"] = state
        return self.rec


def _job():
    return {"id": "job-1", "model": "m", "width": 64, "height": 64, "steps": 4, "cfg": 1.0,
            "positive": "p", "negative": "n", "seeds": [7], "results": []}


class TimingWriteLoggingTest(unittest.TestCase):
    def _run(self, side_effect):
        ticks = iter(range(0, 1000, 5))
        msgs = []
        delivered = []
        store = _Store()
        store.rec = _job()
        st = {"current": None, "pictures": 0}
        with mock.patch.object(runner, "good_image", return_value=True), \
                mock.patch.object(runner.job_plan, "append_timing", side_effect=side_effect) as m:
            outcome = runner._run_job(store, _PF(), _job(), set(), st, "o",
                                      lambda rec, seed, gen: delivered.append(seed),
                                      "/tmp/timings.jsonl", lambda: float(next(ticks)),
                                      msgs.append, lambda: None)
        return outcome, msgs, delivered, m, store

    def test_oserror_is_logged_and_not_fatal(self):
        outcome, msgs, delivered, m, store = self._run(OSError("disk full"))
        self.assertEqual(outcome, "done")
        self.assertEqual(delivered, [7])
        self.assertEqual(store.rec["results"][0]["status"], "ok")
        text = "\n".join(msgs)
        self.assertIn("could not record timing", text)
        self.assertIn("seed 7", text)
        self.assertIn("/tmp/timings.jsonl", text)
        self.assertIn("OSError", text)
        self.assertIn("disk full", text)

    def test_valueerror_is_logged_and_not_fatal(self):
        outcome, msgs, _, _, _ = self._run(ValueError("bad row"))
        self.assertEqual(outcome, "done")
        text = "\n".join(msgs)
        self.assertIn("could not record timing", text)
        self.assertIn("bad row", text)

    def test_success_logs_no_failure(self):
        outcome, msgs, _, m, _ = self._run(None)
        self.assertEqual(outcome, "done")
        m.assert_called_once()
        self.assertNotIn("could not record timing", "\n".join(msgs))


if __name__ == "__main__":
    unittest.main()
