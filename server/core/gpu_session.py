"""Forge GPU session: the Forge family and the Agent Manager pipeline never share the GPU.

The two families are
    pipeline : Agent Manager's local model(s) served by Ollama
    forge    : ComfyUI and everything that drives it (PromptForge, CharacterForge, StoryForge,
               SceneForge, VideoForge, ...)
and only one of them may have models loaded at a time (owner's rule, 2026-10-13).

A *session* is how the Forge family holds the GPU. Beginning one

  1. writes the shared lease (comfyui-lease.json), so the pipeline's lanes stop starting ticks,
  2. waits for the pipeline's in-flight model calls to finish (instances/.model-locks),
  3. evicts EVERY resident Ollama model (not only when ComfyUI would not fit) and waits for the
     card to clear,
  4. brings ComfyUI up.

While the session lasts a keeper thread keeps the lease fresh (and renews `acquiredAt`, so the
pipeline's 15-minute runaway ceiling cannot wake it mid-render; this module enforces its own,
longer `max_s`). The session ends when

  * a Forge component calls `end` (or the user stops it),
  * nothing used the GPU for `idle_s` (default 20 min) and no job is running,
  * the lease file disappears or changes holder: the pipeline was started explicitly
    (scripts/launch.sh stomps the lease) and wins, or
  * `max_s` (default 4 h) passes.

Ending a session releases the lease AND stops ComfyUI, so VRAM and host RAM are fully free for the
pipeline, which reloads its model on its next tick. Everything is injectable for tests.
"""

import json
import logging
import os
import signal
import subprocess
import threading
import time

from . import comfy_proc

logger = logging.getLogger(__name__)

SESSION_IDLE_S = int(os.environ.get("FORGE_SESSION_IDLE_S", str(20 * 60)))
SESSION_MAX_S = int(os.environ.get("FORGE_SESSION_MAX_S", str(4 * 3600)))
KEEPER_TICK_S = int(os.environ.get("FORGE_KEEPER_TICK_S", "20"))
LOCK_WAIT_S = int(os.environ.get("FORGE_LOCK_WAIT_S", "90"))
EVICT_WAIT_S = int(os.environ.get("FORGE_EVICT_WAIT_S", "45"))
LOCKS_DIR = os.path.expanduser(os.environ.get(
    "AGENT_MANAGER_MODEL_LOCKS_DIR", "/media/model-cache/github/agent-manager/instances/.model-locks"))
LOCK_STALE_S = 5 * 60   # matches agent-manager's model-inflight-lock STALE_MS

_lock = threading.RLock()
_session = None          # dict while a session is active
_keeper = None
last_end_reason = None   # why the most recent session ended (shown to a job that lost the GPU)
_activity_probe = lambda: False   # set by comfy.py: "is a PromptForge job running?"


class SessionError(RuntimeError):
    pass


def set_activity_probe(fn):
    global _activity_probe
    _activity_probe = fn


# ------------------------------------------------------------------- probes (patchable)

def inflight_pipeline_calls(locks_dir=None, now=None):
    """Pipeline model calls that are running right now: lock files younger than the stale ceiling whose pid is alive."""
    d = locks_dir or LOCKS_DIR
    now = time.time() if now is None else now
    out = []
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json")]
    except OSError:
        return out
    for n in names:
        p = os.path.join(d, n)
        try:
            if now - os.path.getmtime(p) > LOCK_STALE_S:
                continue
            with open(p) as f:
                data = json.load(f)
            pid = int(data.get("pid") or 0)
            if pid:
                os.kill(pid, 0)
            out.append(data)
        except (OSError, ValueError):
            continue
    return out


