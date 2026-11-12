"""CharacterForge as a slot-fill forge: which entries to fill, what a role needs, how to build the PromptForge request, how to link a picture.

Ported faithfully from the reference batch script /media/model-cache/forge-batches/fill_character_slots.py (spec_for, ensure_clothed, strip_tokens, link, counts); the rules learned
there are kept: the canon clothing tags are dropped for the outfit role and the face tags for the expression role, the outfit selection needs 4 OR MORE entries (only the first four
select it), a human with no clothing words and no outfit gets a modest backstop, and nothing here ever deletes, replaces or flags a picture (no DELETE or PATCH is ever sent).
"""
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .. import banks as banks_mod
from ..adapter import Adapter
from ..errors import SlotFillError

PARCHMENT = "plain pale parchment background, simple background"
NEUTRAL_PATH = "/media/model-cache/github/characterforge/data/neutral.json"
SKIP_KINDS = ("archetype", "squad", "group")
CLOTHING = ("overall", "hat", "cap", "hood", "shirt", "sleeve", "boot", "shoe", "cloak", "robe", "dress", "skirt", "coat", "jacket", "vest", "tunic", "scarf", "glove", "belt",
            "apron", "armor", "armour", "cape", "shorts", "pants", "trouser", "sweater", "hoodie", "backpack", "suit", "tie", "collar", "corset", "gown", "bracer", "pauldron", "mail")
EXPRESSION = ("expression", "mouth", "smile", "smiling", "frown", "grin", "smirk", "eyes closed", "calm face")
HUMAN_TAGS = ("1girl", "1boy", "1woman", "1man", "2girls", "2boys")
CLOTHING_WORDS = CLOTHING + ("clothes", "dress", "outfit", "wearing", "armor", "leather", "tabard", "uniform", "pajama", "coat")


def seed_for(slug, role, rnd):
    return int(hashlib.sha256(("%s|%s|%d" % (slug, role, rnd)).encode()).hexdigest()[:8], 16)


def outfit_of(c):
    """The default outfit selection: 4 or more entries are accepted, only the first four select it."""
    d = c.get("default_outfit")
    return dict(zip(("gender", "genre", "subgenre", "key"), d[:4])) if isinstance(d, list) and len(d) >= 4 else {}


def strip_tokens(keywords, words):
    """Drop every comma separated tag that contains one of the words (so a role can replace what the canon text already says)."""
    keep = [t.strip() for t in (keywords or "").split(",") if t.strip() and not any(w in t.lower() for w in words)]
    return ", ".join(keep)


def emphasise(text, w=1.3):
    return "(%s:%s)" % (text, w) if text else text


def multipart(fields, fname, data, mime):
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (b, k, v)).encode())
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n" % (b, fname, mime)).encode() + data + b"\r\n")
    parts.append(("--%s--\r\n" % b).encode())
    return b"".join(parts), "multipart/form-data; boundary=" + b


