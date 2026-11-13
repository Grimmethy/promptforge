"""Render job queue core: the job record, its checks, the licence rule and an atomic file store. Stdlib only, no Flask.

A job is one frozen generation request: the exact final prompt, the model, size, steps, cfg, a list of seeds (one picture per seed), who asked and
what for. Jobs are built in advance by any client and rendered later in a batch window. One JSON file per job lives at <root>/<state>/<id>.json; the
root is PROMPTFORGE_JOBS_DIR or ~/.local/share/promptforge/jobs, outside the repo. Nothing here talks to ComfyUI or the GPU.
"""
import datetime
import json
import os
import re
import secrets
import threading

PRIORITIES = ("urgent", "normal", "bulk")
STATES = ("queued", "held", "running", "done", "failed", "blocked", "removed")
ARCHIVE = "archive"
MAX_SEEDS = 200
VERSION = 1
SELLABLE = ("yes", "conditional")
# how far along a state is, for picking the right copy of a record found in two folders after a crash
_ORDER = {s: i for i, s in enumerate(("queued", "held", "running", "blocked", "failed", "done", "removed"))}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FINISHED = ("done", "failed", "removed")


class JobError(ValueError):
    """A job or an operation on one is refused. `errors` lists every problem, one readable string each."""

    def __init__(self, errors):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


class JobStateError(JobError):
    """The job exists but is in the wrong state for this operation."""


EDITABLE = ("priority", "needed_by", "notes", "purpose", "positive", "negative", "seeds")
_SPEC_KEYS = ("requester", "purpose", "commercial", "priority", "needed_by", "model", "positive", "negative", "width", "height", "steps", "cfg", "sampler",
              "scheduler", "loras", "seeds", "destination", "card", "notes")
WINDOW_MAX_HOURS = 12
_STAMP = "%Y-%m-%dT%H:%M:%SZ"


def default_root():
    return os.path.abspath(os.path.expanduser(os.environ.get("PROMPTFORGE_JOBS_DIR") or os.path.join("~", ".local", "share", "promptforge", "jobs")))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _utc(now=None):
    return (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc)


def _is_krea(model):
    return isinstance(model, str) and model.startswith("krea2:")


def normalize(spec, now=None, rand=None):
    """The full record for a request spec: defaults filled, id/created_at/state/attempts/error/results added. Touches no disk. Raises JobError listing
    every problem at once. `now` and `rand` (a callable returning 6 hex chars) are injectable so tests get fixed ids."""
    errs = []
    if not isinstance(spec, dict):
        raise JobError("the job must be an object")

    def text(key, low, high, default=None):
        v = spec.get(key, default)
        if v is None or not isinstance(v, str):
            errs.append("%s must be a string of %d to %d characters" % (key, low, high))
            return ""
        if not low <= len(v) <= high:
            errs.append("%s must be %d to %d characters (it is %d)" % (key, low, high, len(v)))
        return v

    requester = text("requester", 1, 60)
    purpose = text("purpose", 0, 500, "")
    model = text("model", 1, 300)
    positive = text("positive", 1, 8000)
    negative = text("negative", 0, 4000, "")
    notes = text("notes", 0, 2000, "")
    commercial = spec.get("commercial", False)
    if not isinstance(commercial, bool):
        errs.append("commercial must be true or false")
    priority = spec.get("priority", "normal")
    if priority not in PRIORITIES:
        errs.append("priority must be one of %s" % ", ".join(PRIORITIES))
    needed_by = spec.get("needed_by")
    if needed_by is not None:
        try:
            if not (isinstance(needed_by, str) and _DATE.match(needed_by)):
                raise ValueError
            datetime.date.fromisoformat(needed_by)
        except ValueError:
            errs.append("needed_by must be a date like 2026-11-20 or null")
    dims = {}
    for k in ("width", "height"):
        v = spec.get(k)
        if not _is_int(v) or not 64 <= v <= 4096 or v % 8:
            errs.append("%s must be an integer from 64 to 4096 and a multiple of 8" % k)
        dims[k] = v
    krea = _is_krea(model)
    steps = spec.get("steps", 8 if krea else 28)
    if not _is_int(steps) or not 1 <= steps <= 150:
        errs.append("steps must be an integer from 1 to 150")
    cfg = spec.get("cfg", 1.0 if krea else 5.0)
    if not _is_num(cfg) or not 0 <= cfg <= 30:
        errs.append("cfg must be a number from 0 to 30")
    for k in ("sampler", "scheduler", "card"):
        if spec.get(k) is not None and not (isinstance(spec[k], str) and 0 < len(spec[k]) <= 80):
            errs.append("%s must be a short string or null" % k)
    loras = spec.get("loras", [])
    if not isinstance(loras, list):
        errs.append("loras must be a list")
        loras = []
    clean_loras = []
    for i, lo in enumerate(loras):
        ok = isinstance(lo, dict) and isinstance(lo.get("file"), str) and lo["file"] and _is_num(lo.get("weight")) and -2 <= lo["weight"] <= 2
        if not ok:
            errs.append("loras[%d] must be {file, weight} with a weight from -2 to 2" % i)
        else:
            clean_loras.append({"file": lo["file"], "weight": lo["weight"]})
    if clean_loras and krea:
        errs.append("loras are not allowed with a krea2: model")
    seeds = spec.get("seeds")
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= MAX_SEEDS:
        errs.append("seeds must be a list of 1 to %d integers" % MAX_SEEDS)
        seeds = []
    for i, s in enumerate(seeds):
        if not _is_int(s) or not 0 <= s <= 4294967295:
            errs.append("seeds[%d] must be an integer 0..4294967295" % i)
    if len(set(x for x in seeds if _is_int(x))) != len([x for x in seeds if _is_int(x)]):
        errs.append("seeds must not repeat (one picture per seed)")
    dest = spec.get("destination", {"kind": "none"})
    kind = dest.get("kind") if isinstance(dest, dict) else None
    if kind == "none":
        dest = {"kind": "none"}
    elif kind == "copy-dir":
        path = dest.get("path")
        if not (isinstance(path, str) and os.path.isabs(path)):
            errs.append("destination.path must be an absolute folder path")
        dest = {"kind": "copy-dir", "path": path}
    elif kind == "characterforge":
        if not all(isinstance(dest.get(k), str) and dest.get(k) for k in ("slug", "role")):
            errs.append("destination needs a slug and a role for characterforge")
        dest = {"kind": "characterforge", "slug": dest.get("slug"), "role": dest.get("role")}
    else:
        errs.append("destination.kind must be none, copy-dir or characterforge")
        dest = {"kind": "none"}
    if errs:
        raise JobError(errs)
    stamp = _utc(now)
    make = rand or (lambda: secrets.token_hex(3))
    return {"version": VERSION, "id": "job-%s-%s" % (stamp.strftime("%Y%m%d%H%M%S"), make()), "requester": requester, "purpose": purpose,
            "commercial": commercial, "priority": priority, "needed_by": needed_by, "created_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "state": "queued", "model": model, "positive": positive, "negative": negative, "width": dims["width"], "height": dims["height"],
            "steps": steps, "cfg": cfg, "sampler": spec.get("sampler"), "scheduler": spec.get("scheduler"), "loras": clean_loras, "seeds": list(seeds),
            "destination": dest, "card": spec.get("card"), "notes": notes, "attempts": 0, "error": None, "results": []}


