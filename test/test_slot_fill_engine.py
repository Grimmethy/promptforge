"""The slot-fill engine foundation: the run loop, the PromptForge client, the headroom check (scripts/slot_fill/).

Run: TMPDIR=/dev/shm/x .venv/bin/python -B -m unittest test.test_slot_fill_engine   (from the repo root)
No real forge is called: a fake Adapter and fake PromptForge client drive the loop, and PFClient itself talks to a tiny http.server in the test.
"""
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from scripts.slot_fill import engine, headroom  # noqa: E402
from scripts.slot_fill.adapter import Adapter  # noqa: E402
from scripts.slot_fill.errors import SlotFillError  # noqa: E402
from scripts.slot_fill.pf_client import Generated, PFClient  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def noisy_png(size=(200, 200)):
    import random
    rnd = random.Random(1)
    im = Image.new("RGB", size)
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(size[0] * size[1])])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def blank_png():
    buf = io.BytesIO()
    Image.new("RGB", (900, 900), (200, 200, 200)).save(buf, "PNG")
    return buf.getvalue()


GOOD = noisy_png() if Image else b"x" * 40000


class FakeAdapter(Adapter):
    name = "fake"

    def __init__(self, slugs=("a", "b"), roles=("r1", "r2"), start=None, targets=None, fail_link=False):
        self._slugs, self._roles = list(slugs), list(roles)
        self.have = {s: {r: 0 for r in roles} for s in slugs}
        for s, d in (start or {}).items():
            self.have[s].update(d)
        self.targets = targets or {}
        self.linked = []
        self.fail_link = fail_link

    def entries(self):
        return [{"slug": s} for s in self._slugs]

    def roles(self):
        return list(self._roles)

    def counts(self, slug):
        return dict(self.have[slug])

    def target(self, entry, role):
        return self.targets.get((entry["slug"], role), 10)

    def build_spec(self, entry, role, rnd):
        return "tag-%s-%d" % (role, rnd), {"slug": entry["slug"], "role": role, "round": rnd}

    def link(self, slug, role, tag, rnd, spec, job, name, data):
        if self.fail_link:
            raise SlotFillError("link refused")
        self.have[slug][role] += 1
        self.linked.append((slug, role, tag, rnd, name, job))
        return {"file": "%s-%02d.png" % (role, self.have[slug][role])}


class FakePF:
    def __init__(self, results=None, data=GOOD):
        self.calls, self.acquired, self.released = [], [], 0
        self.results = list(results or [])         # per call: an exception to raise, or None
        self.data = data
        self.job = {"status": "done", "images": ["x.png"], "nsfw": False}

    def generate(self, spec):
        self.calls.append(spec)
        r = self.results.pop(0) if self.results else None
        if isinstance(r, Exception):
            raise r
        return Generated(self.job, "gal-%d.png" % len(self.calls), self.data)

    def gpu_acquire(self, owner):
        self.acquired.append(owner)

    def gpu_release(self):
        self.released += 1


def tmp():
    return tempfile.mkdtemp(prefix="slotfill-")


