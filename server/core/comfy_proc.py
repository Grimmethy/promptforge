"""Process + GPU coordination for ComfyUI.

PromptForge normally assumes ComfyUI is launched externally. This module lets a
generation bring ComfyUI up on its own when it's down -- but only after checking
whether doing so would collide with the agent-manager pipeline's local model
(qwen via Ollama) on the same GPU.

The rule (see the plan / feedback-gpu-coordination memory):
  * If some GPU already has room for ComfyUI -> just start it there. No eviction,
    no lease, the pipeline is never touched.
  * Only when ComfyUI genuinely can't fit alongside what's already resident on its
    target GPU do we take the shared lease file, evict Ollama's model(s), and
    start ComfyUI. While the lease is held the pipeline yields its local-model
    ticks and gpu-guard.js won't unload ComfyUI.
  * A hard LEASE_MAX_S ceiling (enforced on the agent-manager side) means neither
    workload can starve the other forever, and an explicit `bash scripts/launch.sh`
    stomps the lease.

Everything here is best-effort: a missing nvidia-smi, an unreachable Ollama, or a
spawn failure degrades to "just try to start ComfyUI, no lease".
"""

import calendar
import json
import logging
import os
import shlex
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")

COMFY_AUTOSTART = os.environ.get("COMFY_AUTOSTART", "true").strip().lower() not in (
    "0", "false", "no", "off", "",
)
COMFY_LAUNCH_CMD = os.environ.get(
    "COMFY_LAUNCH_CMD",
    "bash /media/model-cache/github/TheAgent/app/tools/ComfyUI/launch-comfyui.sh",
)
COMFY_LOG = os.path.expanduser(
    os.environ.get("COMFY_LOG", "~/.local/state/comfyui.log")
)
COMFY_PID_PATH = os.path.expanduser(
    os.environ.get("COMFY_PID_PATH", "~/.local/state/promptforge/comfyui.pid")
)
COMFY_LEASE_PATH = os.path.expanduser(
    os.environ.get(
        "COMFY_LEASE_PATH", "~/.local/state/agent-manager/comfyui-lease.json"
    )
)

# Agent Manager's freshness rules for the lease (src/gpu-guard.js readFreshLease): refreshed within TTL, acquired within MAX.
LEASE_TTL_S = int(os.environ.get("AGENT_MANAGER_COMFY_LEASE_TTL_S", "90"))
LEASE_MAX_S = int(os.environ.get("AGENT_MANAGER_COMFY_LEASE_MAX_S", "900"))

COMFY_START_TIMEOUT_S = int(os.environ.get("COMFY_START_TIMEOUT_S", "300"))
# VRAM ComfyUI (SDXL + ControlNet) needs to be considered "fits".
COMFY_REQUIRED_VRAM_MB = int(os.environ.get("COMFY_REQUIRED_VRAM_MB", "9000"))
# ComfyUI's default CUDA device; unset -> let ComfyUI pick (usually 0).
_COMFY_CUDA_DEVICE_RAW = os.environ.get("COMFY_CUDA_DEVICE", "").strip()
COMFY_CUDA_DEVICE = int(_COMFY_CUDA_DEVICE_RAW) if _COMFY_CUDA_DEVICE_RAW else 0

# Host RAM guard for a generation: ComfyUI + the pipeline's model + VMs share one box.
# With almost no RAM available the whole machine thrashes (a generation then crawls at
# minutes per step and the dashboard goes unreachable), so refuse early instead. 0 disables.
COMFY_MIN_AVAILABLE_RAM_MB = int(os.environ.get("COMFY_MIN_AVAILABLE_RAM_MB", "2048"))
COMFY_RAM_WAIT_S = int(os.environ.get("COMFY_RAM_WAIT_S", "20"))

# Below this an /api/ps entry is a rounding artefact / tiny embed model, not a
# resident LLM -- matches gpu-guard.js OLLAMA_RESIDENT_MIN_MB.
_OLLAMA_RESIDENT_MIN_MB = 1024


class ComfyUnavailable(RuntimeError):
    """ComfyUI could not be made ready."""


