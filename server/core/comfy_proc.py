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

import json
import os
import shlex
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

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

COMFY_START_TIMEOUT_S = int(os.environ.get("COMFY_START_TIMEOUT_S", "120"))
# VRAM ComfyUI (SDXL + ControlNet) needs to be considered "fits".
COMFY_REQUIRED_VRAM_MB = int(os.environ.get("COMFY_REQUIRED_VRAM_MB", "9000"))
# ComfyUI's default CUDA device; unset -> let ComfyUI pick (usually 0).
_COMFY_CUDA_DEVICE_RAW = os.environ.get("COMFY_CUDA_DEVICE", "").strip()
COMFY_CUDA_DEVICE = int(_COMFY_CUDA_DEVICE_RAW) if _COMFY_CUDA_DEVICE_RAW else 0

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
    except Exception:
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
    """Ask Ollama to unload each model (keep_alive:0). Best-effort."""
    for name in names:
        if not name:
            continue
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


def lease_acquire(job_id):
    """Write the shared lease file. Called only on the eviction path."""
    try:
        os.makedirs(os.path.dirname(COMFY_LEASE_PATH), exist_ok=True)
        now = _now_iso()
        with open(COMFY_LEASE_PATH, "w") as f:
            json.dump(
                {
                    "holder": "promptforge",
                    "acquiredAt": now,
                    "refreshedAt": now,
                    "jobId": job_id,
                },
                f,
            )
        return True
    except OSError:
        return False


def lease_refresh(job_id):
    """Rewrite refreshedAt, keep the original acquiredAt. No-op if the file is
    gone (an explicit pipeline start stomped it)."""
    try:
        with open(COMFY_LEASE_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
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
