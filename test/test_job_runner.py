"""The render job runner (scripts/jobs/runner.py): order, specs, retries, failures, resume, headroom, the pidfile, interruption and the dry run. A recording fake
PromptForge, temp folders and a fake clock; no network, no ComfyUI, no GPU (python3 -m unittest test.test_job_runner)."""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "server"))
from core import job_plan, jobs  # noqa: E402
from scripts.jobs import runner  # noqa: E402

Generated = namedtuple("Generated", "job name data")
BIG = b"x" * 40000


class TickClock:
    """The store's clock: every call is one second later, so jobs filed in a loop get distinct created_at values (the real one has one-second resolution and
    breaks ties by a random id, which would make the order of same-second jobs random in a test)."""

    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return datetime.datetime(2026, 11, 13, 10, 0, 0, tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=self.n)


class Clock:
    def __init__(self, step=2.0):
        self.t, self.step = 1_800_000_000.0, step

    def __call__(self):
        self.t += self.step
        return self.t


class FakePF:
    def __init__(self, models=None, fail=None, data=BIG):
        self.calls, self.specs, self.attempts, self.models_list, self.data = [], [], {}, models or [], data
        self.fail = fail or (lambda spec, n: None)          # (spec, attempt number of this seed) -> exception to raise, or None

    def gpu_acquire(self, owner):
        self.calls.append("acquire")

    def gpu_release(self):
        self.calls.append("release")

    def models(self):
        return list(self.models_list)

    def generate(self, spec):
        key = (spec["model"], spec["seed"], spec["verbatim"]["positive"])
        n = self.attempts[key] = self.attempts.get(key, 0) + 1
        self.specs.append(spec)
        self.calls.append("generate")
        err = self.fail(spec, n)
        if err:
            raise err
        return Generated({"status": "done"}, "2026-11/%s_%d_0.png" % (spec["verbatim"]["positive"].replace(" ", "_"), spec["seed"]), self.data)


def spec(**kw):
    base = {"requester": "a", "model": "NovaFlatILV9.safetensors", "positive": "cottage", "width": 512, "height": 512, "seeds": [1, 2]}
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "jobs")
        self.store = jobs.JobStore(self.root, lookup=lambda n: {"commercial_use": "yes"}, clock=TickClock())
        self.out = []
        self.clock = Clock()

    def add(self, **kw):
        return self.store.add(spec(**kw))

    def run_runner(self, pf, **kw):
        kw.setdefault("wait", lambda *a, **k: True)
        kw.setdefault("sleep", lambda s: None)
        kw.setdefault("now", self.clock)
        kw.setdefault("out", self.out.append)
        return runner.run(self.store, pf, **kw)

    def state(self, jid):
        return self.store.get(jid)["state"]

    @property
    def pidfile(self):
        return os.path.join(self.root, ".runner", "runner.pid")


class OrderAndSpecTest(Base):
    def test_jobs_run_in_planned_order_across_priorities_and_requesters(self):
        self.add(requester="a", positive="a1")
        self.add(requester="a", positive="a2")
        self.add(requester="b", positive="b1")
        self.add(requester="c", positive="bulk1", priority="bulk")
        self.add(requester="c", positive="urgent1", priority="urgent")
        pf = FakePF()
        self.assertEqual(self.run_runner(pf), 0)
        order = []
        for s in pf.specs:
            if s["verbatim"]["positive"] not in order:
                order.append(s["verbatim"]["positive"])
        self.assertEqual(order, ["urgent1", "a1", "b1", "a2", "bulk1"])

    def test_the_spec_sent_for_a_plain_job(self):
        self.add(seeds=[7], steps=20, cfg=4.5, negative="blurry")
        pf = FakePF()
        self.run_runner(pf)
        self.assertEqual(pf.specs, [{"model": "NovaFlatILV9.safetensors", "width": 512, "height": 512, "steps": 20, "cfg": 4.5, "seed": 7,
                                     "verbatim": {"positive": "cottage", "negative": "blurry"}}])

    def test_sampler_scheduler_loras_and_card_are_sent_only_when_set(self):
        lo = [{"file": "a.safetensors", "weight": 0.8}]
        self.add(seeds=[1], sampler="euler", scheduler="normal", loras=lo, card="3090")
        pf = FakePF()
        self.run_runner(pf)
        s = pf.specs[0]
        self.assertEqual((s["sampler"], s["scheduler"], s["loras"], s["card"]), ("euler", "normal", lo, "3090"))
        self.add(seeds=[2], requester="b")
        self.run_runner(pf)
        for key in ("sampler", "scheduler", "loras", "card"):
            self.assertNotIn(key, pf.specs[-1])

    def test_filters_by_requester_and_priority(self):
        self.add(requester="a", positive="a1")
        b1 = self.add(requester="b", positive="b1", priority="bulk")
        self.add(requester="b", positive="b2")
        pf = FakePF()
        self.run_runner(pf, requester="b", priority="bulk")
        self.assertEqual({s["verbatim"]["positive"] for s in pf.specs}, {"b1"})
        self.assertEqual(self.state(b1["id"]), "done")
        self.assertEqual(len(self.store.list(state="queued")), 2)


