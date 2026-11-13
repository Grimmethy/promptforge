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
from core import jobs  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
