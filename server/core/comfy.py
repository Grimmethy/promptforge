"""Build a ComfyUI API-format graph from a resolved job, submit it to :8188,
poll /history, and pull the finished images.

Three graph shapes by character count:
  0 chars  -> scene-only: encode positive/negative -> KSampler
  1 char   -> same, + ControlNet if a skeleton is supplied
  2 chars  -> per-character encoders -> PromptForgeRegionalCombine -> ControlNet
              apply -> KSampler   (needs the ComfyUI-PromptForge node pack)

Requires ComfyUI running with the ComfyUI-PromptForge + ComfyUI_smZNodes +
comfyui_controlnet_aux packs and `controlnet-openpose-sdxl-1.0.safetensors`.
"""

import io
import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import comfy_proc

logger = logging.getLogger(__name__)

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
CN_MODEL = os.environ.get("PROMPTFORGE_CN_MODEL", "controlnet-openpose-sdxl-1.0.safetensors")

_JOBS = {}          # job_id -> dict
_JOBS_LOCK = threading.Lock()

# States that mean "this job still needs the GPU" -- the lease refresher keeps the
# shared lease alive while any job is in one of these.
_ACTIVE_STATES = {"starting", "starting-comfyui", "queued", "running"}
# Set when an explicit pipeline start deleted the lease out from under an active
# job; a subsequent ComfyUI call failure is then reported as a reclaim, not a raw
# connection error.
_lease_stomped = False
_refresher_started = False
_refresher_lock = threading.Lock()


def _any_active_job():
    with _JOBS_LOCK:
        return any(r.get("status") in _ACTIVE_STATES for r in _JOBS.values())


def _lease_refresher():
    """Runs only after ensure_comfyui_ready actually took a lease. Refreshes it
    every ~30s while a job is active, releases it after a grace period with none,
    and notices if an explicit pipeline start stomped the file."""
    global _lease_stomped
    idle_since = None
    try:
        while True:
            time.sleep(30)
            if _any_active_job():
                idle_since = None
                if not comfy_proc.lease_refresh(_current_job_id()):
                    # File vanished while a job is still running -> reclaimed.
                    _lease_stomped = True
                    return
            else:
                if idle_since is None:
                    idle_since = time.time()
                elif time.time() - idle_since >= 60:
                    comfy_proc.lease_release()
                    return
    finally:
        _reset_refresher_flag()


def _current_job_id():
    with _JOBS_LOCK:
        for r in _JOBS.values():
            if r.get("status") in _ACTIVE_STATES:
                return r.get("id")
    return None


def _start_lease_refresher():
    global _refresher_started
    with _refresher_lock:
        if _refresher_started:
            return
        _refresher_started = True
    threading.Thread(target=_lease_refresher, daemon=True).start()


def _reset_refresher_flag():
    global _refresher_started
    with _refresher_lock:
        _refresher_started = False


# --------------------------------------------------------------- comfy http