def comfy_listener_pid(port=None):
    """pid listening on ComfyUI's port, but only if it really is a ComfyUI main.py (never kill a stranger)."""
    port = port or int(comfy_proc.COMFY_URL.rsplit(":", 1)[-1])
    try:
        out = subprocess.run(["ss", "-ltnpH", "sport = :%d" % port], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    import re
    m = re.search(r"pid=(\d+)", out)
    if not m:
        return None
    pid = int(m.group(1))
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            cmd = f.read().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return None
    return pid if "main.py" in cmd and "--port" in cmd else None


def stop_comfyui_anywhere(wait_s=20, sleep=time.sleep):
    """Stop ComfyUI however it was started (PromptForge, launcher script, by hand): SIGTERM then SIGKILL."""
    pid = comfy_listener_pid()
    if not pid:
        comfy_proc.stop_comfyui()   # clears a stale pidfile
        return False
    for sig, tries in ((signal.SIGTERM, wait_s * 2), (signal.SIGKILL, 10)):
        try:
            os.kill(pid, sig)
        except OSError:
            break
        for _ in range(tries):
            if not comfy_proc._pid_alive(pid):
                break
            sleep(0.5)
        if not comfy_proc._pid_alive(pid):
            break
    comfy_proc._clear_pidfile()
    return not comfy_proc._pid_alive(pid)


# ------------------------------------------------------------------- lease (family-aware)

def _iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def write_lease(owner, job_id=None, keep_acquired=None):
    """(Re)write the lease. `acquiredAt` is renewed on every call on purpose: it defeats the pipeline's fixed
    15-minute ceiling for a live session; the session's own max_s is the real bound."""
    now = _iso()
    data = {"holder": "promptforge", "family": "forge", "owner": owner, "acquiredAt": now, "refreshedAt": now,
            "jobId": job_id, "sessionStartedAt": keep_acquired or now}
    os.makedirs(os.path.dirname(comfy_proc.COMFY_LEASE_PATH), exist_ok=True)
    tmp = comfy_proc.COMFY_LEASE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, comfy_proc.COMFY_LEASE_PATH)


def lease_intact():
    """True while the lease file still says Forge holds the GPU. False = stomped/removed (pipeline start)."""
    try:
        with open(comfy_proc.COMFY_LEASE_PATH) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False
    return d.get("holder") == "promptforge"


# ------------------------------------------------------------------- begin / touch / end

def status():
    with _lock:
        s = dict(_session) if _session else None
    inv = comfy_proc.gpu_inventory()
    return {"active": s is not None, "session": s, "comfy_up": comfy_proc.comfy_up(),
            "ollama_resident": comfy_proc.ollama_models_on_gpu(), "gpu": inv[:1] and inv[0] or None,
            "pipeline_inflight": len(inflight_pipeline_calls()), "idle_s": SESSION_IDLE_S, "max_s": SESSION_MAX_S}


def begin(owner, set_step=None, sleep=time.sleep, now=time.time):
    """Take the GPU for the Forge family (idempotent: a live session just records activity).
    Returns {started_session, evicted, comfy_started}. Raises SessionError / ComfyUnavailable."""
    global _session
    def step(msg):
        if set_step:
            try:
                set_step(msg)
            except Exception:
                pass

    with _lock:
        if _session and lease_intact():
            _session["last_activity"] = now()
            _session["owners"] = sorted(set(_session.get("owners", [])) | {owner})
            write_lease(owner, keep_acquired=_session["started_iso"])
            return {"started_session": False, "evicted": [], "comfy_started": False}
        if _session:   # lease was stomped since the last keeper tick: that session is over
            _end_locked("pipeline-reclaimed", stop_comfy=True)
        started_iso = _iso()
        write_lease(owner, keep_acquired=started_iso)
        _session = {"owners": [owner], "started": now(), "started_iso": started_iso, "last_activity": now(),
                    "max_s": SESSION_MAX_S, "idle_s": SESSION_IDLE_S}

    try:
        step("pipeline yielding…")
        deadline = now() + LOCK_WAIT_S
        while inflight_pipeline_calls() and now() < deadline:
            step("waiting for the pipeline's in-flight model call…")
            sleep(2)
        names = [m["name"] for m in comfy_proc.ollama_models_on_gpu() if m["name"]]
        if names:
            step("clearing the pipeline's model from the GPU…")
            comfy_proc.evict_ollama_models(names)
            deadline = now() + EVICT_WAIT_S
            while comfy_proc.ollama_models_on_gpu() and now() < deadline:
                sleep(1)
            sleep(2)   # let the runner process release its VRAM
        comfy_started = False
        if not comfy_proc.comfy_up():
            step("starting ComfyUI…")
            comfy_started = comfy_proc.ensure_comfyui_ready(set_step=set_step, job_id=None).get("started", True)
        _check_ram(step, sleep, now)
    except Exception:
        end("begin-failed", stop_comfy=False)
        raise
    _ensure_keeper()
    return {"started_session": True, "evicted": names, "comfy_started": comfy_started}


