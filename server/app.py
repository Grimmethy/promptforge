"""PromptForge Studio — Flask backend.

Serves web/ and a /api/* surface over the ported PromptForge core. Generation is
delegated to ComfyUI (:8188) — see core/comfy.py (added next). Bind 127.0.0.1.
"""

import json
import logging
import os
import queue
import re
import urllib.error
import urllib.request

from flask import Flask, Response, jsonify, request, send_from_directory

from core import (assemble, cf_characters, comfy, comfy_proc, data, events, gpu_client, gpu_session, pose_ops, randomizer, rig,
                  secondbrain, themes, thumbs)

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(os.path.dirname(_HERE), "web")
# PROMPTFORGE_GALLERY_DIR lets a checkout/worktree serve the one real gallery: the images are gitignored,
# so a second worktree (the agent-manager-launched promptforge-manual) otherwise starts with an empty grid.
GALLERY_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("PROMPTFORGE_GALLERY_DIR") or os.path.join(os.path.dirname(_HERE), "gallery")))
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")

app = Flask(__name__, static_folder=None)


@app.after_request
def _no_frame_block(resp):
    # embedded as an <iframe> in the agent-manager dashboard
    resp.headers.pop("X-Frame-Options", None)
    # gallery images/thumbs are uuid-addressed -> cache them hard
    if resp.status_code == 200 and (request.path.startswith("/api/image/")
                                    or request.path.startswith("/api/thumb/")):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


# --------------------------------------------------------------------- static

@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    if os.path.isfile(os.path.join(WEB_DIR, path)):
        return send_from_directory(WEB_DIR, path)
    return send_from_directory(WEB_DIR, "index.html")


# ----------------------------------------------------------------- comfy probe

def _comfy_get(path, timeout=4):
    with urllib.request.urlopen(f"{COMFY_URL}{path}", timeout=timeout) as r:
        return json.load(r)


