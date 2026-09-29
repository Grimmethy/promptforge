"""Read characters from CharacterForge, the canonical character store.

CharacterForge is an EARLIER step in the design process than PromptForge: characters are
authored there (questionnaire, backstory, keyword block, image settings) and PromptForge only
renders them. When CharacterForge is present this module is the single source of characters,
replacing both data/characters/*.yaml presets and the Second Brain `Characters/` reader (both
were folded into CharacterForge by its importer).

A CharacterForge character is a Markdown note with a JSON (= valid YAML) frontmatter block
carrying `name`, `weight`, `default_outfit`, `negative_add`, and a `## Keywords` section that is
the Danbooru-style tag block. This reads exactly what PromptForge's own preset YAML used to
provide, as the same dict shape: {name, weight, base, default_outfit, negative_add}.

Where it looks (first match wins):
  CHARACTERFORGE_ROOT   repo root (its `characters/` and `samples/` are read); else a sibling
                        `characterforge` checkout next to this repo if it exists
  CHARACTERFORGE_DIR    override the user-characters dir
  PROMPTFORGE_CHARACTER_SOURCE=local  ignore CharacterForge and use the legacy local presets

Characters with no `## Keywords` block are not listed: there is nothing to render yet.
"""

import os
import re

import yaml

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _root():
    env = os.environ.get("CHARACTERFORGE_ROOT")
    if env:
        return env
    sibling = os.path.join(os.path.dirname(_REPO), "characterforge")
    return sibling if os.path.isdir(sibling) else ""


def _dirs():
    """Directories to read, user characters first (they shadow same-named samples)."""
    if (os.environ.get("PROMPTFORGE_CHARACTER_SOURCE") or "").lower() == "local":
        return []
    root = _root()
    user = os.environ.get("CHARACTERFORGE_DIR") or (os.path.join(root, "characters") if root else "")
    out = [user] if user else []
    if root:
        out.append(os.path.join(root, "samples"))
    return [d for d in out if os.path.isdir(d)]


def configured():
    return bool(_dirs())


def _split(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                meta = yaml.safe_load(text[3:end]) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), text[end + 4:]
    return {}, text


def _keywords(body):
    m = re.search(r"^##\s*keywords?\s*$(.*?)(?=^##\s|\Z)", body, re.I | re.M | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _weight(v):
    try:
        return float(v) if v not in (None, "") else 1.0
    except (TypeError, ValueError):
        return 1.0


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            meta, body = _split(fh.read())
    except OSError:
        return {}
    slug = os.path.splitext(os.path.basename(path))[0]
    outfit = meta.get("default_outfit")
    return {"name": meta.get("name") or slug.replace("_", " "), "weight": _weight(meta.get("weight")),
            "base": _keywords(body), "default_outfit": outfit if isinstance(outfit, list) and outfit else None,
            "negative_add": str(meta.get("negative_add") or ""), "_slug": slug}


def _index():
    """{slug: character} across all dirs, user characters shadowing samples."""
    seen = {}
    for d in _dirs():
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".md") and not fn.startswith((".", "_")):
                slug = os.path.splitext(fn)[0]
                if slug not in seen:
                    c = _load(os.path.join(d, fn))
                    if c:
                        seen[slug] = c
    return seen


def list_characters():
    return sorted(slug for slug, c in _index().items() if c["base"])


def get_character(name):
    """Look up by slug or display name, case-insensitively (older saved shot specs used the
    lowercase preset file stem, e.g. 'cindy' for CharacterForge's 'Cindy'). {} if unknown."""
    want = re.sub(r"[^a-z0-9]+", "", str(name or "").lower())
    if not want:
        return {}
    for slug, c in _index().items():
        if want in (re.sub(r"[^a-z0-9]+", "", slug.lower()), re.sub(r"[^a-z0-9]+", "", c["name"].lower())):
            return {k: v for k, v in c.items() if not k.startswith("_")}
    return {}
