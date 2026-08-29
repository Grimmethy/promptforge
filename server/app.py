"""PromptForge Studio — Flask backend.

Serves web/ and a /api/* surface over the ported PromptForge core. Generation is
delegated to ComfyUI (:8188) — see core/comfy.py (added next). Bind 127.0.0.1.
"""

import json
import os
import urllib.error
import urllib.request

from flask import Flask, Response, jsonify, request, send_from_directory

from core import assemble, comfy, data, pose_ops, randomizer, secondbrain, themes

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
            "outfits": data.character_outfits(name),
            "expressions": data.character_expressions(name),
        })
    sb_chars = [{"source": "secondbrain", "name": n}
                for n in secondbrain.list_characters()]

    cn = []
    for p in data.list_cn_positions():
        doc = data.get_cn_position(p)
        cn.append({"name": p, "people": doc.get("people", 1),
                   "tags": doc.get("tags", ""),
                   "views": data.cn_position_views(p)})

    return jsonify({
        "characters": {"preset": preset_chars, "secondbrain": sb_chars},
        "styles": data.list_entries("styles"),
        "backgrounds": data.list_entries("backgrounds"),
        "positions": data.list_entries("positions"),
        "poses": data.list_entries("poses"),
        "supporting": data.list_entries("supporting"),
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
    doc = data.get_character(name)
    return (jsonify(doc), 200) if doc else (jsonify({"error": "not found"}), 404)


@app.route("/api/theme/<path:name>")
def theme(name):
    return jsonify(themes.resolve(name))


# ----------------------------------------------------------------------- pose

@app.route("/api/pose/<path:position>/<path:view>")
def pose_frame(position, view):
    path, tags = data.cn_view_skeleton(position, view)
    if not path:
        return jsonify({"error": "no skeleton for that position/view"}), 404
    return jsonify({"frame": pose_ops.load_json(path), "view_tags": tags})


@app.route("/api/pose/render", methods=["POST"])
def pose_render():
    b = request.get_json(force=True, silent=True) or {}
    w = int(b.get("w", 1152))
    h = int(b.get("h", 896))
    frame = b.get("keypoints")
    if frame is None and b.get("position"):
        path, _ = data.cn_view_skeleton(b["position"], b.get("view", "from_side"))
        if not path:
            return jsonify({"error": "unknown position"}), 404
        frame = pose_ops.load_json(path)
    if frame is None:
        return jsonify({"error": "keypoints or position required"}), 400
    frame = pose_ops.transform(
        frame,
        mirror_h=bool(b.get("mirror")), scale=float(b.get("scale", 1.0)),
        offset_x=float(b.get("offset_x", 0.0)), offset_y=float(b.get("offset_y", 0.0)),
        dutch_deg=float(b.get("dutch_angle", 0.0)), canvas_w=w, canvas_h=h,
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
            b["position"], b["view"], b["keypoints"],
            b.get("position_tags", ""), int(b.get("people", 1)),
            bool(b.get("update_yaml", True)), bool(b.get("overwrite", False)))
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

    # pose / skeleton
    pose_tags = ""
    frame = None
    sk = spec.get("skeleton") or {}
    if sk.get("people"):
        frame = {"people": sk["people"],
                 "canvas_width": j["width"], "canvas_height": j["height"]}
    elif spec.get("pose_position"):
        path, vtags = data.cn_view_skeleton(spec["pose_position"],
                                            spec.get("pose_view", "from_side"))
        if path:
            frame = pose_ops.load_json(path)
        pdoc = data.get_cn_position(spec["pose_position"])
        pose_tags = data.normalize_kw(pdoc.get("tags", ""), vtags,
                                      data.load_view_tags().get(spec.get("pose_view", ""), ""))
    if frame is not None:
        frame = pose_ops.transform(frame, canvas_w=j["width"], canvas_h=j["height"])
        j["skeleton_png"] = pose_ops.render_png(frame, j["width"], j["height"])

    j["negative"] = data.normalize_kw(
        (spec.get("negative") or "").strip(), *char_negs, th["negative_add"],
        assemble.DEFAULT_NEGATIVE)

    if n == 2:
        j["region_a"] = char_segs[0]
        j["region_b"] = char_segs[1]
        j["base"] = data.normalize_kw(assemble.DEFAULT_QUALITY, scene_tags, pose_tags)
        j["prompt_used"] = {"region_a": j["region_a"], "region_b": j["region_b"],
                            "base": j["base"], "negative": j["negative"]}
    else:
        built = assemble.assemble({
            "characters": chars, "scene_tags": scene_tags, "pose_tags": pose_tags})
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
    return jsonify({"prompt_used": job["prompt_used"], "n_chars": job["n_chars"]})


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