class ResultsTest(Base):
    def test_a_good_job_is_done_with_results_timings_and_a_heartbeat(self):
        j = self.add(seeds=[5, 6])
        pf = FakePF()
        self.assertEqual(self.run_runner(pf), 0)
        rec = self.store.get(j["id"])
        self.assertEqual((rec["state"], rec["attempts"], rec["error"]), ("done", 1, None))
        self.assertEqual([(r["seed"], r["status"], r["seconds"]) for r in rec["results"]], [(5, "ok", 2), (6, "ok", 2)])
        self.assertTrue(all(r["gallery"].endswith("_0.png") for r in rec["results"]))
        self.assertTrue(rec["started_at"].endswith("Z") and rec["finished_at"].endswith("Z"))
        rows = job_plan.load_timings(os.path.join(self.root, "timings.jsonl"))
        self.assertEqual([(r["model"], r["width"], r["steps"], r["seconds"]) for r in rows], [("NovaFlatILV9.safetensors", 512, 28, 2.0)] * 2)
        with open(os.path.join(self.root, ".runner", "heartbeat.json"), encoding="utf-8") as fh:
            beat = json.load(fh)
        self.assertEqual((beat["pictures"], beat["jobs_done"], beat["jobs_failed"], beat["current"]), (2, 1, 0, None))

    def test_the_gpu_is_taken_once_and_released_once(self):
        self.add()
        self.add(requester="b")
        pf = FakePF()
        self.run_runner(pf)
        self.assertEqual((pf.calls.count("acquire"), pf.calls.count("release")), (1, 1))
        self.assertEqual((pf.calls[0], pf.calls[-1]), ("acquire", "release"))

    def test_resume_skips_seeds_that_already_have_an_ok_result(self):
        j = self.add(seeds=[1, 2, 3])
        self.store.set_state(j["id"], "queued", {"results": [{"seed": 1, "status": "ok", "gallery": "x.png", "seconds": 3, "at": "2026-11-13T10:00:00Z"},
                                                           {"seed": 2, "status": "error", "error": "old"}]})
        pf = FakePF()
        self.run_runner(pf)
        self.assertEqual([s["seed"] for s in pf.specs], [2, 3])
        rec = self.store.get(j["id"])
        self.assertEqual((rec["state"], [(r["seed"], r["status"]) for r in rec["results"]]), ("done", [(1, "ok"), (2, "ok"), (3, "ok")]))

    def test_deliver_is_called_per_picture_and_its_error_never_fails_the_picture(self):
        j = self.add(seeds=[1, 2])
        seen = []

        def deliver(job, seed, gen):
            seen.append((job["id"], seed, gen.name))
            if seed == 2:
                raise RuntimeError("disk full")
        self.run_runner(FakePF(), deliver=deliver)
        self.assertEqual([s for _j, s, _n in seen], [1, 2])
        rec = self.store.get(j["id"])
        self.assertEqual(rec["state"], "done")
        self.assertNotIn("delivery_error", rec["results"][0])
        self.assertEqual(rec["results"][1]["delivery_error"], "disk full")

    def test_a_delivered_string_is_recorded_and_a_none_or_an_error_is_not(self):
        j = self.add(seeds=[1, 2, 3])

        def deliver(job, seed, gen):
            if seed == 1:
                return "/art/x_1.png"
            if seed == 2:
                return None
            raise RuntimeError("disk full")
        self.run_runner(FakePF(), deliver=deliver)
        r1, r2, r3 = self.store.get(j["id"])["results"]
        self.assertEqual(r1["delivered"], "/art/x_1.png")
        self.assertNotIn("delivered", r2)
        self.assertNotIn("delivery_error", r2)
        self.assertNotIn("delivered", r3)
        self.assertEqual(r3["delivery_error"], "disk full")

    def test_a_tiny_picture_counts_as_a_failed_attempt(self):
        j = self.add(seeds=[1])
        pf = FakePF(data=b"tiny")
        self.run_runner(pf)
        self.assertEqual(pf.calls.count("generate"), 3)
        rec = self.store.get(j["id"])
        self.assertEqual(rec["state"], "failed")
        self.assertIn("blank or tiny", rec["error"])