def licence_problem(job, lookup=None):
    """None when the job may be queued, else a message. A commercial job needs 'yes' or 'conditional' for its model and every lora file."""
    if not job.get("commercial"):
        return None
    if lookup is None:
        from core import licenses
        lookup = licenses.lookup
    bad = []
    for name in [job.get("model")] + [lo["file"] for lo in job.get("loras") or []]:
        answer = (lookup(name) or {}).get("commercial_use", "unverified")
        if answer not in SELLABLE:
            bad.append("%s is '%s'" % (name, answer))
    if bad:
        return "commercial use is not cleared for: " + "; ".join(bad)
    return None


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def _write_atomic(path, data):
    tmp = "%s.%s.tmp" % (path, secrets.token_hex(4))
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


class JobStore:
    """The job files. All writes are atomic (temp file in the same folder, then link or replace)."""

    def __init__(self, root=None, lookup=None, clock=None, rand=None):
        self.root = os.path.abspath(root) if root else default_root()
        self._lookup, self._clock, self._rand = lookup, clock, rand
        self._lock = threading.Lock()

    def _dir(self, state):
        return os.path.join(self.root, state)

    def _path(self, state, jid):
        return os.path.join(self._dir(state), jid + ".json")

    def add(self, spec):
        """Validate, check the licence, and write the job into queued/. Returns the stored record. JobError on any problem."""
        for attempt in range(5):
            job = normalize(spec, now=self._clock() if self._clock else None, rand=self._rand)
            problem = licence_problem(job, self._lookup)
            if problem:
                raise JobError(problem)
            qdir = self._dir("queued")
            os.makedirs(qdir, exist_ok=True)
            final = self._path("queued", job["id"])
            tmp = os.path.join(qdir, ".%s.%s.tmp" % (job["id"], secrets.token_hex(4)))
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(job, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            try:
                os.link(tmp, final)          # fails when the id exists, so a collision never overwrites a job
            except FileExistsError:
                continue
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return job
        raise JobError("could not find a free job id after 5 tries")

    def _find(self, jid):
        best = None
        for state in STATES:
            rec = _read(self._path(state, jid))
            if rec is not None and (best is None or _ORDER[state] > _ORDER[best[0]]):
                best = (state, rec)
        return best

    def get(self, jid):
        found = self._find(jid) if isinstance(jid, str) and re.fullmatch(r"job-[0-9]{14}-[0-9a-f]{6}", jid) else None
        return found[1] if found else None

    def list(self, state=None, requester=None):
        """Records sorted by created_at then id; a record present in two folders (a crash between steps) appears once, from the later state."""
        seen = {}
        for st in ([state] if state else STATES):
            if st not in STATES:
                raise JobError("unknown state %s" % st)
            try:
                names = os.listdir(self._dir(st))
            except OSError:
                continue
            for name in names:
                if not name.endswith(".json") or name.startswith("."):
                    continue
                rec = _read(os.path.join(self._dir(st), name))
                if rec is None or not isinstance(rec.get("id"), str):
                    continue
                prior = seen.get(rec["id"])
                if prior is None or _ORDER[st] > _ORDER[prior[0]]:
                    seen[rec["id"]] = (st, rec)
        out = [r for _s, r in seen.values() if requester is None or r.get("requester") == requester]
        return sorted(out, key=lambda r: (r.get("created_at") or "", r["id"]))

    def set_state(self, jid, state, patch=None):
        """Move a job to another state folder and rewrite it with the new state and the patch merged in."""
        if state not in STATES:
            raise JobError("unknown state %s" % state)
        with self._lock:
            found = self._find(jid) if isinstance(jid, str) else None
            if not found:
                raise JobError("no job %s" % jid)
            old, rec = found
            rec = dict(rec, **(patch or {}))
            rec["state"] = state
            os.makedirs(self._dir(state), exist_ok=True)
            if old != state:
                os.replace(self._path(old, jid), self._path(state, jid))
            _write_atomic(self._path(state, jid), rec)
            return rec

    def _transition(self, jid, allowed, target):
        rec = self.get(jid)
        if rec is None:
            raise JobError("no job %s" % jid)
        if rec.get("state") not in allowed:
            raise JobStateError("job %s is %s; this is allowed only from %s" % (jid, rec.get("state"), ", ".join(allowed)))
        return self.set_state(jid, target)

    def hold(self, jid):
        return self._transition(jid, ("queued",), "held")

    def release(self, jid):
        return self._transition(jid, ("held",), "queued")

    def remove(self, jid):
        return self._transition(jid, ("queued", "held"), "removed")

    def update(self, jid, patch):
        """Edit a queued or held job. Only the keys in EDITABLE may change; the merged spec is validated like a new job and the licence rule is checked again.
        Results, attempts, created_at and the id are never touched."""
        rec = self.get(jid)
        if rec is None:
            raise JobError("no job %s" % jid)
        if rec.get("state") not in ("queued", "held"):
            raise JobStateError("job %s is %s; it can be edited only while queued or held" % (jid, rec.get("state")))
        if not isinstance(patch, dict):
            raise JobError("the changes must be an object")
        bad = [k for k in patch if k not in EDITABLE]
        if bad:
            raise JobError("cannot edit: %s" % ", ".join(bad))
        merged = {k: rec[k] for k in _SPEC_KEYS if k in rec}
        merged.update(patch)
        clean = normalize(merged)
        problem = licence_problem(clean, self._lookup)
        if problem:
            raise JobError(problem)
        return self.set_state(jid, rec["state"], {k: clean[k] for k in patch})

    def _window_path(self):
        return os.path.join(self.root, "window.json")

    def window(self):
        """The armed window {start, end, armed_at} or None. Arming only records it; nothing here starts a run."""
        rec = _read(self._window_path())
        return rec if rec and all(isinstance(rec.get(k), str) for k in ("start", "end", "armed_at")) else None

    def set_window(self, start, end, now=None):
        errs = []
        parsed = {}
        for name, value in (("start", start), ("end", end)):
            try:
                parsed[name] = datetime.datetime.strptime(value, _STAMP).replace(tzinfo=datetime.timezone.utc)
            except (ValueError, TypeError):
                errs.append("%s must be a UTC time like 2026-11-14T05:00:00Z" % name)
        stamp = _utc(now or (self._clock() if self._clock else None))
        if len(parsed) == 2:
            if parsed["end"] <= parsed["start"]:
                errs.append("end must be after start")
            elif parsed["end"] - parsed["start"] > datetime.timedelta(hours=WINDOW_MAX_HOURS):
                errs.append("a window can be at most %d hours long" % WINDOW_MAX_HOURS)
            if parsed["end"] <= stamp:
                errs.append("end must be in the future")
        if errs:
            raise JobError(errs)
        rec = {"start": start, "end": end, "armed_at": stamp.strftime(_STAMP)}
        os.makedirs(self.root, exist_ok=True)
        _write_atomic(self._window_path(), rec)
        return rec

    def clear_window(self):
        try:
            os.unlink(self._window_path())
            return True
        except OSError:
            return False

    def archive_old(self, days=30):
        """Move done, failed and removed records older than `days` into archive/. Returns their ids. Queued, held, running and blocked are never archived."""
        cutoff = _utc(self._clock() if self._clock else None) - datetime.timedelta(days=days)
        moved = []
        for st in _FINISHED:
            for rec in self.list(state=st):
                when = rec.get("finished_at") or rec.get("created_at") or ""
                try:
                    stamp = datetime.datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                except ValueError:
                    continue
                if stamp < cutoff:
                    os.makedirs(self._dir(ARCHIVE), exist_ok=True)
                    os.replace(self._path(st, rec["id"]), self._path(ARCHIVE, rec["id"]))
                    moved.append(rec["id"])
        return moved