def list_checkpoints():
    try:
        oi = _comfy_get("/object_info/CheckpointLoaderSimple")
        opts = oi["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        return list(opts)
    except Exception as exc:
        logger.warning("list_checkpoints: ComfyUI checkpoint query failed: %s", exc)
        return []


def list_krea2_models():
    """Krea 2 diffusion models ComfyUI has in diffusion_models/, as "krea2:<file>" (comfy.KREA2_PREFIX): they are not checkpoints."""
    try:
        oi = _comfy_get("/object_info/UNETLoader")
        opts = oi["UNETLoader"]["input"]["required"]["unet_name"][0]
        return sorted(comfy.KREA2_PREFIX + n for n in opts if str(n).lower().startswith("krea2_"))
    except Exception as exc:
        logger.warning("list_krea2_models: ComfyUI UNETLoader query failed: %s", exc)
        return []


def list_models():
    """Every model a job can name: the SDXL checkpoints, then the Krea 2 diffusion models."""
    return list_checkpoints() + list_krea2_models()


def _comfy_log_tail(n=40):
    try:
        with open(comfy_proc.COMFY_LOG, "rb") as f:
            return b"".join(f.readlines()[-n:]).decode("utf-8", "replace")
    except OSError:
        return ""


def _comfy_extra_status():
    pid = comfy_proc.managed_pid()
    return {
        "managed": pid is not None,
        "pid": pid,
        "autostart": comfy_proc.COMFY_AUTOSTART,
        "autostarting": comfy.any_job_starting_comfyui(),
        "lease_held": comfy_proc.lease_held(),
    }


@app.route("/api/comfy/status")
def comfy_status():
    try:
        st = _comfy_get("/system_stats", timeout=3)
        dev = (st.get("devices") or [{}])[0]
        return jsonify({
            "up": True,
            "vram_free": dev.get("vram_free"),
            "vram_total": dev.get("vram_total"),
            "checkpoints": list_models(),
            **_comfy_extra_status(),
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "up": False, "error": str(exc), "checkpoints": [],
            "log_tail": _comfy_log_tail(),
            **_comfy_extra_status(),
        })


# ------------------------------------------------------------ Forge GPU session (family exclusion)
# One HTTP surface for every Forge component (VideoForge, scripts, ...) so they need no shared code.
# Protocol: agent-manager-gpu-broker Docs/GPU_FAMILY_PROTOCOL.md (PromptForge Docs/GPU_FAMILY_PROTOCOL.md is a pointer). acquire = pipeline's model evicted + ComfyUI up; heartbeat while
# working; release = lease dropped and ComfyUI stopped; yield = the pipeline asking for the card back.

def _gpu_forward(method, path):
    """When PromptForge is a broker client (GPU_BROKER_URL set) its /api/gpu routes proxy to the broker: one session, never two."""
    body = request.get_json(silent=True) if method == "POST" else None
    status, payload = gpu_client.forward(method, path, body)
    return jsonify(payload), status


@app.route("/api/gpu/status")
def gpu_status():
    if gpu_client.enabled():
        return _gpu_forward("GET", "/api/gpu/status")
    return jsonify(gpu_session.status())


@app.route("/api/gpu/cards")
def gpu_cards():
    """The GPU cards the broker knows and who holds each, for a card picker. Only a broker knows cards: in-process PromptForge has the one local GPU."""
    if gpu_client.enabled():
        return _gpu_forward("GET", "/api/gpu/cards")
    return jsonify({"cards": [], "broker": False})


@app.route("/api/gpu/acquire", methods=["POST"])
def gpu_acquire():
    if gpu_client.enabled():
        return _gpu_forward("POST", "/api/gpu/acquire")
    owner = str((request.get_json(silent=True) or {}).get("owner") or "").strip()[:40]
    if not owner:
        return jsonify({"error": "owner is required"}), 400
    try:
        return jsonify({**gpu_session.begin(owner), "status": gpu_session.status()})
    except (comfy_proc.ComfyUnavailable, gpu_session.SessionError) as exc:
        return jsonify({"error": str(exc)}), 503


@app.route("/api/gpu/heartbeat", methods=["POST"])
def gpu_heartbeat():
    if gpu_client.enabled():
        return _gpu_forward("POST", "/api/gpu/heartbeat")
    owner = str((request.get_json(silent=True) or {}).get("owner") or "").strip()[:40]
    if not gpu_session.touch(owner or None):
        return jsonify({"error": "no active session (the pipeline may have reclaimed the GPU)"}), 409
    return jsonify({"ok": True})


@app.route("/api/gpu/release", methods=["POST"])
def gpu_release():
    if gpu_client.enabled():
        return _gpu_forward("POST", "/api/gpu/release")
    return jsonify({"ended": gpu_session.end("released", stop_comfy=True), **{"comfy_up": comfy_proc.comfy_up()}})


@app.route("/api/gpu/yield", methods=["POST"])
def gpu_yield():
    if gpu_client.enabled():
        return _gpu_forward("POST", "/api/gpu/yield")
    return jsonify(gpu_session.yield_to_pipeline())


@app.route("/api/comfy/start", methods=["POST"])
def comfy_start():
    try:
        result = comfy_proc.ensure_comfyui_ready()
        return jsonify({"started": bool(result.get("started")), "up": comfy_proc.comfy_up(),
                        **_comfy_extra_status()})
    except comfy_proc.ComfyUnavailable as exc:
        return jsonify({"started": False, "up": False, "error": str(exc),
                        "log_tail": _comfy_log_tail()}), 502


@app.route("/api/comfy/stop", methods=["POST"])
def comfy_stop():
    stopped = comfy_proc.stop_comfyui()
    comfy_proc.lease_release()
    return jsonify({"stopped": bool(stopped), "up": comfy_proc.comfy_up(),
                    **_comfy_extra_status()})


# -------------------------------------------------------------------- presets

@app.route("/api/presets")
def presets():
    preset_chars = []
    for name in data.list_characters():
        doc = data.get_character(name)
        preset_chars.append({
            "source": "preset", "name": name,
            "default_outfit": doc.get("default_outfit"),
            # CharacterForge condition ladder (id + label), in worst-last order; [] if none
            "conditions": [{"id": r["id"], "name": r["name"]} for r in doc.get("conditions") or []],
        })
    # With CharacterForge present it is the single character source (Second Brain notes and
    # the local presets were imported into it), so the vault list would only duplicate them.
    cf = cf_characters.configured()
    sb_chars = [] if cf else [{"source": "secondbrain", "name": n}
                              for n in secondbrain.list_characters()]

    cn = []
    for p in data.list_cn_positions():
        doc = data.get_cn_position(p)
        cn.append({"name": p, "people": doc.get("people", 1),
                   "tags": doc.get("tags", "")})

    return jsonify({
        "nsfw": data.nsfw_enabled(),
        "characters": {"preset": preset_chars, "secondbrain": sb_chars},
        "character_source": "characterforge" if cf else "local",
        "styles": data.list_entries("styles"),
        "backgrounds": data.list_entries("backgrounds"),
        "positions": data.list_entries("positions"),
        "poses": data.list_entries("poses"),
        "supporting": data.list_entries("supporting"),
        "expressions": data.list_entries("expressions"),
        "outfits": data.load_outfit_tree(),
        "colors": [data.get_entry("colors", k)[0] or k
                   for k in data.list_entries("colors")],
        "themes": [{"name": n, **themes.get_theme(n)} for n in themes.list_themes()],
        "cn_positions": cn,
        "view_tags": data.load_view_tags(),
        "randomizer": {
            "species": randomizer.species_choices(),
            "outfit_styles": randomizer.style_choices(),
        },
        "checkpoints": list_models(),
        "secondbrain_dir": secondbrain.SB_DIR or None,
    })


@app.route("/api/nsfw", methods=["GET", "POST"])
def nsfw_toggle():
    """Process-global NSFW toggle. GET reads it; POST {enabled: bool} sets it.
    While on, data.py merges the gitignored data/nsfw/ overlay into every read."""
    if request.method == "POST":
        b = request.get_json(force=True, silent=True) or {}
        data.set_nsfw(bool(b.get("enabled")))
    return jsonify({"enabled": data.nsfw_enabled()})


@app.route("/api/character/<source>/<path:name>")
def character(source, name):
    if source == "secondbrain":
        c = secondbrain.get_character(name)
        return (jsonify(c), 200) if c else (jsonify({"error": "not found"}), 404)
    if request.args.get("raw"):
        try:
            return jsonify({"name": name, "yaml": data.raw_character(name)})
        except FileNotFoundError:
            return jsonify({"error": "not found"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    doc = data.get_character(name)
    return (jsonify(doc), 200) if doc else (jsonify({"error": "not found"}), 404)


@app.route("/api/theme/<path:name>")
def theme(name):
    return jsonify(themes.resolve(name))


# ----------------------------------------------------------------------- pose

@app.route("/api/pose/<path:position>")
def pose_frame(position):
    r, tags = data.cn_canonical_rig(position)
    if r is None:
        return jsonify({"error": "no skeleton for that position"}), 404
    key = "rigs" if isinstance(r, list) else "rig"
    return jsonify({key: r, "tags": tags})


def _azel(b):
    return float(b.get("yaw", 0.0) or 0.0) * 3.141592653589793, \
        float(b.get("pitch", 0.0) or 0.0) * 1.5707963267948966


@app.route("/api/pose/render", methods=["POST"])
def pose_render():
    b = request.get_json(force=True, silent=True) or {}
    w = int(b.get("w", 1152))
    h = int(b.get("h", 896))
    az, el = _azel(b)
    frame = b.get("keypoints")
    if frame is None and (b.get("rig") or b.get("rigs")):
        frame = rig.project_frame(b.get("rigs") or b.get("rig"), az, el, w, h)
    if frame is None and b.get("position"):
        r, _ = data.cn_canonical_rig(b["position"])
        if r is None:
            return jsonify({"error": "unknown position"}), 404
        frame = rig.project_frame(r, az, el, w, h)
    if frame is None:
        return jsonify({"error": "keypoints, rig or position required"}), 400
    frame = pose_ops.transform(
        frame,
        mirror_h=bool(b.get("mirror")), scale=float(b.get("scale", 1.0)),
        offset_x=float(b.get("offset_x", 0.0)), offset_y=float(b.get("offset_y", 0.0)),
        canvas_w=w, canvas_h=h,
    )
    png = pose_ops.render_png(frame, w, h,
                              draw_hands=bool(b.get("draw_hands")),
                              draw_face=bool(b.get("draw_face")))
    return Response(png, mimetype="image/png")


@app.route("/api/save-pose", methods=["POST"])
def save_pose():
    b = request.get_json(force=True, silent=True) or {}
    try:
        path = data.save_cn_pose(
            b["position"], rig=b.get("rig"), rigs=b.get("rigs"),
            position_tags=b.get("position_tags", ""), people=int(b.get("people", 1)),
            update_yaml=bool(b.get("update_yaml", True)),
            overwrite=bool(b.get("overwrite", False)))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400
    return jsonify({"saved": path})


@app.route("/api/save-character", methods=["POST"])
def save_char():
    b = request.get_json(force=True, silent=True) or {}
    try:
        path = data.save_character(b["filename"], b["spec_yaml"])
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400
    return jsonify({"saved": path})


@app.route("/api/character-template")
def character_template():
    path = os.path.join(data.CHAR_DIR, "_TEMPLATE.yaml")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return jsonify({"yaml": fh.read()})
    except OSError as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 404


@app.route("/api/entry", methods=["POST"])
def entry_op():
    """Add / update / delete one entry in a data YAML from the UI's inline boxes.
    `action`: "add" (default) | "update" | "delete" | "get" (read current value)."""
    b = request.get_json(force=True, silent=True) or {}
    action = b.get("action", "add")
    try:
        if action == "get":
            tgt = b["target"]
            v = data.get_entry(tgt, b["key"])[0] if tgt in data.CATEGORIES else ""
            return jsonify({"value": v.strip()})
        tgt, path, key = b["target"], b.get("path") or [], b["key"]
        if action == "update":
            saved = data.update_entry(tgt, path, key,
                                      value=b.get("value"), fields=b.get("fields"))
        elif action == "delete":
            saved = data.delete_entry(tgt, path, key)
        else:
            saved = data.add_entry(tgt, path, key,
                                   value=b.get("value"), fields=b.get("fields"))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400
    return jsonify({"saved": saved})


@app.route("/api/randomize-character", methods=["POST"])
def randomize_character():
    b = request.get_json(force=True, silent=True) or {}
    seg, spec = randomizer.roll(
        int(b.get("seed", 0)),
        b.get("species", "random"), b.get("outfit_style", "random"),
        bool(b.get("vary_body", True)), bool(b.get("vary_hair", True)),
        bool(b.get("vary_eyes", True)), bool(b.get("vary_features", True)),
        bool(b.get("vary_accessories", True)))
    return jsonify({"segment": seg, "spec": spec})


# -------------------------------------------------------------- prompt preview

@app.route("/api/assemble", methods=["POST"])
def api_assemble():
    spec = request.get_json(force=True, silent=True) or {}
    return jsonify(assemble.assemble(spec))


# --------------------------------------------------------------- generation

_DEFAULTS = dict(cfg=6.0, steps=30, sampler="dpmpp_2m_sde", scheduler="karras",
                 seed=-1, width=1152, height=896, batch=1,
                 cn_strength=0.9, cn_end=0.8, layout="side by side - A left")


_GAZE_AT_CAMERA = {
    "looking at viewer", "looking at camera", "looking at another", "eye contact",
    "facing viewer", "facing the viewer", "front view", "front view, facing viewer",
    "looking back", "looking back at viewer",
}


def _face_away(text):
    """Drop gaze-toward-camera tokens — the 3/4-to-profile-back band."""
    return ", ".join(t.strip() for t in str(text).split(",")
                     if t.strip() and t.strip().lower() not in _GAZE_AT_CAMERA)


# --- full back view: face + front-of-body tokens all contradict the pose ------
_FACE_WORDS = {
    "eye", "eyes", "eyebrow", "eyebrows", "eyelash", "eyelashes", "pupil", "pupils",
    "iris", "gaze", "nose", "lips", "lip", "mouth", "teeth", "tooth", "fang", "fangs",
    "tongue", "snaggletooth", "blush", "blushing", "cheek", "cheeks", "smile",
    "smiling", "smirk", "grin", "grinning", "frown", "pout", "scowl", "smug",
    "expression", "wink", "ahegao", "drool", "saliva", "makeup", "lipstick",
    "eyeshadow", "facial", "freckle", "freckles", "eyepatch", "eyewear", "glasses",
}
_FRONT_BODY_WORDS = {
    "breast", "breasts", "cleavage", "nipple", "nipples", "areola", "areolae",
    "underboob", "sideboob", "busty", "bosom", "boob", "boobs", "navel", "belly",
}
_FACE_PHRASES = {
    "looking at viewer", "looking at camera", "looking at another", "eye contact",
    "open mouth", "closed eyes", "half-closed eyes", "parted lips", "facing viewer",
    "facing the viewer", "front view", "gentle expression", "soft features", "face",
    "flat chest", "small breasts", "medium breasts", "large breasts", "huge breasts",
    "bare breasts", "covered nipples",
}
_LOOK_BACK = ("looking back", "over the shoulder", "over-the-shoulder",
              "head turned", "turning head", "looking over")
_WORD_RE = re.compile(r"[a-z]+")


def _rear_strip(text):
    """Full back view: drop every face and front-of-torso token so 'back view'
    isn't outvoted. Skipped entirely if the subject is turning to face the
    viewer (looking back over the shoulder)."""
    low = text.lower()
    if any(p in low for p in _LOOK_BACK):
        return text
    out = []
    for tok in str(text).split(","):
        t = tok.strip()
        if not t:
            continue
        tl = t.lower()
        if tl in _FACE_PHRASES:
            continue
        words = set(_WORD_RE.findall(tl))
        if words & _FACE_WORDS or words & _FRONT_BODY_WORDS:
            continue
        out.append(t)
    return ", ".join(out)


def _frame_has_joints(frame):
    """True if any person in the frame has at least a few confident keypoints."""
    for person in frame.get("people", []):
        flat = person.get("pose_keypoints_2d") or []
        if sum(1 for i in range(2, len(flat), 3) if flat[i] > 0) >= 4:
            return True
    return False


def _cat_tags(category, value):
    """A preset key -> its tag string; anything else -> passed through."""
    v = (value or "").strip()
    if not v or v == "none":
        return ""
    if v in data.list_entries(category):
        return data.get_entry(category, v)[0]
    return v


def resolve_job(spec):
    j = dict(_DEFAULTS)
    for k in _DEFAULTS:
        if spec.get(k) is not None:
            j[k] = spec[k]
    j["model"] = spec.get("model") or (list_checkpoints() or ["novaFurryXL_ilV140.safetensors"])[0]

    th = themes.resolve(spec.get("theme"))
    style_tags = _cat_tags("styles", spec.get("style")) or th["style"]
    setting_tags = _cat_tags("backgrounds", spec.get("setting")) or th["setting_bias"]
    scene_tags = data.normalize_kw(setting_tags, style_tags, th["palette"], th["lighting"])

    chars = [c for c in (spec.get("characters") or []) if c][:2]
    n = len(chars)
    j["n_chars"] = n

    nsfw = False
    char_segs, char_negs = [], []
    for c in chars:
        if data.outfit_in_overlay(c.get("outfit") or {}) or data.entry_in_overlay(
                "expressions", c.get("expression") or ""):
            nsfw = True
        if c.get("source") == "secondbrain" and not c.get("text"):
            sb = secondbrain.get_character(c.get("name", ""))
            c = {**c, "text": sb.get("base", "")}
        seg, neg = assemble.character_segment(c)
        char_segs.append(seg)
        if neg:
            char_negs.append(neg)
    # per-character injected text, in card order — surfaced in /api/preview so
    # each character card can show exactly what it contributes to the prompt
    j["char_segments"] = [{"segment": s} for s in char_segs]

    # pose / skeleton
    #
    # Two independent things come out of here:
    #   - `frame`: COCO-18 keypoints that become the ControlNet skeleton PNG —
    #     this alone drives the pose.
    #   - `pose_tags`: ONLY orbit-camera facing words (from yaw/pitch). A library
    #     pose's own `tags` (e.g. "standing, arms at sides") are deliberately NOT
    #     put in the prompt — they used to fight an edited skeleton.
    # The editor sends its live 3-D rig projection as `skeleton.people` (already
    # oriented — do not re-project it). An API caller may instead pass only
    # `pose_position` (+ yaw/pitch); then we project the stored rig here.
    pose_tags = ""
    frame = None
    sk = spec.get("skeleton") or {}
    pos_name = spec.get("pose_position")
    yaw = float(spec.get("yaw", 0.0) or 0.0)
    pitch = float(spec.get("pitch", 0.0) or 0.0)
    az, el = yaw * 3.141592653589793, pitch * 1.15   # inverse of editor exportYawPitch
    if yaw or pitch:
        pose_tags = data.orientation_tags(yaw, pitch)
    pk = (spec.get("pose_keywords") or "").strip()
    if pk:
        pose_tags = data.normalize_kw(pose_tags, pk)
    if pos_name and data.cn_position_in_overlay(pos_name):
        nsfw = True
    if pos_name and not sk.get("people"):
        # API fallback: a pose name but no edited skeleton -> project the stored rig.
        _rig, _ = data.cn_canonical_rig(pos_name)
        if _rig is not None:
            frame = rig.project_frame(_rig, az, el, j["width"], j["height"])
    if sk.get("people"):
        frame = {"people": sk["people"],
                 "canvas_width": j["width"], "canvas_height": j["height"]}
    # skip ControlNet if the projected skeleton collapsed to nothing usable
    if frame is not None and _frame_has_joints(frame):
        frame = pose_ops.transform(frame, canvas_w=j["width"], canvas_h=j["height"])
        j["skeleton_png"] = pose_ops.render_png(frame, j["width"], j["height"])
    j["pose_tags"] = pose_tags
    j["scene_tags"] = scene_tags

    # A rear camera contradicts the character's face/front-of-body tokens.
    #  - 3/4 .. near-profile-back (|yaw| > 0.55): just drop gaze-at-camera.
    #  - full back view (|yaw| >= 0.8): strip every face + breast/front token so
    #    "back view" isn't outvoted; skipped if the subject is looking back.
    rear_neg = ""
    ay = abs(yaw)
    if ay > 0.55:
        strip = _rear_strip if ay >= 0.8 else _face_away
        char_segs = [strip(s) for s in char_segs]
        pose_tags = data.normalize_kw(strip(pose_tags),
                                      "(facing away:1.2), back turned to viewer")
        j["pose_tags"] = pose_tags
        j["char_segments"] = [{"segment": s} for s in char_segs]
        if ay >= 0.8 and not any(p in " ".join(char_segs).lower()
                                 for p in _LOOK_BACK):
            rear_neg = ("looking at viewer, eye contact, facing viewer, front view, "
                        "face focus, portrait")

    j["negative"] = data.normalize_kw(
        (spec.get("negative") or "").strip(), rear_neg, *char_negs, th["negative_add"],
        assemble.DEFAULT_NEGATIVE)

    if n == 2:
        j["region_a"] = char_segs[0]
        j["region_b"] = char_segs[1]
        j["base"] = data.normalize_kw(assemble.DEFAULT_QUALITY, scene_tags, pose_tags)
        j["prompt_used"] = {"region_a": j["region_a"], "region_b": j["region_b"],
                            "base": j["base"], "negative": j["negative"]}
    else:
        built = assemble.assemble({
            "characters": chars, "scene_tags": scene_tags, "pose_tags": pose_tags,
            "char_segments": char_segs})
        j["positive"] = built["positive"]
        j["prompt_used"] = {"positive": j["positive"], "negative": j["negative"]}

    if comfy.is_krea2(j["model"]):
        # Krea 2 Turbo is distilled: 8 steps, cfg 1, euler/simple. The SDXL defaults would ruin it, so an unspecified value gets the Krea
        # one and an explicit one is honoured. There is no ControlNet for it: a pose skeleton is dropped, loudly.
        for k, v in (("steps", 8), ("cfg", 1.0), ("sampler", "euler"), ("scheduler", "simple")):
            if spec.get(k) is None:
                j[k] = v
        
        # Validate and handle LoRAs for Krea 2
        if "loras" in spec:
            try:
                j["loras"] = comfy.validate_krea2_loras(spec["loras"])
            except (ValueError, FileNotFoundError) as e:
                raise ValueError(f"Invalid LoRA configuration: {e}")
        
        # Round dimensions to multiples of 16 for Krea 2
        rounded_w, rounded_h, size_warning = comfy.round_krea2_size(j["width"], j["height"])
        if size_warning:
            j["warnings"] = j.get("warnings") or []
            j["warnings"].append(size_warning)
            j["width"] = rounded_w
            j["height"] = rounded_h
        
        if j.pop("skeleton_png", None):
            j["warnings"] = j.get("warnings") or []
            j["warnings"].append("pose skeleton ignored: Krea 2 has no ControlNet in PromptForge yet")
            # log it too: the warning rides on the job record / API response, but a caller expecting
            # a ControlNet pose would otherwise have no trace that the skeleton never reached ComfyUI
            logger.warning("job model %s: dropping pose skeleton (Krea 2 has no ControlNet in PromptForge yet)", j["model"])
        text = (spec.get("prompt_text") or "").strip()
        lead = (spec.get("prompt_lead") or "").strip()
        if lead and not text:
            # a sentence from the caller (a StoryForge shot) + the characters' descriptions + the style, as one natural-language prompt
            # Pass LoRAs to krea2_prompt for trigger prefixing
            text = comfy.krea2_prompt(lead, [x["segment"] for x in j["char_segments"]], j["scene_tags"], loras=j.get("loras"))
        if text:    # Krea 2 reads natural language: a caller may hand over the sentence prompt instead of the tag soup
            j["positive"] = text
            j["prompt_used"] = {"positive": text, "negative": j["negative"]}
    else:
        # Non-Krea-2 model: ignore LoRAs with a warning
        if "loras" in spec:
            j["warnings"] = j.get("warnings") or []
            j["warnings"].append(f"LoRAs ignored for non-Krea-2 model '{j['model']}'")

    j["spec"] = spec
    j["nsfw"] = nsfw
    return j


@app.route("/api/preview", methods=["POST"])
def preview():
    spec = request.get_json(force=True, silent=True) or {}
    try:
        job = resolve_job(spec)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400
    return jsonify({"prompt_used": job["prompt_used"], "n_chars": job["n_chars"],
                    "pose_tags": job.get("pose_tags", ""),
                    "char_segments": job.get("char_segments", [])})


@app.route("/api/generate", methods=["POST"])
def generate():
    spec = request.get_json(force=True, silent=True) or {}
    try:
        job = resolve_job(spec)
        if spec.get("card"):
            job["card"] = str(spec["card"]).strip()[:40]   # the broker card this job runs on (GET /api/gpu/cards); absent = the broker's default
        jid = comfy.start(job, GALLERY_DIR)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400
    return jsonify({"job_id": jid, "prompt_used": job["prompt_used"], "seed": job["seed"],
                    "warnings": job.get("warnings") or []})


_REPAIRS = {}   # job_id -> (original RGBA, mask L): kept so the finished picture can be pasted back through the mask


def _parse_background(raw):
    """Optional `background` form field: #rrggbb or rrggbb. Returns (rgb tuple or None, error string or None); None means the default grey."""
    raw = (raw or "").strip().lstrip("#")
    if not raw:
        return None, None
    if not re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        return None, "background must be a hex colour like #f4e8cd"
    return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4)), None


def _repair_job_defaults(form, model):
    """Steps / cfg / sampler / scheduler / pose / warnings shared by /api/regenerate and /api/repair. An SDXL model keeps the old defaults
    (steps 28, cfg 5.0, pose as requested); a Krea 2 model is distilled (8 steps, cfg 1.0, euler/simple unless the form says
    otherwise) and has no ControlNet here, so a requested pose is ignored with a warning on the job record."""
    krea = comfy.is_krea2(model)
    out = {"steps": int(form.get("steps") or (8 if krea else 28)),
           "cfg": float(form.get("cfg") or (1.0 if krea else 5.0)), "warnings": []}
    if krea:
        out["sampler"], out["scheduler"] = "euler", "simple"
        if form.get("pose", "") not in ("", "0", "false"):
            out["warnings"].append("pose skeleton ignored: Krea 2 has no ControlNet in PromptForge yet")
            logger.warning("job model %s: ignoring pose request (Krea 2 has no ControlNet in PromptForge yet)", model)
    return out


@app.route("/api/repair", methods=["POST"])
def api_repair():
    """Inpaint. multipart: image (PNG, alpha = holes), mask (PNG, white = repaint, optional), prompt, negative,
    denoise (0.3-1.0, default 0.7), seed, model, steps, cfg. Returns {job_id}; poll /api/job/<id>, then GET
    /api/repair/<id>/result.png for the original with only the masked part replaced."""
    from PIL import Image
    from core import repair
    f = request.files.get("image")
    if not f:
        return jsonify({"error": "image is required"}), 400
    try:
        img = Image.open(f.stream).convert("RGBA")
        m = request.files.get("mask")
        mask = Image.open(m.stream).convert("L") if m else Image.new("L", img.size, 0)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"bad image: {exc}"}), 400
    prompt = (request.form.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400
    bg, bg_err = _parse_background(request.form.get("background"))
    if bg_err:
        return jsonify({"error": bg_err}), 400
    model = request.form.get("model") or (list_checkpoints() or ["novaFurryXL_ilV140.safetensors"])[0]
    img_png, mask_png, size = repair.prepare(img, mask, snap=16 if comfy.is_krea2(model) else 8, background=bg or repair.GREY)
    denoise = min(1.0, max(0.2, float(request.form.get("denoise") or 0.7)))
    j = dict(_DEFAULTS)
    j.update(
        positive=prompt, negative=(request.form.get("negative") or "").strip(),
        denoise=denoise, seed=int(request.form.get("seed") or -1),
        width=size[0], height=size[1], n_chars=1, model=model,
        repair_image_png=img_png, repair_mask_png=mask_png,
        spec={"repair": True, "prompt": prompt, "denoise": denoise, "source": f.filename or "",
              "background": "#%02x%02x%02x" % (bg or repair.GREY)},
        prompt_used={"positive": prompt, "negative": ""})
    j.update(_repair_job_defaults(request.form, model))
    try:
        jid = comfy.start(j, GALLERY_DIR)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400
    _REPAIRS[jid] = (img, mask)
    while len(_REPAIRS) > 20:
        _REPAIRS.pop(next(iter(_REPAIRS)))
    return jsonify({"job_id": jid, "seed": j["seed"], "model": j["model"]})


@app.route("/api/regenerate", methods=["POST"])
def api_regenerate():
    """Redraw a whole character using the picture as the reference. multipart: image (PNG; transparency is
    flattened onto grey), prompt, negative, denoise (0.3-1.0, default 0.65), pose (1 = hold the pose with the
    openpose ControlNet, default 1), seed, model, steps, cfg. Returns {job_id}; poll /api/job/<id>, then GET
    /api/regenerate/<id>/result.png."""
    from PIL import Image
    from core import repair
    f = request.files.get("image")
    prompt = (request.form.get("prompt") or "").strip()
    if not f or not prompt:
        return jsonify({"error": "image and prompt are required"}), 400
    try:
        img = Image.open(f.stream).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"bad image: {exc}"}), 400
    bg, bg_err = _parse_background(request.form.get("background"))
    if bg_err:
        return jsonify({"error": bg_err}), 400
    model = request.form.get("model") or (list_checkpoints() or ["novaFurryXL_ilV140.safetensors"])[0]
    img_png, _, size = repair.prepare(img, Image.new("L", img.size, 0), snap=16 if comfy.is_krea2(model) else 8,
                                      background=bg or repair.GREY)
    denoise = min(1.0, max(0.3, float(request.form.get("denoise") or 0.65)))
    j = dict(_DEFAULTS)
    j.update(
        positive=prompt, negative=(request.form.get("negative") or "").strip(),
        denoise=denoise, pose=request.form.get("pose", "1") not in ("0", "false", ""),
        seed=int(request.form.get("seed") or -1), width=size[0], height=size[1], n_chars=1, model=model,
        repair_image_png=img_png,
        spec={"regenerate": True, "prompt": prompt, "denoise": denoise, "source": f.filename or "",
              "background": "#%02x%02x%02x" % (bg or repair.GREY)},
        prompt_used={"positive": prompt, "negative": ""})
    j.update(_repair_job_defaults(request.form, model))
    if comfy.is_krea2(model):
        j["pose"] = False
    try:
        jid = comfy.start(j, GALLERY_DIR)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400
    return jsonify({"job_id": jid, "seed": j["seed"], "model": j["model"], "pose": j["pose"]})


