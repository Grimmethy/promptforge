"""The render job queue in the PromptForge header (web/queue.js): pure formatting and grouping helpers loaded with node (skipped when node is missing), plus
the wiring in app.js and index.html pinned as text. No server, no ComfyUI, no GPU (python3 -m unittest test.test_queue_ui)."""
import json
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "..", "web")
NODE = shutil.which("node")


def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as fh:
        return fh.read()


def js(expr):
    code = "const q = require(%s); console.log(JSON.stringify(%s));" % (json.dumps(os.path.join(WEB, "queue.js")), expr)
    return json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)


@unittest.skipUnless(NODE, "node is not installed")
class FormatDurationTest(unittest.TestCase):
    def test_boundaries(self):
        got = js("[0, 1, 59, 59.4, 60, 119, 3599, 3600, 3900, 7325, null, 'x'].map(q.formatDuration)")
        self.assertEqual(got, ["0s", "1s", "59s", "59s", "1m", "1m", "59m", "1h 00m", "1h 05m", "2h 02m", "0s", "0s"])

    def test_matches_the_runner_format(self):
        import sys
        sys.path.insert(0, os.path.join(HERE, ".."))
        from scripts.jobs import runner
        for n in (0, 5, 59, 60, 61, 599, 3599, 3600, 3661, 9000, 86399):
            self.assertEqual(js("q.formatDuration(%d)" % n), runner.format_seconds(n), n)


@unittest.skipUnless(NODE, "node is not installed")
class SummaryTest(unittest.TestCase):
    def test_empty_and_missing_give_nothing(self):
        self.assertEqual(js("[q.summaryText(null), q.summaryText(undefined), q.summaryText({}), q.summaryText({queued: 0, pictures: 0, total_seconds: 0})]"), ["", "", "", ""])

    def test_plural_singular_and_rough(self):
        self.assertEqual(js("q.summaryText({queued: 73, pictures: 236, total_seconds: 2580, rough: true})"), "Queue: 73 jobs · 236 pictures · ~43m (rough)")
        self.assertEqual(js("q.summaryText({queued: 1, pictures: 1, total_seconds: 30, rough: false})"), "Queue: 1 job · 1 picture · ~30s")
        self.assertEqual(js("q.summaryText({queued: 2, pictures: 1, total_seconds: 4000})"), "Queue: 2 jobs · 1 picture · ~1h 06m")


@unittest.skipUnless(NODE, "node is not installed")
class GroupTest(unittest.TestCase):
    def test_groups_sorted_by_requester_keep_job_order_and_sum_pictures(self):
        jobs = [{"id": "1", "requester": "mapforge", "seeds": [1, 2]}, {"id": "2", "requester": "authorforge", "seeds": [1]},
                {"id": "3", "requester": "mapforge", "seeds": [5, 6, 7]}, {"id": "4", "requester": "authorforge"}]
        got = js("q.groupByRequester(%s).map(g => [g.requester, g.jobs.map(j => j.id), g.pictures])" % json.dumps(jobs))
        self.assertEqual(got, [["authorforge", ["2", "4"], 1], ["mapforge", ["1", "3"], 5]])
        self.assertEqual(js("[q.groupByRequester(null), q.groupByRequester([])]"), [[], []])

    def test_job_row_flags_per_state(self):
        for state, hold, rel, rem in (("queued", True, False, True), ("held", False, True, True), ("running", False, False, False),
                                      ("done", False, False, False), ("failed", False, False, False), ("blocked", False, False, False)):
            job = {"id": "j", "purpose": "p", "model": "m", "seeds": [1, 2, 3], "priority": "bulk", "state": state}
            row = js("q.jobRow(%s, {j: 42})" % json.dumps(job))
            self.assertEqual((row["canHold"], row["canRelease"], row["canRemove"]), (hold, rel, rem), state)
            self.assertEqual((row["id"], row["purpose"], row["model"], row["seeds"], row["priority"], row["seconds"]), ("j", "p", "m", 3, "bulk", 42))

    def test_job_row_seconds_missing_or_zero(self):
        job = {"id": "j", "model": "m", "seeds": [1], "priority": "normal", "state": "queued"}
        self.assertIsNone(js("q.jobRow(%s, {}).seconds" % json.dumps(job)))
        self.assertIsNone(js("q.jobRow(%s).seconds" % json.dumps(job)))
        self.assertEqual(js("q.jobRow(%s, {j: 0}).seconds" % json.dumps(job)), 0)
        self.assertEqual(js("q.jobRow(%s, {}).purpose" % json.dumps(job)), "")


class WiringTest(unittest.TestCase):
    def test_queue_js_is_pure(self):
        self.assertNotRegex(read("queue.js"), r"setInterval|setTimeout|fetch\(|document\.|window\.[a-z]")

    def test_queue_js_loads_before_app_js_and_the_header_has_its_elements(self):
        html = read("index.html")
        self.assertTrue(0 <= html.find("/queue.js") < html.find("/app.js"))
        for needle in ('id="queueSummary"', 'id="queuePanel"'):
            self.assertIn(needle, html)

    def test_app_js_uses_the_helpers_and_adds_no_timer(self):
        app = read("app.js")
        for needle in ("PFQueue.summaryText", "PFQueue.groupByRequester", "PFQueue.jobRow", "/api/jobs/estimate", "'/api/jobs/' + id + '/' + what"):
            self.assertIn(needle, app)
        self.assertEqual(len(re.findall(r"setInterval\(", app)), 1)                 # still only the 15 s comfyStatus poll
        body = app[app.index("async function comfyStatus()"):app.index("async function queueStatus()")]
        self.assertIn("queueStatus();", body)

    def test_opening_the_panel_draws_it_at_once(self):
        app = read("app.js")
        click = app[app.index("sum.onclick"):]
        click = click[:click.index("\n")]
        self.assertIn("queueStatus();", click)

    def test_the_panel_offers_only_the_contracted_actions(self):
        app = read("app.js")
        panel = app[app.index("function renderQueuePanel"):app.index("function renderComfyBtn")]
        for what in ("'hold'", "'release'", "'remove'", "'edit'"):
            self.assertIn("act(row.id, " + what, panel)
        self.assertNotRegex(panel, r"/api/jobs/run|forge")


if __name__ == "__main__":
    unittest.main()