# ------------------------------------------------------------------- probes

def comfy_up(timeout=2):
    """True if ComfyUI answers /system_stats."""
    try:
        with urllib.request.urlopen(
            f"{COMFY_URL}/system_stats", timeout=timeout
        ) as r:
            json.load(r)
        return True
    except Exception:
        return False


def gpu_inventory():
    """[{index, free_mb, total_mb}, ...] from nvidia-smi, or [] if unavailable.

    An empty list means "unknown" -- callers then just start ComfyUI with no
    lease and no eviction.
    """
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except Exception:
        return []
    rows = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            rows.append({
                "index": int(parts[0]),
                "free_mb": int(float(parts[1])),
                "total_mb": int(float(parts[2])),
            })
        except ValueError:
            continue
    return rows


def ollama_models_on_gpu():
    """[{name, size_vram_mb}, ...] resident in Ollama (GET /api/ps), or []."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=5) as r:
            body = json.load(r)
    except Exception as exc:
        logger.warning(
            "Ollama /api/ps poll failed: %s: %s (url=%s, timeout=5)",
            type(exc).__name__, exc, OLLAMA_URL,
        )
        return []
    models = body.get("models") if isinstance(body, dict) else None
    if not isinstance(models, list):
        return []
    out = []
    for m in models:
        try:
            vram_mb = round(int(m.get("size_vram") or 0) / (1024 * 1024))
        except (TypeError, ValueError):
            vram_mb = 0
        if vram_mb <= 0:
            continue
        out.append({"name": m.get("name") or m.get("model") or "", "size_vram_mb": vram_mb})
    return out


# ------------------------------------------------------------------- placement

@dataclass
class Placement:
    can_fit: bool
    target_gpu: object          # int device index, or None ("unknown, best effort")
    need_eviction: bool
    evict_names: list = field(default_factory=list)


def plan_comfyui_placement(
    inventory=None, ollama_models=None,
    default_gpu=COMFY_CUDA_DEVICE, need=COMFY_REQUIRED_VRAM_MB,
):
    """Decide where ComfyUI should start and whether the pipeline must yield.

    Never mutates anything -- pure function of the two measurements so it can be
    unit-tested with fakes.
    """
    inv = gpu_inventory() if inventory is None else inventory
    models = ollama_models_on_gpu() if ollama_models is None else ollama_models

    if not inv:
        # No GPU visibility -- just try to start it, don't touch the pipeline.
        return Placement(can_fit=True, target_gpu=None, need_eviction=False)

    by_index = {g["index"]: g for g in inv}
    target = by_index.get(default_gpu, inv[0])
    target_idx = target["index"]

    # 1. Target GPU already has room.
    if target["free_mb"] >= need:
        return Placement(True, target_idx, False, [])

    # 2. Some other GPU has room -- steer ComfyUI there, nothing to evict.
    for g in inv:
        if g["index"] != target_idx and g["free_mb"] >= need:
            return Placement(True, g["index"], False, [])

    # 3. Evicting Ollama's resident model(s) on the target GPU would create room.
    #    (Ollama is single-GPU in every deployment this runs on; treat all
    #    resident models as being on the target device.)
    evictable_mb = sum(m["size_vram_mb"] for m in models)
    evict_names = [m["name"] for m in models if m["name"]]
    if target["free_mb"] + evictable_mb >= need and evict_names:
        return Placement(True, target_idx, True, evict_names)

    # 4. Won't fit even after eviction (unusual on a 24GB card). Evict what we
    #    can and start best-effort; ComfyUI will surface its own OOM if it must.
    return Placement(False, target_idx, True, evict_names)


# ------------------------------------------------------------------- actions

def evict_ollama_models(names):
    """Ask Ollama to unload each model (keep_alive:0). Best-effort, concurrent."""
    from concurrent.futures import ThreadPoolExecutor

    def _evict_one(name):
        if not name:
            return
        try:
            payload = json.dumps(
                {"model": name, "keep_alive": 0, "prompt": ""}
            ).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate",
                data=payload,
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_evict_one, name) for name in names]
        for f in futures:
            try:
                f.result()
            except Exception:
                pass


def _write_pid(pid):
    try:
        os.makedirs(os.path.dirname(COMFY_PID_PATH), exist_ok=True)
        with open(COMFY_PID_PATH, "w") as f:
            f.write(str(pid))
    except OSError:
        pass


def _read_pid():
    try:
        with open(COMFY_PID_PATH) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def managed_pid():
    """The pid of a ComfyUI we started, if it's still alive; else None."""
    pid = _read_pid()
    return pid if _pid_alive(pid) else None


