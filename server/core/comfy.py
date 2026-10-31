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
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import comfy_proc, gpu_client, gpu_session

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


gpu_session.set_activity_probe(_any_active_job)


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


# --- Krea 2 Turbo -----------------------------------------------------------------------------------------------------------------------
# A model named "krea2:<file>" is a diffusion model in ComfyUI's diffusion_models/ folder (not a checkpoint): it needs its own Qwen3-VL text
# encoder and VAE, and runs a distilled 8-step, cfg 1 graph with a zeroed negative. There is no ControlNet for it here (the openpose one is
# SDXL-only), so a pose skeleton is dropped (resolve_job warns).
KREA2_PREFIX = "krea2:"
KREA2_TE = os.environ.get("PROMPTFORGE_KREA2_TE", "qwen3vl_4b_fp8_scaled.safetensors")
KREA2_VAE = os.environ.get("PROMPTFORGE_KREA2_VAE", "qwen_image_vae.safetensors")


def is_krea2(model):
    return str(model or "").startswith(KREA2_PREFIX)


def plain_tags(text):
    """Tag text for a natural-language model: (word:1.1) weights and parentheses removed ("(snout:1.1), plain" -> "snout, plain")."""
    t = re.sub(r"\(([^()]*?):-?\d+(?:\.\d+)?\)", r"\1", str(text or ""))
    t = re.sub(r"[()]", "", t)
    return re.sub(r":-?\d+(?:\.\d+)?(?=\s*(?:,|$))", "", t).strip(" ,")      # and a bare "word:1.3" weight left without parentheses


# Tag-list words that mean nothing to a natural-language model, and the props a character's text holds ("(holding a whip:1.1)", "ledger at belt"):
# a prop in the character beats the shot's own action, so the shot's sentence names what is held instead.
_META_TAGS = {"1other", "1boy", "1girl", "2boys", "2girls", "anthro", "furry", "caprine", "male", "female", "solo"}
_PROP_TAG = re.compile(r"^(holding|carrying|wielding|gripping|clutching|hauling|dragging|lugging)\b|\bat (his |her |the )?belt\b", re.I)
_SPECIES = ("pig", "goat", "wolf", "fox", "rabbit", "cat", "dog", "mouse", "rat", "bear", "horse", "sheep", "cow", "deer", "owl", "crow", "lizard")


def _split_tags(text):
    out, depth, cur = [], 0, []
    for ch in str(text or ""):
        depth += ch == "("
        depth -= ch == ")" and depth > 0
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return [t for t in out if t]


def describe_character(text):
    """A character's tag text as words for Krea 2: "an upright anthropomorphic pig with a pig head, <the other tags>" (species from the tags when
    `anthro` / `furry` marks an animal person), without the tag meta words and without prop tags."""
    tags = [plain_tags(t) for t in _split_tags(text)]
    species = next((t.lower() for t in tags if t.lower() in _SPECIES), None) if any(t.lower() in ("anthro", "furry") for t in tags) else None
    keep = [t for t in tags if t and t.lower() not in _META_TAGS and t.lower() != species and not _PROP_TAG.search(t)]
    head = ["an upright anthropomorphic %s with a %s head" % (species, species)] if species else []
    return ", ".join(head + keep)


def krea2_prompt(lead, segments, style_tags, loras=None):
    """One natural-language prompt for Krea 2: the caller's sentence `lead`, then each character's description (two characters labelled left /
    right, A first, as PromptForge's regional layout does), then the style. No SDXL quality tags, no weight syntax.
    
    If `loras` is provided and any LoRA has a 'trigger', those triggers are prefixed to the prompt (comma-separated, deduplicated)."""
    parts = [str(lead).strip().rstrip(".") or ""]
    segs = [describe_character(x) for x in segments if describe_character(x)]
    if len(segs) == 2:
        parts += ["On the left: " + segs[0], "On the right: " + segs[1]]
    else:
        parts += segs
    style = plain_tags(style_tags)
    if style:
        parts.append("Style: " + style)
    
    # Prefix LoRA triggers
    if loras:
        triggers = [l.get("trigger") for l in loras if l.get("trigger")]
        if triggers:
            # Deduplicate while preserving order
            seen = set()
            unique_triggers = []
            for t in triggers:
                if t not in seen:
                    seen.add(t)
                    unique_triggers.append(t)
            
            trigger_str = ", ".join(unique_triggers)
            joined = ". ".join(p for p in parts if p)
            if not joined.startswith(trigger_str):
                parts.insert(0, trigger_str)
    
    return ". ".join(p for p in parts if p)


