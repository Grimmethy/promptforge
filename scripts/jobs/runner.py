"""The render job runner: takes the GPU session once, then renders queued jobs one picture at a time through PromptForge until none is left.

    python3 -m scripts.jobs run [--limit N] [--requester NAME] [--priority urgent|normal|bulk] [--dry-run]

Order comes from core/job_plan.py. Exit codes: 0 finished or the picture limit reached, 1 three jobs failed in a row (the service is probably down; the rest stay
queued), 2 no headroom for 30 minutes, 3 another runner is alive. A job that is interrupted goes back to 'queued' with its finished pictures kept, and a re-run
renders only the seeds that have no 'ok' result yet. Nothing here judges or deletes a picture: a blank or tiny one counts as a failed attempt, nothing else.
"""
import datetime
import json
import os
import sys
import logging
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "server"))

logger = logging.getLogger(__name__)
from core import job_plan  # noqa: E402

from scripts.slot_fill import headroom  # noqa: E402
from scripts.slot_fill.engine import good_image  # noqa: E402
from scripts.slot_fill.pf_client import PFClient  # noqa: E402

ATTEMPTS = 3                 # one try and two retries of the same seed
FAIL_STOP = 3                # jobs failing in a row that stop the run


class JobPF(PFClient):
    def models(self):
        """The model names ComfyUI lists (GET /api/comfy/status). [] when the call fails or ComfyUI is not up yet."""
        try:
            names = self._call("/api/comfy/status", timeout=10).get("checkpoints") or []
            return [n for n in names if isinstance(n, str)]
        except Exception:  # noqa: BLE001 - an unknown list just skips the check
            return []