@app.route("/api/regenerate/<jid>/result.png")
def api_regenerate_result(jid):
    rec = comfy.get_job(jid)
    if not rec:
        return jsonify({"error": "unknown job"}), 404
    if rec.get("status") != "done" or not rec.get("images"):
        return jsonify({"error": "not finished", "status": rec.get("status")}), 409
    with open(_safe_gallery_path(rec["images"][0]), "rb") as fh:
        return Response(fh.read(), mimetype="image/png")


@app.route("/api/repair/<jid>/result.png")
def api_repair_result(jid):
    import io
    from PIL import Image
    from core import repair
    rec, orig = comfy.get_job(jid), _REPAIRS.get(jid)
    if not rec or not orig:
        return jsonify({"error": "unknown repair job"}), 404
    if rec.get("status") != "done" or not rec.get("images"):
        return jsonify({"error": "not finished", "status": rec.get("status")}), 409
    path = _safe_gallery_path(rec["images"][0])
    out = repair.finish(orig[0], orig[1], Image.open(path))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return Response(buf.getvalue(), mimetype="image/png")


SSE_PING_S = 15


@app.route("/api/events")
def api_events():
    """Server-sent events for the gallery: `gallery-new` {job, images, nsfw} when a generation finishes, `gallery-removed` {file} when an
    image is deleted. A `: ping` comment goes out when idle so proxies keep the stream open; the subscriber is released on disconnect."""
    q = events.subscribe()
    if q is None:
        return jsonify({"error": "too many event streams open"}), 503

    def stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    name, payload = q.get(timeout=SSE_PING_S)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                yield f"event: {name}\ndata: {payload}\n\n"
        finally:
            events.unsubscribe(q)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/job/<jid>")