def validate_krea2_loras(loras, loras_dir=None):
    """Validate LoRA list for Krea 2 jobs.
    
    Args:
        loras: List of LoRA dicts with 'name' and optional 'weight'/'trigger'
        loras_dir: Directory to check for LoRA files (defaults to models/loras relative to ComfyUI)
    
    Returns:
        Cleaned list of validated LoRA dicts
    
    Raises:
        ValueError: If validation fails (weight out of bounds, too many LoRAs, etc.)
        FileNotFoundError: If a LoRA file doesn't exist
    """
    if not isinstance(loras, list):
        raise ValueError("loras must be a list")
    if len(loras) > 4:
        raise ValueError(f"Maximum 4 LoRAs allowed, got {len(loras)}")
    
    # Default to ComfyUI models/loras directory
    if loras_dir is None:
        # Try to find ComfyUI models directory
        comfy_models = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ComfyUI", "models")
        loras_dir = os.path.join(comfy_models, "loras")
    
    cleaned = []
    for i, lora in enumerate(loras):
        if not isinstance(lora, dict):
            raise ValueError(f"LoRA #{i} must be a dict")
        if "name" not in lora:
            raise ValueError(f"LoRA #{i} missing 'name'")
        if "weight" not in lora:
            raise ValueError(f"LoRA #{i} missing 'weight'")
        
        weight = lora["weight"]
        if not isinstance(weight, (int, float)) or weight < -2 or weight > 2:
            raise ValueError(f"LoRA '{lora['name']}' weight {weight} out of bounds [-2, 2]")
        
        lora_file = os.path.join(loras_dir, lora["name"])
        if not os.path.exists(lora_file):
            raise FileNotFoundError(f"LoRA file not found: {lora['name']} (checked {loras_dir})")
        
        cleaned.append({
            "name": lora["name"],
            "weight": float(weight),
            "trigger": lora.get("trigger")
        })
    return cleaned


def round_krea2_size(width, height):
    """Round width and height down to nearest multiple of 16 for Krea 2.
    
    Args:
        width: Original width
        height: Original height
    
    Returns:
        Tuple of (rounded_width, rounded_height, warning_message_or_None)
    """
    if width % 16 != 0 or height % 16 != 0:
        rounded_w = width - (width % 16)
        rounded_h = height - (height % 16)
        warning = f"Krea 2 requires dimensions to be multiples of 16: rounded {width}x{height} to {rounded_w}x{rounded_h}"
        return rounded_w, rounded_h, warning
    return width, height, None


