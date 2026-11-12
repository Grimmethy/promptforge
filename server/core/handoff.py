"""Hand-offs from the library forges (CharacterForge ...) to the Create form: a picture to tweak, or an empty library slot to fill.

A hand-off is a small record in HANDOFF_DIR (from PROMPTFORGE_HANDOFF_DIR, default <repo>/handoff, git-ignored) that lives one hour:
  <id>.json   {id, created_at, payload}   payload = {kind: 'image' | 'slot', source, prompt, negative, model, seed, style, spec, slot}
  <id>.<ext>  the attached picture (kind 'image'), never copied into the gallery
kind 'image' = tweak this picture (the picture is attached); kind 'slot' = make a new one for this empty slot (build_slot_spec turns the slot into a
form spec from the entry's canon text and data/role-keywords.json: ten variations per role).
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
HANDOFF_DIR = os.path.abspath(os.path.expanduser(os.environ.get("PROMPTFORGE_HANDOFF_DIR") or os.path.join(REPO, "handoff")))
ROLE_KEYWORDS_PATH = os.path.join(REPO, "data", "role-keywords.json")
CHARACTERFORGE_URL = (os.environ.get("CHARACTERFORGE_URL") or "http://localhost:7433").rstrip("/")
MAX_AGE_S = 3600
MAX_IMAGE_BYTES = 12 * 1024 * 1024
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
KINDS = ("image", "slot")
_ID = re.compile(r"^[0-9a-f]{32}$")
_TEXT_KEYS = ("prompt", "negative", "model", "style")


class HandoffError(ValueError):
    """Bad input: an unknown kind, an oversized or unsupported picture, a malformed id."""


class NotFound(LookupError):
    """No such hand-off (never made, or older than an hour)."""


def _clip(v, n=20000):
    return v[:n] if isinstance(v, str) else v


def _clean_payload(p):
    if not isinstance(p, dict):
        raise HandoffError("payload must be a JSON object")
    kind = p.get("kind")
    if kind not in KINDS:
        raise HandoffError("kind must be 'image' or 'slot'")
    out = {"kind": kind}
    for k in _TEXT_KEYS:
        if p.get(k) is not None:
            if not isinstance(p[k], str):
                raise HandoffError("%s must be text" % k)
            out[k] = _clip(p[k])
    if p.get("seed") is not None:
        if isinstance(p["seed"], bool) or not isinstance(p["seed"], (int, float)):
            raise HandoffError("seed must be a number")
        out["seed"] = p["seed"]
    for k in ("source", "spec", "slot"):
        if p.get(k) is not None:
            if not isinstance(p[k], dict):
                raise HandoffError("%s must be an object" % k)
            out[k] = p[k]
    if kind == "slot":
        slot = out.get("slot") or {}
        if not (isinstance(slot.get("forge"), str) and isinstance(slot.get("slug"), str) and isinstance(slot.get("role"), str)):
            raise HandoffError("a slot hand-off needs slot {forge, slug, role}")
        if not isinstance(slot.get("have", []), list):
            raise HandoffError("slot.have must be a list")
    return out


def purge(now=None, max_age_s=MAX_AGE_S):
    """Delete expired hand-offs: a record older than max_age_s (by its created_at) goes with its picture; a picture with no record goes when its file is
    older than max_age_s. Returns how many files went. Only files named <32 hex>.<ext> directly in HANDOFF_DIR are ever touched."""
    now = time.time() if now is None else now
    if not os.path.isdir(HANDOFF_DIR):
        return 0
    n = 0
    names = [x for x in os.listdir(HANDOFF_DIR) if re.match(r"^[0-9a-f]{32}\.[a-z]+$", x) and os.path.isfile(os.path.join(HANDOFF_DIR, x))]
    kept_pictures = set()
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(HANDOFF_DIR, name)
        created, image = None, None
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
            created, image = rec.get("created_at"), rec.get("image")
        except (OSError, ValueError):
            pass
        if not isinstance(created, (int, float)):
            created = os.path.getmtime(path)
        if now - created > max_age_s:
            for victim in (path, os.path.join(HANDOFF_DIR, image) if isinstance(image, str) and re.match(r"^[0-9a-f]{32}\.[a-z]+$", image) else None):
                if victim and os.path.isfile(victim):
                    os.remove(victim)
                    n += 1
        elif isinstance(image, str):
            kept_pictures.add(image)
    for name in names:
        if not name.endswith(".json") and name not in kept_pictures:
            path = os.path.join(HANDOFF_DIR, name)
            if os.path.isfile(path) and now - os.path.getmtime(path) > max_age_s:        # a picture whose record is gone
                os.remove(path)
                n += 1
    return n


def create(payload, image=None, ext=".png", now=None):
    """Store a hand-off; returns its id. image is the picture's bytes (kind 'image'), ext its type."""
    now = time.time() if now is None else now
    purge(now)
    p = _clean_payload(payload)
    if image is not None:
        if p["kind"] != "image":
            raise HandoffError("only an image hand-off carries a picture")
        if not image or len(image) > MAX_IMAGE_BYTES:
            raise HandoffError("picture is empty or larger than %d MB" % (MAX_IMAGE_BYTES // 1048576))
        ext = (ext or "").lower()
        if ext not in IMAGE_EXTS:
            raise HandoffError("unsupported picture type (png, jpg, jpeg, webp)")
    hid = uuid.uuid4().hex
    os.makedirs(HANDOFF_DIR, exist_ok=True)
    rec = {"id": hid, "created_at": now, "payload": p}
    if image is not None:
        rec["image"] = hid + ext
        with open(os.path.join(HANDOFF_DIR, hid + ext), "wb") as fh:
            fh.write(image)
    with open(os.path.join(HANDOFF_DIR, hid + ".json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    return hid


def get(hid, now=None):
    """The record {id, created_at, payload, image?}; NotFound when it never existed or is older than an hour."""
    if not isinstance(hid, str) or not _ID.match(hid):
        raise HandoffError("malformed id")
    now = time.time() if now is None else now
    purge(now)
    try:
        with open(os.path.join(HANDOFF_DIR, hid + ".json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        raise NotFound("no such hand-off (they expire after an hour)")


def image_path(hid, now=None):
    rec = get(hid, now)
    name = rec.get("image")
    p = os.path.join(HANDOFF_DIR, name) if name else None
    if not p or not os.path.isfile(p):
        raise NotFound("this hand-off has no picture")
    return p


# ---- empty-slot hand-offs: a form spec from the entry's canon text and the role's variations ----
def load_role_keywords(path=None):
    with open(path or ROLE_KEYWORDS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def strip_tokens(keywords, words):
    """Drop every comma separated tag that contains one of the words (so a role can replace what the canon text already says)."""
    return ", ".join(t.strip() for t in (keywords or "").split(",") if t.strip() and not any(w in t.lower() for w in words))


def pick_variation(variations, have):
    """The first variation whose tag is not already used in the role (else the first)."""
    used = set(have or [])
    for v in variations:
        if v.get("tag") not in used:
            return v
    return variations[0]


def build_slot_spec(entry, role, have, banks):
    """The Create form's spec for an empty slot. entry = CharacterForge's entry {slug, keywords, default_outfit, negative_add}; banks = role-keywords.json.

    Raises HandoffError for an unknown role or an entry without keywords. The first unused variation of the role is used, so pressing the plus card of the
    same role again after the picture was added gives the next one. The outfit role drops the canon clothing tags, the expression role the face tags."""
    variations = (banks.get("roles") or {}).get(role)
    if not variations:
        raise HandoffError("unknown role: %s" % role)
    keywords = (entry.get("keywords") or "").strip()
    if not keywords:
        raise HandoffError("the entry has no keywords to build a prompt from")
    v = pick_variation(variations, have)
    base = keywords
    if role == "outfit":
        base = strip_tokens(keywords, list(banks.get("clothing_words", [])) + list(banks.get("expression_words", [])))
    elif role == "expression":
        base = strip_tokens(keywords, banks.get("expression_words", []))
    text = ", ".join(x for x in (base, v.get("extra") or "") if x)
    pose = v.get("pose_keywords") or ""
    if role in ("pose", "action", "expression") and pose:
        pose = "(%s:1.3)" % pose
    neg = ", ".join(x for x in ((entry.get("negative_add") or "").strip(), banks.get("negative_extra", "")) if x)
    outfit = {}
    d = entry.get("default_outfit")
    if role != "outfit" and isinstance(d, list) and len(d) >= 4:
        outfit = dict(zip(("gender", "genre", "subgenre", "key"), d[:4]))
    return {"characters": [{"source": "custom", "name": entry.get("slug") or "character", "expression": "flat" if role == "neutral-reference" else "none",
                            "weight": 1, "outfit": outfit, "colors": {}, "text": text}],
            "theme": "none", "style": v.get("style") or "", "setting": v.get("setting") or banks.get("parchment", ""), "pose_keywords": pose,
            "negative": neg, "slot_tag": v.get("tag")}


def fetch_entry(forge, slug, opener=None):
    """CharacterForge's entry for a slug (GET /api/characters/<slug>); HandoffError when the forge is unknown or unreachable."""
    if forge != "characterforge":
        raise HandoffError("unknown forge: %s" % forge)
    open_ = opener or (lambda url: urllib.request.urlopen(url, timeout=10).read())
    try:
        return json.loads(open_("%s/api/characters/%s" % (CHARACTERFORGE_URL, urllib.request.quote(slug, safe=""))))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise HandoffError("could not read the entry from CharacterForge: %s" % e)


def resolve(rec, banks=None, opener=None):
    """The payload with a `spec` filled in for a slot hand-off (an image hand-off already carries its own); never raises: a problem goes in `warning`."""
    p = dict(rec["payload"])
    if p["kind"] == "slot" and "spec" not in p:
        try:
            slot = p["slot"]
            entry = fetch_entry(slot["forge"], slot["slug"], opener)
            p["spec"] = build_slot_spec(entry, slot["role"], slot.get("have", []), banks or load_role_keywords())
        except (HandoffError, OSError, ValueError) as e:
            p["warning"] = str(e)
    return p