def _check_ram(step, sleep, now):
    need = comfy_proc.COMFY_MIN_AVAILABLE_RAM_MB
    if need <= 0:
        return
    deadline = now() + comfy_proc.COMFY_RAM_WAIT_S
    avail = comfy_proc.mem_available_mb()
    while avail is not None and avail < need and now() < deadline:
        step("waiting for host RAM (%d MB free)…" % avail)
        sleep(2)
        avail = comfy_proc.mem_available_mb()
    if avail is not None and avail < need:
        raise comfy_proc.ComfyUnavailable(
            "host memory is too low even with the pipeline's model unloaded (%d MB available, need %d MB)" % (avail, need))


def touch(owner=None, now=time.time):
    """Record Forge activity (a job started/finished, a heartbeat). False if there is no session."""
    with _lock:
        if not _session:
            return False
        _session["last_activity"] = now()
        if owner:
            _session["owners"] = sorted(set(_session.get("owners", [])) | {owner})
        return True


def end(reason="released", stop_comfy=True):
    with _lock:
        return _end_locked(reason, stop_comfy)


def _end_locked(reason, stop_comfy):
    global _session, last_end_reason
    was = _session is not None
    _session = None
    if was:
        last_end_reason = reason
    if lease_intact():
        comfy_proc.lease_release()
    if stop_comfy:
        try:
            stop_comfyui_anywhere()
        except Exception as e:   # noqa: BLE001
            logger.warning("stopping ComfyUI failed: %s", e)
    if was:
        logger.info("forge GPU session ended: %s", reason)
    return was


def yield_to_pipeline():
    """The pipeline asks for the GPU. Granted unless a Forge job is running right now."""
    with _lock:
        if _session and _activity_probe():
            return {"yielded": False, "reason": "a Forge job is running"}
    ended = end("pipeline-requested", stop_comfy=True)
    if not ended and comfy_proc.comfy_up() and not _activity_probe():
        stop_comfyui_anywhere()      # ComfyUI left running with no session (manual launch): stop it too
    return {"yielded": True, "reason": "forge session ended" if ended else "no session"}


# ------------------------------------------------------------------- keeper

def keeper_tick(now=time.time):
    """One keeper step: returns the action taken ('refreshed' | 'ended:<why>' | 'none'). Public for tests."""
    with _lock:
        s = _session
        if not s:
            return "none"
        if not lease_intact():
            _end_locked("pipeline-reclaimed", stop_comfy=True)
            return "ended:pipeline-reclaimed"
        t = now()
        busy = _activity_probe()
        if busy:
            s["last_activity"] = t
        if t - s["started"] > s["max_s"]:
            _end_locked("max-duration", stop_comfy=True)
            return "ended:max-duration"
        if t - s["last_activity"] > s["idle_s"]:
            _end_locked("idle", stop_comfy=True)
            return "ended:idle"
        write_lease((s.get("owners") or ["forge"])[-1], keep_acquired=s["started_iso"])
        return "refreshed"


def _keeper_loop():
    global _keeper
    try:
        while True:
            time.sleep(KEEPER_TICK_S)
            try:
                if keeper_tick() == "none":
                    return
            except Exception:   # noqa: BLE001 -- the keeper must survive a bad tick
                logger.exception("keeper tick failed")
            with _lock:
                if not _session:
                    return
    finally:
        _keeper = None


def _ensure_keeper():
    global _keeper
    with _lock:
        if _keeper and _keeper.is_alive():
            return
        _keeper = threading.Thread(target=_keeper_loop, daemon=True, name="forge-gpu-keeper")
        _keeper.start()