def _post(path, payload):
    req = urllib.request.Request(
        f"{COMFY_URL}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _get(path, timeout=15):
    with urllib.request.urlopen(f"{COMFY_URL}{path}", timeout=timeout) as r:
        return json.load(r)


def upload_image(png_bytes, name):
    """POST /upload/image (multipart) -> the name ComfyUI stored it under."""
    boundary = "----promptforge" + uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(f'Content-Disposition: form-data; name="image"; filename="{name}"\r\n'.encode())
    body.write(b"Content-Type: image/png\r\n\r\n")
    body.write(png_bytes)
    body.write(f"\r\n--{boundary}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n')
    body.write(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"{COMFY_URL}/upload/image", data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    sub = d.get("subfolder", "")
    return f"{sub}/{d['name']}" if sub else d["name"]


def fetch_view(filename, subfolder="", ftype="output"):
    q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": ftype})
    with urllib.request.urlopen(f"{COMFY_URL}/view?{q}", timeout=30) as r:
        return r.read()


# --------------------------------------------------------------- graph build

def _smz(text, clip_ref, w, h):
    return {"class_type": "smZ CLIPTextEncode", "inputs": {
        "text": text, "clip": clip_ref,
        "parser": "A1111", "mean_normalization": False, "multi_conditioning": False,
        "use_old_emphasis_implementation": False, "with_SDXL": False, "ascore": 6.0,
        "width": w, "height": h, "crop_w": 0, "crop_h": 0,
        "target_width": w, "target_height": h, "text_g": "", "text_l": ""}}


def build_graph(job):
    """job: resolved dict (see module docstring / generate()). Returns API graph."""
    w, h = int(job["width"]), int(job["height"])
    seed = int(job["seed"])
    n = job["n_chars"]
    prefix = f"promptforge_{job['id'][:8]}"

    g = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": job["model"]}},
        "50": {"class_type": "CLIPSetLastLayer",
               "inputs": {"stop_at_clip_layer": -2, "clip": ["1", 1]}},
        "10": {"class_type": "EmptyLatentImage",
               "inputs": {"width": w, "height": h, "batch_size": int(job["batch"])}},
        "12": {"class_type": "VAEDecode",
               "inputs": {"samples": ["11", 0], "vae": ["1", 2]}},
        "13": {"class_type": "SaveImage",
               "inputs": {"filename_prefix": prefix, "images": ["12", 0]}},
    }
    ks = {"class_type": "KSampler", "inputs": {
        "seed": seed, "steps": int(job["steps"]), "cfg": float(job["cfg"]),
        "sampler_name": job["sampler"], "scheduler": job["scheduler"], "denoise": 1.0,
        "model": ["1", 0], "latent_image": ["10", 0]}}

    if n == 2:
        g["6"] = _smz(job["region_a"], ["50", 0], w, h)
        g["7"] = _smz(job["region_b"], ["50", 0], w, h)
        g["8"] = _smz(job["base"], ["50", 0], w, h)
        g["9"] = _smz(job["negative"], ["50", 0], w, h)
        g["30"] = {"class_type": "PromptForgeRegionalCombine", "inputs": {
            "region_a": ["6", 0], "region_b": ["7", 0], "base": ["8", 0],
            "layout": job.get("layout", "side by side - A left"),
            "region_strength": 1.05, "feather": 24, "base_strength": 0.45,
            "region_c_bounds": "unused"}}
        pos_ref, neg_ref = ["30", 0], ["9", 0]
    else:
        g["6"] = _smz(job["positive"], ["50", 0], w, h)
        g["7"] = _smz(job["negative"], ["50", 0], w, h)
        pos_ref, neg_ref = ["6", 0], ["7", 0]

    if job.get("skeleton_name"):
        g["40"] = {"class_type": "ControlNetLoader",
                   "inputs": {"control_net_name": CN_MODEL}}
        g["41"] = {"class_type": "LoadImage",
                   "inputs": {"image": job["skeleton_name"]}}
        g["42"] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
            "positive": pos_ref, "negative": neg_ref,
            "control_net": ["40", 0], "image": ["41", 0],
            "strength": float(job.get("cn_strength", 0.9)),
            "start_percent": 0.0, "end_percent": float(job.get("cn_end", 0.8))}}
        ks["inputs"]["positive"] = ["42", 0]
        ks["inputs"]["negative"] = ["42", 1]
    else:
        ks["inputs"]["positive"] = pos_ref
        ks["inputs"]["negative"] = neg_ref

    g["11"] = ks
    return g


# --------------------------------------------------------------- job runner

def start(job, gallery_dir):
    """job must already be resolved: positive/region_*/base, negative, n_chars,
    model, cfg, steps, sampler, scheduler, seed(-1=random), width, height, batch,
    skeleton_png(bytes|None), layout, cn_strength, cn_end, spec (echo).
    Returns job_id. Runs the ComfyUI round-trip on a background thread."""
    jid = uuid.uuid4().hex
    job["id"] = jid
    if int(job.get("seed", -1)) < 0:
        job["seed"] = random.randint(0, 2**31 - 1)

    rec = {"id": jid, "status": "starting", "images": [], "error": None,
           "seed": job["seed"], "spec": job.get("spec"),
           "prompt_used": job.get("prompt_used")}
    with _JOBS_LOCK:
        _JOBS[jid] = rec

    threading.Thread(target=_run, args=(job, rec, gallery_dir), daemon=True).start()
    return jid


