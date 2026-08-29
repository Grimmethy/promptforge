"""Seeded, coherent character rolls for the Character Randomizer node."""

import random

from . import data

# axis -> whether a single pick (True) or a range pick (tuple = (min, max))
_SINGLE_AXES = ("body", "skin", "hair_length", "hair_style", "hair_color",
                "eyes", "face_features")
_MULTI_AXES = {"distinguishing": (0, 2), "accessories": (0, 2)}

_FALLBACK = {
    "species": [{"kw": "1girl", "weight": 1}],
    "body": ["slim"],
    "skin": ["fair skin"],
    "hair_length": ["medium hair"],
    "hair_style": ["straight hair"],
    "hair_color": ["brown hair"],
    "eyes": ["brown eyes"],
    "face_features": ["soft features"],
    "distinguishing": [],
    "accessories": [],
}


def _pool(traits, axis):
    return traits.get(axis) or _FALLBACK.get(axis, [])


def _weighted_choice(rng, entries):
    """entries: list of str or {kw, weight}. Returns the kw string."""
    norm = []
    for e in entries:
        if isinstance(e, dict):
            norm.append((str(e.get("kw", "")), float(e.get("weight", 1))))
        else:
            norm.append((str(e), 1.0))
    norm = [(k, w) for k, w in norm if k]
    if not norm:
        return ""
    total = sum(w for _, w in norm)
    r = rng.uniform(0, total)
    upto = 0.0
    for k, w in norm:
        upto += w
        if r <= upto:
            return k
    return norm[-1][0]


def roll(seed, species="random", outfit_style="random",
         vary_body=True, vary_hair=True, vary_eyes=True,
         vary_features=True, vary_accessories=True):
    """Return (keywords_str, spec_dict)."""
    traits = data.load_traits()
    pools = data.load_outfit_pools()

    # Per-axis RNG streams derived from the seed, so freezing one axis
    # (vary_* = False) leaves every other axis identical for the same seed.
    def axis_rng(name):
        return random.Random(f"{int(seed)}:{name}")

    parts = []

    # species -------------------------------------------------------------
    sp_entries = _pool(traits, "species")
    if species and species != "random":
        chosen_sp = next(
            (
                (e.get("kw") if isinstance(e, dict) else e)
                for e in sp_entries
                if _species_key(e) == species
            ),
            None,
        )
        if chosen_sp is None:
            chosen_sp = _weighted_choice(axis_rng("species"), sp_entries)
    else:
        chosen_sp = _weighted_choice(axis_rng("species"), sp_entries)
    parts.append("1girl")
    parts.append(chosen_sp)

    # single axes -------------------------------------------------------------
    axis_toggle = {
        "body": vary_body,
        "skin": vary_body,
        "hair_length": vary_hair,
        "hair_style": vary_hair,
        "hair_color": vary_hair,
        "eyes": vary_eyes,
        "face_features": vary_features,
    }
    for axis in _SINGLE_AXES:
        pool = _pool(traits, axis)
        if not pool:
            continue
        if axis_toggle.get(axis, True):
            parts.append(str(axis_rng(axis).choice(pool)))
        else:
            parts.append(str(pool[0]))  # frozen -> first entry

    # multi axes -------------------------------------------------------------
    for axis, (lo, hi) in _MULTI_AXES.items():
        if axis == "accessories" and not vary_accessories:
            continue
        if axis == "distinguishing" and not vary_features:
            continue
        pool = [p for p in _pool(traits, axis) if str(p).lower() != "none"]
        if not pool:
            continue
        r = axis_rng(axis)
        n = r.randint(lo, min(hi, len(pool)))
        parts.extend(str(x) for x in r.sample(pool, n))

    # outfit -------------------------------------------------------------
    style = outfit_style
    if style == "random":
        style = axis_rng("outfit_style").choice([k for k in pools.keys()] or ["none"])
    outfit_kw = ""
    if style and style != "none":
        opts = pools.get(style) or []
        if opts:
            outfit_kw = str(axis_rng("outfit").choice(opts))

    keywords = data.normalize_kw(*parts)
    spec = {
        "name": f"Random_{int(seed)}",
        "base": keywords,
        "outfits": {"default": outfit_kw or ""},
    }
    full_segment = data.normalize_kw(keywords, outfit_kw)
    return full_segment, spec


def _species_key(entry):
    kw = entry.get("kw") if isinstance(entry, dict) else entry
    return str(kw).split(",")[0].strip().lower()


def species_choices():
    entries = data.load_traits().get("species") or []
    keys = ["random"]
    for e in entries:
        k = _species_key(e)
        if k and k not in keys:
            keys.append(k)
    return keys


def style_choices():
    return ["random"] + list(data.load_outfit_pools().keys()) + ["none"]
