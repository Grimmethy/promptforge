"""Read the generation recipe out of an image: which checkpoint and LoRAs made it, and the sampler settings and prompt. Pure, PIL only.

Three shapes are understood: a ComfyUI API graph in a PNG text chunk named `prompt`; Tungsten's JSON in the EXIF UserComment of a WebP/JPEG
({"type": "sdxl", "data": {prompt, negative_prompt, sampler, steps, cfg, clip_skip, seed, width, height, vae, checkpoint: {name}, loras:
[{name, weight}], embeddings}}); and an A1111 `parameters` text (a PNG chunk or the EXIF UserComment). An image with none of them, or a
corrupt one, gives source 'none' and empty fields; this module never raises for an ordinary image.
"""
import io
import json
import re

from PIL import Image

EMPTY = {"source": "none", "checkpoint": None, "loras": [], "embeddings": [], "vae": None, "sampler": None, "scheduler": None, "steps": None,
         "cfg": None, "clip_skip": None, "seed": None, "width": None, "height": None, "prompt": None, "negative": None, "raw_keys": [], "warnings": []}
_USER_COMMENT = 0x9286
_EXIF_IFD = 0x8769
_LORA_TAG = re.compile(r"<lora:([^:>]+)(?::([-0-9.]+))?(?::([-0-9.]+))?>", re.I)
_EMBEDDING = re.compile(r"\bembedding:([A-Za-z0-9_.\-]+)")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) and abs(f) < 1e15 else f


def _blank():
    d = dict(EMPTY)
    for k in ("loras", "embeddings", "raw_keys", "warnings"):
        d[k] = []
    return d


def read(source):
    """The recipe dict (see EMPTY) for a file path or the image bytes."""
    out = _blank()
    try:
        im = Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
        info = dict(im.info or {})
        out["raw_keys"] = sorted(str(k) for k in info)
        comment = _user_comment(im)
    except Exception as e:  # noqa: BLE001 - a broken image must not break the lightbox
        out["warnings"].append("cannot read the image: %s" % e)
        return out
    if isinstance(info.get("prompt"), str):
        graph = _json(info["prompt"], out)
        if isinstance(graph, dict) and graph and all(isinstance(v, dict) and "class_type" in v for v in graph.values()):
            return _from_comfy(graph, out)
        if graph is not None:
            out["warnings"].append("the prompt chunk is not a ComfyUI graph")
    if comment:
        parsed = _json(comment, None)
        if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
            return _from_tungsten(parsed["data"], out)
        if isinstance(parsed, dict) and parsed and all(isinstance(v, dict) and "class_type" in v for v in parsed.values()):
            return _from_comfy(parsed, out)
        if parsed is None and "Steps:" in comment:
            return _from_a1111(comment, out)
    if isinstance(info.get("parameters"), str):
        return _from_a1111(info["parameters"], out)
    return out


def _json(text, out):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        if out is not None:
            out["warnings"].append("a metadata chunk is not valid JSON")
        return None


def _user_comment(im):
    """The EXIF UserComment as text (UNICODE prefix in either byte order, ASCII, or plain UTF-8), else None."""
    try:
        ex = im.getexif()
        raw = ex.get_ifd(_EXIF_IFD).get(_USER_COMMENT) or ex.get(_USER_COMMENT)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        return None
    raw = bytes(raw)
    head, body = raw[:8], raw[8:]
    if head.startswith(b"UNICODE"):
        for enc in ("utf-16-be", "utf-16-le"):
            try:
                text = body.decode(enc)
            except UnicodeDecodeError:
                continue
            if text.lstrip().startswith(("{", "[")) or "Steps:" in text:
                return text
        return None
    if head.startswith(b"ASCII"):
        return body.decode("ascii", "replace")
    return raw.decode("utf-8", "replace")


# ------------------------------------------------------------------------------------------------ ComfyUI

def _node_id_key(k):
    return (0, int(k)) if str(k).isdigit() else (1, str(k))