class FailureTest(Base):
    def test_retries_use_the_same_seed_and_take_the_gpu_session_again(self):
        j = self.add(seeds=[9])
        pf = FakePF(fail=lambda s, n: RuntimeError("hiccup") if n < 3 else None)
        self.run_runner(pf)
        self.assertEqual(pf.calls, ["acquire", "generate", "acquire", "generate", "acquire", "generate", "release"])
        self.assertEqual({s["seed"] for s in pf.specs}, {9})
        self.assertEqual(self.state(j["id"]), "done")

    def test_a_later_seed_that_fails_every_attempt_fails_the_job_but_the_other_seeds_render(self):
        j = self.add(seeds=[1, 2, 3])
        pf = FakePF(fail=lambda s, n: RuntimeError("bad seed") if s["seed"] == 2 else None)
        self.run_runner(pf)
        rec = self.store.get(j["id"])
        self.assertEqual(rec["state"], "failed")
        self.assertEqual([(r["seed"], r["status"]) for r in rec["results"]], [(1, "ok"), (2, "error"), (3, "ok")])
        self.assertEqual(rec["error"], "1 of 3 seeds failed: bad seed")

    def test_the_first_picture_failing_fails_the_job_at_once_and_skips_the_rest(self):
        j = self.add(seeds=[1, 2, 3])
        pf = FakePF(fail=lambda s, n: RuntimeError("no model"))
        self.run_runner(pf)
        self.assertEqual(pf.calls.count("generate"), 3)                    # exactly the three attempts of the first seed
        rec = self.store.get(j["id"])
        self.assertEqual(rec["state"], "failed")
        self.assertEqual(rec["error"], "first picture failed after 3 attempts: no model; the remaining 2 seeds were not tried")
        self.assertEqual([r["seed"] for r in rec["results"]], [1])

    def test_one_bad_job_does_not_stop_the_batch(self):
        bad = self.add(requester="a", positive="bad")
        good = self.add(requester="b", positive="good")
        pf = FakePF(fail=lambda s, n: RuntimeError("boom") if s["verbatim"]["positive"] == "bad" else None)
        self.assertEqual(self.run_runner(pf), 0)
        self.assertEqual((self.state(bad["id"]), self.state(good["id"])), ("failed", "done"))

    def test_three_failed_jobs_in_a_row_stop_the_run_and_leave_the_rest_queued(self):
        ids = [self.add(requester="r%d" % i, positive="p%d" % i)["id"] for i in range(5)]
        pf = FakePF(fail=lambda s, n: RuntimeError("service down"))
        self.assertEqual(self.run_runner(pf), 1)
        self.assertEqual([self.state(i) for i in ids], ["failed", "failed", "failed", "queued", "queued"])
        self.assertIn("three failed jobs in a row: stopping", self.out)

    def test_a_done_job_resets_the_failure_count_and_a_blocked_job_does_not_count(self):
        names = ["fail1", "fail2", "ok", "fail3", "fail4"]
        ids = {n: self.add(requester="r%d" % i, positive=n)["id"] for i, n in enumerate(names)}
        pf = FakePF(fail=lambda s, n: RuntimeError("boom") if s["verbatim"]["positive"].startswith("fail") else None)
        self.assertEqual(self.run_runner(pf), 0)
        self.assertEqual({n: self.state(i) for n, i in ids.items()}, {"fail1": "failed", "fail2": "failed", "ok": "done", "fail3": "failed", "fail4": "failed"})

    def test_blocked_jobs_do_not_count_as_failures(self):
        blocked = [self.add(requester="b%d" % i, model="Missing%d.safetensors" % i, positive="m%d" % i)["id"] for i in range(4)]
        good = self.add(requester="z", positive="fine")
        pf = FakePF(models=["NovaFlatILV9.safetensors"])
        self.assertEqual(self.run_runner(pf), 0)
        self.assertEqual({self.state(i) for i in blocked}, {"blocked"})
        self.assertEqual(self.state(good["id"]), "done")


