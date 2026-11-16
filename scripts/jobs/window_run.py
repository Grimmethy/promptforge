"""One render window, start to finish, started by a click in the PromptForge page (POST /api/jobs/run) and never by anything on its own:

    python3 -m scripts.jobs.window_run --hours H        (H from 0.25 to 12, default 2)

Takes Agent Manager's Forge mode (pauses the pipeline, unloads the local model, takes the GPU session), runs the job runner until the stop time or until a
stop is requested, and ALWAYS leaves Forge mode again, whatever happened. Progress is written to <jobs root>/.runner/window-run.json (the page reads it).
Exit codes: 0 finished or stopped, 2 hours out of range, 3 another window run is alive, 4 Forge mode refused (nothing was changed), 5 Forge mode could not
be entered in time. Every collaborator (HTTP, clock, sleep, the runner) is injected so the tests never start anything real.
"""
import argparse
import datetime
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "server"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from core import jobs  # noqa: E402
from scripts.jobs.run_steps import step  # noqa: E402

DEFAULT_HOURS = 2
MIN_HOURS, MAX_HOURS = 0.25, 12
ENTER_WAIT = 300           # seconds to wait for Forge mode to finish switching on
LEAVE_WAIT = 180           # seconds to wait for it to finish switching off


def _iso(t):
    return datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def paths(store):
    d = os.path.join(store.root, ".runner")
    return {"dir": d, "state": os.path.join(d, "window-run.json"), "stop": os.path.join(d, "stop"), "heartbeat": os.path.join(d, "heartbeat.json")}


def read_state(store):
    try:
        with open(paths(store)["state"], encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def _write_state(store, rec):
    p = paths(store)
    os.makedirs(p["dir"], exist_ok=True)
    tmp = "%s.%d.tmp" % (p["state"], os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    os.replace(tmp, p["state"])


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True
    return True


def http_json(method, url, body=None, timeout=30):
    """(status code, parsed JSON or {}) for one request; a connection failure is (0, {'error': ...})."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, {"error": str(getattr(exc, "reason", exc))}


def supervise(store, hours, *, post=None, get=None, run_fn=None, sleep=time.sleep, now=time.time, out=print, am_url=None):
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not MIN_HOURS <= hours <= MAX_HOURS:
        out("error: hours must be from %s to %s" % (MIN_HOURS, MAX_HOURS))
        return 2
    am = (am_url or os.environ.get("AGENT_MANAGER_URL") or "http://localhost:7420").rstrip("/")
    post = post or (lambda path, body: http_json("POST", am + path, body))
    get = get or (lambda path: http_json("GET", am + path))
    if run_fn is None:
        from scripts.jobs import destinations, runner
        run_fn = lambda **kw: runner.run(store, runner.JobPF(), deliver=destinations.make_deliver(), out=out, **kw)  # noqa: E731
    p = paths(store)
    prev = read_state(store)
    if prev and prev.get("phase") not in ("done", "refused", "forge-failed") and pid_alive(prev.get("pid")) and prev.get("pid") != os.getpid():
        out("another window run is running (pid %s)" % prev.get("pid"))
        return 3
    started = now()
    until = _iso(started + hours * 3600)
    state = {"pid": os.getpid(), "started_at": _iso(started), "until": until, "hours": hours, "phase": "forge-on", "reasons": [], "exit_code": None}

    def phase(name, **extra):
        state["phase"] = name
        state.update(extra)
        _write_state(store, state)

    os.makedirs(p["dir"], exist_ok=True)
    try:
        os.unlink(p["stop"])
    except OSError:
        pass
    phase("forge-on")
    code, body = post("/api/pipeline/forge-mode", {"on": True, "vm": "keep"})
    if code != 202:
        reasons = body.get("reasons") or [body.get("error") or "the dashboard answered %s" % code]
        out("Forge mode refused: %s" % "; ".join(reasons))
        phase("refused", reasons=reasons, exit_code=4)         # nothing was changed, so nothing is switched off
        return 4
    entered = False
    try:
        out("%s wait start: enter Forge mode" % _iso(now()))
        deadline = now() + ENTER_WAIT
        while now() < deadline:
            c, st = get("/api/pipeline/forge-mode")
            if c == 200 and st.get("paused") and (st.get("gpuSession") or {}).get("active"):
                entered = True
                break
            with step("wait_supervisor"):  # poll-ok: pre-existing bounded Forge-mode wait (ENTER_WAIT 300s), not added here
                sleep(5)
        out("%s wait end: enter Forge mode entered=%s" % (_iso(now()), entered))
        if not entered:
            out("Forge mode did not finish switching on within %d seconds" % ENTER_WAIT)
            phase("forge-failed", reasons=["Forge mode did not finish switching on in time"], exit_code=5)
            return 5
        phase("rendering")
        out("%s runner call start" % _iso(now()))
        t_run = now()
        rc = run_fn(until=until, stop_file=p["stop"])
        out("%s runner call end duration_s=%.3f" % (_iso(now()), now() - t_run))
        state["exit_code"] = rc
        return 0 if rc in (0, None) else rc
    except BaseException as exc:  # noqa: BLE001 - the finally below must run for anything, then the error goes on
        state["reasons"] = [str(exc)[:200] or exc.__class__.__name__]
        raise
    finally:
        phase("leaving")
        try:
            post("/api/pipeline/forge-mode", {"on": False})
            out("%s wait start: leave Forge mode" % _iso(now()))
            deadline = now() + LEAVE_WAIT
            while now() < deadline:
                c, st = get("/api/pipeline/forge-mode")
                if c == 200 and not st.get("paused"):
                    break
                with step("wait_supervisor"):  # poll-ok: pre-existing bounded Forge-mode wait (LEAVE_WAIT 180s), not added here
                    sleep(5)
            out("%s wait end: leave Forge mode" % _iso(now()))
        except Exception as exc:  # noqa: BLE001
            state["reasons"] = state.get("reasons", []) + ["could not confirm Forge mode is off: %s" % str(exc)[:120]]
        try:
            os.unlink(p["stop"])
        except OSError:
            pass
        phase("done")


def main(argv=None, store=None, **kw):
    ap = argparse.ArgumentParser(prog="python3 -m scripts.jobs.window_run")
    ap.add_argument("--hours", type=float, default=DEFAULT_HOURS)
    args = ap.parse_args(argv)
    store = store or jobs.JobStore()

    def on_term(signum, frame):  # a stop request: the runner finishes the picture in hand, then the finally block leaves Forge mode
        try:
            os.makedirs(paths(store)["dir"], exist_ok=True)
            open(paths(store)["stop"], "w").close()
        except OSError:
            pass
    signal.signal(signal.SIGTERM, on_term)
    return supervise(store, args.hours, **kw)


if __name__ == "__main__":
    sys.exit(main())
