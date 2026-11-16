"""Planning logic of the render job runner: which queued job runs next, and how long the queue will take. Pure functions plus two tiny timing-file helpers;
no ComfyUI, no network, no GPU. The runner (scripts/jobs/runner.py) calls next_job() after every job and estimate() for the queue page and the status line.

Order: urgent before normal before bulk. Inside a level, jobs with a needed_by date come first (earliest date first); the rest are served round-robin between
requesters so one big request cannot starve the others.
"""
import json
import os
import statistics

LEVELS = ("urgent", "normal", "bulk")
MODEL_LOAD_SECONDS = 30
FALLBACK_SECONDS_PER_MPIXEL_STEP = {"sdxl": 0.35, "krea2": 2.5}
_KEYS = ("model", "width", "height", "steps", "seconds")


def _level(job):
    p = job.get("priority")
    return LEVELS.index(p) if p in LEVELS else LEVELS.index("normal")


def _own_key(job):
    # the middle element is the filing order (filed_us) that keeps same-second jobs in the order they were filed
    return (job.get("created_at") or "", int(job.get("filed_us") or 0), job.get("id") or "")


def _round_robin(jobs, last_requester):
    """One job per requester per pass. Requesters are ordered by (earliest created_at among their jobs, name); the first pass starts after last_requester."""
    by = {}
    for j in sorted(jobs, key=_own_key):
        by.setdefault(j.get("requester") or "", []).append(j)
    order = sorted(by, key=lambda r: (_own_key(by[r][0])[0], r))
    if last_requester in order:
        i = order.index(last_requester) + 1
        order = order[i:] + order[:i]
    out = []
    while any(by[r] for r in order):
        for r in order:
            if by[r]:
                out.append(by[r].pop(0))
    return out


def order_jobs(jobs, last_requester=None):
    """The planned sequence of the queued jobs in `jobs` (other states are ignored). Never mutates its input."""
    queued = [j for j in jobs if j.get("state") == "queued"]
    out = []
    for lvl in range(len(LEVELS)):
        level = [j for j in queued if _level(j) == lvl]
        dated = sorted((j for j in level if j.get("needed_by")), key=lambda j: (j["needed_by"],) + _own_key(j))
        out += dated + _round_robin([j for j in level if not j.get("needed_by")], last_requester)
    return out


def next_job(jobs, last_requester=None):
    planned = order_jobs(jobs, last_requester)
    return planned[0] if planned else None


def _valid_row(row):
    return (isinstance(row, dict) and all(k in row for k in _KEYS) and isinstance(row["model"], str) and bool(row["model"])
            and all(isinstance(row[k], (int, float)) and not isinstance(row[k], bool) and row[k] > 0 for k in _KEYS[1:]))


def load_timings(path, limit=500):
    """The last `limit` valid rows of a JSONL timing file, in file order. A missing file gives []; blank, corrupt and invalid lines are skipped."""
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if _valid_row(row):
                    rows.append(row)
    except OSError:
        return []
    return rows[-limit:] if limit else rows


def append_timing(path, row):
    """Append one timing row (seconds for ONE picture). One os.write on an O_APPEND file, so concurrent writers never interleave a line."""
    if not _valid_row(row):
        raise ValueError("invalid timing row")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = (json.dumps({k: row[k] for k in _KEYS}) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _family(model):
    return "krea2" if isinstance(model, str) and model.startswith("krea2:") else "sdxl"


def _seconds_per_picture(job, timings):
    """(seconds for one picture, used_fallback)."""
    w, h, steps, model = job["width"], job["height"], job["steps"], job["model"]
    same = [r for r in timings if r["model"] == model]
    exact = [r["seconds"] for r in same if (r["width"], r["height"], r["steps"]) == (w, h, steps)]
    if exact:
        return statistics.median(exact), False
    if same:
        per_unit = statistics.median(r["seconds"] / (r["width"] * r["height"] * r["steps"]) for r in same)
        return per_unit * w * h * steps, False
    return FALLBACK_SECONDS_PER_MPIXEL_STEP[_family(model)] * (w * h / 1e6) * steps, True


def estimate(jobs, timings):
    """{"total_seconds", "per_job": {id: seconds}, "pictures", "rough"} for the queued jobs. The model load time is added once per distinct model, in planned order.
    `rough` is true when any job had no timing for its model and used the built-in fallback."""
    planned = order_jobs(jobs)
    per_job, pictures, rough, models, total = {}, 0, False, set(), 0
    for j in planned:
        each, fallback = _seconds_per_picture(j, timings)
        rough = rough or fallback
        secs = round(each * len(j["seeds"]))
        per_job[j["id"]] = secs
        pictures += len(j["seeds"])
        total += secs
        if j["model"] not in models:
            models.add(j["model"])
            total += MODEL_LOAD_SECONDS
    return {"total_seconds": int(total), "per_job": per_job, "pictures": pictures, "rough": rough}
