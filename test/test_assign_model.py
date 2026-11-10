"""The lightbox "Assign to..." panel's pure helpers (web/assign-model.js): grouping, which sections start open, no-image first, the role selector's
n/10, result lines and the tile badge text. Loaded with node (skipped when node is missing); the real-page check is recorded on the task.
No ComfyUI, no GPU (python3 -m unittest discover -s test -p 'test_*.py')."""
import json
import os
import shutil
import subprocess
import unittest

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")


def node_eval(expr):
    js = "const m = require(%s); console.log(JSON.stringify(%s));" % (json.dumps(os.path.join(WEB, "assign-model.js")), expr)
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


E = [{"slug": "Gobs", "name": "Gobs", "kind": "character", "faction": "goblins", "hasImage": True},
     {"slug": "Snik", "name": "Snik", "kind": "character", "faction": "goblins", "hasImage": False},
     {"slug": "Ada", "name": "Ada", "kind": "character", "faction": "goblins", "hasImage": False},
     {"slug": "Hooble", "name": "Hooble", "kind": "character", "project": "tea", "hasImage": True},
     {"slug": "Rex", "name": "Rex", "kind": "character", "hasImage": True},
     {"slug": "Goblin", "name": "Goblin", "kind": "group", "faction": "goblins", "hasImage": False}]


@unittest.skipIf(shutil.which("node") is None, "node not installed")
class AssignModelTest(unittest.TestCase):
    def group(self, **opts):
        return node_eval("m.groupEntries(%s, %s)" % (json.dumps(E), json.dumps(opts)))

    def test_characters_and_groups_are_separate_sections(self):
        secs = self.group()
        self.assertEqual([(s["title"], s["count"]) for s in secs], [("Characters", 5), ("Groups", 1)])
        self.assertEqual([e["slug"] for g in secs[1]["groups"] for e in g["entries"]], ["Goblin"])

    def test_subgroups_by_faction_or_project_collapsed_unless_preselected_or_searching(self):
        chars = self.group()[0]["groups"]
        self.assertEqual([(g["label"], g["open"]) for g in chars], [("goblins", False), ("Other", False), ("tea", False)])
        pre = self.group(preselected=["Hooble"])[0]["groups"]
        self.assertEqual([(g["label"], g["open"]) for g in pre], [("tea", True), ("goblins", False), ("Other", False)])  # the pre-selected sub-group leads and is open
        self.assertEqual([e["slug"] for g in pre for e in g["entries"] if e["selected"]], ["Hooble"])
        found = self.group(query="gob")
        self.assertTrue(all(g["open"] for s in found for g in s["groups"]))
        self.assertEqual([(s["title"], s["count"]) for s in found], [("Characters", 3), ("Groups", 1)])

    def test_entries_with_no_image_come_first_and_are_marked(self):
        goblins = self.group()[0]["groups"][0]["entries"]
        self.assertEqual([(e["slug"], e["noImage"]) for e in goblins], [("Ada", True), ("Snik", True), ("Gobs", False)])

    def test_search_matches_name_slug_faction_and_ignores_case(self):
        self.assertEqual([e["slug"] for s in self.group(query="TEA") for g in s["groups"] for e in g["entries"]], ["Hooble"])
        self.assertEqual([e["slug"] for s in self.group(query="rex") for g in s["groups"] for e in g["entries"]], ["Rex"])
        self.assertEqual(self.group(query="zzz")[0]["count"], 0)

    def test_role_selector_shows_the_highest_count_among_the_selected(self):
        cats = [{"id": "pose", "label": "Pose"}, {"id": "action", "label": "Action"}]
        opts = node_eval("m.roleOptions(%s, ['action','pose','other'], %s, ['A','B'])" % (json.dumps(cats), json.dumps({"A": {"pose": 3}, "B": {"pose": 10, "action": 1}})))
        self.assertEqual([(o["id"], o["text"], o["full"]) for o in opts], [("pose", "Pose 10/10", True), ("action", "Action 1/10", False), ("other", "other 0/10", False)])
        self.assertEqual(node_eval("m.roleOptions([], ['pose'], {}, [])")[0]["text"], "pose 0/10")

    def test_build_targets_and_result_lines(self):
        self.assertEqual(node_eval("m.buildTargets(['A','B'], 'pose', {A: 'Alpha'})"),
                         {"targets": [{"slug": "A", "role": "pose"}, {"slug": "B", "role": "pose"}], "names": {"A": "Alpha", "B": "B"}})
        lines = node_eval("[m.describeResult({slug:'A',role:'pose',ok:true,file:'pose-01.png'}), m.describeResult({slug:'A',role:'pose',ok:true,already:true}),"
                          " m.describeResult({slug:'A',role:'pose',ok:false,status:409,count:10}), m.describeResult({slug:'A',role:'pose',ok:false,error:'x is not reachable'})]")
        self.assertEqual(lines, ["A: added as pose-01.png", "A: already linked as pose, nothing sent", "A: pose is full (10/10) - choose a picture to replace; the old one goes to history", "A: x is not reachable"])

    def test_tile_badge_text(self):
        self.assertEqual(node_eval("m.badgeText({name:'Gobs', role:'pose', more:0})"), "-> Gobs . pose")
        self.assertEqual(node_eval("m.badgeText({name:'Gobs', role:'pose', more:2})"), "-> Gobs . pose +2")
        self.assertEqual(node_eval("m.badgeText(null)"), "")


if __name__ == "__main__":
    unittest.main()
