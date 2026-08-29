"""Turn a shot spec into positive / negative prompt strings.

Reimplements the pure logic of ComfyUI-PromptForge's Character / category nodes
and the BREAK-chunk Assembler. Framework-free.
"""

from . import data

DEFAULT_QUALITY = ("masterpiece, best quality, amazing quality, "
                   "very aware, newest, absurdres")
DEFAULT_NEGATIVE = ("worst quality, low quality, lowres, bad anatomy, bad hands, "
                    "extra digits, fewer digits, missing fingers, extra limbs, "
                    "fused fingers, mutated hands, deformed, jpeg artifacts, blurry, "
                    "watermark, signature, text, censored, greyscale, 3d, realistic")
SEP = "\nBREAK\n"


# ------------------------------------------------------------------ characters

def character_segment(ch):
    """ch: {source, name, outfit, expression, weight, text} -> (segment, negative).

    source: 'preset' | 'secondbrain' | 'custom'. For 'custom', `text` is the
    whole base. For 'preset', pull data/characters/<name>.yaml. For 'secondbrain'
    the caller must have resolved the note body into `text` already (see
    secondbrain.py). outfit (a {gender,genre,subgenre,key} cascade selection),
    `colors` ({base,contrast,detail}) and expression are universal pools applied
    to every source.
    """
    if not ch or ch.get("enabled") is False:
        return "", ""
    src = (ch.get("source") or "preset").lower()
    weight = ch.get("weight", 1.0)
    custom = (ch.get("text") or "").strip()
    # expressions + outfits are universal pools (data/expressions.yaml,
    # data/outfits.yaml), picked per character; the resolved fragments still land
    # only in this character's segment.
    expr_kw, _ = data.get_entry("expressions", ch.get("expression") or "flat")
    # outfit is a {gender,genre,subgenre,key} cascade selection; the fragment may
    # carry {base}/{contrast}/{detail} slots filled from this character's colours.
    outfit_kw = data.fill_outfit(
        data.outfit_fragment(ch.get("outfit") or {}), ch.get("colors") or {})

    if src == "custom":
        seg = data.normalize_kw(custom, outfit_kw, expr_kw)
        return data.apply_weight(seg, weight), ""

    if src == "secondbrain":
        seg = data.normalize_kw(custom, outfit_kw, expr_kw)
        return data.apply_weight(seg, weight), ""

    # preset
    doc = data.get_character(ch.get("name", ""))
    base = str(doc.get("base", ""))
    seg = data.normalize_kw(base, outfit_kw, expr_kw, custom)
    seg = data.apply_weight(seg, weight)
    neg = data.normalize_kw(str(doc.get("negative_add", "")))
    return seg, neg


# -------------------------------------------------------------------- assembler

def assemble(spec):
    """spec: the shot spec dict. Returns {positive, negative, positive_chunks}.

    Recognised keys: characters[], scene_tags (already-resolved theme/setting/
    style/palette/lighting), pose_tags, position_tags, quality (bool, default
    True), quality_text, manual_prefix, manual_suffix, negative (global),
    group_scene (bool, default True).
    """
    chars = [c for c in (spec.get("characters") or []) if c]
    quality = spec.get("quality", True)
    quality_text = (spec.get("quality_text") or DEFAULT_QUALITY).strip()
    manual_prefix = (spec.get("manual_prefix") or "").strip()
    manual_suffix = (spec.get("manual_suffix") or "").strip()
    group_scene = spec.get("group_scene", True)

    char_segs, char_negs = [], []
    for c in chars[:2]:
        seg, neg = character_segment(c)
        char_segs.append(seg)
        if neg:
            char_negs.append(neg)
    # caller may hand us already-processed segments (e.g. gaze stripped for a
    # rear camera) — use them for the positive, keep the negatives above
    override = spec.get("char_segments")
    if override is not None:
        char_segs = [str(s) for s in override][:2]

    chunks = []

    pose_tags = (spec.get("pose_tags") or "").strip()

    head = []
    if quality and quality_text:
        head.append(quality_text)
    if manual_prefix:
        head.append(manual_prefix)
    if char_segs and char_segs[0]:
        head.append(char_segs[0])
    # With exactly one character, keep the pose words in the same BREAK chunk as
    # the subject so they bind to it (later chunks carry less weight). With two
    # characters the pose is shared scene context and stays in the scene chunk.
    single_char_pose = pose_tags if len(char_segs) == 1 else ""
    if single_char_pose:
        head.append(single_char_pose)
    if head:
        chunks.append(", ".join(head))

    for seg in char_segs[1:]:
        if seg:
            chunks.append(seg)

    scene = [s for s in (
        ("" if single_char_pose else pose_tags),
        spec.get("position_tags"), spec.get("scene_tags"),
    ) if s and str(s).strip()]
    if scene:
        if group_scene:
            chunks.append(data.normalize_kw(*scene))
        else:
            chunks.extend(data.normalize_kw(s) for s in scene)

    if manual_suffix:
        chunks.append(manual_suffix)

    positive = SEP.join(ch for ch in chunks if ch.strip())
    positive = data.dedupe_across(positive)

    negative = data.normalize_kw(
        (spec.get("negative") or "").strip(), *char_negs, DEFAULT_NEGATIVE)

    return {"positive": positive, "negative": negative,
            "positive_chunks": [c for c in positive.split(SEP) if c.strip()]}
