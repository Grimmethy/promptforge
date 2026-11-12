"""ObjectForge as a slot-fill forge: a STUB. ObjectForge has no entries or linking yet (a separate task), so only the listing of the wanted objects works (the dry run); counts, build_spec
and link raise NotImplementedError. The wanted objects live in /media/model-cache/github/objectforge/lists/*.md as lines '- <slug>: <description>' under a heading per list; the
list file name is the category, and lists/priority.txt names the slugs to generate first (one per line, # comments).
"""
import glob
import os
import re

from ..adapter import Adapter

LISTS_DIR = "/media/model-cache/github/objectforge/lists"
ROLES = ["neutral-reference", "angle", "in-use", "style-variant", "magic-variant", "scene-cutout", "other"]
NOT_YET = "ObjectForge has no linking yet (a separate task)"
LINE = re.compile(r"^-\s+([a-z0-9][a-z0-9_-]*)\s*:\s*(.+?)\s*$")


class ObjectForgeAdapter(Adapter):
    name = "objects"
    linking = False         # the CLI refuses a real run before it touches forge-mode or the GPU

    def __init__(self, lists_dir=None):
        self.lists_dir = lists_dir or os.environ.get("OBJECTFORGE_LISTS") or LISTS_DIR

    def _priority(self):
        try:
            with open(os.path.join(self.lists_dir, "priority.txt")) as fh:
                return [x.strip() for x in fh if x.strip() and not x.lstrip().startswith("#")]
        except OSError:
            return []

    def entries(self):
        """[{slug, name, category}]: the priority slugs first (in their listed order), then the rest in list-file order."""
        found, order = {}, []
        for path in sorted(glob.glob(os.path.join(self.lists_dir, "*.md"))):
            category = os.path.splitext(os.path.basename(path))[0]
            with open(path) as fh:
                for line in fh:
                    m = LINE.match(line)
                    if m and m.group(1) not in found:
                        found[m.group(1)] = {"slug": m.group(1), "name": m.group(2), "category": category}
                        order.append(m.group(1))
        first = [s for s in self._priority() if s in found]
        return [found[s] for s in first + [s for s in order if s not in first]]

    def roles(self):
        return list(ROLES)

    def counts(self, slug):
        raise NotImplementedError(NOT_YET)

    def build_spec(self, entry, role, rnd):
        raise NotImplementedError(NOT_YET)

    def link(self, slug, role, tag, rnd, spec, job, name, data):
        raise NotImplementedError(NOT_YET)