def job(jid):
    rec = comfy.get_job(jid)
    if not rec:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(rec)


def _sidecar_path_for(rel):
    d, base = os.path.split(rel)
    return os.path.join(GALLERY_DIR, d, base.split("_")[0] + ".json")


def _image_is_nsfw(rel):
    """Sidecar `nsfw` flag for a gallery image relpath (missing sidecar / key
    defaults to False, so images predating this feature stay visible)."""
    try:
        with open(_sidecar_path_for(rel)) as fh:
            return bool(json.load(fh).get("nsfw"))
    except (OSError, ValueError):
        return False


def _gallery_pngs():
    """(relpath, mtime) for every gallery PNG, recursing month sub-dirs, skipping
    the hidden .thumbs cache. NSFW-tagged images are omitted while the NSFW
    toggle is off. Newest first."""
    show_nsfw = data.nsfw_enabled()
    rows = []
    for root, dirs, files in os.walk(GALLERY_DIR):
        dirs[:] = [d for d in dirs if d != thumbs.THUMB_SUBDIR]
        for f in files:
            if f.endswith(".png"):
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, GALLERY_DIR)
                if not show_nsfw and _image_is_nsfw(rel):
                    continue
                try:
                    rows.append((rel, os.path.getmtime(fp)))
                except OSError:
                    pass
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def _safe_gallery_path(name):
    """Resolve a client-supplied gallery relpath, refusing anything outside."""
    p = os.path.abspath(os.path.join(GALLERY_DIR, name))
    root = os.path.abspath(GALLERY_DIR)
    return p if (p == root or p.startswith(root + os.sep)) else None