def _set(rec, **kw):
    with _JOBS_LOCK:
        rec.update(kw)


def _run(job, rec, gallery_dir):
    global _lease_stomped
    try:
        if comfy_proc.COMFY_AUTOSTART and not comfy_proc.comfy_up():
            _lease_stomped = False
            _set(rec, status="starting-comfyui", comfy_step="checking GPU…")
            try:
                result = comfy_proc.ensure_comfyui_ready(
                    set_step=lambda s: _set(rec, comfy_step=s),
                    job_id=job["id"],
                )
            except comfy_proc.ComfyUnavailable as exc:
                _set(rec, status="error", error=str(exc))
                comfy_proc.lease_release()
                return
            if result.get("lease_taken"):
                _start_lease_refresher()

        if job.get("skeleton_png"):
            name = upload_image(job["skeleton_png"], f"promptforge_pose_{job['id'][:8]}.png")
            job["skeleton_name"] = name

        graph = build_graph(job)
        client_id = uuid.uuid4().hex
        _set(rec, status="queued")
        resp = _post("/prompt", {"prompt": graph, "client_id": client_id})
        pid = resp["prompt_id"]
        _set(rec, comfy_prompt_id=pid)

        for _ in range(600):  # ~20 min ceiling
            time.sleep(2)
            hist = _get(f"/history/{pid}")
            if pid not in hist:
                _set(rec, status="running")
                continue
            entry = hist[pid]
            st = entry.get("status", {})
            if st.get("status_str") == "error":
                msg = "generation error"
                for m in st.get("messages", []):
                    if m[0] == "execution_error":
                        msg = m[1].get("exception_message", msg)
                _set(rec, status="error", error=msg)
                return
            outs = entry.get("outputs", {})
            saved = []
            subdir = time.strftime("%Y-%m")
            outdir = os.path.join(gallery_dir, subdir)
            os.makedirs(outdir, exist_ok=True)
            for node_out in outs.values():
                for i, im in enumerate(node_out.get("images", [])):
                    data = fetch_view(im["filename"], im.get("subfolder", ""),
                                      im.get("type", "output"))
                    fn = f"{job['id'][:8]}_{i}.png"
                    with open(os.path.join(outdir, fn), "wb") as fh:
                        fh.write(data)
                    rel = f"{subdir}/{fn}"
                    saved.append(rel)
                    try:
                        from . import thumbs
                        thumbs.ensure_thumb(gallery_dir, rel)
                    except Exception:  # noqa: BLE001
                        logger.warning("job %s: thumbnail generation failed for %s",
                                      job["id"], rel, exc_info=True)
            if saved:
                sidecar = {"id": job["id"], "seed": job["seed"],
                           "model": job["model"], "spec": job.get("spec"),
                           "prompt_used": job.get("prompt_used"),
                           "nsfw": bool(job.get("nsfw")),
                           "images": saved, "ts": time.time()}
                with open(os.path.join(outdir, f"{job['id'][:8]}.json"), "w") as fh:
                    json.dump(sidecar, fh, indent=1)
                _set(rec, status="done", images=saved)
                return
        _set(rec, status="error", error="timed out waiting for ComfyUI")
    except urllib.error.HTTPError as exc:
        _set(rec, status="error", error=f"{exc.code}: {exc.read().decode()[:400]}")
    except Exception as exc:  # noqa: BLE001
        if _lease_stomped:
            _set(rec, status="error", error=(
                "the agent-manager pipeline reclaimed the GPU -- stop it, or "
                "retry once it settles"))
        else:
            _set(rec, status="error", error=str(exc))
    finally:
        # A job that finished before the refresher's next tick still needs the
        # lease dropped promptly if nothing else is active.
        if not _any_active_job() and comfy_proc.lease_held():
            comfy_proc.lease_release()


def get_job(jid):
    with _JOBS_LOCK:
        return dict(_JOBS.get(jid) or {})


def any_job_starting_comfyui():
    with _JOBS_LOCK:
        return any(r.get("status") == "starting-comfyui" for r in _JOBS.values())