def put(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def jload(path):
    with open(path) as fh:
        return json.load(fh)


def go(adapter, pf, **kw):
    d = tmp()
    lines = []
    kw.setdefault("state_dir", os.path.join(d, "state"))
    kw.setdefault("heartbeat_path", os.path.join(d, "hb", "heartbeat.json"))
    kw.setdefault("wait", lambda *a, **k: True)
    kw.setdefault("sleep", lambda s: None)
    code = engine.run(adapter, pf, out=lines.append, **kw)
    return code, lines, kw["state_dir"], kw["heartbeat_path"]


def rows(state_dir, name="fake"):
    with open(os.path.join(state_dir, name + ".jsonl")) as fh:
        return [json.loads(x) for x in fh if x.strip()]


class RunLoop(unittest.TestCase):
    def test_breadth_first_order(self):
        a = FakeAdapter()
        code, _, sd, _ = go(a, FakePF(), round_to=2)
        self.assertEqual(code, 0)
        self.assertEqual([(s, r, n) for s, r, _t, n, _g, _j in a.linked],
                         [("a", "r1", 1), ("a", "r2", 1), ("b", "r1", 1), ("b", "r2", 1), ("a", "r1", 2), ("a", "r2", 2), ("b", "r1", 2), ("b", "r2", 2)])

    def test_round_to_defaults_to_the_largest_target(self):
        a = FakeAdapter(slugs=("a",), roles=("r1",), targets={("a", "r1"): 3})
        go(a, FakePF())
        self.assertEqual(len(a.linked), 3)

    def test_resume_from_counts_and_nothing_to_do_at_target(self):
        a = FakeAdapter(slugs=("a",), roles=("r1", "r2"), start={"a": {"r1": 2, "r2": 1}}, targets={("a", "r1"): 2, ("a", "r2"): 2})
        pf = FakePF()
        go(a, pf)
        self.assertEqual([(r, n) for _s, r, _t, n, _g, _j in a.linked], [("r2", 2)])           # r1 is at its target, r2 needs round 2 only
        pf2 = FakePF()
        go(a, pf2)
        self.assertEqual(pf2.calls, [])

    def test_target_per_entry_and_role_is_honoured(self):
        a = FakeAdapter(targets={("a", "r1"): 1, ("a", "r2"): 0, ("b", "r1"): 2, ("b", "r2"): 2})
        go(a, FakePF(), round_to=3)
        got = sorted((s, r) for s, r, *_ in a.linked)
        self.assertEqual(got, [("a", "r1"), ("b", "r1"), ("b", "r1"), ("b", "r2"), ("b", "r2")])

    def test_limit_and_slugs_filter(self):
        a = FakeAdapter()
        go(a, FakePF(), round_to=2, limit=3)
        self.assertEqual(len(a.linked), 3)
        b = FakeAdapter()
        go(b, FakePF(), round_to=1, slugs=["b"])
        self.assertEqual({s for s, *_ in b.linked}, {"b"})

    def test_round_from(self):
        a = FakeAdapter(slugs=("a",), roles=("r1",), start={"a": {"r1": 1}})
        go(a, FakePF(), round_from=2, round_to=2)
        self.assertEqual([n for _s, _r, _t, n, _g, _j in a.linked], [2])

    def test_three_failures_in_a_row_stop_the_run(self):
        a = FakeAdapter(slugs=("a",), roles=("r1", "r2", "r3", "r4"))
        boom = SlotFillError("boom")
        pf = FakePF(results=[boom, boom, boom, boom, boom, boom, None, None])        # each picture fails twice (call + one retry): three pictures fail
        code, lines, sd, _ = go(a, pf, round_to=1)
        self.assertEqual(code, 1)
        self.assertIn("three failures in a row: stopping", lines)
        r = [x for x in rows(sd) if x["status"] != "run-start"]
        self.assertEqual([x["status"] for x in r], ["error", "error", "error"])
        self.assertEqual(len(pf.acquired), 3)

    def test_a_success_resets_the_failure_count(self):
        a = FakeAdapter(slugs=("a",), roles=("r1", "r2", "r3", "r4", "r5"))
        boom = SlotFillError("boom")
        pf = FakePF(results=[boom, boom, boom, boom, None, boom, boom, boom, boom, None])      # fail, fail, ok, fail, fail, ok ...: never three in a row
        pf.results += [None] * 10
        code, _, _, _ = go(a, pf, round_to=1)
        self.assertEqual(code, 0)

    def test_one_reacquire_and_retry_then_the_picture_is_linked(self):
        a = FakeAdapter(slugs=("a",), roles=("r1",))
        pf = FakePF(results=[SlotFillError("session ended")])
        code, _, sd, _ = go(a, pf, round_to=1, owner="tester")
        self.assertEqual(code, 0)
        self.assertEqual(pf.acquired, ["tester"])
        self.assertEqual(len(pf.calls), 2)
        self.assertEqual(len(a.linked), 1)
        self.assertEqual(rows(sd)[-1]["status"], "linked")

    def test_os_errors_also_take_the_retry(self):
        a = FakeAdapter(slugs=("a",), roles=("r1",))
        pf = FakePF(results=[OSError("reset")])
        go(a, pf, round_to=1)
        self.assertEqual(len(pf.acquired), 1)
        self.assertEqual(len(a.linked), 1)

    def test_no_headroom_exits_2(self):
        a = FakeAdapter()
        pf = FakePF()
        code, lines, _, _ = go(a, pf, round_to=1, wait=lambda *a_, **k: False)
        self.assertEqual(code, 2)
        self.assertEqual(pf.calls, [])
        self.assertIn("no headroom for 30 minutes: stopping", lines)

    def test_wait_is_asked_before_every_picture_with_the_light_profile(self):
        asked = []
        a = FakeAdapter(slugs=("a",), roles=("r1", "r2"))
        go(a, FakePF(), round_to=1, wait=lambda profile, *rest: asked.append(profile) or True)
        self.assertEqual(asked, ["light", "light"])

    def test_a_blank_picture_is_an_error_and_not_linked(self):
        a = FakeAdapter(slugs=("a",), roles=("r1",))
        pf = FakePF(data=b"tiny")
        code, _, sd, _ = go(a, pf, round_to=1)
        self.assertEqual(a.linked, [])
        self.assertEqual(rows(sd)[-1]["status"], "error")
        self.assertIn("blank or tiny", rows(sd)[-1]["error"])

    def test_nothing_is_ever_held_back_a_picture_the_job_calls_nsfw_is_still_linked(self):
        a = FakeAdapter(slugs=("a",), roles=("r1",))
        pf = FakePF()
        pf.job = {"status": "done", "images": ["x.png"], "nsfw": True}
        go(a, pf, round_to=1)
        self.assertEqual(len(a.linked), 1)
        self.assertTrue(a.linked[0][5]["nsfw"])

    def test_a_link_error_is_counted_not_raised(self):
        a = FakeAdapter(slugs=("a",), roles=("r1",), fail_link=True)
        code, _, sd, _ = go(a, FakePF(), round_to=1)
        self.assertEqual(code, 0)
        self.assertEqual(rows(sd)[-1]["error"], "link refused")


class StateFiles(unittest.TestCase):
    def test_dry_run_prints_and_writes_nothing(self):
        a = FakeAdapter(slugs=("a",), roles=("r1", "r2"))
        pf = FakePF()
        code, lines, sd, hb = go(a, pf, round_to=1, dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["1 a r1 tag-r1-1", "1 a r2 tag-r2-1"])
        self.assertEqual(pf.calls, [])
        self.assertFalse(os.path.exists(sd))
        self.assertFalse(os.path.exists(hb))

    def test_manifest_rows_and_the_run_start_row(self):
        a = FakeAdapter(slugs=("a",), roles=("r1",))
        _, _, sd, _ = go(a, FakePF(), round_to=1, slugs=["a"], limit=5)
        r = rows(sd)
        self.assertEqual(r[0]["status"], "run-start")
        self.assertEqual(r[0]["args"], {"slugs": ["a"], "round_from": 1, "round_to": 1, "limit": 5})
        self.assertEqual({k: r[1][k] for k in ("slug", "role", "round", "tag", "status", "file", "gallery")},
                         {"slug": "a", "role": "r1", "round": 1, "tag": "tag-r1-1", "status": "linked", "file": "r1-01.png", "gallery": "gal-1.png"})
        self.assertIn("seconds", r[1])
        self.assertIn("at", r[1])

    def test_pidfile_written_during_the_run_and_removed_after(self):
        seen = {}
        d = tmp()
        sd = os.path.join(d, "state")
        a = FakeAdapter(slugs=("a",), roles=("r1",))
        orig = a.link

        def link(*args):
            with open(os.path.join(sd, "fake.pid")) as fh:
                seen["pid"] = fh.read().strip()
            return orig(*args)
        a.link = link
        go(a, FakePF(), round_to=1, state_dir=sd)
        self.assertEqual(seen["pid"], str(os.getpid()))
        self.assertFalse(os.path.exists(os.path.join(sd, "fake.pid")))

    def test_pidfile_removed_even_when_the_run_stops_early(self):
        a = FakeAdapter(slugs=("a",), roles=("r1",))
        _, _, sd, _ = go(a, FakePF(), round_to=1, wait=lambda *x: False)
        self.assertFalse(os.path.exists(os.path.join(sd, "fake.pid")))

    def test_heartbeat_is_updated_per_step_and_final_write_says_stopped(self):
        beats = []
        d = tmp()
        hb = os.path.join(d, "hb", "heartbeat.json")
        a = FakeAdapter(slugs=("a",), roles=("r1", "r2"))
        orig = a.link

        def link(*args):
            beats.append(jload(hb))
            return orig(*args)
        a.link = link
        code, _, _, _ = go(a, FakePF(), round_to=1, heartbeat_path=hb)
        self.assertEqual([b["running"] for b in beats], [True, True])
        self.assertEqual(beats[1]["done"], 1)                          # the first step is already counted when the second link runs
        self.assertEqual(beats[1]["last"], {"slug": "a", "role": "r1", "round": 1, "tag": "tag-r1-1", "status": "linked"})
        final = jload(hb)
        self.assertEqual((final["running"], final["exit"], final["done"], final["errors"], final["forge"], final["pid"]), (False, 0, 2, 0, "fake", os.getpid()))

    def test_exit_code_in_the_final_heartbeat_for_a_failure_stop(self):
        a = FakeAdapter(slugs=("a",), roles=("r1", "r2", "r3"))
        boom = SlotFillError("boom")
        code, _, _, hb = go(a, FakePF(results=[boom] * 6), round_to=1)
        self.assertEqual(code, 1)
        final = jload(hb)
        self.assertEqual((final["running"], final["exit"], final["errors"]), (False, 1, 3))


class GoodImage(unittest.TestCase):
    @unittest.skipIf(Image is None, "Pillow missing")
    def test_blank_fails_and_noise_passes(self):
        self.assertFalse(engine.good_image(blank_png()))
        self.assertTrue(engine.good_image(GOOD))
        self.assertFalse(engine.good_image(b"x" * 100))


class FakePFServer(http.server.BaseHTTPRequestHandler):
    log = []
    polls = 0
    mode = "ok"

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None):
        body = raw if raw is not None else json.dumps(obj or {}).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        FakePFServer.log.append(("POST", self.path, body))
        if self.path == "/api/generate":
            if FakePFServer.mode == "nojob":
                return self._send(200, {"error": "no model"})
            return self._send(200, {"job_id": "j1"})
        if self.path in ("/api/gpu/acquire", "/api/gpu/release"):
            return self._send(200, {"ok": True})
        self._send(404)

    def do_GET(self):
        FakePFServer.log.append(("GET", self.path))
        if self.path == "/api/job/j1":
            FakePFServer.polls += 1
            if FakePFServer.mode == "failed":
                return self._send(200, {"status": "failed", "error": "out of memory"})
            if FakePFServer.mode == "noimage":
                return self._send(200, {"status": "done", "images": []})
            if FakePFServer.mode == "slow" or FakePFServer.polls < 2:
                return self._send(200, {"status": "running"})
            return self._send(200, {"status": "done", "images": ["sub/pic one.png"], "seed": 7})
        if self.path.startswith("/api/image/"):
            return self._send(200, raw=b"PNGDATA")
        self._send(404)


class PFClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), FakePFServer)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        FakePFServer.log, FakePFServer.polls, FakePFServer.mode = [], 0, "ok"
        self.sleeps = []
        self.t = [0.0]

        def now():
            return self.t[0]

        def sleep(s):
            self.sleeps.append(s)
            self.t[0] += s
        self.pf = PFClient(self.url, sleep=sleep, now=now)

    def test_generate_polls_then_fetches_the_first_picture(self):
        g = self.pf.generate({"prompt": "x"})
        self.assertEqual((g.name, g.data, g.job["seed"]), ("sub/pic one.png", b"PNGDATA", 7))
        self.assertEqual(self.sleeps, [2.0])                                  # one poll said running, polled every 2 s
        self.assertEqual(FakePFServer.log[0], ("POST", "/api/generate", {"prompt": "x"}))
        self.assertEqual(FakePFServer.log[-1], ("GET", "/api/image/sub/pic%20one.png"))

    def test_failed_job_raises_with_its_error(self):
        FakePFServer.mode = "failed"
        with self.assertRaisesRegex(SlotFillError, "out of memory"):
            self.pf.generate({})

    def test_no_job_id_and_no_image_raise(self):
        FakePFServer.mode = "nojob"
        with self.assertRaisesRegex(SlotFillError, "no model"):
            self.pf.generate({})
        FakePFServer.mode = "noimage"
        with self.assertRaisesRegex(SlotFillError, "no image returned"):
            self.pf.generate({})

    def test_timeout(self):
        FakePFServer.mode = "slow"
        with self.assertRaisesRegex(SlotFillError, "timeout"):
            self.pf.generate({}, timeout=10)
        self.assertEqual(sum(self.sleeps), 10.0)

    def test_gpu_acquire_and_release(self):
        self.pf.gpu_acquire("me")
        self.pf.gpu_release()
        self.assertEqual(FakePFServer.log, [("POST", "/api/gpu/acquire", {"owner": "me"}), ("POST", "/api/gpu/release", {})])

    def test_unreachable_promptforge_is_a_slotfill_error(self):
        with self.assertRaisesRegex(SlotFillError, "not reachable"):
            PFClient("http://127.0.0.1:1").generate({})

    def test_default_url_comes_from_the_environment(self):
        old = os.environ.get("PROMPTFORGE_URL")
        os.environ["PROMPTFORGE_URL"] = "http://example.invalid:9/"
        try:
            self.assertEqual(PFClient().base, "http://example.invalid:9")
        finally:
            if old is None:
                del os.environ["PROMPTFORGE_URL"]
            else:
                os.environ["PROMPTFORGE_URL"] = old


