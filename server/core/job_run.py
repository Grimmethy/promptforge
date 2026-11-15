"""Route helpers for the 'Run queue' button (POST/GET /api/jobs/run, POST /api/jobs/run/stop). They only read and write the files scripts/jobs/window_run.py
keeps under <jobs root>/.runner and start or signal that supervisor; the supervisor does the real work (Forge mode, the runner, leaving Forge mode). Nothing
here starts a run by itself: every start comes from a request a person made in the page. Spawning and signalling are injected so the tests never start a process.
"""
import json
import os
import signal
import subprocess
import sys

MIN_HOURS, MAX_HOURS, DEFAULT_HOURS = 0.25, 12, 2
FINISHED = ("done", "refused", "forge-failed")


class RunError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError, OverflowError):
        return False
    except PermissionError:
        return True
    return True


def parse_hours(value):
    if value is None:
        return DEFAULT_HOURS
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not MIN_HOURS <= value <= MAX_HOURS:
        raise RunError("hours must be a number from %s to %s" % (MIN_HOURS, MAX_HOURS))
    return value


def status(store, alive=pid_alive):
    """What the page shows: {running, phase, started_at, until, pictures, jobs_done, jobs_failed, current, reasons, exit_code}; idle when nothing ever ran."""
    run_dir = os.path.join(store.root, ".runner")
    st = _read(os.path.join(run_dir, "window-run.json"))
    if st is None:
        return {"running": False, "phase": "idle", "started_at": None, "until": None, "pictures": 0, "jobs_done": 0, "jobs_failed": 0, "current": None,
                "reasons": [], "exit_code": None}
    phase = st.get("phase")
    running = phase not in FINISHED and bool(alive(st.get("pid")))
    if phase not in FINISHED and not running:
        phase = "ended"                                    # the supervisor died without writing a final phase
    hb = _read(os.path.join(run_dir, "heartbeat.json")) or {}
    fresh = isinstance(hb.get("at"), str) and hb["at"] >= str(st.get("started_at") or "")
    return {"running": running, "phase": phase, "started_at": st.get("started_at"), "until": st.get("until"),
            "pictures": hb.get("pictures", 0) if fresh else 0, "jobs_done": hb.get("jobs_done", 0) if fresh else 0,
            "jobs_failed": hb.get("jobs_failed", 0) if fresh else 0, "current": hb.get("current") if fresh else None,
            "reasons": st.get("reasons") or [], "exit_code": st.get("exit_code")}


def default_spawn(store, hours):
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    run_dir = os.path.join(store.root, ".runner")
    os.makedirs(run_dir, exist_ok=True)
    log = open(os.path.join(run_dir, "window-run.log"), "ab")
    env = dict(os.environ, PROMPTFORGE_JOBS_DIR=store.root)
    proc = subprocess.Popen([sys.executable, "-m", "scripts.jobs.window_run", "--hours", str(hours)], cwd=repo, env=env, stdout=log, stderr=log,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    log.close()
    return proc.pid


def start(store, hours, spawn=None, alive=pid_alive):
    hours = parse_hours(hours)
    if status(store, alive)["running"]:
        raise RunError("a render window is already running", 409)
    if not store.list(state="queued"):
        raise RunError("nothing queued", 409)
    return {"started": True, "pid": (spawn or default_spawn)(store, hours), "hours": hours}


def stop(store, kill=os.kill, alive=pid_alive):
    st = _read(os.path.join(store.root, ".runner", "window-run.json"))
    if not st or st.get("phase") in FINISHED or not alive(st.get("pid")):
        raise RunError("no render window is running", 409)
    kill(int(st["pid"]), signal.SIGTERM)
    return {"stopping": True}