def _text_for(graph, ref, depth=0):
    """The prompt text reached from a link [node id, slot]: a node with a `text` input, else follow the node's linked inputs. A node that has
    both a `positive` and a `negative` input (a ControlNet apply) passes them through by slot: output 0 is the positive, output 1 the negative."""
    if depth > 8 or not (isinstance(ref, list) and ref):
        return None
    node = graph.get(str(ref[0]))
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs") or {}
    if isinstance(inputs.get("text"), str):
        return inputs["text"]
    if isinstance(inputs.get("positive"), list) and isinstance(inputs.get("negative"), list):
        slot = ref[1] if len(ref) > 1 and isinstance(ref[1], int) else 0
        return _text_for(graph, inputs["negative" if slot == 1 else "positive"], depth + 1)
    for key in ("positive", "negative", "conditioning", "conditioning_to", "conditioning_1", "base"):
        if isinstance(inputs.get(key), list):
            t = _text_for(graph, inputs[key], depth + 1)
            if t:
                return t
    return None


def _from_comfy(graph, out):
    out["source"] = "comfyui"
    nodes = sorted(graph.items(), key=lambda kv: _node_id_key(kv[0]))
    sampler = None
    for nid, node in nodes:
        t, i = node.get("class_type"), node.get("inputs") or {}
        if t in ("CheckpointLoaderSimple", "CheckpointLoader", "ImageOnlyCheckpointLoader") and out["checkpoint"] is None:
            out["checkpoint"] = i.get("ckpt_name")
        elif t == "UNETLoader" and out["checkpoint"] is None:
            out["checkpoint"] = i.get("unet_name")
        elif t == "VAELoader" and out["vae"] is None:
            out["vae"] = i.get("vae_name")
        elif t in ("LoraLoader", "LoraLoaderModelOnly") and isinstance(i.get("lora_name"), str):
            out["loras"].append({"name": i["lora_name"], "weight": _num(i.get("strength_model")),
                                 "clip_weight": _num(i.get("strength_clip", i.get("strength_model")))})
        elif t == "CLIPSetLastLayer" and out["clip_skip"] is None and _num(i.get("stop_at_clip_layer")) is not None:
            out["clip_skip"] = abs(_num(i["stop_at_clip_layer"]))
        elif t == "EmptyLatentImage" and out["width"] is None:
            out["width"], out["height"] = _num(i.get("width")), _num(i.get("height"))
        elif t in ("KSampler", "KSamplerAdvanced") and sampler is None:
            sampler = i
    if sampler is not None:
        out["seed"] = _num(sampler.get("seed", sampler.get("noise_seed")))
        out["steps"], out["cfg"] = _num(sampler.get("steps")), _num(sampler.get("cfg"))
        out["sampler"], out["scheduler"] = sampler.get("sampler_name"), sampler.get("scheduler")
        out["prompt"] = _text_for(graph, sampler.get("positive"))
        out["negative"] = _text_for(graph, sampler.get("negative"))
    if out["prompt"] is None:
        texts = [(len(str((n.get("inputs") or {}).get("text"))), str(n["inputs"]["text"])) for _, n in nodes
                 if isinstance((n.get("inputs") or {}).get("text"), str)]
        out["prompt"] = max(texts)[1] if texts else None
    _embeddings(out)
    return out


def _embeddings(out):
    found = []
    for text in (out.get("prompt"), out.get("negative")):
        found += _EMBEDDING.findall(text or "")
    out["embeddings"] = sorted(set(found) | set(out["embeddings"]))


# ------------------------------------------------------------------------------------------------ Tungsten

def _from_tungsten(d, out):
    out["source"] = "tungsten"
    ck = d.get("checkpoint")
    out["checkpoint"] = ck.get("name") if isinstance(ck, dict) else ck if isinstance(ck, str) else None
    for lo in d.get("loras") or []:
        if isinstance(lo, dict) and lo.get("name"):
            w = _num(lo.get("weight"))
            out["loras"].append({"name": str(lo["name"]), "weight": w, "clip_weight": w})
    out["embeddings"] = [str(e.get("name") if isinstance(e, dict) else e) for e in d.get("embeddings") or []]
    out["vae"] = d.get("vae")
    out["sampler"] = d.get("sampler")
    out["steps"], out["cfg"], out["clip_skip"] = _num(d.get("steps")), _num(d.get("cfg")), _num(d.get("clip_skip"))
    out["seed"], out["width"], out["height"] = _num(d.get("seed")), _num(d.get("width")), _num(d.get("height"))
    out["prompt"], out["negative"] = d.get("prompt"), d.get("negative_prompt")
    _embeddings(out)
    return out


