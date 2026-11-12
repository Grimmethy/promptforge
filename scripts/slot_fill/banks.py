"""The variation banks as data: data/slot-banks/<forge>.json (8 roles x 10 variations for characters), defaults.json and overrides.json.

The owner edits these as plain JSON. New variations proposed by a model go to data/slot-banks/proposed.json and are NOT read here: they are used only after the owner moves
them into the bank file. Standard library only.
"""
import hashlib
import json
import os

from .errors import SlotFillError

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FIELDS = ("tag", "pose", "setting", "style", "extra")


def _read(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except OSError as e:
        raise SlotFillError("%s: cannot be read (%s)" % (path, e.strerror or e))
    except ValueError as e:
        raise SlotFillError("%s: not valid JSON (%s)" % (path, e))


class Banks:
    def __init__(self, roles, defaults, overrides):
        self.roles = roles
        self.defaults = defaults
        self.overrides = overrides

    def variation(self, slug, role, rnd):
        """The variation {tag, pose, setting, style, extra} for this entry and round: the bank rotated by a stable hash of the slug, so entries differ and a re-run repeats."""
        bank = self.roles.get(role)
        if not bank:
            raise SlotFillError("no variation bank for role %r" % role)
        off = int(hashlib.sha256(slug.encode()).hexdigest()[:4], 16)
        return dict(bank[(rnd - 1 + off) % len(bank)])

    def override(self, slug):
        return dict(self.overrides.get("entries", {}).get(slug) or {})

    def target(self, slug, role, default=10):
        """Pictures wanted for this entry and role: its own override, else the defaults' target, else `default`. 0 is allowed and means skip the role."""
        own = (self.override(slug).get("targets") or {}).get(role)
        if isinstance(own, int) and not isinstance(own, bool) and own >= 0:
            return own
        if role in (self.override(slug).get("skip_roles") or []):
            return 0
        d = self.defaults.get("target")
        return d if isinstance(d, int) and not isinstance(d, bool) and d >= 0 else default

    @property
    def negative_extra(self):
        return self.defaults.get("negative_extra", "")

    def size(self, role):
        s = (self.defaults.get("sizes") or {}).get(role)
        return (s[0], s[1]) if s else None

    @property
    def priority(self):
        return list(self.defaults.get("priority") or [])


def _validate(path, roles, defaults):
    if not isinstance(roles, dict) or not roles:
        raise SlotFillError("%s: needs a non-empty 'roles' object" % path)
    for role, bank in roles.items():
        if not isinstance(bank, list) or not bank:
            raise SlotFillError("%s: role %r must be a non-empty list of variations" % (path, role))
        tags = set()
        for i, v in enumerate(bank):
            if not isinstance(v, dict) or not isinstance(v.get("tag"), str) or not v["tag"]:
                raise SlotFillError("%s: role %r variation %d has no tag" % (path, role, i + 1))
            if v["tag"] in tags:
                raise SlotFillError("%s: role %r has the tag %r twice" % (path, role, v["tag"]))
            tags.add(v["tag"])
            for f in FIELDS:
                v.setdefault(f, None if f in ("setting", "style") else "")
    for role, s in (defaults.get("sizes") or {}).items():
        if not (isinstance(s, list) and len(s) == 2 and all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in s)):
            raise SlotFillError("defaults.json: size of %r must be two positive integers" % role)


def load(forge="characters", root=None):
    """Read the three files from <root or the repo>/data/slot-banks. Raises SlotFillError naming the file and the problem. proposed.json is never read."""
    base = os.path.join(root or REPO, "data", "slot-banks")
    bank_path = os.path.join(base, forge + ".json")
    raw = _read(bank_path)
    roles = raw.get("roles") if isinstance(raw, dict) else None
    defaults = _read(os.path.join(base, "defaults.json"))
    overrides = _read(os.path.join(base, "overrides.json"))
    _validate(bank_path, roles, defaults if isinstance(defaults, dict) else {})
    return Banks(roles, defaults if isinstance(defaults, dict) else {}, overrides if isinstance(overrides, dict) else {})
