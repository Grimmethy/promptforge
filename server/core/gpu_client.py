"""Client of the standalone GPU broker (repo agent-manager-gpu-broker, default port 7449).

PromptForge used to run the Forge GPU session in-process (gpu_session.py). With GPU_BROKER_URL set (for example http://localhost:7449) it asks the
broker instead and runs no session of its own; with it unset nothing here is used and PromptForge behaves exactly as before. The broker serves the same
five /api/gpu routes (docs/GPU_FAMILY_PROTOCOL.md) and adds `acquire_steps` to the status: the latest progress text of each acquire still running.

Standard library only. Every call returns the broker's HTTP status together with its JSON, so a 409 from heartbeat ("no session, the pipeline took
the GPU back") is an answer, not an error; BrokerError means the broker could not be reached, or refused an acquire.
"""

import json
import os
import threading
import urllib.error
import urllib.request

STEP_POLL_S = float(os.environ.get("FORGE_BROKER_STEP_POLL_S", "1.0"))
HEARTBEAT_S = float(os.environ.get("FORGE_BROKER_HEARTBEAT_S", "60"))
ACQUIRE_TIMEOUT_S = int(os.environ.get("FORGE_BROKER_ACQUIRE_TIMEOUT_S", "900"))


class BrokerError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def base_url():
    return (os.environ.get("GPU_BROKER_URL") or "").strip().rstrip("/")


def enabled():
    return bool(base_url())


def _call(method, path, body=None, timeout=30):
    """(http_status, parsed_json_dict) for any HTTP status; BrokerError only when the broker cannot be reached."""
    url = base_url() + path
    data = None if method == "GET" else json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _json(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _json(exc.read())
    except (urllib.error.URLError, OSError) as exc:
        raise BrokerError("the GPU broker is not reachable at %s: %s" % (base_url(), getattr(exc, "reason", exc)))


def _json(raw):
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _poll_steps(owner, on_step, stop):
    last = None
    while not stop.wait(STEP_POLL_S):
        try:
            _status, payload = _call("GET", "/api/gpu/status", timeout=5)
        except BrokerError:
            continue
        step = (payload.get("acquire_steps") or {}).get(owner)
        if step and step != last:
            last = step
            try:
                on_step(step)
            except Exception:   # noqa: BLE001 -- a progress display must never break the acquire
                pass


def acquire(owner, on_step=None, card=None):
    """Take the GPU for the Forge family. Blocks until the pipeline's model is gone and ComfyUI answers (minutes on a cold start);
    on_step(text) is called with each new progress step meanwhile. `card` names the broker card (GET /api/gpu/cards) to take; left out, the broker
    uses its default card. Raises BrokerError with the broker's reason when it refuses."""
    stop = threading.Event()
    if on_step:
        threading.Thread(target=_poll_steps, args=(owner, on_step, stop), daemon=True, name="gpu-broker-steps").start()
    try:
        status, payload = _call("POST", "/api/gpu/acquire", {"owner": owner, **({"card": card} if card else {})}, timeout=ACQUIRE_TIMEOUT_S)
    finally:
        stop.set()
    if status != 200:
        raise BrokerError(payload.get("error") or "the GPU broker answered HTTP %d" % status, status)
    return payload


def heartbeat(owner):
    """True when the broker has a session and recorded the activity; False on any other answer (409: the pipeline took the GPU back)."""
    status, _payload = _call("POST", "/api/gpu/heartbeat", {"owner": owner}, timeout=15)
    return status == 200


def forward(method, path, body=None):
    """(http_status, json) of the broker's answer to the same request. PromptForge's own /api/gpu routes use it when it is a broker client, so they
    proxy to the broker and PromptForge never runs a second session. An unreachable broker is a 503 carrying the reason."""
    try:
        return _call(method, path, body, timeout=ACQUIRE_TIMEOUT_S if path.endswith("/acquire") else 60)
    except BrokerError as exc:
        return 503, {"error": str(exc)}


def release():
    return _call("POST", "/api/gpu/release", {}, timeout=60)[1]


def yield_to_pipeline():
    return _call("POST", "/api/gpu/yield", {}, timeout=30)[1]


def status():
    return _call("GET", "/api/gpu/status", timeout=15)[1]


def cards():
    """The broker's cards with who holds each (GET /api/gpu/cards); a BrokerError when the broker cannot be reached."""
    return _call("GET", "/api/gpu/cards", timeout=15)[1]


# ------------------------------------------------------------------- job hold
# The broker cannot see PromptForge's jobs, so while any job runs a thread keeps the session's activity fresh (the broker ends a session that was
# idle for FORGE_SESSION_IDLE_S) and notices if the pipeline took the GPU back.

_hold_lock = threading.Lock()
_holds = 0
_hold_stop = None
_reclaimed = False


def _beat(owner, stop):
    global _reclaimed
    while not stop.wait(HEARTBEAT_S):
        try:
            if not heartbeat(owner):
                _reclaimed = True
        except BrokerError:
            pass   # the broker is away for a moment: the next beat tries again


def job_started(owner):
    global _holds, _hold_stop, _reclaimed
    with _hold_lock:
        if _holds == 0:
            _reclaimed = False
            _hold_stop = threading.Event()
            threading.Thread(target=_beat, args=(owner, _hold_stop), daemon=True, name="gpu-broker-heartbeat").start()
        _holds += 1


def job_finished(owner):
    global _holds
    with _hold_lock:
        _holds = max(0, _holds - 1)
        if _holds == 0 and _hold_stop is not None:
            _hold_stop.set()
    try:
        heartbeat(owner)   # the finished job counts as activity, as gpu_session.touch did in-process
    except BrokerError:
        pass


def reclaimed():
    """True when a heartbeat during the current or last job found no session at the broker (the pipeline took the GPU back)."""
    return _reclaimed
