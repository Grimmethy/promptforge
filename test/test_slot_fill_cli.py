"""The slot-fill commands (scripts/slot_fill/cli.py, adapters/objectforge.py, __main__.py): argument parsing, forge-mode and GPU handling, the ObjectForge listing, status.

Run: TMPDIR=/dev/shm/x .venv/bin/python -B -m unittest test.test_slot_fill_cli   (from the repo root)
Everything is faked: no forge, no forge-mode command, no GPU session and no real gallery is touched.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from scripts.slot_fill import cli  # noqa: E402
from scripts.slot_fill.adapter import Adapter  # noqa: E402
from scripts.slot_fill.adapters.objectforge import ObjectForgeAdapter  # noqa: E402
from scripts.slot_fill.errors import SlotFillError  # noqa: E402


class FakeAdapter(Adapter):
    name = "fake"

    def entries(self):
        return [{"slug": "a"}, {"slug": "b"}]

    def roles(self):
        return ["r1", "r2"]

    def counts(self, slug):
        return {"a": {"r1": 3, "r2": 0}, "b": {"r1": 12, "r2": 1}}[slug]

    def target(self, entry, role):
        return 5 if role == "r1" else 2


class FakePF:
    def __init__(self, fail_acquire=False):
        self.events = []
        self.fail_acquire = fail_acquire

    def gpu_acquire(self, owner):
        self.events.append("acquire:" + owner)
        if self.fail_acquire:
            raise SlotFillError("no gpu")

    def gpu_release(self):
        self.events.append("release")


class Harness:
    def __init__(self, run_result=0, run_raises=None, forge_mode_ok=True, pf=None):
        self.events = []
        self.pf = pf or FakePF()
        self.pf.events = self.events
        self.lines, self.errs, self.calls = [], [], []
        self.run_result, self.run_raises, self.forge_mode_ok = run_result, run_raises, forge_mode_ok

    def run(self, adapter, pf, **kw):
        self.events.append("engine")
        self.calls.append(kw)
        if self.run_raises:
            raise self.run_raises
        return self.run_result

    def forge_mode(self, arg):
        self.events.append("forge-mode:" + arg)
        return self.forge_mode_ok if arg == "on" else True

    def main(self, argv, adapters=None, **kw):
        state = tempfile.mkdtemp(prefix="slotcli-")
        return cli.main(argv, adapters=adapters or {"characters": FakeAdapter, "objects": ObjectForgeAdapter}, pf=self.pf, run=self.run, forge_mode_fn=self.forge_mode,
                        state_dir=kw.pop("state_dir", state), out=self.lines.append, err=self.errs.append, **kw)


class Parsing(unittest.TestCase):
    def test_run_arguments_reach_the_engine(self):
        h = Harness()
        code = h.main(["run", "--forge", "characters", "--slugs", "a,b", "--round-from", "2", "--round-to", "4", "--limit", "7"])
        self.assertEqual(code, 0)
        kw = h.calls[0]
        self.assertEqual((kw["slugs"], kw["round_from"], kw["round_to"], kw["limit"]), (["a", "b"], 2, 4, 7))
        self.assertNotIn("dry_run", kw)

    def test_defaults(self):
        h = Harness()
        h.main(["run"])
        kw = h.calls[0]
        self.assertEqual((kw["slugs"], kw["round_from"], kw["round_to"], kw["limit"]), ([], 1, None, 0))

    def test_no_subcommand_prints_usage_and_exits_2(self):
        h = Harness()
        self.assertEqual(h.main([]), 2)
        self.assertIn("usage", h.errs[0])
        self.assertEqual(h.events, [])

    def test_an_unknown_forge_is_refused_before_anything_runs(self):
        h = Harness()
        self.assertEqual(h.main(["run", "--forge", "nonsense"]), 2)
        self.assertEqual(h.events, [])


class RunSequence(unittest.TestCase):
    def test_forge_mode_on_then_gpu_then_engine_then_release_then_forge_mode_off(self):
        h = Harness()
        self.assertEqual(h.main(["run"]), 0)
        self.assertEqual(h.events, ["forge-mode:on", "acquire:slot-fill", "engine", "release", "forge-mode:off"])

    def test_the_engines_exit_code_is_the_commands(self):
        self.assertEqual(Harness(run_result=1).main(["run"]), 1)
        self.assertEqual(Harness(run_result=2).main(["run"]), 2)

    def test_release_and_forge_mode_off_happen_even_when_the_engine_raises(self):
        h = Harness(run_raises=SlotFillError("boom"))
        self.assertEqual(h.main(["run"]), 2)
        self.assertEqual(h.events, ["forge-mode:on", "acquire:slot-fill", "engine", "release", "forge-mode:off"])
        self.assertIn("boom", h.errs[0])

    def test_a_failed_gpu_acquire_skips_the_engine_but_still_restores_forge_mode(self):
        h = Harness(pf=FakePF(fail_acquire=True))
        self.assertEqual(h.main(["run"]), 2)
        self.assertEqual(h.events, ["forge-mode:on", "acquire:slot-fill", "forge-mode:off"])          # nothing acquired, so nothing to release

    def test_forge_mode_failing_stops_before_the_engine_and_the_gpu(self):
        h = Harness(forge_mode_ok=False)
        self.assertEqual(h.main(["run"]), 2)
        self.assertEqual(h.events, ["forge-mode:on"])

    def test_the_dry_run_changes_nothing_and_acquires_nothing(self):
        h = Harness()
        self.assertEqual(h.main(["run", "--dry-run"]), 0)
        self.assertEqual(h.events, ["engine"])
        self.assertTrue(h.calls[0]["dry_run"])


class ForgeMode(unittest.TestCase):
    def test_a_missing_command_prints_the_instruction_and_goes_on_after_one_headroom_check(self):
        lines, checks = [], []
        ok = cli.forge_mode("on", which=lambda n: None, out=lines.append, check=lambda p: checks.append(p) or (True, []))
        self.assertTrue(ok)
        self.assertEqual(lines, [cli.NO_FORGE_MODE])
        self.assertEqual(checks, ["light"])

    def test_a_missing_command_with_no_headroom_says_so_but_still_goes_on(self):
        lines = []
        self.assertTrue(cli.forge_mode("on", which=lambda n: None, out=lines.append, check=lambda p: (False, ["io_some10 90.0 > 85.0"])))
        self.assertIn("io_some10", lines[1])

    def test_a_missing_command_says_nothing_on_the_way_out(self):
        lines = []
        self.assertTrue(cli.forge_mode("off", which=lambda n: None, out=lines.append))
        self.assertEqual(lines, [])

    def test_the_command_is_run_with_its_argument_and_a_nonzero_exit_stops(self):
        calls, lines = [], []

        def runner(args):
            calls.append(args)
            return subprocess.CompletedProcess(args, 3)
        self.assertFalse(cli.forge_mode("on", which=lambda n: "/bin/forge-mode", runner=runner, out=lines.append))
        self.assertEqual(calls, [["forge-mode", "on"]])
        self.assertIn("failed (exit 3)", lines[0])
        self.assertTrue(cli.forge_mode("off", which=lambda n: "/bin/forge-mode", runner=lambda a: subprocess.CompletedProcess(a, 0)))


class ObjectForge(unittest.TestCase):
    def lists(self):
        d = tempfile.mkdtemp(prefix="objlists-")
        with open(os.path.join(d, "coffee-shop.md"), "w") as fh:
            fh.write("# Coffee shop (wanted objects)\n\nTheme: cozy.\n\n- espresso-machine: big brass machine\n- coffee-mug: one fat mug\n- not a line\n")
        with open(os.path.join(d, "diner.md"), "w") as fh:
            fh.write("# Diner\n\n- jukebox: chrome jukebox\n- coffee-mug: duplicate of the coffee shop one\n  - nested-ignored: x\n")
        with open(os.path.join(d, "priority.txt"), "w") as fh:
            fh.write("# generate first\njukebox\nunknown-slug\ncoffee-mug\n")
        return d

    def test_entries_come_from_the_list_files_with_priority_first(self):
        a = ObjectForgeAdapter(self.lists())
        e = a.entries()
        self.assertEqual([x["slug"] for x in e], ["jukebox", "coffee-mug", "espresso-machine"])
        self.assertEqual(e[0], {"slug": "jukebox", "name": "chrome jukebox", "category": "diner"})
        self.assertEqual(e[1]["category"], "coffee-shop")                          # the first list that names a slug keeps it

    def test_roles_and_the_not_yet_message(self):
        a = ObjectForgeAdapter(self.lists())
        self.assertEqual(a.roles(), ["neutral-reference", "angle", "in-use", "style-variant", "magic-variant", "scene-cutout", "other"])
        for call in (lambda: a.counts("x"), lambda: a.build_spec({}, "other", 1), lambda: a.link("x", "other", "t", 1, {}, {}, "n", b"")):
            with self.assertRaisesRegex(NotImplementedError, "ObjectForge has no linking yet"):
                call()

    def test_the_dry_run_listing_works_through_the_cli_when_the_engine_cannot_count(self):
        d = self.lists()
        h = Harness()

        def run(adapter, pf, **kw):
            raise NotImplementedError("x")
        code = cli.main(["run", "--forge", "objects", "--dry-run"], adapters={"objects": lambda: ObjectForgeAdapter(d)}, pf=h.pf, run=run, forge_mode_fn=h.forge_mode, out=h.lines.append, err=h.errs.append)
        self.assertEqual(code, 0)
        self.assertEqual(h.lines[0], "jukebox\tdiner\tchrome jukebox")
        self.assertEqual(len(h.lines), 3)
        self.assertEqual(h.events, [])

    def test_a_real_run_for_objects_is_refused_before_forge_mode_or_the_gpu(self):
        d = self.lists()
        h = Harness()
        code = h.main(["run", "--forge", "objects"], adapters={"objects": lambda: ObjectForgeAdapter(d)})
        self.assertEqual(code, 3)
        self.assertEqual(h.events, [])
        self.assertIn("no linking yet", h.errs[0])

    def test_the_real_lists_parse_when_present(self):
        if not os.path.isdir("/media/model-cache/github/objectforge/lists"):
            self.skipTest("no objectforge lists on this machine")
        e = ObjectForgeAdapter().entries()
        self.assertGreater(len(e), 200)
        self.assertEqual(e[0]["slug"], "for-sale-sign")


class Status(unittest.TestCase):
    def test_slots_filled_and_open_per_entry_and_in_total(self):
        h = Harness()
        self.assertEqual(h.main(["status"]), 0)
        self.assertEqual(h.lines[0], "a: r1 3/5, r2 0/2")
        self.assertEqual(h.lines[1], "b: r1 12/5, r2 1/2")
        self.assertEqual(h.lines[2], "total: 9 filled, 5 open")           # 3 + 0 + min(12, 5) + 1 filled of 5 + 2 + 5 + 2 = 14 wanted
        self.assertEqual(h.events, [])

    def test_disk_used_sums_the_manifests_gallery_files_and_ignores_missing_ones(self):
        state, gal = tempfile.mkdtemp(prefix="st-"), tempfile.mkdtemp(prefix="gal-")
        with open(os.path.join(gal, "one.png"), "wb") as fh:
            fh.write(b"x" * (1024 * 1024))
        with open(os.path.join(gal, "two.png"), "wb") as fh:
            fh.write(b"x" * (512 * 1024))
        with open(os.path.join(state, "fake.jsonl"), "w") as fh:
            for row in ({"status": "run-start"}, {"gallery": "one.png"}, {"gallery": "two.png"}, {"gallery": "missing.png"}, {"status": "error"}):
                fh.write(json.dumps(row) + "\n")
            fh.write("not json\n")
        h = Harness()
        h.main(["status"], state_dir=state, gallery_dir=gal)
        self.assertEqual(h.lines[-1], "disk used: 1.5 MB")

    def test_no_manifest_is_zero_megabytes(self):
        h = Harness()
        h.main(["status"])
        self.assertEqual(h.lines[-1], "disk used: 0.0 MB")

    def test_the_gallery_comes_from_the_environment(self):
        gal = tempfile.mkdtemp(prefix="gal-")
        state = tempfile.mkdtemp(prefix="st-")
        with open(os.path.join(gal, "e.png"), "wb") as fh:
            fh.write(b"x" * (2 * 1024 * 1024))
        with open(os.path.join(state, "fake.jsonl"), "w") as fh:
            fh.write(json.dumps({"gallery": "e.png"}) + "\n")
        old = os.environ.get("PROMPTFORGE_GALLERY_DIR")
        os.environ["PROMPTFORGE_GALLERY_DIR"] = gal
        try:
            h = Harness()
            h.main(["status"], state_dir=state)
            self.assertEqual(h.lines[-1], "disk used: 2.0 MB")
        finally:
            if old is None:
                del os.environ["PROMPTFORGE_GALLERY_DIR"]
            else:
                os.environ["PROMPTFORGE_GALLERY_DIR"] = old


class ModuleEntry(unittest.TestCase):
    def test_python_m_prints_usage_without_a_subcommand(self):
        repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        r = subprocess.run([sys.executable, "-m", "scripts.slot_fill"], cwd=repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage: python -m scripts.slot_fill", r.stderr)


if __name__ == "__main__":
    unittest.main()
