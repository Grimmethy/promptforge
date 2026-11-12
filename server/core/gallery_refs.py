"""Which gallery pictures do other forges point at? (read-only)

A forge that saves a gallery picture NAME in its own data (a StoryForge shot's reference image, for example) breaks if the sweep deletes that
picture. This module lists those names: for every source in data/sweep-reference-sources.json it reads the matching files as text and finds
every gallery path like 2026-11/abcd1234_0.png. It never writes anything. CharacterForge is not a source on purpose (see the data file).
"""
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SOURCES_PATH = os.path.join(REPO, "data", "sweep-reference-sources.json")
FORGES_ROOT = os.path.abspath(os.path.expanduser(os.environ.get("PROMPTFORGE_FORGES_ROOT") or os.path.dirname(REPO)))
PATTERN = re.compile(r"(?<![0-9A-Za-z/])(\d{4}-\d{2}/[0-9a-f]{8}_\d+\.(?:png|jpg|jpeg|webp))")
MAX_FILE_BYTES = 8 * 1024 * 1024


def load_sources(path=None):
    """The list of {forge, repo, globs}; an unreadable or malformed file gives []."""
    try:
        with open(path or SOURCES_PATH, encoding="utf-8") as fh:
            rows = json.load(fh).get("sources", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("forge") and r.get("repo") and isinstance(r.get("globs"), list)]


def referenced(sources=None, forges_root=None):
    """{gallery relpath: [{forge, record}]}: every picture name found in a source file; record is the file's path inside its repo."""
    root = forges_root or FORGES_ROOT
    out = {}
    for src in (load_sources() if sources is None else sources):
        base = os.path.join(root, src["repo"])
        for pat in src["globs"]:
            for path in sorted(glob.glob(os.path.join(base, pat), recursive=True)):
                if not os.path.isfile(path) or "node_modules" in path or os.path.getsize(path) > MAX_FILE_BYTES:
                    continue
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    continue
                rec = os.path.relpath(path, base)
                for name in sorted(set(PATTERN.findall(text))):
                    holders = out.setdefault(name, [])
                    if not any(h["forge"] == src["forge"] and h["record"] == rec for h in holders):
                        holders.append({"forge": src["forge"], "record": rec})
    return out
