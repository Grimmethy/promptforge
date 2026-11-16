"""Unit test for queueRowLabel in web/app.js (run: python3 -I test/test_queue_label.py).

app.js touches `document`, so it cannot be require()d. The helper source is sliced out of the file by index and run under node
when node is installed. When node is missing, a pure-Python port of the same logic is checked instead (RUNTIME says which ran);
the port was written by reading the JS, so it only covers the logic, not the JS engine."""
import json
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "web", "app.js")
NODE = shutil.which("node")
RUNTIME = "node" if NODE else "python-port (node is not installed)"

with open(APP, encoding="utf-8") as fh:
    _APP = fh.read()
_START = _APP.index("function queueRowLabel")
_END = _APP.index("\n}\n", _START) + 3
HELPER = _APP[_START:_END]


def _py_label(job, prefix):
    """Mirror of the JS helper, used only when node is missing (checked by reading, not by executing the JS)."""
    purpose = job.get("purpose") or ""
    rest = purpose[len(prefix):] if prefix and purpose.startswith(prefix) else purpose
    rest = re.sub(r"^[\s:\-\u2013,]+", "", rest).strip()
    return rest or purpose or job.get("id")


def label(job, prefix):
    if not NODE:
        return _py_label(job, prefix)
    code = HELPER + "\nconsole.log(JSON.stringify(queueRowLabel(%s, %s)));" % (json.dumps(job), json.dumps(prefix))
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


class QueueRowLabelTest(unittest.TestCase):
    PREFIX = "Christmas coloring book"

    def test_shared_prefix_is_dropped(self):
        job = {"id": "j1", "purpose": self.PREFIX + ", spread 3 pass A"}
        got = label(job, self.PREFIX)
        self.assertNotIn(self.PREFIX, got)
        self.assertEqual(got, "spread 3 pass A")

    def test_spread_and_pass_survive_24_char_truncation(self):
        a = {"id": "a", "purpose": self.PREFIX + ", spread 3 pass A, lineart with fine detail"}
        b = {"id": "b", "purpose": self.PREFIX + ", spread 3 pass B, lineart with fine detail"}
        la = label(a, self.PREFIX)[:24]
        lb = label(b, self.PREFIX)[:24]
        self.assertEqual(la, "spread 3 pass A, lineart")
        self.assertEqual(lb, "spread 3 pass B, lineart")
        self.assertNotEqual(la, lb)

    def test_no_spread_id_falls_back_to_remainder(self):
        job = {"id": "j9", "purpose": self.PREFIX + ", lineart page 7"}
        got = label(job, self.PREFIX)
        self.assertEqual(got, "lineart page 7")
        self.assertNotEqual(got, "undefined")
        self.assertTrue(got)


if __name__ == "__main__":
    print("runtime:", RUNTIME)
    unittest.main()
