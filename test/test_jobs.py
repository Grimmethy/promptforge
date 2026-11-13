"""Render job queue core (server/core/jobs.py): the job record and its defaults, the licence rule, and the atomic file store. Temp folders only, no network,
no ComfyUI, no GPU (python3 -m unittest test.test_jobs)."""
import datetime
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from core import jobs  # noqa: E402

NOW = datetime.datetime(2026, 11, 13, 12, 0, 0, tzinfo=datetime.timezone.utc)


def spec(**kw):
    base = {"requester": "mapforge", "model": "NovaFlatILV9.safetensors", "positive": "a cottage", "width": 512, "height": 512, "seeds": [1, 2, 3]}
    base.update(kw)
    return base


def lookup_for(table):
    return lambda name: {"commercial_use": table.get(name, "unverified")}


class NormalizeTest(unittest.TestCase):
    def test_defaults_are_filled(self):
        j = jobs.normalize(spec(), now=NOW, rand=lambda: "abc123")
        self.assertEqual((j["version"], j["id"], j["state"], j["created_at"]), (1, "job-20261113120000-abc123", "queued", "2026-11-13T12:00:00Z"))
        self.assertEqual((j["priority"], j["commercial"], j["steps"], j["cfg"], j["negative"], j["loras"]), ("normal", False, 28, 5.0, "", []))
        self.assertEqual((j["destination"], j["needed_by"], j["card"], j["attempts"], j["error"], j["results"]), ({"kind": "none"}, None, None, 0, None, []))

    def test_a_krea2_model_gets_its_own_steps_and_cfg(self):
        j = jobs.normalize(spec(model="krea2:krea2_turbo.safetensors"), now=NOW)
        self.assertEqual((j["steps"], j["cfg"]), (8, 1.0))
        j = jobs.normalize(spec(model="krea2:k.safetensors", steps=12, cfg=2), now=NOW)
        self.assertEqual((j["steps"], j["cfg"]), (12, 2))

    def test_every_problem_is_reported_at_once(self):
        bad = spec(priority="soon", seeds=[], width=100, positive="", requester="x" * 61, destination={"kind": "copy-dir", "path": "rel/dir"})
        with self.assertRaises(jobs.JobError) as cm:
            jobs.normalize(bad, now=NOW)
        text = " | ".join(cm.exception.errors)
        for needle in ("priority", "seeds must be a list of 1 to 200", "width", "positive", "requester", "destination.path"):
            self.assertIn(needle, text)
        self.assertGreaterEqual(len(cm.exception.errors), 6)

    def test_seed_rules(self):
        with self.assertRaisesRegex(jobs.JobError, "201 entries|1 to 200"):
            jobs.normalize(spec(seeds=list(range(201))), now=NOW)
        jobs.normalize(spec(seeds=list(range(200))), now=NOW)
        with self.assertRaisesRegex(jobs.JobError, "must not repeat"):
            jobs.normalize(spec(seeds=[5, 5]), now=NOW)
        with self.assertRaisesRegex(jobs.JobError, r"seeds\[2\] must be an integer 0..4294967295"):
            jobs.normalize(spec(seeds=[1, 2, -1]), now=NOW)
        with self.assertRaisesRegex(jobs.JobError, r"seeds\[1\]"):
            jobs.normalize(spec(seeds=[1, True]), now=NOW)

    def test_loras_and_krea2(self):
        j = jobs.normalize(spec(loras=[{"file": "a.safetensors", "weight": 0.8}]), now=NOW)
        self.assertEqual(j["loras"], [{"file": "a.safetensors", "weight": 0.8}])
        with self.assertRaisesRegex(jobs.JobError, "not allowed with a krea2"):
            jobs.normalize(spec(model="krea2:k.safetensors", loras=[{"file": "a.safetensors", "weight": 1}]), now=NOW)
        with self.assertRaisesRegex(jobs.JobError, r"loras\[0\]"):
            jobs.normalize(spec(loras=[{"file": "a.safetensors", "weight": 3}]), now=NOW)

    def test_sizes_dates_and_destinations(self):
        for w in (63, 4104, 513):
            with self.assertRaises(jobs.JobError):
                jobs.normalize(spec(width=w), now=NOW)
        with self.assertRaisesRegex(jobs.JobError, "needed_by"):
            jobs.normalize(spec(needed_by="2026-13-40"), now=NOW)
        jobs.normalize(spec(needed_by="2026-11-20"), now=NOW)
        j = jobs.normalize(spec(destination={"kind": "copy-dir", "path": "/tmp/out"}), now=NOW)
        self.assertEqual(j["destination"], {"kind": "copy-dir", "path": "/tmp/out"})
        j = jobs.normalize(spec(destination={"kind": "characterforge", "slug": "cindy", "role": "portrait"}), now=NOW)
        self.assertEqual(j["destination"]["slug"], "cindy")
        with self.assertRaisesRegex(jobs.JobError, "destination.kind"):
            jobs.normalize(spec(destination={"kind": "ftp"}), now=NOW)