def build_krea2_graph(job):
    """The official Comfy-Org Krea-2 Turbo text-to-image graph: UNET + Qwen3-VL CLIP (type krea2) + VAE, the positive encoded once and a
    zeroed copy of it as the negative, one KSampler (8 steps, cfg 1, euler/simple), decode, save.
    
    If `loras` is in the job, chains LoraLoader nodes between the UNET and KSampler, each taking the previous model/clip output."""
    positive = (job.get("positive") or "").strip() or ". ".join(
        t for t in (job.get("base"), job.get("region_a"), job.get("region_b")) if t)
    
    # Start with base graph
    graph = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": str(job["model"])[len(KREA2_PREFIX):], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": os.environ.get("PROMPTFORGE_KREA2_TE", KREA2_TE), "type": "krea2", "device": "default"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": os.environ.get("PROMPTFORGE_KREA2_VAE", KREA2_VAE)}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["2", 0]}},
        "7": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "10": {"class_type": "EmptyLatentImage",
               "inputs": {"width": int(job["width"]), "height": int(job["height"]), "batch_size": int(job["batch"])}},
    }
    
    # Chain LoRAs if present
    loras = job.get("loras")
    if loras:
        model_ref = ["1", 0]  # UNETLoader output
        clip_ref = ["2", 0]   # CLIPLoader output
        for i, lora in enumerate(loras):
            lora_id = str(100 + i)  # Use IDs starting at 100 to avoid conflicts
            graph[lora_id] = {
                "class_type": "LoraLoader",
                "inputs": {
                    "model": model_ref,
                    "clip": clip_ref,
                    "lora_name": lora["name"],
                    "strength_model": lora.get("weight", 1.0),
                    "strength_clip": lora.get("weight", 1.0)
                }
            }
            model_ref = [lora_id, 0]
            clip_ref = [lora_id, 1]
    else:
        model_ref = ["1", 0]
        clip_ref = ["2", 0]
    
    # Update CLIPTextEncode and ConditioningZeroOut to use the final clip reference
    graph["6"]["inputs"]["clip"] = clip_ref
    graph["7"]["inputs"]["conditioning"] = ["6", 0]
    
    # KSampler takes the final model output
    graph["11"] = {"class_type": "KSampler", "inputs": {
        "seed": int(job["seed"]), "steps": int(job["steps"]), "cfg": float(job["cfg"]),
        "sampler_name": job["sampler"], "scheduler": job["scheduler"], "denoise": 1.0,
        "model": model_ref, "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["10", 0]}}
    graph["12"] = {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}}
    graph["13"] = {"class_type": "SaveImage",
                   "inputs": {"filename_prefix": f"promptforge_{job['id'][:8]}", "images": ["12", 0]}}
    return graph


def build_repair_graph(job):
    """Image-to-image from a picture already uploaded to ComfyUI (`image_name`).

    With `mask_name` (white = repaint, grown a little) only that area is redone: an inpaint. Without it the whole
    picture is regenerated at `denoise`, the sprite acting as the reference. With `pose` the picture's OpenPose
    skeleton is extracted and fed to the openpose ControlNet, which holds the pose so denoise can go higher."""
    prefix = f"promptforge_repair_{job['id'][:8]}"
    g = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": job["model"]}},
        "50": {"class_type": "CLIPSetLastLayer", "inputs": {"stop_at_clip_layer": -2, "clip": ["1", 1]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": job["positive"], "clip": ["50", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": job["negative"], "clip": ["50", 0]}},
        "20": {"class_type": "LoadImage", "inputs": {"image": job["image_name"]}},
        "23": {"class_type": "VAEEncode", "inputs": {"pixels": ["20", 0], "vae": ["1", 2]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["1", 2]}},
        "13": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["12", 0]}},
    }
    latent = ["23", 0]
    if job.get("mask_name"):
        g["21"] = {"class_type": "LoadImageMask", "inputs": {"image": job["mask_name"], "channel": "red"}}
        g["22"] = {"class_type": "GrowMask", "inputs": {"mask": ["21", 0], "expand": int(job.get("grow", 6)),
                                                          "tapered_corners": True}}
        g["24"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["23", 0], "mask": ["22", 0]}}
        latent = ["24", 0]
    pos, neg = ["6", 0], ["7", 0]
    if job.get("pose"):
        g["30"] = {"class_type": "OpenposePreprocessor", "inputs": {
            "image": ["20", 0], "detect_hand": "enable", "detect_body": "enable", "detect_face": "enable",
            "resolution": 1024, "scale_stick_for_xinsr_cn": "disable"}}
        g["40"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": CN_MODEL}}
        g["42"] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
            "positive": pos, "negative": neg, "control_net": ["40", 0], "image": ["30", 0],
            "strength": float(job.get("cn_strength", 0.8)), "start_percent": 0.0,
            "end_percent": float(job.get("cn_end", 0.8))}}
        pos, neg = ["42", 0], ["42", 1]
    g["11"] = {"class_type": "KSampler", "inputs": {
        "seed": int(job["seed"]), "steps": int(job["steps"]), "cfg": float(job["cfg"]),
        "sampler_name": job["sampler"], "scheduler": job["scheduler"], "denoise": float(job["denoise"]),
        "model": ["1", 0], "positive": pos, "negative": neg, "latent_image": latent}}
    return g