# ------------------------------------------------------------------------------------------------ A1111

def _from_a1111(text, out):
    out["source"] = "a1111"
    lines = text.strip().split("\n")
    settings = lines[-1] if lines and lines[-1].count(":") >= 2 and "Steps:" in lines[-1] else ""
    body = lines[:-1] if settings else lines
    neg_at = next((i for i, l in enumerate(body) if l.startswith("Negative prompt:")), None)
    prompt = "\n".join(body if neg_at is None else body[:neg_at]).strip()
    out["prompt"] = prompt or None
    if neg_at is not None:
        out["negative"] = ("\n".join(body[neg_at:])[len("Negative prompt:"):]).strip() or None
    fields = {}
    for part in re.split(r",\s(?=[A-Za-z][A-Za-z0-9 ]*:\s)", settings):
        if ":" in part:
            k, v = part.split(":", 1)
            fields[k.strip().lower()] = v.strip()
    out["steps"], out["cfg"] = _num(fields.get("steps")), _num(fields.get("cfg scale"))
    out["seed"], out["clip_skip"] = _num(fields.get("seed")), _num(fields.get("clip skip"))
    out["sampler"], out["scheduler"] = fields.get("sampler"), fields.get("schedule type")
    out["checkpoint"] = fields.get("model")
    m = re.match(r"^(\d+)x(\d+)$", fields.get("size", ""))
    if m:
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
    for name, w, cw in _LORA_TAG.findall(out["prompt"] or ""):
        out["loras"].append({"name": name, "weight": _num(w) if w else 1, "clip_weight": _num(cw) if cw else (_num(w) if w else 1)})
    _embeddings(out)
    return out


# ------------------------------------------------------------------------------------------------ registry

def _norm(text):
    stem = re.sub(r"\.(safetensors|ckpt|pt)$", "", str(text or "").strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", stem.lower())


def match_registry(meta, registry=None, installed=None):
    """Match a recipe's LoRAs and checkpoint against the LoRA registry ({loras: [...], checkpoints: {...}}; None means empty).

    A LoRA matches an entry when its name equals, ignoring case, punctuation and the file extension, the entry's id, name, file or one of its
    `aliases` (the display names other sites give the same file); nothing looser (a different version or base of the same artist must not match). `installed` is an optional collection of the file names in the
    LoRAs folder: given, each match says whether its file is installed. Returns {checkpoint, checkpoint_base, loras: [{name, weight, clip_weight,
    registry_id, file, base, known, installed}], known, unknown, not_installed}."""
    reg = registry or {}
    entries = reg.get("loras") or []
    have = {_norm(f) for f in installed} if installed is not None else None
    rows = []
    for lo in meta.get("loras") or []:
        key = _norm(lo.get("name"))
        entry = next((e for e in entries if key and key in {_norm(e.get("id")), _norm(e.get("name")), _norm(e.get("file")),
                                                            *(_norm(a) for a in e.get("aliases") or [])}), None)
        rows.append({"name": lo.get("name"), "weight": lo.get("weight"), "clip_weight": lo.get("clip_weight"),
                     "registry_id": entry.get("id") if entry else None, "file": entry.get("file") if entry else None,
                     "base": entry.get("base") if entry else None, "known": entry is not None,
                     "installed": None if (entry is None or have is None) else _norm(entry.get("file")) in have})
    ck = _norm(meta.get("checkpoint"))
    base = next((b for f, b in (reg.get("checkpoints") or {}).items() if ck and _norm(f) == ck), None)
    return {"checkpoint": meta.get("checkpoint"), "checkpoint_base": base, "loras": rows,
            "known": sum(r["known"] for r in rows), "unknown": sum(not r["known"] for r in rows),
            "not_installed": sum(r["installed"] is False for r in rows)}
