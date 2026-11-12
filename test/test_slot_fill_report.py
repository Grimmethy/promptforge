"""The slot-fill report (scripts/slot_fill/report.py and the `report` command): summary.md and one contact sheet per entry.

Run: TMPDIR=/dev/shm/x .venv/bin/python -B -m unittest test.test_slot_fill_report   (from the repo root)
Everything runs on temporary state, gallery and output folders; a snapshot test proves nothing outside the output folder changes.
"""
import json
import os
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from scripts.slot_fill import cli, report  # noqa: E402
from scripts.slot_fill.adapter import Adapter  # noqa: E402
from scripts.slot_fill.errors import SlotFillError  # noqa: E402


def tmp(prefix):
    return tempfile.mkdtemp(prefix=prefix)


def png(path, size=(300, 400), color=(120, 40, 40)):
    Image.new("RGB", size, color).save(path, "PNG")


def write_manifest(state, rows, name="fake"):
    with open(os.path.join(state, name + ".jsonl"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


class FakeAdapter(Adapter):
    name = "fake"

    def entries(self):
        return [{"slug": "a"}, {"slug": "b"}]

    def roles(self):
        return ["r1", "r2"]

    def counts(self, slug):
        return {"a": {"r1": 2, "r2": 0}, "b": {"r1": 3, "r2": 5}}[slug]

    def target(self, entry, role):
        return 3


def world(n_a=7):
    state, gal, out = tmp("st-"), tmp("gal-"), tmp("out-")
    rows = [
        {"status": "run-start", "at": "2026-11-12T08:00:00", "args": {}},
        {"slug": "old", "role": "r1", "round": 1, "tag": "t", "status": "linked", "gallery": "old.png", "seconds": 99, "at": "2026-11-12T08:01:00"},
        {"status": "run-start", "at": "2026-11-12T09:00:00", "args": {}},
    ]
    for i in range(n_a):
        png(os.path.join(gal, "a%d.png" % i), color=(20 * i, 80, 80))
        rows.append({"slug": "a", "role": "r1" if i < 4 else "r2", "round": 1, "tag": "tag%d" % i, "status": "linked", "gallery": "a%d.png" % i, "seconds": 10 + i, "at": "2026-11-12T09:%02d:00" % (i + 1)})
    png(os.path.join(gal, "b0.png"))
    rows.append({"slug": "b", "role": "r1", "round": 1, "tag": "x", "status": "linked", "gallery": "b0.png", "seconds": 4, "at": "2026-11-12T09:20:00"})
    rows.append({"slug": "b", "role": "r2", "round": 1, "status": "error", "error": "timeout", "seconds": 600, "at": "2026-11-12T09:30:00"})
    rows.append({"slug": "b", "role": "r2", "round": 2, "status": "error", "error": "out of memory", "seconds": 1, "at": "2026-11-12T09:31:00"})
    rows.append({"slug": "b", "role": "r2", "round": 3, "status": "error", "error": "timeout", "seconds": 1, "at": "2026-11-12T09:32:00"})
    write_manifest(state, rows)
    return state, gal, out


def make(state, gal, out, **kw):
    path = report.make_report("fake", state_dir=state, gallery_dir=gal, out_root=out, **kw)
    with open(path) as fh:
        return path, fh.read()


def tree(root):
    snap = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            snap[p] = (os.path.getsize(p), os.path.getmtime(p))
    return snap


class Summary(unittest.TestCase):
    def test_only_the_last_run_is_reported(self):
        state, gal, out = world()
        _, text = make(state, gal, out)
        self.assertIn("pictures made (linked): 8", text)                      # 7 + 1, not the earlier run's picture
        self.assertNotIn("old", text.split("## Pictures per entry")[1].split("##")[0])
        self.assertIn("Window: 2026-11-12T09:00:00 to 2026-11-12T09:32:00", text)

    def test_counts_errors_seconds_and_pictures_per_entry(self):
        state, gal, out = world()
        _, text = make(state, gal, out)
        self.assertIn("- errors: 3", text)
        self.assertEqual(text.count("  - timeout"), 1)                           # distinct messages, once each
        self.assertIn("  - out of memory", text)
        self.assertIn("seconds per picture: mean 11.9, median 12.5", text)       # linked seconds 4, 10..16: sum 95 over 8 = 11.875; median (12 + 13) / 2
        self.assertIn("- a: r1 4, r2 3", text)
        self.assertIn("- b: r1 1", text)

    def test_disk_used_counts_the_linked_files(self):
        state, gal, out = world()
        with open(os.path.join(gal, "a0.png"), "ab") as fh:
            fh.write(b"\0" * (3 * 1024 * 1024))                                  # trailing bytes keep it a valid PNG but make the sum visible
        _, text = make(state, gal, out)
        total = sum(os.path.getsize(os.path.join(gal, "a%d.png" % i)) for i in range(7)) + os.path.getsize(os.path.join(gal, "b0.png"))
        self.assertIn("disk used by the new pictures: %.1f MB" % (total / 1048576.0), text)

    def test_open_slots_come_from_the_adapter_target_minus_counts(self):
        state, gal, out = world()
        _, text = make(state, gal, out, adapter=FakeAdapter())
        section = text.split("## Slots still open")[1].split("##")[0]
        self.assertIn("- a: r1 1, r2 3", section)                                # a has 2 of 3 and 0 of 3
        self.assertNotIn("- b:", section)                                        # b is at or over its target everywhere

    def test_no_adapter_and_an_unreachable_forge_do_not_break_the_report(self):
        state, gal, out = world()
        self.assertIn("not checked", make(state, gal, out)[1])

        class Down(FakeAdapter):
            def entries(self):
                raise SlotFillError("forge down")
        self.assertIn("not available: forge down", make(state, gal, out, adapter=Down())[1])

    def test_since_selects_rows_by_time(self):
        state, gal, out = world()
        _, text = make(state, gal, out, since="2026-11-12T09:20:00")             # a row stamped exactly then is included
        self.assertIn("pictures made (linked): 1", text)
        self.assertIn("- errors: 3", text)
        _, text = make(state, gal, out, since="2026-11-12T00:00:00")
        self.assertIn("pictures made (linked): 9", text)                         # the earlier run's picture too

    def test_a_manifest_without_a_run_start_reports_every_row_and_a_missing_one_reports_nothing(self):
        state, gal, out = tmp("st-"), tmp("gal-"), tmp("out-")
        write_manifest(state, [{"slug": "a", "role": "r1", "tag": "t", "status": "linked", "gallery": "x.png", "seconds": 1, "at": "2026-11-12T09:00:00"}])
        self.assertIn("pictures made (linked): 1", make(state, gal, out)[1])
        state2 = tmp("st-")
        text = make(state2, gal, out)[1]
        self.assertIn("pictures made (linked): 0", text)
        self.assertIn("- none", text.split("## Pictures per entry and role")[1].split("##")[0])


class Sheets(unittest.TestCase):
    def test_one_jpeg_per_entry_with_five_columns_and_the_right_size(self):
        state, gal, out = world(n_a=7)
        path, text = make(state, gal, out)
        d = os.path.dirname(path)
        self.assertEqual(sorted(f for f in os.listdir(d) if f.endswith(".jpg")), ["a.jpg", "b.jpg"])
        with Image.open(os.path.join(d, "a.jpg")) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertEqual(im.size, (5 * 192, 2 * (192 + 16)))                  # 7 pictures: 5 columns, 2 rows
        with Image.open(os.path.join(d, "b.jpg")) as im:
            self.assertEqual(im.size, (5 * 192, 192 + 16))                        # 1 picture: 1 row
        self.assertIn(os.path.join(d, "a.jpg"), text)

    def test_a_missing_gallery_file_is_skipped_and_named_never_an_error(self):
        state, gal, out = world(n_a=3)
        os.remove(os.path.join(gal, "a1.png"))
        path, text = make(state, gal, out)
        self.assertIn("a1.png", text.split("## Pictures missing")[1])
        with Image.open(os.path.join(os.path.dirname(path), "a.jpg")) as im:
            self.assertEqual(im.size, (5 * 192, 192 + 16))                        # 2 pictures left
        state2, out2 = tmp("st-"), tmp("out-")
        write_manifest(state2, [{"status": "run-start", "at": "2026-11-12T09:00:00"}, {"slug": "z", "role": "r1", "tag": "t", "status": "linked", "gallery": "gone.png", "seconds": 1, "at": "2026-11-12T09:01:00"}])
        p2, t2 = make(state2, tmp("gal-"), out2)
        self.assertIn("gone.png", t2)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(p2), "z.jpg")))


