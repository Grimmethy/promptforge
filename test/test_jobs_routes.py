"""The render job queue HTTP routes (/api/jobs). A fresh Flask app with the routes registered on a temp job folder and an injected licence lookup; no network,
no ComfyUI, no GPU. Needs flask, so run it with the repo venv:  .venv/bin/python -m unittest test.test_jobs_routes"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from flask import Flask  # noqa: E402

from core import jobs, jobs_routes  # noqa: E402

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
REMOTE = {"REMOTE_ADDR": "100.80.1.14"}


def spec(**kw):
    base = {"requester": "mapforge", "model": "NovaFlatILV9.safetensors", "positive": "a cottage", "width": 512, "height": 512, "seeds": [1, 2]}
    base.update(kw)
    return base


class RoutesBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "jobs")
        self.table = {"NovaFlatILV9.safetensors": "yes"}
        self.store = jobs.JobStore(self.root, lookup=lambda n: {"commercial_use": self.table.get(n, "unverified")})
        app = Flask(__name__)
        jobs_routes.register(app, lambda: self.store)
        self.client = app.test_client()

    def post(self, path, body=None, env=LOCAL, **kw):
        return self.client.post(path, json=body, environ_overrides=env, **kw)

    def file_job(self, **kw):
        r = self.post("/api/jobs", spec(**kw))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["job"]


class AddTest(RoutesBase):
    def test_a_good_job_is_filed_and_the_file_exists(self):
        r = self.post("/api/jobs", spec())
        self.assertEqual(r.status_code, 201)
        job = r.get_json()["job"]
        self.assertEqual((job["state"], job["steps"]), ("queued", 28))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "queued", job["id"] + ".json")))

    def test_bad_requests_answer_400_with_an_errors_list(self):
        r = self.post("/api/jobs", spec(seeds=[], width=100))
        body = r.get_json()
        self.assertEqual(r.status_code, 400)
        self.assertGreaterEqual(len(body["errors"]), 2)
        self.assertIn("seeds", body["error"])
        self.table["NovaFlatILV9.safetensors"] = "no"
        r = self.post("/api/jobs", spec(commercial=True))
        self.assertEqual(r.status_code, 400)
        self.assertIn("commercial use is not cleared", r.get_json()["error"])
        for bad in (["not", "an", "object"], "text", 5):
            self.assertEqual(self.post("/api/jobs", bad).status_code, 400)
        r = self.client.post("/api/jobs", data="{broken", content_type="application/json", environ_overrides=LOCAL)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.store.list(), [])

    def test_writes_must_come_from_this_machine(self):
        r = self.post("/api/jobs", spec(), env=REMOTE)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["error"], "jobs can only be filed from this machine")
        r = self.client.post("/api/jobs", json=spec(), environ_overrides=LOCAL, headers={"X-Forwarded-For": "100.80.1.14"})
        self.assertEqual(r.status_code, 403)
        for header in ("Forwarded", "X-Real-IP"):
            r = self.client.post("/api/jobs", json=spec(), environ_overrides=LOCAL, headers={header: "x"})
            self.assertEqual(r.status_code, 403, header)
        self.assertEqual(self.store.list(), [])


class ReadTest(RoutesBase):
    def test_list_filters_and_counts(self):
        a = self.file_job(requester="a")
        b = self.file_job(requester="b")
        self.store.set_state(b["id"], "done")
        body = self.client.get("/api/jobs", environ_overrides=LOCAL).get_json()
        self.assertEqual(len(body["jobs"]), 2)
        self.assertEqual((body["counts"]["queued"], body["counts"]["done"], body["counts"]["held"]), (1, 1, 0))
        self.assertEqual(sorted(body["counts"]), sorted(jobs.STATES))
        q = self.client.get("/api/jobs?state=queued").get_json()
        self.assertEqual([j["id"] for j in q["jobs"]], [a["id"]])
        self.assertEqual(q["counts"]["done"], 1)            # counts always cover every job, not just the filtered ones
        r = self.client.get("/api/jobs?requester=b").get_json()
        self.assertEqual([j["id"] for j in r["jobs"]], [b["id"]])
        self.assertEqual(self.client.get("/api/jobs?state=paused").status_code, 400)

    def test_get_one_and_missing(self):
        j = self.file_job()
        r = self.client.get("/api/jobs/" + j["id"])
        self.assertEqual((r.status_code, r.get_json()["job"]["id"]), (200, j["id"]))
        self.assertEqual(self.client.get("/api/jobs/job-20260101000000-000000").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/not-an-id").status_code, 404)

    def test_summary_counts_by_state_requester_and_priority(self):
        self.file_job(requester="a", priority="urgent")
        self.file_job(requester="a")
        done = self.file_job(requester="b", priority="bulk")
        self.store.set_state(done["id"], "done")
        s = self.client.get("/api/jobs/summary").get_json()
        self.assertEqual((s["counts"]["queued"], s["counts"]["done"]), (2, 1))
        self.assertEqual((s["by_requester"]["a"]["queued"], s["by_requester"]["b"]["done"]), (2, 1))
        self.assertEqual(s["by_priority"], {"urgent": 1, "normal": 1, "bulk": 1})

    def test_reads_are_allowed_from_a_remote_address(self):
        self.file_job()
        self.assertEqual(self.client.get("/api/jobs", environ_overrides=REMOTE).status_code, 200)
        self.assertEqual(self.client.get("/api/jobs/summary", environ_overrides=REMOTE).status_code, 200)


class TransitionTest(RoutesBase):
    def test_hold_release_remove_and_their_refusals(self):
        j = self.file_job()
        self.assertEqual(self.post("/api/jobs/%s/hold" % j["id"]).get_json()["job"]["state"], "held")
        self.assertEqual(self.post("/api/jobs/%s/hold" % j["id"]).status_code, 409)
        self.assertEqual(self.post("/api/jobs/%s/release" % j["id"]).get_json()["job"]["state"], "queued")
        self.assertEqual(self.post("/api/jobs/%s/release" % j["id"]).status_code, 409)
        self.assertEqual(self.post("/api/jobs/%s/remove" % j["id"]).get_json()["job"]["state"], "removed")
        self.assertEqual(self.post("/api/jobs/%s/remove" % j["id"]).status_code, 409)
        self.assertEqual(self.post("/api/jobs/job-20260101000000-000000/hold").status_code, 404)

    def test_transitions_must_come_from_this_machine(self):
        j = self.file_job()
        for action in ("hold", "release", "remove"):
            self.assertEqual(self.post("/api/jobs/%s/%s" % (j["id"], action), env=REMOTE).status_code, 403)
        self.assertEqual(self.store.get(j["id"])["state"], "queued")


class RealAppTest(unittest.TestCase):
    def test_the_real_application_has_the_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PROMPTFORGE_JOBS_DIR"] = tmp
            self.addCleanup(os.environ.pop, "PROMPTFORGE_JOBS_DIR", None)
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import app as pf
            rules = {r.rule for r in pf.app.url_map.iter_rules()}
            for rule in ("/api/jobs", "/api/jobs/summary", "/api/jobs/<jid>", "/api/jobs/<jid>/hold"):
                self.assertIn(rule, rules)
            r = pf.app.test_client().get("/api/jobs/summary")
            self.assertEqual((r.status_code, r.get_json()["counts"]["queued"]), (200, 0))
            self.assertEqual(os.listdir(tmp), [])            # reading wrote nothing


if __name__ == "__main__":
    unittest.main()