@app.route("/api/gallery")
def gallery():
    """Newest-first page of gallery image relpaths (no sidecar meta — see
    /api/gallery-meta). ?offset= &limit= for infinite scroll."""
    if not os.path.isdir(GALLERY_DIR):
        return jsonify({"items": [], "total": 0, "offset": 0, "limit": 0})
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        limit = max(1, min(500, int(request.args.get("limit", 60))))
    except ValueError:
        offset, limit = 0, 60
    rows = _gallery_pngs()
    page = rows[offset:offset + limit]
    return jsonify({
        "items": [{"file": rel, "ts": ts} for rel, ts in page],
        "total": len(rows), "offset": offset, "limit": limit,
    })


@app.route("/api/gallery-meta/<path:name>")
def gallery_meta(name):
    """The sidecar dict for a gallery image relpath (spec / prompt_used / seed …)."""
    p = _safe_gallery_path(name)
    if not p:
        return jsonify({}), 400
    d, base = os.path.split(p)
    sp = os.path.join(d, base.split("_")[0] + ".json")
    try:
        with open(sp) as fh:
            return jsonify(json.load(fh))
    except OSError:
        return jsonify({})


@app.route("/api/thumb/<path:name>")
def thumb(name):
    """Cached ~512px WebP thumbnail for a gallery image (lazily generated)."""
    dst = thumbs.ensure_thumb(GALLERY_DIR, name)
    if not dst:
        return send_from_directory(GALLERY_DIR, name)   # fall back to the original
    return send_from_directory(GALLERY_DIR, os.path.relpath(dst, GALLERY_DIR))