def format_seconds(n):
    n = int(round(n))
    if n < 60:
        return "%ds" % n
    if n < 3600:
        return "%dm" % (n // 60)
    return "%dh %02dm" % (n // 3600, (n % 3600) // 60)


def _iso(t):
    return datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _live_pid(path):
    """The pid of ANOTHER live process written in the pidfile, else None (a missing file, junk, this process and a dead pid are all 'none')."""
    try:
        with open(path, encoding="utf-8") as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    if pid == os.getpid():
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")
    os.replace(tmp, path)


def _spec_for(job, seed):
    spec = {"model": job["model"], "width": job["width"], "height": job["height"], "steps": job["steps"], "cfg": job["cfg"], "seed": seed,
            "verbatim": {"positive": job["positive"], "negative": job["negative"]}}
    for key in ("sampler", "scheduler", "card"):
        if job.get(key) is not None:
            spec[key] = job[key]
    if job.get("loras"):
        spec["loras"] = job["loras"]
    return spec


def _render(pf, spec, owner):
    """One picture, up to ATTEMPTS tries with the same seed; the GPU session is taken again before each retry. Returns (generated, None) or (None, message)."""
    last = None
    for attempt in range(ATTEMPTS):
        if attempt:
            try:
                pf.gpu_acquire(owner)
            except Exception:  # noqa: BLE001 - the retry itself will report the real problem
                pass
        try:
            gen = pf.generate(spec)
            if not good_image(gen.data):
                raise ValueError("the picture is blank or tiny")
            return gen, None
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001
            last = str(exc) or exc.__class__.__name__
    return None, last


def _dry_run(store, requester, priority, timings_path, out):
    queued = [j for j in store.list(state="queued") if (requester is None or j["requester"] == requester) and (priority is None or j["priority"] == priority)]
    for i, j in enumerate(job_plan.order_jobs(queued), 1):
        out("%d. %s  %s  %s  %d seeds  %s" % (i, j["id"], j["requester"], j["priority"], len(j["seeds"]), j["model"]))
    est = job_plan.estimate(queued, job_plan.load_timings(timings_path))
    out("%d jobs, %d pictures, about %s%s" % (len(queued), est["pictures"], format_seconds(est["total_seconds"]), " (rough)" if est["rough"] else ""))
    return 0


def _parse_until(until):
    """Epoch seconds for a UTC ISO string like 2026-11-14T05:00:00Z, or None when until is None. ValueError for anything else."""
    if until is None:
        return None
    return datetime.datetime.strptime(until, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()


def run(store, pf, *, limit=0, requester=None, priority=None, dry_run=False, owner="job-queue", deliver=None, timings_path=None, sleep=time.sleep,
        now=time.time, wait=headroom.wait, out=print, until=None, stop_file=None, reasons=None):
    try:
        until_ts = _parse_until(until)
    except (ValueError, TypeError):
        out("error: until must be a UTC time like 2026-11-14T05:00:00Z")
        return 1
    def halt():
        """Why the run must stop now (a stop-file exists, or the stop time has come), else None. Checked before each job and before each picture."""
        if stop_file and os.path.exists(stop_file):
            return "stop requested: stopping"
        if until_ts is not None and now() >= until_ts:
            return "window ended: stopping"
        return None

    state_dir = os.path.join(store.root, ".runner")
    timings_path = timings_path or os.path.join(store.root, "timings.jsonl")
    if dry_run:
        return _dry_run(store, requester, priority, timings_path, out)
    pidfile, heartbeat = os.path.join(state_dir, "runner.pid"), os.path.join(state_dir, "heartbeat.json")
    other = _live_pid(pidfile)
    if other:
        out("another runner is running (pid %d)" % other)
        return 3
    os.makedirs(state_dir, exist_ok=True)
    with open(pidfile, "w", encoding="utf-8") as fh:
        fh.write("%d\n" % os.getpid())
    st = {"pictures": 0, "jobs_done": 0, "jobs_failed": 0, "current": None}
    code, acquired = 0, False

    def beat():
        _write_json(heartbeat, {"pid": os.getpid(), "at": _iso(now()), "pictures": st["pictures"], "jobs_done": st["jobs_done"],
                                "jobs_failed": st["jobs_failed"], "current": st["current"]})

    try:
        for rec in store.list(state="running"):             # no live runner owns these: an earlier run was interrupted
            store.set_state(rec["id"], "queued")
        try:
            pf.gpu_acquire(owner)
            acquired = True
        except Exception as exc:  # noqa: BLE001
            reason = str(exc)[:300] or exc.__class__.__name__
            out("could not take the GPU session: %s" % reason)
            if reasons is not None:
                reasons.append("could not take the GPU session: %s" % reason)
            return 1
        try:
            available = set(pf.models() or [])
        except Exception:  # noqa: BLE001
            available = set()
        beat()
        fails, last_requester = 0, None
        while True:
            if limit and st["pictures"] >= limit:
                break
            queued = [j for j in store.list(state="queued") if (requester is None or j["requester"] == requester) and (priority is None or j["priority"] == priority)]
            job = job_plan.next_job(queued, last_requester)
            if job is None:
                break
            why = halt()
            if why:
                out(why)
                break
            if not wait("light", 1800, 30, sleep, time.monotonic):
                out("no headroom for 30 minutes: stopping")
                code = 2
                break
            last_requester = job["requester"]
            outcome = _run_job(store, pf, job, available, st, owner, deliver, timings_path, now, out, beat, halt)
            if outcome == "stopped":
                out(halt() or "window ended: stopping")
                break
            if outcome == "failed":
                fails += 1
                st["jobs_failed"] += 1
            elif outcome == "done":
                fails = 0
                st["jobs_done"] += 1
            st["current"] = None
            beat()
            if fails >= FAIL_STOP:
                out("three failed jobs in a row: stopping")
                code = 1
                break
        return code
    except (KeyboardInterrupt, SystemExit):
        if st["current"]:
            try:
                store.set_state(st["current"], "queued")
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        if acquired:
            try:
                pf.gpu_release()
            except Exception as exc:  # noqa: BLE001
                out("could not release the GPU session: %s" % exc)
        try:
            os.unlink(pidfile)
        except OSError:
            pass
        st["current"] = None
        try:
            beat()
        except OSError:
            pass


def _run_job(store, pf, job, available, st, owner, deliver, timings_path, now, out, beat, halt=None):
    """Render one job. Returns 'done', 'failed', 'blocked' or 'stopped' (the window ended between pictures; the job is back in 'queued' with its results)."""
    if available and job["model"] not in available:
        store.set_state(job["id"], "blocked", {"error": "model %s is not installed in ComfyUI" % job["model"]})
        out("%s blocked: model %s is not installed in ComfyUI" % (job["id"], job["model"]))
        return "blocked"
    rec = store.set_state(job["id"], "running", {"started_at": _iso(now()), "attempts": job.get("attempts", 0) + 1})
    st["current"] = rec["id"]
    results = [dict(r) for r in rec.get("results") or [] if r.get("status") == "ok"]       # earlier errors are retried
    have = {r["seed"] for r in results}
    todo = [s for s in rec["seeds"] if s not in have]
    failed, first_error = 0, None
    for i, seed in enumerate(todo):
        if halt is not None and halt():
            store.set_state(rec["id"], "queued", {"results": results})
            return "stopped"
        t0 = now()
        gen, err = _render(pf, _spec_for(rec, seed), owner)
        secs = now() - t0
        if gen is None:
            results.append({"seed": seed, "status": "error", "error": (err or "")[:200]})
            store.set_state(rec["id"], "running", {"results": results})
            failed += 1
            first_error = first_error or err
            out("%s seed %d failed: %s" % (rec["id"], seed, (err or "")[:100]))
            if i == 0:
                msg = "first picture failed after %d attempts: %s; the remaining %d seeds were not tried" % (ATTEMPTS, (err or "")[:150], len(todo) - 1)
                store.set_state(rec["id"], "failed", {"results": results, "finished_at": _iso(now()), "error": msg})
                return "failed"
            continue
        row = {"seed": seed, "status": "ok", "gallery": gen.name, "seconds": int(round(secs)), "at": _iso(now())}
        if secs > 0:
            try:
                job_plan.append_timing(timings_path, {"model": rec["model"], "width": rec["width"], "height": rec["height"], "steps": rec["steps"], "seconds": secs})
            except (ValueError, OSError) as exc:
                out("%s seed %d: could not record timing to %s: %s: %s" % (rec["id"], seed, timings_path, exc.__class__.__name__, exc))
        if deliver is not None:
            try:
                delivered = deliver(rec, seed, gen)
                if isinstance(delivered, str) and delivered:
                    row["delivered"] = delivered
            except Exception as exc:  # noqa: BLE001 - the picture exists in the gallery; delivery is reported, never fatal
                row["delivery_error"] = str(exc)[:200]
        results.append(row)
        store.set_state(rec["id"], "running", {"results": results})
        st["pictures"] += 1
        beat()
        out("%s seed %d ok %ds" % (rec["id"], seed, row["seconds"]))
    if failed:
        store.set_state(rec["id"], "failed", {"results": results, "finished_at": _iso(now()), "error": "%d of %d seeds failed: %s" % (failed, len(rec["seeds"]), (first_error or "")[:150])})
        return "failed"
    store.set_state(rec["id"], "done", {"results": results, "finished_at": _iso(now()), "error": None})
    return "done"
