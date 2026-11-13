"""The jobs command line (python3 -m scripts.jobs): add, status, show, hold, release, remove. Temp folders only, no network, no GPU, plain python3:
python3 -m unittest test.test_jobs_cli"""
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "server"))
from core import job_plan, jobs  # noqa: E402
from scripts.jobs import cli  # noqa: E402


def spec(**kw):
    base = {"requester": "mapforge", "model": "NovaFlatILV9.safetensors", "positive": "a cottage", "width": 512, "height": 512, "seeds": [1, 2, 3]}
    base.update(kw)
    return base


class CliBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = jobs.JobStore(os.path.join(self.tmp.name, "jobs"), lookup=lambda n: {"commercial_use": "yes"})
        self.out, self.err = [], []

    def run_cli(self, *argv, stdin=None):
        code = cli.main(list(argv), store=self.store, out=self.out.append, err=self.err.append, stdin=io.StringIO(stdin) if stdin is not None else None)
        return code

    def spec_file(self, body):
        p = os.path.join(self.tmp.name, "spec.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body if isinstance(body, str) else json.dumps(body))
        return p


class AddTest(CliBase):
    def test_add_from_a_file_prints_the_id(self):
        self.assertEqual(self.run_cli("add", "--file", self.spec_file(spec())), 0)
        (jid,) = self.out
        self.assertRegex(jid, r"^job-\d{14}-[0-9a-f]{6}$")
        self.assertEqual(self.store.get(jid)["requester"], "mapforge")

    def test_add_from_stdin_and_json_output(self):
        self.assertEqual(self.run_cli("add", "--file", "-", "--json", stdin=json.dumps(spec(requester="stdin"))), 0)
        rec = json.loads(self.out[0])
        self.assertEqual((rec["requester"], rec["state"]), ("stdin", "queued"))

    def test_a_bad_spec_exits_2_with_one_error_line_per_problem(self):
        self.assertEqual(self.run_cli("add", "--file", self.spec_file(spec(seeds=[], width=100))), 2)
        self.assertGreaterEqual(len(self.err), 2)
        self.assertTrue(all(e.startswith("error: ") for e in self.err))
        self.assertEqual(self.out, [])
        self.assertEqual(self.store.list(), [])

    def test_unparseable_json_and_a_missing_file_exit_1(self):
        self.assertEqual(self.run_cli("add", "--file", self.spec_file("{broken")), 1)
        self.assertEqual(len(self.err), 1)
        self.assertTrue(self.err[0].startswith("error: "))
        self.err.clear()
        self.assertEqual(self.run_cli("add", "--file", os.path.join(self.tmp.name, "nope.json")), 1)
        self.assertEqual(len(self.err), 1)


class StatusTest(CliBase):
    def test_status_text_has_a_counts_line_and_one_line_per_job(self):
        a = self.store.add(spec(requester="a"))
        b = self.store.add(spec(requester="b", priority="urgent", seeds=[9]))
        self.store.set_state(b["id"], "done")
        self.assertEqual(self.run_cli("status"), 0)
        self.assertEqual(self.out[0], "queued 1 | held 0 | running 0 | done 1 | failed 0 | blocked 0 | removed 0")
        lines = self.out[1:]
        self.assertEqual(len(lines), 2)
        self.assertIn("%s  queued  normal  3 seeds  NovaFlatILV9.safetensors  a" % a["id"], lines)
        self.assertIn("%s  done  urgent  1 seeds  NovaFlatILV9.safetensors  b" % b["id"], lines)

    def test_filters_and_json(self):
        a = self.store.add(spec(requester="a"))
        self.store.add(spec(requester="b"))
        self.assertEqual(self.run_cli("status", "--requester", "a"), 0)
        self.assertEqual(len(self.out), 2)
        self.assertTrue(self.out[0].startswith("queued 2"))        # the counts line always covers every job
        self.assertTrue(self.out[1].startswith(a["id"]))
        self.out.clear()
        self.assertEqual(self.run_cli("status", "--state", "held"), 0)
        self.assertEqual(len(self.out), 1)
        self.out.clear()
        self.assertEqual(self.run_cli("status", "--json", "--requester", "b"), 0)
        body = json.loads(self.out[0])
        self.assertEqual([j["requester"] for j in body["jobs"]], ["b"])
        self.assertEqual(body["counts"]["queued"], 2)


class ManageTest(CliBase):
    def test_show_and_the_transitions(self):
        j = self.store.add(spec())
        self.assertEqual(self.run_cli("show", j["id"]), 0)
        self.assertEqual(json.loads(self.out[0])["id"], j["id"])
        self.out.clear()
        for cmd, state in (("hold", "held"), ("release", "queued"), ("remove", "removed")):
            self.assertEqual(self.run_cli(cmd, j["id"]), 0)
            self.assertEqual(self.out[-1], state)
        self.assertEqual(self.store.get(j["id"])["state"], "removed")

    def test_refusals_and_unknown_ids_exit_2(self):
        j = self.store.add(spec())
        self.assertEqual(self.run_cli("release", j["id"]), 2)
        self.assertIn("allowed only from held", self.err[-1])
        for cmd in ("show", "hold", "release", "remove"):
            self.err.clear()
            self.assertEqual(self.run_cli(cmd, "job-20260101000000-000000"), 2, cmd)
            self.assertIn("no job", self.err[0])

    def test_bad_arguments_exit_1(self):
        self.assertEqual(self.run_cli("status", "--state", "paused"), 1)
        self.assertEqual(self.run_cli("frobnicate"), 1)


class RunAndEstimateTest(CliBase):
    def test_run_passes_its_options_to_the_runner_and_returns_its_code(self):
        calls = []

        def fake_run(store, pf, **kw):
            calls.append((store, type(pf).__name__, kw))
            return 3
        code = cli.main(["run", "--limit", "5", "--requester", "mapforge", "--priority", "urgent", "--dry-run"], store=self.store, out=self.out.append,
                        err=self.err.append, run_fn=fake_run)
        self.assertEqual(code, 3)
        (store, pf_name, kw), = calls
        self.assertIs(store, self.store)
        self.assertEqual(pf_name, "JobPF")
        self.assertEqual((kw["limit"], kw["requester"], kw["priority"], kw["dry_run"]), (5, "mapforge", "urgent", True))
        calls.clear()
        cli.main(["run"], store=self.store, out=self.out.append, err=self.err.append, run_fn=fake_run)
        self.assertEqual((calls[0][2]["limit"], calls[0][2]["requester"], calls[0][2]["priority"], calls[0][2]["dry_run"]), (0, None, None, False))

    def test_run_rejects_a_bad_priority(self):
        self.assertEqual(self.run_cli("run", "--priority", "soon"), 1)

    def test_estimate_prints_one_line(self):
        self.assertEqual(self.run_cli("estimate"), 0)
        self.assertEqual(self.out, ["queue is empty"])
        self.out.clear()
        self.store.add(spec(seeds=[1, 2], width=1000, height=1000))
        self.assertEqual(self.run_cli("estimate"), 0)
        self.assertEqual(self.out, ["queued 1 jobs, 2 pictures, about 50s (rough)"])        # 0.35 s per MP-step x 28 x 2 pictures + 30 s model load
        self.out.clear()
        job_plan.append_timing(os.path.join(self.store.root, "timings.jsonl"), {"model": "NovaFlatILV9.safetensors", "width": 1000, "height": 1000, "steps": 28, "seconds": 10})
        self.assertEqual(self.run_cli("estimate"), 0)
        self.assertEqual(self.out, ["queued 1 jobs, 2 pictures, about 50s"])
        self.out.clear()
        self.assertEqual(self.run_cli("estimate", "--requester", "nobody"), 0)
        self.assertEqual(self.out, ["queue is empty"])


class ResultsCommandTest(CliBase):
    def test_results_lists_every_seed_and_counts_the_ok_ones(self):
        j = self.store.add(spec(seeds=[1, 2, 3, 4]))
        self.store.set_state(j["id"], "running", {"results": [
            {"seed": 2, "status": "error", "error": "no model"},
            {"seed": 1, "status": "ok", "seconds": 7, "gallery": "2026-11/a_0.png", "delivered": "/art/a.png"},
            {"seed": 3, "status": "ok", "seconds": 9, "gallery": "2026-11/b_0.png"}]})
        self.assertEqual(self.run_cli("results", j["id"]), 0)
        self.assertEqual(self.out, ["1  ok  7s  2026-11/a_0.png  /art/a.png", "2  error  no model", "3  ok  9s  2026-11/b_0.png  -", "4  pending", "2 of 4 seeds ok"])

    def test_results_of_an_unknown_job_exits_2(self):
        self.assertEqual(self.run_cli("results", "job-20260101000000-000000"), 2)
        self.assertEqual(self.err, ["error: no job job-20260101000000-000000"])

    def test_run_passes_a_deliver_function(self):
        seen = {}
        cli.main(["run"], store=self.store, out=self.out.append, err=self.err.append, run_fn=lambda store, pf, **kw: seen.update(kw) or 0)
        self.assertTrue(callable(seen["deliver"]))


class WindowCommandTest(CliBase):
    def setUp(self):
        super().setUp()
        import datetime
        self.store = jobs.JobStore(os.path.join(self.tmp.name, "jobs"), lookup=lambda n: {"commercial_use": "yes"},
                                   clock=lambda: datetime.datetime.now(datetime.timezone.utc))

    @staticmethod
    def later(hours):
        import datetime
        return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_show_with_nothing_armed(self):
        self.assertEqual(self.run_cli("window", "show"), 0)
        self.assertEqual(self.out, ["no window armed"])

    def test_arm_then_show_then_disarm(self):
        start, end = self.later(1), self.later(5)
        self.assertEqual(self.run_cli("window", "arm", start, end), 0)
        self.assertRegex(self.out[-1], r"^window %s to %s \(armed \d{4}-" % (start, end))
        self.assertEqual(self.store.window()["end"], end)
        self.out.clear()
        self.assertEqual(self.run_cli("window", "show"), 0)
        self.assertIn(start, self.out[0])
        self.out.clear()
        self.assertEqual(self.run_cli("window", "disarm"), 0)
        self.assertEqual(self.out, ["window removed"])
        self.assertIsNone(self.store.window())
        self.out.clear()
        self.assertEqual(self.run_cli("window", "disarm"), 0)
        self.assertEqual(self.out, ["no window armed"])

    def test_a_bad_window_exits_2_with_the_reasons_and_records_nothing(self):
        self.assertEqual(self.run_cli("window", "arm", self.later(5), self.later(1)), 2)
        self.assertEqual(self.err, ["error: end must be after start"])
        self.assertIsNone(self.store.window())

    def test_arm_needs_both_times(self):
        self.assertEqual(self.run_cli("window", "arm", self.later(1)), 1)
        self.assertIsNone(self.store.window())

    def test_run_passes_the_stop_time_to_the_runner(self):
        seen = {}
        def fake_run(store, pf, **kw):
            seen.update(kw)
            return 0
        code = cli.main(["run", "--until", "2026-11-14T05:00:00Z"], store=self.store, out=self.out.append, err=self.err.append, run_fn=fake_run)
        self.assertEqual((code, seen["until"]), (0, "2026-11-14T05:00:00Z"))
        seen.clear()
        cli.main(["run"], store=self.store, out=self.out.append, err=self.err.append, run_fn=fake_run)
        self.assertIsNone(seen["until"])


if __name__ == "__main__":
    unittest.main()
