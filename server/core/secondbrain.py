"""Read-only Second Brain integration.

Lists character notes under ``$SECOND_BRAIN_DIR/Characters/*.md`` and turns a note
body into a character spec the generator can use. No writes to the vault (that is
the future story plugin's job).
"""

import glob
import os
import re

from . import data

SB_DIR = os.environ.get("SECOND_BRAIN_DIR") or ""
CHAR_DIR = os.path.join(SB_DIR, "Characters") if SB_DIR else ""


def available():
    return bool(CHAR_DIR) and os.path.isdir(CHAR_DIR)


def _strip_frontmatter(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def _body_to_base(text):
    """Prefer a `## Keywords` / `## Prompt` section; else the whole body minus
    headings and list bullets. `normalize_kw` collapses it to a tag string."""
    body = _strip_frontmatter(text)
    m = re.search(r"^#{1,6}\s*(keywords?|prompt|appearance)\s*$(.*?)(^#{1,6}\s|\Z)",
                  body, re.I | re.M | re.S)
    section = m.group(2) if m else body
    section = re.sub(r"^#{1,6}\s.*$", "", section, flags=re.M)   # drop headings
    section = re.sub(r"^\s*[-*]\s+", "", section, flags=re.M)     # drop bullets
    section = re.sub(r"[*_`>#\[\]]", "", section)                 # strip md marks
    return data.normalize_kw(section)


def list_characters():
    if not available():
        return []
    out = []
    for p in sorted(glob.glob(os.path.join(CHAR_DIR, "*.md"))):
        out.append(os.path.splitext(os.path.basename(p))[0])
    return out


def get_character(name):
    """Return {name, base} or {} if not found."""
    if not available():
        return {}
    safe = re.sub(r"[^A-Za-z0-9 _.\-]", "", str(name))
    path = os.path.join(CHAR_DIR, safe + ".md")
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    title = None
    mt = re.search(r"^#\s+(.+)$", text, re.M)
    if mt:
        title = mt.group(1).strip()
    return {"name": title or safe.replace("_", " "),
            "base": _body_to_base(text)}