def build_graph(job):
    """job: resolved dict (see module docstring / generate()). Returns API graph."""
    if is_krea2(job.get("model")):
        return build_krea2_graph(job)
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
           "prompt_used": job.get("prompt_used"), "warnings": list(job.get("warnings") or [])}
    with _JOBS_LOCK:
        _JOBS[jid] = rec

    threading.Thread(target=_run, args=(job, rec, gallery_dir), daemon=True).start()
    return jid


def _gpu_errors():
    """What a failed GPU claim raises: the in-process session's errors, or the broker client's when PromptForge is a broker client."""
    return (comfy_proc.ComfyUnavailable, gpu_session.SessionError, gpu_client.BrokerError)


def _take_gpu(rec, job=None):
    """Claim the GPU for a job. With GPU_BROKER_URL set the standalone broker runs the session (and PromptForge keeps it alive while the job runs),
    on the job's `card` when it names one (the broker's default card otherwise); otherwise the session runs in-process, exactly as before."""
    on_step = lambda s: _set(rec, comfy_step=s)   # noqa: E731
    if gpu_client.enabled():
        card = (job or {}).get("card")
        if card:
            gpu_client.acquire("promptforge", on_step=on_step, card=card)
        else:
            gpu_client.acquire("promptforge", on_step=on_step)
        gpu_client.job_started("promptforge")
    else:
        gpu_session.begin("promptforge", set_step=on_step)


def _gpu_job_done(took_gpu):
    """A finished job counts as GPU activity. Broker mode releases the job's hold only if the claim succeeded, so a failed claim never ends another
    job's heartbeat."""
    if gpu_client.enabled():
        if took_gpu:
            gpu_client.job_finished("promptforge")
    else:
        gpu_session.touch("promptforge")


def _gpu_reclaimed():
    return _lease_stomped or gpu_session.last_end_reason == "pipeline-reclaimed" or (gpu_client.enabled() and gpu_client.reclaimed())


def _set(rec, **kw):
    with _JOBS_LOCK:
        rec.update(kw)


def _run(job, rec, gallery_dir):
    global _lease_stomped
    gpu_held = False
    try:
        # Every generation claims the GPU (lease + evict the pipeline's model + RAM check),
        # whether or not ComfyUI had to be started: see comfy_proc.claim_gpu.
        _lease_stomped = False
        _set(rec, status="starting-comfyui", comfy_step="checking GPU…")
        try:
            # Forge family takes the whole GPU: pipeline model evicted, session lease kept fresh by
            # gpu_session's keeper (see gpu_session.py). Idle sessions end and stop ComfyUI.
            _take_gpu(rec, job)
            gpu_held = True
        except _gpu_errors() as exc:
            _set(rec, status="error", error=str(exc))
            return

        if job.get("repair_image_png"):
            job["image_name"] = upload_image(job["repair_image_png"], f"promptforge_repair_{job['id'][:8]}.png")
            if job.get("repair_mask_png"):
                job["mask_name"] = upload_image(job["repair_mask_png"],
                                                f"promptforge_repair_mask_{job['id'][:8]}.png")

        if job.get("skeleton_png"):
            name = upload_image(job["skeleton_png"], f"promptforge_pose_{job['id'][:8]}.png")
            job["skeleton_name"] = name

        graph = build_repair_graph(job) if job.get("repair_image_png") else build_graph(job)
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
        if _gpu_reclaimed():
            _set(rec, status="error", error=(
                "the agent-manager pipeline reclaimed the GPU -- stop it, or "
                "retry once it settles"))
        else:
            _set(rec, status="error", error=str(exc))
    finally:
        # The session (not the job) owns the lease now; a finished job just counts as activity.
        _gpu_job_done(gpu_held)


def get_job(jid):
    with _JOBS_LOCK:
        return dict(_JOBS.get(jid) or {})


def any_job_starting_comfyui():
    with _JOBS_LOCK:
        return any(r.get("status") == "starting-comfyui" for r in _JOBS.values())
