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

    def test_queue_rows_have_distinct_labels_and_full_titles(self):
        # SOURCE-LEVEL check, not render-level. This harness loads only web/queue.js
        # under node (js() helper) and has no DOM stub, so renderQueuePanel cannot run
        # here. We assert on the generated label and panel source in web/app.js instead.
        app = read("app.js")
        self.assertIn("function queueRowLabel(", app)
        label = app[app.index("function queueRowLabel("):app.index("function renderQueuePanel")]
        panel = app[app.index("function renderQueuePanel"):app.index("function renderComfyBtn")]

        # Distinct visible text: the panel passes sharedPrefix to queueRowLabel,
        # and the label strips the shared prefix from the purpose.
        self.assertIn("sharedPrefix", label)
        self.assertRegex(panel, r"queueRowLabel\(\s*\w+\s*,\s*sharedPrefix\s*\)")
        self.assertRegex(label, r"sharedPrefix\.length|startsWith\(\s*sharedPrefix|replace\(\s*sharedPrefix")

        # Estimate: read from perJob[job.id] and rendered as "~N s".
        # The literal "seconds" is deliberately not required: the code uses perJob[j.id] as a number.
        self.assertRegex(panel, r"perJob\[[^\]]+\]")
        self.assertIn("~", panel)
        self.assertIn("' s'", panel)

        # Title contract: full purpose, then the first 80 chars of the prompt.
        self.assertNotIn("title: row.id", panel)
        self.assertRegex(panel, r"fullPurpose|purpose")
        self.assertRegex(panel, r"slice\(\s*0\s*,\s*80\s*\)|substring\(\s*0\s*,\s*80\s*\)")


@unittest.skipUnless(NODE, "node is not installed")
class QueueRowLabelTest(unittest.TestCase):
    def test_shared_prefix_rows_get_distinct_visible_labels(self):
        # queueRowLabel is pure, so its source is taken from web/app.js and run under node.
        app = read("app.js")
        start = app.index("function queueRowLabel(")
        fn = app[start:app.index("\n}\n", start) + 3]
        prefix = "Christmas coloring book - page "
        code = fn + "\nconsole.log(JSON.stringify([queueRowLabel({id: 'a', purpose: %s}, %s), queueRowLabel({id: 'b', purpose: %s}, %s)]));" % (json.dumps(prefix + "3"), json.dumps(prefix), json.dumps(prefix + "4"), json.dumps(prefix))
        labels = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)
        self.assertNotEqual(labels[0], labels[1])
        self.assertTrue(all(labels))


@unittest.skipUnless(NODE, "node is not installed")
class RunHelpersTest(unittest.TestCase):
    def test_parse_hours(self):
        got = js("['2', ' 0.25 ', '12', '7.5', '0.24', '12.1', '0', '', 'abc', '1e1', '-1', '2h', null, undefined].map(q.parseHours)")
        self.assertEqual(got, [2, 0.25, 12, 7.5, None, None, None, None, None, None, None, None, None, None])

    def test_can_run(self):
        self.assertEqual(js("[q.canRun({queued: 3}, null), q.canRun({queued: 3}, {running: false}), q.canRun({queued: 3}, {running: true}), q.canRun({queued: 0}, null), "
                            "q.canRun(null, null), q.canRun(undefined, {running: false})]"), [True, True, False, False, False, False])

    def test_confirm_text_says_what_happens_to_the_machine(self):
        text = js("q.confirmText({queued: 73, pictures: 236, total_seconds: 2580, rough: true}, 2)")
        for needle in ("pauses the Agent Manager pipeline", "unloads the local model", "takes the GPU", "renders 236 pictures", "about 43m, rough", "at most 2 hours"):
            self.assertIn(needle, text)
        self.assertIn("at most 1 hour.", js("q.confirmText({queued: 1, pictures: 1, total_seconds: 30}, 1)"))
        self.assertIn("renders 1 picture ", js("q.confirmText({queued: 1, pictures: 1, total_seconds: 30}, 1)"))
        self.assertNotIn("renders", js("q.confirmText(null, 2)"))
        self.assertIn("takes the GPU, and stops by itself", js("q.confirmText(null, 2)"))

    def test_run_label(self):
        fmt = "t => 'T' + t.slice(11, 16)"
        run = {"running": True, "phase": "rendering", "pictures": 14, "until": "2026-11-13T21:30:00Z"}
        self.assertEqual(js("q.runLabel(%s, %s)" % (json.dumps(run), fmt)), "Window: rendering · 14 pictures · until T21:30")
        self.assertEqual(js("q.runLabel(%s)" % json.dumps(dict(run, phase="forge-on", pictures=1))), "Window: entering Forge mode · 1 picture · until 2026-11-13T21:30:00Z")
        self.assertEqual(js("q.runLabel(%s)" % json.dumps(dict(run, phase="leaving", until=None))), "Window: leaving Forge mode · 14 pictures")
        self.assertEqual(js("q.runLabel({running: false, phase: 'refused', reasons: ['a', 'b']})"), "Last window did not start: a; b")
        self.assertEqual(js("q.runLabel({running: false, phase: 'forge-failed', reasons: []})"), "Last window did not start: forge-failed")
        self.assertEqual(js("q.runLabel({running: false, phase: 'ended'})"), "Last window ended unexpectedly")
        self.assertEqual(js("[q.runLabel(null), q.runLabel({running: false, phase: 'done'}), q.runLabel({running: false, phase: 'idle'})]"), ["", "", ""])