class LicenceTest(unittest.TestCase):
    def job(self, **kw):
        return jobs.normalize(spec(**kw), now=NOW)

    def test_commercial_job_needs_yes_or_conditional(self):
        for answer in ("no", "unverified"):
            msg = jobs.licence_problem(self.job(commercial=True), lookup_for({"NovaFlatILV9.safetensors": answer}))
            self.assertIn("NovaFlatILV9.safetensors", msg)
            self.assertIn("'%s'" % answer, msg)
        for answer in ("yes", "conditional"):
            self.assertIsNone(jobs.licence_problem(self.job(commercial=True), lookup_for({"NovaFlatILV9.safetensors": answer})))

    def test_every_lora_is_checked_too(self):
        j = self.job(commercial=True, loras=[{"file": "bad.safetensors", "weight": 1}])
        msg = jobs.licence_problem(j, lookup_for({"NovaFlatILV9.safetensors": "yes", "bad.safetensors": "no"}))
        self.assertIn("bad.safetensors is 'no'", msg)
        self.assertNotIn("NovaFlatILV9", msg)

    def test_non_commercial_is_never_checked(self):
        calls = []
        self.assertIsNone(jobs.licence_problem(self.job(commercial=False), lambda n: calls.append(n) or {"commercial_use": "no"}))
        self.assertEqual(calls, [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "jobs")
        self.table = {"NovaFlatILV9.safetensors": "yes"}

    def store(self, **kw):
        kw.setdefault("lookup", lookup_for(self.table))
        kw.setdefault("clock", lambda: NOW)
        return jobs.JobStore(self.root, **kw)

    def files(self, state):
        d = os.path.join(self.root, state)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_add_writes_one_file_and_no_temp(self):
        j = self.store().add(spec())
        self.assertEqual(self.files("queued"), [j["id"] + ".json"])
        with open(os.path.join(self.root, "queued", j["id"] + ".json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), j)

    def test_add_refuses_a_bad_spec_and_an_uncleared_commercial_job(self):
        s = self.store()
        with self.assertRaises(jobs.JobError):
            s.add(spec(seeds=[]))
        self.table["NovaFlatILV9.safetensors"] = "no"
        with self.assertRaisesRegex(jobs.JobError, "commercial use is not cleared"):
            s.add(spec(commercial=True))
        self.assertEqual(self.files("queued"), [])

    def test_an_id_collision_retries_and_both_jobs_exist(self):
        seq = iter(["aaaaaa", "aaaaaa", "bbbbbb"])
        s = self.store(rand=lambda: next(seq))
        a, b = s.add(spec()), s.add(spec(requester="other"))
        self.assertEqual((a["id"][-6:], b["id"][-6:]), ("aaaaaa", "bbbbbb"))
        self.assertEqual(len(self.files("queued")), 2)
        self.assertEqual(s.get(a["id"])["requester"], "mapforge")
        self.assertEqual(s.get(b["id"])["requester"], "other")

    def test_forty_threads_adding_at_once_give_forty_valid_files(self):
        s = jobs.JobStore(self.root, lookup=lookup_for(self.table))
        errors = []

        def work(i):
            try:
                s.add(spec(requester="t%d" % i, seeds=[i]))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=work, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        names = self.files("queued")
        self.assertEqual(len(names), 40)
        self.assertFalse([n for n in names if n.startswith(".") or n.endswith(".tmp")])
        self.assertEqual(len(s.list()), 40)

    def test_hold_release_remove_and_their_refusals(self):
        s = self.store()
        j = s.add(spec())
        self.assertEqual(s.hold(j["id"])["state"], "held")
        self.assertEqual((self.files("queued"), len(self.files("held"))), ([], 1))
        with self.assertRaisesRegex(jobs.JobError, "allowed only from queued"):
            s.hold(j["id"])
        self.assertEqual(s.release(j["id"])["state"], "queued")
        with self.assertRaisesRegex(jobs.JobError, "allowed only from held"):
            s.release(j["id"])
        self.assertEqual(s.remove(j["id"])["state"], "removed")
        with self.assertRaisesRegex(jobs.JobError, "allowed only from queued, held"):
            s.remove(j["id"])
        with self.assertRaisesRegex(jobs.JobError, "no job"):
            s.hold("job-20260101000000-000000")
        running = s.add(spec())
        s.set_state(running["id"], "running")
        with self.assertRaises(jobs.JobError):
            s.hold(running["id"])
        with self.assertRaises(jobs.JobError):
            s.remove(running["id"])

    def test_set_state_moves_the_file_and_merges_the_patch(self):
        s = self.store()
        j = s.add(spec())
        rec = s.set_state(j["id"], "running", {"attempts": 1})
        self.assertEqual((rec["state"], rec["attempts"]), ("running", 1))
        self.assertEqual((self.files("queued"), self.files("running")), ([], [j["id"] + ".json"]))
        with self.assertRaisesRegex(jobs.JobError, "unknown state"):
            s.set_state(j["id"], "paused")

    def test_list_filters_and_a_record_in_two_folders_appears_once(self):
        s = self.store()
        a, b = s.add(spec(requester="a")), s.add(spec(requester="b"))
        s.set_state(b["id"], "done")
        self.assertEqual([r["id"] for r in s.list(state="queued")], [a["id"]])
        self.assertEqual([r["id"] for r in s.list(requester="b")], [b["id"]])
        os.makedirs(os.path.join(self.root, "queued"), exist_ok=True)
        with open(os.path.join(self.root, "queued", b["id"] + ".json"), "w", encoding="utf-8") as fh:   # the crash leftover: same id, earlier state
            json.dump(dict(b, state="queued"), fh)
        both = s.list()
        self.assertEqual(sorted(r["id"] for r in both), sorted([a["id"], b["id"]]))
        self.assertEqual([r["state"] for r in both if r["id"] == b["id"]], ["done"])
        self.assertEqual(s.get(b["id"])["state"], "done")

    def test_a_corrupt_file_is_skipped(self):
        s = self.store()
        j = s.add(spec())
        with open(os.path.join(self.root, "queued", "job-20260101000000-ffffff.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual([r["id"] for r in s.list()], [j["id"]])
        self.assertIsNone(s.get("job-20260101000000-ffffff"))

    def test_archive_old_moves_only_old_finished_records(self):
        s = self.store()
        old = NOW - datetime.timedelta(days=40)
        ids = {}
        for name in ("done", "failed", "removed", "queued", "held", "running", "blocked", "newdone"):
            rec = s.add(spec(requester=name))
            ids[name] = rec["id"]
            state = "done" if name == "newdone" else name
            if state != "queued":
                patch = {"finished_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")} if name == "newdone" else {"created_at": old.strftime("%Y-%m-%dT%H:%M:%SZ")}
                s.set_state(rec["id"], state, patch)
            else:
                s.set_state(rec["id"], "queued", {"created_at": old.strftime("%Y-%m-%dT%H:%M:%SZ")})
        moved = s.archive_old(days=30)
        self.assertEqual(sorted(moved), sorted([ids["done"], ids["failed"], ids["removed"]]))
        self.assertEqual(sorted(self.files("archive")), sorted(i + ".json" for i in moved))
        for name in ("queued", "held", "running", "blocked", "newdone"):
            self.assertIsNotNone(s.get(ids[name]), name)
        self.assertIsNone(s.get(ids["done"]))


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.table = {"NovaFlatILV9.safetensors": "yes"}
        self.store = jobs.JobStore(os.path.join(self.tmp.name, "jobs"), lookup=lookup_for(self.table), clock=lambda: NOW)

    def test_only_the_patched_keys_change_and_results_and_id_stay(self):
        j = self.store.add(spec())
        self.store.set_state(j["id"], "queued", {"results": [{"seed": 1, "status": "ok"}]})
        rec = self.store.update(j["id"], {"priority": "urgent", "notes": "hi", "seeds": [5, 6]})
        self.assertEqual((rec["priority"], rec["notes"], rec["seeds"]), ("urgent", "hi", [5, 6]))
        for key in ("id", "created_at", "model", "positive", "width", "requester", "state", "attempts"):
            self.assertEqual(rec[key], j[key], key)
        self.assertEqual(rec["results"], [{"seed": 1, "status": "ok"}])
        self.assertEqual(self.store.get(j["id"]), rec)

    def test_a_held_job_can_be_edited_and_stays_held(self):
        j = self.store.add(spec())
        self.store.hold(j["id"])
        rec = self.store.update(j["id"], {"positive": "a new prompt", "negative": "blurry", "needed_by": "2026-12-01", "purpose": "new purpose"})
        self.assertEqual((rec["state"], rec["positive"], rec["negative"], rec["needed_by"], rec["purpose"]), ("held", "a new prompt", "blurry", "2026-12-01", "new purpose"))

    def test_keys_outside_the_editable_list_are_refused_together(self):
        j = self.store.add(spec())
        with self.assertRaisesRegex(jobs.JobError, "cannot edit: model"):
            self.store.update(j["id"], {"model": "x.safetensors"})
        with self.assertRaisesRegex(jobs.JobError, "cannot edit: width, requester"):
            self.store.update(j["id"], {"width": 64, "requester": "x"})
        self.assertEqual(self.store.get(j["id"])["model"], j["model"])

    def test_bad_values_report_every_problem_and_change_nothing(self):
        j = self.store.add(spec())
        with self.assertRaises(jobs.JobError) as cm:
            self.store.update(j["id"], {"priority": "soon", "seeds": []})
        self.assertGreaterEqual(len(cm.exception.errors), 2)
        self.assertEqual(self.store.get(j["id"]), j)
        with self.assertRaisesRegex(jobs.JobError, "changes must be an object"):
            self.store.update(j["id"], ["priority"])

    def test_a_running_or_finished_job_gives_a_state_error_with_the_exact_message(self):
        j = self.store.add(spec())
        for state in ("running", "done"):
            self.store.set_state(j["id"], state)
            with self.assertRaises(jobs.JobStateError) as cm:
                self.store.update(j["id"], {"notes": "x"})
            self.assertEqual(str(cm.exception), "job %s is %s; it can be edited only while queued or held" % (j["id"], state))

    def test_a_missing_job_is_a_plain_job_error(self):
        with self.assertRaises(jobs.JobError) as cm:
            self.store.update("job-20260101000000-000000", {"notes": "x"})
        self.assertNotIsInstance(cm.exception, jobs.JobStateError)
        self.assertIn("no job", str(cm.exception))

    def test_the_licence_rule_is_checked_again(self):
        j = self.store.add(spec(commercial=True))
        self.table["NovaFlatILV9.safetensors"] = "no"
        with self.assertRaisesRegex(jobs.JobError, "commercial use is not cleared"):
            self.store.update(j["id"], {"notes": "x"})

    def test_wrong_state_transitions_raise_a_state_error_and_a_missing_job_does_not(self):
        j = self.store.add(spec())
        with self.assertRaises(jobs.JobStateError):
            self.store.release(j["id"])
        self.store.set_state(j["id"], "running")
        with self.assertRaises(jobs.JobStateError):
            self.store.hold(j["id"])
        with self.assertRaises(jobs.JobStateError):
            self.store.remove(j["id"])
        with self.assertRaises(jobs.JobError) as cm:
            self.store.hold("job-20260101000000-000000")
        self.assertNotIsInstance(cm.exception, jobs.JobStateError)


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "jobs")
        self.store = jobs.JobStore(self.root, clock=lambda: NOW)

    def test_a_valid_window_is_recorded_and_read_back(self):
        self.assertIsNone(self.store.window())
        rec = self.store.set_window("2026-11-14T01:00:00Z", "2026-11-14T05:00:00Z")
        self.assertEqual(rec, {"start": "2026-11-14T01:00:00Z", "end": "2026-11-14T05:00:00Z", "armed_at": "2026-11-13T12:00:00Z"})
        self.assertEqual(self.store.window(), rec)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "window.json")))
        self.assertEqual(os.listdir(self.root), ["window.json"])                          # no temp file left

    def test_a_window_that_has_already_started_is_fine_while_its_end_is_ahead(self):
        self.store.set_window("2026-11-13T11:00:00Z", "2026-11-13T15:00:00Z")

    def test_each_problem_is_reported(self):
        cases = (("tomorrow", "2026-11-14T05:00:00Z", "start must be a UTC time"), ("2026-11-14T01:00:00Z", None, "end must be a UTC time"),
                 ("2026-11-14T05:00:00Z", "2026-11-14T01:00:00Z", "end must be after start"),
                 ("2026-11-14T01:00:00Z", "2026-11-14T14:00:00Z", "at most 12 hours"),
                 ("2026-11-13T09:00:00Z", "2026-11-13T11:00:00Z", "end must be in the future"))
        for start, end, text in cases:
            with self.assertRaisesRegex(jobs.JobError, text):
                self.store.set_window(start, end)
        for start, end in (("2026-11-14T01:00:00Z", "2026-11-14T01:00:00Z"), ("2026-11-13T11:00:00Z", "2026-11-13T12:00:00Z")):     # zero length; ending exactly now
            with self.assertRaises(jobs.JobError, msg=end):
                self.store.set_window(start, end)
        with self.assertRaises(jobs.JobError) as cm:
            self.store.set_window("x", "y")
        self.assertEqual(len(cm.exception.errors), 2)
        self.assertIsNone(self.store.window())
        self.assertEqual(self.store.set_window("2026-11-14T01:00:00Z", "2026-11-14T13:00:00Z")["end"], "2026-11-14T13:00:00Z")     # exactly 12 hours is allowed

    def test_clear_window(self):
        self.store.set_window("2026-11-14T01:00:00Z", "2026-11-14T05:00:00Z")
        self.assertTrue(self.store.clear_window())
        self.assertFalse(self.store.clear_window())
        self.assertIsNone(self.store.window())

    def test_a_corrupt_or_incomplete_window_file_reads_as_none(self):
        os.makedirs(self.root)
        path = os.path.join(self.root, "window.json")
        for text in ("{broken", "{}", '{"start": "a", "end": "b"}', "[1, 2]"):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.assertIsNone(self.store.window(), text)


if __name__ == "__main__":
    unittest.main()