class ModelCheckTest(Base):
    def test_a_missing_model_blocks_the_job_and_renders_nothing(self):
        j = self.add(model="Gone.safetensors")
        pf = FakePF(models=["Other.safetensors"])
        self.run_runner(pf)
        rec = self.store.get(j["id"])
        self.assertEqual((rec["state"], rec["error"]), ("blocked", "model Gone.safetensors is not installed in ComfyUI"))
        self.assertNotIn("generate", pf.calls)

    def test_an_empty_model_list_skips_the_check(self):
        j = self.add(model="Anything.safetensors")
        self.run_runner(FakePF(models=[]))
        self.assertEqual(self.state(j["id"]), "done")


class LimitHeadroomTest(Base):
    def test_the_limit_counts_pictures_and_lets_a_started_job_finish(self):
        a = self.add(requester="a", seeds=[1, 2, 3])
        b = self.add(requester="b", seeds=[1, 2])
        pf = FakePF()
        self.assertEqual(self.run_runner(pf, limit=2), 0)
        self.assertEqual((self.state(a["id"]), self.state(b["id"])), ("done", "queued"))       # job a's three pictures all rendered
        self.assertEqual(pf.calls.count("generate"), 3)

    def test_a_limit_reached_exactly_stops_before_the_next_job(self):
        a = self.add(requester="a", seeds=[1])
        b = self.add(requester="b", seeds=[1])
        pf = FakePF()
        self.assertEqual(self.run_runner(pf, limit=1), 0)
        self.assertEqual((self.state(a["id"]), self.state(b["id"])), ("done", "queued"))
        self.assertEqual(pf.calls.count("generate"), 1)

    def test_no_headroom_returns_2_and_starts_nothing(self):
        j = self.add()
        pf = FakePF()
        self.assertEqual(self.run_runner(pf, wait=lambda *a, **k: False), 2)
        self.assertEqual(self.state(j["id"]), "queued")
        self.assertNotIn("generate", pf.calls)
        self.assertIn("no headroom for 30 minutes: stopping", self.out)

    def test_headroom_is_asked_with_the_light_profile_before_each_job(self):
        self.add()
        self.add(requester="b")
        asked = []
        self.run_runner(FakePF(), wait=lambda *a, **k: asked.append(a[:3]) or True)
        self.assertEqual(asked, [("light", 1800, 30)] * 2)


