"""PromptForge Studio — Flask backend.

Serves web/ and a /api/* surface over the ported PromptForge core. Generation is
delegated to ComfyUI (:8188) — see core/comfy.py (added next). Bind 127.0.0.1.
"""

import json
import os
import re
import urllib.error
import urllib.request

from flask import Flask, Response, jsonify, request, send_from_directory

from core import assemble, comfy, data, pose_ops, randomizer, rig, secondbrain, themes

_HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(os.path.dirname(_HERE), "web")
GALLERY_DIR = os.path.join(os.path.dirname(_HERE), "gallery")
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")

app = Flask(__name__, static_folder=None)


@app.after_request
def _no_frame_block(resp):
    # embedded as an <iframe> in the agent-manager dashboard
    resp.headers.pop("X-Frame-Options", None)
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
    except Exception:
        return []


@app.route("/api/comfy/status")
def comfy_status():
    try:
        st = _comfy_get("/system_stats", timeout=3)
        dev = (st.get("devices") or [{}])[0]
        return jsonify({
            "up": True,
            "vram_free": dev.get("vram_free"),
            "vram_total": dev.get("vram_total"),
            "checkpoints": list_checkpoints(),
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"up": False, "error": str(exc), "checkpoints": []})


# -------------------------------------------------------------------- presets

@app.route("/api/presets")
def presets():
    preset_chars = []
    for name in data.list_characters():
        preset_chars.append({
            "source": "preset", "name": name,
            "default_outfit": data.get_character(name).get("default_outfit"),
        })
    sb_chars = [{"source": "secondbrain", "name": n}
                for n in secondbrain.list_characters()]

    cn = []
    for p in data.list_cn_positions():
        doc = data.get_cn_position(p)
        cn.append({"name": p, "people": doc.get("people", 1),
                   "tags": doc.get("tags", "")})

    return jsonify({
        "characters": {"preset": preset_chars, "secondbrain": sb_chars},
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
        "checkpoints": list_checkpoints(),
        "secondbrain_dir": secondbrain.SB_DIR or None,
    })


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
            return jsonify({"value": v})
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

    char_segs, char_negs = [], []
    for c in chars:
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

    j["spec"] = spec
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
        jid = comfy.start(job, GALLERY_DIR)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400
    return jsonify({"job_id": jid, "prompt_used": job["prompt_used"], "seed": job["seed"]})


@app.route("/api/job/<jid>")
def job(jid):
    rec = comfy.get_job(jid)
    if not rec:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(rec)


@app.route("/api/gallery")
def gallery():
    """Newest-first list of gallery images with their sidecar metadata."""
    items = []
    if not os.path.isdir(GALLERY_DIR):
        return jsonify(items)
    pngs = [f for f in os.listdir(GALLERY_DIR) if f.endswith(".png")]
    pngs.sort(key=lambda f: os.path.getmtime(os.path.join(GALLERY_DIR, f)), reverse=True)
    side_cache = {}
    for f in pngs:
        stem = f.split("_")[0]
        if stem not in side_cache:
            sp = os.path.join(GALLERY_DIR, f"{stem}.json")
            try:
                with open(sp) as fh:
                    side_cache[stem] = json.load(fh)
            except OSError:
                side_cache[stem] = {}
        items.append({"file": f, "meta": side_cache[stem]})
    return jsonify(items)


@app.route("/api/image/<path:name>", methods=["GET", "DELETE"])
def image(name):
    if request.method == "DELETE":
        safe = os.path.basename(name)
        p = os.path.join(GALLERY_DIR, safe)
        if os.path.isfile(p):
            os.remove(p)
        return jsonify({"deleted": safe})
    return send_from_directory(GALLERY_DIR, name)


if __name__ == "__main__":
    os.makedirs(GALLERY_DIR, exist_ok=True)
    app.run(host="127.0.0.1", port=int(os.environ.get("PROMPTFORGE_PORT", 7430)),
            debug=bool(os.environ.get("PROMPTFORGE_DEBUG")))