@unittest.skipUnless(NODE, "node is not installed")
class HeaderTextTest(unittest.TestCase):
    def test_header_text(self):
        run = {"running": True, "phase": "rendering", "pictures": 3, "until": "U"}
        self.assertEqual(js("q.headerText('Queue: 2 jobs', %s)" % json.dumps(run)), "Window: rendering · 3 pictures · until U")        # progress replaces the summary
        refused = {"running": False, "phase": "refused", "reasons": ["too little memory"]}
        self.assertEqual(js("q.headerText('Queue: 2 jobs', %s)" % json.dumps(refused)), "Queue: 2 jobs · Last window did not start: too little memory")
        self.assertEqual(js("q.headerText('Queue: 2 jobs', {running: false, phase: 'done'})"), "Queue: 2 jobs")
        self.assertEqual(js("q.headerText('', %s)" % json.dumps(refused)), "Last window did not start: too little memory")
        self.assertEqual(js("[q.headerText('', null), q.headerText('Queue: 1 job', null)]"), ["", "Queue: 1 job"])

    def test_app_js_builds_the_line_with_the_helper(self):
        self.assertIn("PFQueue.headerText(", read("app.js"))


class RunWiringTest(unittest.TestCase):
    def test_the_header_has_the_run_controls(self):
        html = read("index.html")
        for needle in ('id="runQueueBtn"', 'id="stopRunBtn"', 'id="runDialog"', 'id="runHours"', 'id="runStart"', 'id="runCancel"'):
            self.assertIn(needle, html)

    def test_only_a_click_starts_a_window(self):
        app = read("app.js")
        self.assertEqual(app.count("'/api/jobs/run', { method: 'POST'"), 1)
        start = app[app.index("$('#runStart').onclick"):]
        start = start[:start.index("\n  };")]
        self.assertIn("'/api/jobs/run', { method: 'POST'", start)
        self.assertIn("PFQueue.parseHours", start)
        self.assertIn("if (h === null) return;", start)                          # an invalid stop time never posts
        self.assertIn("$('#runStart').disabled = h === null;", app)
        self.assertEqual(len(re.findall(r"setInterval\(", app)), 1)                  # still only the 15 s poll
        self.assertNotRegex(read("queue.js"), r"setInterval|setTimeout|fetch\(|document\.")

    def test_the_stop_button_posts_the_stop_route_and_the_dialog_states_the_effect(self):
        app = read("app.js")
        self.assertIn("'/api/jobs/run/stop', { method: 'POST' }", app)
        self.assertIn("PFQueue.confirmText(est", app)
        self.assertIn("PFQueue.canRun(est, run)", app)
        self.assertIn("PFQueue.headerText(", app)


if __name__ == "__main__":
    unittest.main()