class PidfileAndInterruptTest(Base):
    def test_a_live_runner_returns_3(self):
        self.add()
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        os.makedirs(os.path.dirname(self.pidfile))
        with open(self.pidfile, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % proc.pid)
        pf = FakePF()
        self.assertEqual(self.run_runner(pf), 3)
        self.assertEqual(pf.calls, [])
        self.assertIn("another runner is running (pid %d)" % proc.pid, self.out)
        self.assertTrue(os.path.exists(self.pidfile))                       # the other runner's file is left alone

    def test_a_stale_pidfile_is_replaced_and_the_file_is_removed_after_the_run(self):
        j = self.add()
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        os.makedirs(os.path.dirname(self.pidfile))
        with open(self.pidfile, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % dead.pid)
        self.assertEqual(self.run_runner(FakePF()), 0)
        self.assertEqual(self.state(j["id"]), "done")
        self.assertFalse(os.path.exists(self.pidfile))

    def test_an_interrupt_returns_the_job_to_queued_with_its_pictures_and_cleans_up(self):
        j = self.add(seeds=[1, 2, 3])

        def fail(s, n):
            return KeyboardInterrupt() if s["seed"] == 2 else None
        pf = FakePF(fail=fail)
        with self.assertRaises(KeyboardInterrupt):
            self.run_runner(pf)
        rec = self.store.get(j["id"])
        self.assertEqual(rec["state"], "queued")
        self.assertEqual([(r["seed"], r["status"]) for r in rec["results"]], [(1, "ok")])
        self.assertEqual(pf.calls[-1], "release")
        self.assertFalse(os.path.exists(self.pidfile))
        pf2 = FakePF()
        self.run_runner(pf2)                                                # the re-run renders only the rest
        self.assertEqual([s["seed"] for s in pf2.specs], [2, 3])
        self.assertEqual(self.state(j["id"]), "done")

    def test_jobs_left_running_by_a_dead_runner_are_requeued_and_run(self):
        j = self.add(seeds=[1, 2])
        self.store.set_state(j["id"], "running", {"results": [{"seed": 1, "status": "ok", "gallery": "x.png", "seconds": 1, "at": "2026-11-13T10:00:00Z"}]})
        pf = FakePF()
        self.run_runner(pf)
        self.assertEqual([s["seed"] for s in pf.specs], [2])
        self.assertEqual(self.state(j["id"]), "done")

    def test_a_gpu_session_that_cannot_be_taken_returns_1_without_running(self):
        j = self.add()

        class NoGpu(FakePF):
            def gpu_acquire(self, owner):
                raise OSError("no session")
        pf = NoGpu()
        self.assertEqual(self.run_runner(pf), 1)
        self.assertEqual(self.state(j["id"]), "queued")
        self.assertNotIn("release", pf.calls)                               # nothing was acquired, so nothing is released
        self.assertFalse(os.path.exists(self.pidfile))


class DryRunTest(Base):
    def test_a_dry_run_prints_the_order_and_the_estimate_and_changes_nothing(self):
        a = self.add(requester="a", positive="a1", seeds=[1, 2])
        b = self.add(requester="b", positive="b1", seeds=[1], priority="urgent")

        class Strict:
            def __getattr__(self, name):
                raise AssertionError("dry run must not call pf.%s" % name)
        self.assertEqual(self.run_runner(Strict(), dry_run=True), 0)
        self.assertEqual(self.out[0], "1. %s  b  urgent  1 seeds  NovaFlatILV9.safetensors" % b["id"])
        self.assertEqual(self.out[1], "2. %s  a  normal  2 seeds  NovaFlatILV9.safetensors" % a["id"])
        self.assertTrue(self.out[2].startswith("2 jobs, 3 pictures, about "))
        self.assertTrue(self.out[2].endswith(" (rough)"))
        self.assertEqual((self.state(a["id"]), self.state(b["id"])), ("queued", "queued"))
        self.assertFalse(os.path.exists(self.pidfile))
        self.assertFalse(os.path.isdir(os.path.join(self.root, ".runner")))

    def test_the_estimate_is_not_rough_once_there_are_timings(self):
        self.add(seeds=[1, 2])
        job_plan.append_timing(os.path.join(self.root, "timings.jsonl"), {"model": "NovaFlatILV9.safetensors", "width": 512, "height": 512, "steps": 28, "seconds": 10})
        self.run_runner(FakePF(), dry_run=True)
        self.assertEqual(self.out[-1], "1 jobs, 2 pictures, about 50s")             # 2 x 10 s plus the 30 s model load


class FormatTest(unittest.TestCase):
    def test_format_seconds(self):
        for n, text in ((0, "0s"), (45, "45s"), (59.6, "1m"), (60, "1m"), (720, "12m"), (3599, "59m"), (3600, "1h 00m"), (3900, "1h 05m"), (7560, "2h 06m")):
            self.assertEqual(runner.format_seconds(n), text, n)


