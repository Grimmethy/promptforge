"""Planning logic of the render job runner (server/core/job_plan.py): next-job order and time estimates. Pure functions and temp files only, no network, no GPU
(python3 -m unittest test.test_job_plan)."""
import copy
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from core import job_plan  # noqa: E402


def job(jid, requester="a", priority="normal", needed_by=None, created="2026-11-13T10:00:00Z", state="queued", seeds=(1,), model="M.safetensors",
        width=512, height=512, steps=20):
    return {"id": jid, "requester": requester, "priority": priority, "needed_by": needed_by, "created_at": created, "state": state,
            "seeds": list(seeds), "model": model, "width": width, "height": height, "steps": steps}


def ids(jobs):
    return [j["id"] for j in jobs]


class OrderTest(unittest.TestCase):
    def test_levels_urgent_then_normal_then_bulk(self):
        jobs = [job("b1", priority="bulk"), job("n1", priority="normal"), job("u1", priority="urgent"), job("b2", priority="bulk", created="2026-11-13T09:00:00Z")]
        self.assertEqual(ids(job_plan.order_jobs(jobs)), ["u1", "n1", "b2", "b1"])

    def test_dated_jobs_come_first_in_their_level_by_date_then_the_rest(self):
        jobs = [job("late", needed_by="2026-12-01", requester="x"), job("none", requester="y", created="2026-11-13T08:00:00Z"),
                job("soon", needed_by="2026-11-20", requester="z"), job("soon2", needed_by="2026-11-20", requester="x", created="2026-11-13T11:00:00Z"),
                job("bulkdated", priority="bulk", needed_by="2026-11-14")]
        self.assertEqual(ids(job_plan.order_jobs(jobs)), ["soon", "soon2", "late", "none", "bulkdated"])

    def test_round_robin_between_requesters(self):
        t = lambda n: "2026-11-13T10:00:0%dZ" % n  # noqa: E731
        jobs = [job("a1", "a", created=t(1)), job("a2", "a", created=t(4)), job("a3", "a", created=t(7)),
                job("b1", "b", created=t(2)), job("c1", "c", created=t(3)), job("c2", "c", created=t(5))]
        self.assertEqual(ids(job_plan.order_jobs(jobs)), ["a1", "b1", "c1", "a2", "c2", "a3"])

    def test_last_requester_rotates_the_first_pass(self):
        t = lambda n: "2026-11-13T10:00:0%dZ" % n  # noqa: E731
        jobs = [job("a1", "a", created=t(1)), job("a2", "a", created=t(4)), job("a3", "a", created=t(7)),
                job("b1", "b", created=t(2)), job("c1", "c", created=t(3)), job("c2", "c", created=t(5))]
        self.assertEqual(ids(job_plan.order_jobs(jobs, "a")), ["b1", "c1", "a1", "c2", "a2", "a3"])
        self.assertEqual(ids(job_plan.order_jobs(jobs, "b")), ["c1", "a1", "b1", "c2", "a2", "a3"])
        self.assertEqual(ids(job_plan.order_jobs(jobs, "c")), ids(job_plan.order_jobs(jobs)))      # wraps around
        self.assertEqual(ids(job_plan.order_jobs(jobs, "nobody")), ids(job_plan.order_jobs(jobs)))

    def test_one_requesters_own_jobs_run_in_creation_order(self):
        jobs = [job("z", created="2026-11-13T10:00:03Z"), job("y", created="2026-11-13T10:00:01Z"), job("x", created="2026-11-13T10:00:02Z")]
        self.assertEqual(ids(job_plan.order_jobs(jobs)), ["y", "x", "z"])
        same = [job("b", created="2026-11-13T10:00:00Z"), job("a", created="2026-11-13T10:00:00Z")]
        self.assertEqual(ids(job_plan.order_jobs(same)), ["a", "b"])                           # the id breaks a tie

    def test_only_queued_jobs_are_planned_and_inputs_are_not_changed(self):
        jobs = [job("q1"), job("h", state="held"), job("r", state="running"), job("d", state="done"), job("f", state="failed"), job("bl", state="blocked"),
                job("rm", state="removed")]
        before = copy.deepcopy(jobs)
        self.assertEqual(ids(job_plan.order_jobs(jobs)), ["q1"])
        self.assertEqual(jobs, before)

    def test_next_job(self):
        self.assertIsNone(job_plan.next_job([]))
        self.assertIsNone(job_plan.next_job([job("h", state="held")]))
        jobs = [job("a1", "a", created="2026-11-13T10:00:01Z"), job("b1", "b", created="2026-11-13T10:00:02Z")]
        self.assertEqual(job_plan.next_job(jobs)["id"], "a1")
        self.assertEqual(job_plan.next_job(jobs, "a")["id"], "b1")

    def test_an_unknown_priority_counts_as_normal(self):
        jobs = [job("odd", priority="whenever"), job("u", priority="urgent"), job("b", priority="bulk")]
        self.assertEqual(ids(job_plan.order_jobs(jobs)), ["u", "odd", "b"])


class EstimateTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(job_plan.estimate([], []), {"total_seconds": 0, "per_job": {}, "pictures": 0, "rough": False})
        self.assertEqual(job_plan.estimate([job("h", state="held")], [])["total_seconds"], 0)

    def test_the_median_of_the_same_model_and_size_wins(self):
        timings = [{"model": "M.safetensors", "width": 512, "height": 512, "steps": 20, "seconds": s} for s in (5, 9, 7)]
        timings.append({"model": "Other.safetensors", "width": 512, "height": 512, "steps": 20, "seconds": 100})
        e = job_plan.estimate([job("j1", seeds=(1, 2, 3, 4))], timings)
        self.assertEqual(e["per_job"], {"j1": 28})
        self.assertEqual((e["total_seconds"], e["pictures"], e["rough"]), (58, 4, False))

    def test_a_different_size_is_scaled_from_the_same_model(self):
        timings = [{"model": "M.safetensors", "width": 512, "height": 512, "steps": 20, "seconds": 10}]
        e = job_plan.estimate([job("j1", width=1024, height=512)], timings)            # twice the pixels, so twice the seconds
        self.assertEqual(e["per_job"], {"j1": 20})
        self.assertEqual((e["total_seconds"], e["rough"]), (50, False))
        e = job_plan.estimate([job("j2", steps=40)], timings)                            # twice the steps
        self.assertEqual(e["per_job"], {"j2": 20})

    def test_the_fallback_is_rough_and_depends_on_the_model_family(self):
        e = job_plan.estimate([job("s", width=1000, height=1000, steps=28, seeds=(1, 2))], [])
        self.assertEqual(e["per_job"], {"s": 20})                                        # 0.35 * 1.0 MP * 28 steps * 2 pictures = 19.6
        self.assertEqual((e["total_seconds"], e["rough"]), (50, True))
        e = job_plan.estimate([job("k", model="krea2:k.safetensors", width=1000, height=1000, steps=8, seeds=(1, 2, 3))], [])
        self.assertEqual(e["per_job"], {"k": 60})                                        # 2.5 * 1.0 * 8 * 3
        self.assertEqual((e["total_seconds"], e["rough"]), (90, True))

    def test_the_rough_flag_is_set_when_any_job_lacks_timings(self):
        timings = [{"model": "M.safetensors", "width": 512, "height": 512, "steps": 20, "seconds": 5}]
        e = job_plan.estimate([job("known"), job("new", model="New.safetensors", requester="b")], timings)
        self.assertTrue(e["rough"])
        self.assertEqual(job_plan.estimate([job("known")], timings)["rough"], False)

    def test_a_fallback_job_anywhere_in_the_order_makes_the_estimate_rough(self):
        timings = [{"model": "M.safetensors", "width": 512, "height": 512, "steps": 20, "seconds": 5}]
        first = [job("new", "a", model="New.safetensors"), job("known", "b")]                    # planned order: the fallback job is first, a known one last
        self.assertEqual([j["id"] for j in job_plan.order_jobs(first)], ["new", "known"])
        self.assertTrue(job_plan.estimate(first, timings)["rough"])

    def test_the_model_load_time_is_added_once_per_distinct_model(self):
        timings = [{"model": m, "width": 512, "height": 512, "steps": 20, "seconds": 10} for m in ("M.safetensors", "N.safetensors")]
        same = job_plan.estimate([job("a", "x"), job("b", "y", model="M.safetensors")], timings)
        self.assertEqual(same["total_seconds"], 10 + 10 + 30)
        two = job_plan.estimate([job("a", "x"), job("b", "y", model="N.safetensors")], timings)
        self.assertEqual(two["total_seconds"], 10 + 10 + 60)

    def test_non_queued_jobs_are_ignored_and_the_input_is_unchanged(self):
        jobs = [job("q"), job("d", state="done", seeds=(1, 2, 3, 4, 5))]
        before = copy.deepcopy(jobs)
        e = job_plan.estimate(jobs, [])
        self.assertEqual(list(e["per_job"]), ["q"])
        self.assertEqual(jobs, before)


class TimingFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "sub", "timings.jsonl")

    def row(self, seconds=5, **kw):
        r = {"model": "M.safetensors", "width": 512, "height": 512, "steps": 20, "seconds": seconds}
        r.update(kw)
        return r

    def test_a_missing_file_gives_nothing(self):
        self.assertEqual(job_plan.load_timings(self.path), [])

    def test_blank_corrupt_and_invalid_lines_are_skipped(self):
        os.makedirs(os.path.dirname(self.path))
        bad = [self.row(seconds=-1), self.row(seconds=True), self.row(model=""), self.row(model=5), {"model": "M", "width": 1}, [1, 2], "text", self.row(width=0)]
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(self.row(1)) + "\n\n{broken\n")
            for b in bad:
                fh.write(json.dumps(b) + "\n")
            fh.write(json.dumps(self.row(2)) + "\n")
        self.assertEqual([r["seconds"] for r in job_plan.load_timings(self.path)], [1, 2])

    def test_the_limit_keeps_the_last_rows(self):
        for s in range(1, 8):
            job_plan.append_timing(self.path, self.row(s))
        self.assertEqual([r["seconds"] for r in job_plan.load_timings(self.path, limit=3)], [5, 6, 7])
        self.assertEqual(len(job_plan.load_timings(self.path)), 7)

    def test_append_validates_and_creates_the_folder(self):
        for bad in (self.row(seconds=0), self.row(model=""), {"model": "M"}, "x", None):
            with self.assertRaisesRegex(ValueError, "invalid timing row"):
                job_plan.append_timing(self.path, bad)
        self.assertFalse(os.path.exists(self.path))
        job_plan.append_timing(self.path, dict(self.row(3), extra="ignored"))
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.loads(fh.read()), self.row(3))

    def test_twenty_threads_appending_give_twenty_intact_lines(self):
        os.makedirs(os.path.dirname(self.path))
        threads = [threading.Thread(target=job_plan.append_timing, args=(self.path, self.row(i + 1, model="M%d.safetensors" % i))) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with open(self.path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 20)
        self.assertEqual(sorted(json.loads(x)["seconds"] for x in lines), list(range(1, 21)))


if __name__ == "__main__":
    unittest.main()