def spawn_comfyui(cuda_device=None):
    """Launch ComfyUI detached, log to COMFY_LOG, record the pid."""
    env = dict(os.environ)
    if cuda_device not in (None, 0):
        env["CUDA_VISIBLE_DEVICES"] = str(cuda_device)
    try:
        os.makedirs(os.path.dirname(COMFY_LOG), exist_ok=True)
    except OSError:
        pass
    logf = open(COMFY_LOG, "a")
    proc = subprocess.Popen(
        shlex.split(COMFY_LAUNCH_CMD),
        start_new_session=True,
        stdout=logf,
        stderr=subprocess.STDOUT,
        env=env,
    )
    _write_pid(proc.pid)
    return proc.pid


def stop_comfyui():
    """SIGTERM the ComfyUI we started, escalate to SIGKILL, clear the pidfile."""
    pid = _read_pid()
    if not _pid_alive(pid):
        _clear_pidfile()
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(20):
        if not _pid_alive(pid):
            break
        time.sleep(0.5)
    if _pid_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    _clear_pidfile()
    return True


def _clear_pidfile():
    try:
        os.unlink(COMFY_PID_PATH)
    except OSError:
        pass


# ------------------------------------------------------------------- lease

def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _lease_file():
    try:
        with open(COMFY_LEASE_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _iso_ts(s):
    """Epoch seconds of an ISO-8601 UTC stamp ("...Z", with or without milliseconds), or None."""
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def foreign_lease(now=None):
    """The lease when another holder (a KaggleForge eval, ...) holds it fresh by Agent Manager's rules, else None.
    The lease file has room for one holder: writing ours over it would end that holder's GPU reservation."""
    data = _lease_file()
    if not data or data.get("holder") == "promptforge":
        return None
    now = time.time() if now is None else now
    refreshed, acquired = _iso_ts(data.get("refreshedAt")), _iso_ts(data.get("acquiredAt"))
    if refreshed is None or acquired is None or now - refreshed > LEASE_TTL_S or now - acquired > LEASE_MAX_S:
        return None
    return data


def foreign_lease_message(data):
    where = ", ".join(data.get("endpoints") or []) or "every card"
    return (f"the GPU is reserved by {data.get('holder') or 'another holder'} ({data.get('owner') or '?'}, {where}); "
            "PromptForge will not take it until that reservation ends")


def refuse_if_foreign():
    data = foreign_lease()
    if data:
        raise ComfyUnavailable(foreign_lease_message(data))


def lease_endpoints(environ=None):
    """The Ollama URLs a lease written by PromptForge covers, so only the pipeline lanes on those cards yield.

    FORGE_LEASE_ENDPOINTS (comma-separated URLs) wins; the single word `all` means unscoped (every lane yields, the behaviour before
    2026-10-22). Unset: the Ollama this module evicts from (OLLAMA_URL), because that is the card ComfyUI is about to take. Extra URLs
    pause their lanes but nothing is evicted from them. Agent Manager reads the list as `endpoints` (src/gpu-guard.js leaseHeldFor)."""
    env = os.environ if environ is None else environ
    raw = (env.get("FORGE_LEASE_ENDPOINTS") or "").strip()
    if raw.lower() == "all":
        return []
    urls = [u.strip() for u in raw.split(",") if u.strip()] if raw else [OLLAMA_URL]
    return urls


def lease_scope(environ=None):
    """{"endpoints": [...]} for a lease dict, or {} when the lease is unscoped (the key is omitted, as the pipeline expects)."""
    urls = lease_endpoints(environ)
    return {"endpoints": urls} if urls else {}


def lease_acquire(job_id):
    """Write the shared lease file. Called only on the eviction path. Raises ComfyUnavailable while another holder's lease is fresh."""
    refuse_if_foreign()
    try:
        os.makedirs(os.path.dirname(COMFY_LEASE_PATH), exist_ok=True)
        now = _now_iso()
        data = {
            "holder": "promptforge",
            "acquiredAt": now,
            "refreshedAt": now,
            "jobId": job_id,
        }
        data.update(lease_scope())
        with open(COMFY_LEASE_PATH, "w") as f:
            json.dump(data, f)
        return True
    except OSError:
        return False


def lease_refresh(job_id):
    """Rewrite refreshedAt, keep the original acquiredAt. No-op if the file is
    gone (an explicit pipeline start stomped it) or another holder wrote it."""
    data = _lease_file()
    if not data or data.get("holder") != "promptforge":
        return False
    data["refreshedAt"] = _now_iso()
    if job_id is not None:
        data["jobId"] = job_id
    try:
        with open(COMFY_LEASE_PATH, "w") as f:
            json.dump(data, f)
        return True
    except OSError:
        return False


def lease_release():
    """Delete the lease, only if it is ours: another holder's lease is theirs to end."""
    if not lease_held():
        return
    try:
        os.unlink(COMFY_LEASE_PATH)
    except OSError:
        pass


def lease_held():
    """True if *we* currently hold the lease file (holder == promptforge)."""
    try:
        with open(COMFY_LEASE_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    return data.get("holder") == "promptforge"


# ------------------------------------------------------------------- orchestrator

def ensure_comfyui_ready(set_step=None, job_id=None):
    """Bring ComfyUI up if it's down, coordinating GPU use with the pipeline.

    set_step(text) is an optional progress callback (wired to the job record's
    comfy_step). Raises ComfyUnavailable if ComfyUI doesn't answer within
    COMFY_START_TIMEOUT_S. Returns a dict describing what it did:
      {started, lease_taken, target_gpu, evicted}
    """
    def step(msg):
        if set_step:
            try:
                set_step(msg)
            except Exception:
                pass

    if comfy_up():
        return {"started": False, "lease_taken": False, "target_gpu": None, "evicted": []}
    refuse_if_foreign()   # even with room to spare: the reservation holder (e.g. a vLLM eval) needs the card it reserved

    plan = plan_comfyui_placement()
    lease_taken = False
    evicted = []

    try:
        if plan.need_eviction:
            lease_taken = lease_acquire(job_id)
            step("freeing GPU…")
            evict_ollama_models(plan.evict_names)
            evicted = list(plan.evict_names)
            # Give the driver a moment to report the freed VRAM.
            deadline = time.time() + 10
            while time.time() < deadline:
                inv = {g["index"]: g for g in gpu_inventory()}
                tgt = inv.get(plan.target_gpu)
                if tgt and tgt["free_mb"] >= COMFY_REQUIRED_VRAM_MB:
                    break
                time.sleep(1)

        step("starting ComfyUI…")
        spawn_comfyui(plan.target_gpu)

        deadline = time.time() + COMFY_START_TIMEOUT_S
        while time.time() < deadline:
            if comfy_up():
                return {
                    "started": True,
                    "lease_taken": lease_taken,
                    "target_gpu": plan.target_gpu,
                    "evicted": evicted,
                }
            time.sleep(2)

        raise ComfyUnavailable(
            f"ComfyUI did not become ready within {COMFY_START_TIMEOUT_S}s -- see {COMFY_LOG}"
        )
    except ComfyUnavailable:
        if lease_taken:
            lease_release()
        raise
    except Exception as exc:
        if lease_taken:
            lease_release()
        raise ComfyUnavailable(f"failed to start ComfyUI: {exc}") from exc


# ------------------------------------------------------------------- generation claim

def mem_available_mb():
    """Host MemAvailable in MB from /proc/meminfo, or None if unreadable."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def models_to_evict(inventory, ollama_models, device=COMFY_CUDA_DEVICE, need=COMFY_REQUIRED_VRAM_MB):
    """Names of resident Ollama models to unload so a generation fits on `device`.

    ComfyUI is already running and pinned to its device, so unlike
    plan_comfyui_placement there is no other GPU to steer to. Pure function.
    Nothing to evict when GPU visibility is missing, the device already has room,
    or nothing is resident.
    """
    if not inventory:
        return []
    by_index = {g["index"]: g for g in inventory}
    target = by_index.get(device, inventory[0])
    if target["free_mb"] >= need:
        return []
    return [m["name"] for m in ollama_models if m.get("name")]


def claim_gpu(job_id, set_step=None, inventory=None, ollama_models=None,
              mem_mb=None, sleep=time.sleep, now=time.time):
    """Give a generation the GPU: hold the shared lease for the job (the pipeline
    yields its local-model ticks while it is held), evict the pipeline's resident
    model when ComfyUI would not fit next to it, then check host RAM.

    Runs for EVERY generation, including when ComfyUI was already up -- the lease
    used to be taken only on a cold start, so a generation against a running
    ComfyUI collided with the pipeline's model (2026-10-12: 3.3 GB left for
    ComfyUI, a job that crawled for minutes and a global OOM kill).

    The inputs are injectable for tests. Returns {lease_taken, evicted}; raises
    ComfyUnavailable (after releasing the lease) if host RAM stays too low.
    """
    def step(msg):
        if set_step:
            try:
                set_step(msg)
            except Exception:
                pass

    if lease_held():
        lease_taken = lease_refresh(job_id)
    else:
        lease_taken = lease_acquire(job_id)

    evicted = []
    try:
        inv = gpu_inventory() if inventory is None else inventory
        models = ollama_models_on_gpu() if ollama_models is None else ollama_models
        names = models_to_evict(inv, models)
        if names:
            step("freeing GPU…")
            evict_ollama_models(names)
            evicted = list(names)
            deadline = now() + 10
            while now() < deadline:
                cur = gpu_inventory() if inventory is None else inventory
                by_index = {g["index"]: g for g in cur}
                tgt = by_index.get(COMFY_CUDA_DEVICE, cur[0] if cur else None)
                if tgt is None or tgt["free_mb"] >= COMFY_REQUIRED_VRAM_MB:
                    break
                sleep(1)

        if COMFY_MIN_AVAILABLE_RAM_MB > 0:
            deadline = now() + COMFY_RAM_WAIT_S
            avail = mem_available_mb() if mem_mb is None else mem_mb
            while avail is not None and avail < COMFY_MIN_AVAILABLE_RAM_MB and now() < deadline:
                step(f"waiting for host RAM ({avail} MB free)…")
                sleep(2)
                avail = mem_available_mb() if mem_mb is None else mem_mb
            if avail is not None and avail < COMFY_MIN_AVAILABLE_RAM_MB:
                raise ComfyUnavailable(
                    f"host memory is too low for a generation ({avail} MB available, "
                    f"need {COMFY_MIN_AVAILABLE_RAM_MB} MB) -- the machine would thrash; "
                    "free some RAM or set COMFY_MIN_AVAILABLE_RAM_MB=0"
                )
    except Exception:
        if lease_taken:
            lease_release()
        raise
    return {"lease_taken": lease_taken, "evicted": evicted}


def ensure_gpu_for_generation(set_step=None, job_id=None):
    """Bring ComfyUI up if needed, then claim the GPU for this job. Returns
    {started, lease_taken, evicted}. With autostart off and ComfyUI down it does
    nothing (the generation then errors on the first ComfyUI call, as before)."""
    started = False
    if not comfy_up():
        if not COMFY_AUTOSTART:
            return {"started": False, "lease_taken": False, "evicted": []}
        started = ensure_comfyui_ready(set_step=set_step, job_id=job_id)["started"]
    claim = claim_gpu(job_id, set_step=set_step)
    return {"started": started, **claim}