class CharacterForgeAdapter(Adapter):
    name = "characters"

    def __init__(self, cf_url=None, banks=None, neutral=None):
        self.cf = (cf_url or os.environ.get("CHARACTERFORGE_URL") or "http://localhost:7433").rstrip("/")
        self.banks = banks or banks_mod.load("characters")
        if neutral is None:
            path = os.environ.get("CHARACTERFORGE_NEUTRAL") or NEUTRAL_PATH
            try:
                with open(path) as fh:
                    neutral = json.load(fh)
            except (OSError, ValueError) as e:
                raise SlotFillError("CharacterForge's neutral.json cannot be read at %s (%s)" % (path, e))
        self.neutral = neutral

    # ---- CharacterForge's HTTP API ----
    def _get(self, path):
        try:
            with urllib.request.urlopen(self.cf + path, timeout=60) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raise SlotFillError("CharacterForge %s answered %s" % (path, e.code))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SlotFillError("CharacterForge not reachable at %s (%s)" % (self.cf, getattr(e, "reason", e)))

    def _renderable(self, d):
        return bool(d.get("kind") not in SKIP_KINDS and (d.get("keywords") or "").strip() and not d.get("sample") and d.get("status") != "retired"
                    and not self.banks.override(d.get("slug") or "").get("skip"))

    # ---- the Adapter interface ----
    def entries(self):
        """Renderable entries with their full detail (the list payload has no keywords): priority slugs first, then alphabetical."""
        found = {}
        for c in self._get("/api/characters"):
            if c.get("kind") in SKIP_KINDS:
                continue
            d = self._get("/api/characters/%s" % urllib.parse.quote(c["slug"], safe=""))
            d.setdefault("slug", c["slug"])
            if self._renderable(d):
                found[c["slug"]] = d
        first = [s for s in self.banks.priority if s in found]
        return [found[s] for s in first + sorted(s for s in found if s not in first)]

    def roles(self):
        t = self._get("/api/link-target")
        return list(t.get("roles") or [c["id"] for c in t.get("categories", [])])

    def counts(self, slug):
        """Pictures per role over every record, rejected ones too: a rejected picture still holds its slot."""
        out = {r: 0 for r in self.roles()}
        for i in self._get("/api/characters/%s/images" % urllib.parse.quote(slug, safe="")).get("images", []):
            out[i.get("role")] = out.get(i.get("role"), 0) + 1
        return out

    def target(self, entry, role):
        return self.banks.target(entry["slug"], role)

    def build_spec(self, entry, role, rnd):
        c, slug, n = entry, entry["slug"], self.neutral
        v = self.banks.variation(slug, role, rnd)
        tag, pose_kw, setting, style, extra = v["tag"], v["pose"], v["setting"], v["style"], v["extra"]
        size = self.banks.size(role) or (n.get("width", 896), n.get("height", 1152))
        if role in ("neutral-reference", "cameo-variant"):
            text = " ".join(x for x in ((c.get("neutral_text") or "").strip(), extra) if x)
            char = {"source": "preset", "name": slug, "expression": "flat", "weight": 1, "outfit": outfit_of(c), "colors": {}, "text": self.ensure_clothed(c, text, outfit_of(c))}
        else:
            # custom source: the canon keywords, minus whatever this role is meant to change (the outfit for an outfit slot, the face for an expression slot)
            base = c.get("keywords") or ""
            if role == "outfit":
                base = strip_tokens(base, CLOTHING + EXPRESSION)
            elif role == "expression":
                base = strip_tokens(base, EXPRESSION)
            else:
                base = strip_tokens(base, ("thoughtful expression", "closed mouth"))
            keep_outfit = {} if role == "outfit" else outfit_of(c)
            char = {"source": "custom", "name": slug, "expression": "none", "weight": 1, "outfit": keep_outfit, "colors": {},
                    "text": self.ensure_clothed(c, ", ".join(x for x in (base, extra) if x), keep_outfit)}
            if role in ("pose", "action", "expression"):
                pose_kw = emphasise(pose_kw)
        spec = {"characters": [char], "theme": "none", "style": style or n["style"], "setting": setting or PARCHMENT, "pose_keywords": pose_kw, "model": n.get("model"),
                "seed": seed_for(slug, role, rnd), "cfg": n.get("cfg", 4.5), "steps": n.get("steps", 8), "sampler": n.get("sampler", "euler"),
                "negative": ", ".join(x for x in (n.get("negative", ""), (c.get("neutral_negative") or "").strip(), (c.get("negative_add") or "").strip(), self.banks.negative_extra) if x),
                "width": size[0], "height": size[1], "batch": 1}
        if not spec["model"]:
            spec.pop("model")
        return tag, spec

    def ensure_clothed(self, c, text, outfit):
        """A human whose text names no clothing and who has no outfit selection gets a plain modest outfit, so it can never render undressed. Never a garment in a generic backstop."""
        low = ((c.get("keywords") or "") + ", " + (text or "")).lower()
        human = bool(self.banks.override(c.get("slug") or "").get("always_clothed")) or any(low.startswith(t) or (", " + t) in low for t in HUMAN_TAGS)
        named = any(w in low for w in CLOTHING_WORDS)
        if human and not named and not outfit:
            text = (text + ", " if text else "") + "wearing a simple modest tunic, trousers and shoes"
        return (text + ", fully clothed") if human else text

    def link(self, slug, role, tag, rnd, spec, job, name, data):
        """POST the picture to CharacterForge's own route as needs-work for the owner to rank. Never replaces, patches or deletes anything."""
        prov = {"model": spec.get("model") or job.get("model"), "seed": job.get("seed", spec["seed"]), "style": spec["style"], "steps": spec["steps"], "cfg": spec["cfg"],
                "prompt": (job.get("prompt_used") or {}).get("positive"), "batch": "slot-fill", "round": rnd, "licence_note": self.neutral.get("licence_note") or ""}
        fields = {"role": role, "tag": tag, "note": "auto batch, round %d" % rnd, "verdict": "needs-work", "provenance": json.dumps(prov), "source": json.dumps({"forge": "promptforge", "file": name})}
        body, ctype = multipart(fields, os.path.basename(name), data, "image/png")
        req = urllib.request.Request(self.cf + "/api/characters/%s/images" % urllib.parse.quote(slug, safe=""), data=body, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode()).get("error") or e.code
            except (ValueError, OSError):
                msg = e.code
            raise SlotFillError("CharacterForge refused the picture: %s" % msg)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SlotFillError("CharacterForge not reachable at %s (%s)" % (self.cf, getattr(e, "reason", e)))
