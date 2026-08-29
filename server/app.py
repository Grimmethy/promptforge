"""PromptForge Studio — Flask backend.

Serves web/ and a /api/* surface over the ported PromptForge core. Generation is
delegated to ComfyUI (:8188) — see core/comfy.py (added next). Bind 127.0.0.1.
"""

import json
import os
import urllib.error
import urllib.request

from flask import Flask, Response, jsonify, request, send_from_directory

from core import assemble, data, pose_ops, randomizer, secondbrain, themes

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


# --------------------------------------------------------- generation (stub)

@app.route("/api/generate", methods=["POST"])
def generate():
    return jsonify({"error": "not implemented yet (scaffold)"}), 501


@app.route("/api/job/<jid>")
def job(jid):
    return jsonify({"error": "not implemented yet (scaffold)"}), 501


@app.route("/api/gallery")
def gallery():
    items = []
    if os.path.isdir(GALLERY_DIR):
        for f in sorted(os.listdir(GALLERY_DIR), reverse=True):
            if f.endswith(".png"):
                items.append({"file": f})
    return jsonify(items)


@app.route("/api/image/<path:name>")
def image(name):
    return send_from_directory(GALLERY_DIR, name)


if __name__ == "__main__":
    os.makedirs(GALLERY_DIR, exist_ok=True)
    app.run(host="127.0.0.1", port=int(os.environ.get("PROMPTFORGE_PORT", 7430)),
            debug=bool(os.environ.get("PROMPTFORGE_DEBUG")))