@app.route("/api/image/<path:name>", methods=["GET", "DELETE"])
def image(name):
    p = _safe_gallery_path(name)
    if not p:
        return jsonify({"error": "bad path"}), 400
    if request.method == "DELETE":
        deleted = []
        if os.path.isfile(p):
            os.remove(p)
            deleted.append(name)
        d, base = os.path.split(p)
        stem = base.split("_")[0]
        tp = os.path.join(d, thumbs.THUMB_SUBDIR, os.path.splitext(base)[0] + ".webp")
        if os.path.isfile(tp):
            os.remove(tp)
        # drop the sidecar once no image for that job remains
        if os.path.isdir(d) and not any(
                f.startswith(stem + "_") and f.endswith(".png") for f in os.listdir(d)):
            sp = os.path.join(d, stem + ".json")
            if os.path.isfile(sp):
                os.remove(sp)
        if deleted:
            events.publish("gallery-removed", {"file": name})
        return jsonify({"deleted": deleted})
    return send_from_directory(GALLERY_DIR, name)


if __name__ == "__main__":
    os.makedirs(GALLERY_DIR, exist_ok=True)
    app.run(host="127.0.0.1", port=int(os.environ.get("PROMPTFORGE_PORT", 7430)),
            debug=bool(os.environ.get("PROMPTFORGE_DEBUG")), threaded=True)