class UntilTest(Base):
    BASE_T = 1_800_000_000.0

    def until(self, offset):
        return runner._iso(self.BASE_T + offset)

    def fixed_now(self, pf, after_pictures=None, early=0.0, late=1000.0):
        """A clock that sits at BASE_T + early until `after_pictures` pictures were generated, then at BASE_T + late."""
        return lambda: self.BASE_T + (late if after_pictures is not None and len(pf.specs) >= after_pictures else early)

    def test_a_window_that_has_already_ended_renders_nothing_and_leaves_the_jobs_queued(self):
        a = self.add()
        pf = FakePF()
        self.assertEqual(self.run_runner(pf, until=self.until(-10), now=self.fixed_now(pf)), 0)
        self.assertEqual(self.state(a["id"]), "queued")
        self.assertNotIn("generate", pf.calls)
        self.assertEqual(self.out.count("window ended: stopping"), 1)
        self.assertEqual(pf.calls, ["acquire", "release"])

    def test_the_stop_time_itself_counts_as_ended(self):
        a = self.add()
        pf = FakePF()
        self.run_runner(pf, until=self.until(0), now=self.fixed_now(pf))
        self.assertEqual((self.state(a["id"]), pf.calls.count("generate")), ("queued", 0))
        self.assertEqual(self.store.get(a["id"])["attempts"], 0)                       # the job was never started

    def test_the_stop_time_reached_exactly_between_pictures_stops_the_job(self):
        a = self.add(seeds=[1, 2, 3])
        pf = FakePF()
        self.run_runner(pf, until=self.until(500), now=self.fixed_now(pf, after_pictures=1, late=500))
        self.assertEqual((self.state(a["id"]), pf.calls.count("generate")), ("queued", 1))

    def test_the_window_ending_between_pictures_stops_the_job_with_its_pictures_kept(self):
        a = self.add(requester="a", seeds=[1, 2, 3])
        b = self.add(requester="b", seeds=[1])
        pf = FakePF()
        self.assertEqual(self.run_runner(pf, until=self.until(500), now=self.fixed_now(pf, after_pictures=1)), 0)
        rec = self.store.get(a["id"])
        self.assertEqual(rec["state"], "queued")
        self.assertEqual([(r["seed"], r["status"]) for r in rec["results"]], [(1, "ok")])
        self.assertEqual(self.state(b["id"]), "queued")
        self.assertEqual(pf.calls.count("generate"), 1)
        self.assertEqual(self.out.count("window ended: stopping"), 1)
        self.assertEqual(pf.calls[-1], "release")
        self.assertFalse(os.path.exists(self.pidfile))

    def test_a_later_run_without_a_stop_time_renders_only_the_seeds_that_are_left(self):
        a = self.add(seeds=[1, 2, 3])
        pf = FakePF()
        self.run_runner(pf, until=self.until(500), now=self.fixed_now(pf, after_pictures=1))
        pf2 = FakePF()
        self.assertEqual(self.run_runner(pf2), 0)
        self.assertEqual([s["seed"] for s in pf2.specs], [2, 3])
        self.assertEqual(self.state(a["id"]), "done")

    def test_a_stop_time_far_ahead_changes_nothing(self):
        a = self.add(seeds=[1, 2])
        pf = FakePF()
        self.assertEqual(self.run_runner(pf, until=self.until(10**6), now=self.fixed_now(pf)), 0)
        self.assertEqual((self.state(a["id"]), pf.calls.count("generate")), ("done", 2))
        self.assertNotIn("window ended: stopping", self.out)

    def test_a_bad_stop_time_returns_1_before_anything_is_touched(self):
        a = self.add()
        for bad in ("tomorrow", "2026-11-14 05:00", 12345):
            pf = FakePF()
            self.out.clear()
            self.assertEqual(self.run_runner(pf, until=bad), 1, bad)
            self.assertEqual(self.out, ["error: until must be a UTC time like 2026-11-14T05:00:00Z"])
            self.assertEqual(pf.calls, [])
        self.assertEqual(self.state(a["id"]), "queued")
        self.assertFalse(os.path.exists(self.pidfile))

    def test_a_dry_run_ignores_the_stop_time(self):
        self.add()
        pf = FakePF()
        self.assertEqual(self.run_runner(pf, dry_run=True, until=self.until(-10), now=self.fixed_now(pf)), 0)
        self.assertEqual(pf.calls, [])


if __name__ == "__main__":
    unittest.main()