def fake_proc(io10=0.0, io60=0.0, mem10=0.0, avail_kb=8 * 1024 * 1024, swap=(0, 0)):
    d = tmp()
    os.makedirs(os.path.join(d, "pressure"))
    put(os.path.join(d, "pressure", "io"), "some avg10=%.2f avg60=%.2f avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=1\n" % (io10, io60))
    put(os.path.join(d, "pressure", "memory"), "some avg10=%.2f avg60=0.00 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=1\n" % mem10)
    put(os.path.join(d, "meminfo"), "MemTotal: 20000000 kB\nMemAvailable: %d kB\n" % avail_kb)
    put(os.path.join(d, "vmstat"), "pswpin %d\npswpout %d\n" % swap)
    return d


class HeadroomTests(unittest.TestCase):
    def test_light_thresholds(self):
        self.assertEqual(headroom.check("light", fake_proc(io10=85.0, mem10=30.0, avail_kb=1200 * 1024)), (True, []))
        ok, why = headroom.check("light", fake_proc(io10=85.1))
        self.assertFalse(ok)
        self.assertIn("io_some10", why[0])
        ok, why = headroom.check("light", fake_proc(mem10=30.1))
        self.assertFalse(ok)
        ok, why = headroom.check("light", fake_proc(avail_kb=1199 * 1024))
        self.assertFalse(ok)
        self.assertIn("mem_avail_mb", why[0])

    def test_heavy_thresholds_including_swap_traffic(self):
        self.assertTrue(headroom.check("heavy", fake_proc(io10=50.0, io60=60.0, mem10=15.0, avail_kb=2500 * 1024), sleep=lambda s: None)[0])
        self.assertFalse(headroom.check("heavy", fake_proc(io10=50.1), sleep=lambda s: None)[0])
        self.assertFalse(headroom.check("heavy", fake_proc(io60=60.1), sleep=lambda s: None)[0])
        self.assertFalse(headroom.check("heavy", fake_proc(mem10=15.1), sleep=lambda s: None)[0])
        self.assertFalse(headroom.check("heavy", fake_proc(avail_kb=2499 * 1024), sleep=lambda s: None)[0])
        d = fake_proc(swap=(0, 0))

        def bump(s):
            put(os.path.join(d, "vmstat"), "pswpin 150\npswpout 0\n")       # 150 pages in 1 s
        ok, why = headroom.check("heavy", d, sample_s=1.0, sleep=bump)
        self.assertFalse(ok)
        self.assertIn("swap_rate_pps", why[0])

    def test_unreadable_readings_count_as_ok(self):
        self.assertEqual(headroom.check("light", os.path.join(tmp(), "nothing")), (True, []))

    def test_environment_override(self):
        os.environ["HEADROOM_LIGHT_IO_SOME10"] = "10"
        try:
            self.assertFalse(headroom.check("light", fake_proc(io10=11.0))[0])
        finally:
            del os.environ["HEADROOM_LIGHT_IO_SOME10"]

    def test_wait_opens_at_once_when_ok_and_gives_up_at_the_limit(self):
        sleeps = []
        self.assertTrue(headroom.wait("light", 100, 30, sleep=sleeps.append, now=lambda: 0.0, checker=lambda p: (True, [])))
        self.assertEqual(sleeps, [])
        t = [0.0]

        def sleep(s):
            sleeps.append(s)
            t[0] += s
        self.assertFalse(headroom.wait("light", 100, 30, sleep=sleep, now=lambda: t[0], checker=lambda p: (False, ["busy"])))
        self.assertEqual(sum(sleeps), 120)                                   # polled every 30 s until 100 s had passed

    def test_wait_opens_when_the_machine_quiets_down(self):
        answers = [(False, ["busy"]), (False, ["busy"]), (True, [])]
        t = [0.0]
        ok = headroom.wait("light", 1000, 30, sleep=lambda s: t.__setitem__(0, t[0] + s), now=lambda: t[0], checker=lambda p: answers.pop(0))
        self.assertTrue(ok)
        self.assertEqual(t[0], 60)


if __name__ == "__main__":
    unittest.main()