class Safety(unittest.TestCase):
    def test_nothing_outside_the_output_folder_changes(self):
        state, gal, out = world()
        before_state, before_gal = tree(state), tree(gal)
        make(state, gal, out)
        self.assertEqual(tree(state), before_state)
        self.assertEqual(tree(gal), before_gal)
        self.assertTrue(all(p.startswith(out) for p in tree(out)))

    def test_two_reports_in_different_seconds_do_not_overwrite_each_other(self):
        state, gal, out = world(n_a=1)
        p1 = report.make_report("fake", state_dir=state, gallery_dir=gal, out_root=out, now=lambda: 1000000000)
        p2 = report.make_report("fake", state_dir=state, gallery_dir=gal, out_root=out, now=lambda: 1000000060)
        self.assertNotEqual(os.path.dirname(p1), os.path.dirname(p2))
        self.assertTrue(os.path.exists(p1) and os.path.exists(p2))


class Command(unittest.TestCase):
    def test_the_subcommand_prints_the_summary_path_and_passes_since(self):
        got = {}

        def fake_report(name, **kw):
            got.update(name=name, **kw)
            return "/tmp/x/summary.md"
        lines = []
        code = cli.main(["report", "--forge", "characters", "--since", "2026-11-12T09:00:00"], adapters={"characters": FakeAdapter}, report_fn=fake_report, out=lines.append, err=lines.append,
                        state_dir="/s", gallery_dir="/g", out_root="/o")
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["/tmp/x/summary.md"])
        self.assertEqual((got["name"], got["since"], got["state_dir"], got["gallery_dir"], got["out_root"]), ("fake", "2026-11-12T09:00:00", "/s", "/g", "/o"))
        self.assertIsInstance(got["adapter"], FakeAdapter)

    def test_the_usage_line_names_report(self):
        lines = []
        cli.main([], adapters={"characters": FakeAdapter}, err=lines.append)
        self.assertIn("run|status|report", lines[0])

    def test_gitignore_and_readme(self):
        repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        with open(os.path.join(repo, ".gitignore")) as fh:
            self.assertIn("data/slot-fill-runs/", fh.read().splitlines())
        with open(os.path.join(repo, "scripts", "slot_fill", "README.md")) as fh:
            readme = fh.read()
        self.assertLess(len(readme.splitlines()), 80)
        for word in ("run", "status", "report", "proposed.json", "never rejects"):
            self.assertIn(word, readme)


if __name__ == "__main__":
    unittest.main()
