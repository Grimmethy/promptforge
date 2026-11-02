# Function Length Decomposition Candidates

### AC-1 · Decompose `charCard` into per-section builder functions
Strength: Strong
Files: web/app.js
Snippet:
```

function charCard(c, i) {
  normalizeChar(c);
  const setSrc = s => { c.source = s; renderChars(); schedulePreview(); };
  const hd = el('div', { class: 'hd' }, [
    el('span', { class: 'tag ' + (i === 0 ? 'A' : 'B') }, i === 0 ? 'A · left' : 'B · right'),
    el('div', { class: 'seg' }, ['preset', 'secondbrain', 'custom'].map(s =>
      el('button', { class: c.source === s ? 'on' : '', onclick: () => setSrc(s) }, s === 'secondbrain' ? 'SB' : s))),
    el('span', { style: 'flex:1' }),
    el('button', { class: 'x', onclick: () => { state.chars.splice(i, 1); renderChars(); schedulePreview(); } }, '✕'),
  ]);

  // Expressions are a universal pool (data/expressions.yaml) — same list on
  // every card type, still chosen per character.
  const exprList = PRESETS.expressions || ['flat'];
  if (!exprList.includes(c.expression)) c.expression = exprList[0] || 'flat';
  const exprAdd = addEntryRow({ target: 'expressions', current: () => c.expression, onDone: k => { if (k) c.expression = k; } });
  const expSel = el('select', { onchange: e => { c.expression = e.target.value; exprAdd._resync(); schedulePreview(); } }, exprList.map(o => el('option', {}, o)));
  expSel.value = c.expression;
  const exprRow = () => el('div', {}, [el('label', {}, 'Expression'), expSel, exprAdd]);

  // Outfits are a universal 4-level pool (data/outfits.yaml): gender -> genre ->
  // subgenre -> outfit. Changing an upper level clears the levels below it and
  // rebuilds the card so the dependent option lists recompute. Editing the YAML
  // on disk needs a page reload (PRESETS is only re-fetched after a save).
  const dressControls = () => {
    const T = PRESETS.outfits || {};
    const o = c.outfit;
    const L1 = Object.keys(T);
    if (o.gender && !L1.includes(o.gender)) { o.gender = o.genre = o.subgenre = o.key = ''; }
    const L2 = o.gender ? Object.keys(T[o.gender] || {}) : [];
    if (o.genre && !L2.includes(o.genre)) { o.genre = o.subgenre = o.key = ''; }
```

Problem:
`charCard` is 118 lines and packs three structurally independent widget sections into a single closure: a header/source-switcher (segmented control, close button, `setSrc` helper), an expression picker (pool validation via `exprList.includes`, a `<select>`, and an `addEntryRow` call wired to a `_resync` callback), and a four-level outfit cascade (`o.gender → o.genre → o.subgenre → o.key`) where mutating any upper level must null out every lower level and re-invoke the cascade. Each section carries its own data-validation rule, its own event-handler closure, and its own failure mode, yet a developer debugging "why did my subgenre reset after I changed gender?" must scroll past ~70 lines of header and expression code to reach the `dressControls` if/else chain. The function also closes over the shared `c` (character) object in ways that differ per section, making it impossible to reason about one section's invariants without holding the other two in working memory.

Solution:
Extract three sibling builder functions that each receive `c` (the character object) and a `container` DOM node, and return nothing (they mutate `container` in place, mirroring the current inline behaviour). First, `buildCardHeader(c, container)` contains the segmented-control markup, the close-button listener, and the `setSrc` helper; it no longer references `exprList` or `o`. Second, `buildExpressionPicker(c, container)` contains the `exprList.includes` guard, the `<select>` population loop, and the `addEntryRow(c, …, { _resync })` call; it no longer references `o.gender` or the cascade. Third, `buildOutfitCascade(c, container)` contains the entire `dressControls` closure, the four-level if/else reset chain, and the re-invocation call that fires after any upper-level mutation; it is the only extracted function that reads or writes `o.genre`, `o.subgenre`, or `o.key`. The parent `charCard` shrinks to roughly 20 lines: create the root `<div>`, call the three builders in order, and attach the single shared `c` reference. No new module or file is introduced; all three builders live in the same `web/app.js` scope, immediately above `charCard`, so the existing `exprList` and `addEntryRow` imports remain in scope without any `require`/`import` changes.

Benefits:
Each builder is independently unit-testable: a test can call `buildOutfitCascade` with a stub `c.outfit = { gender:'f', genre:null, subgenre:null, key:null }`, fire the gender `<select>` change handler, and assert that `c.outfit.genre` was nulled—without constructing a header or expression DOM. Code review diffs become section-scoped: a change to the expression pool validation touches only `buildExpressionPicker`, and a reviewer can approve or reject it without reading the cascade logic. The 118-line wall of code becomes a 20-line dispatcher plus three 25–40-line functions, each short enough to fit on a single screen, which removes the "scroll-past-unrelated-code" cost that made the outfit cascade the hardest part of the file to debug.

### AC-2 · Monolithic Orchestrator with Tangled Responsibilities
Strength: Strong
Files: server/app.py
Symbol: resolve_job
Snippet:
```


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

    nsfw = False
    char_segs, char_negs = [], []
    for c in chars:
        if data.outfit_in_overlay(c.get("outfit") or {}) or data.entry_in_overlay(
                "expressions", c.get("expression") or ""):
            nsfw = True
        if c.get("source") == "secondbrain" and not c.get("text"):
            sb = secondbrain.get_character(c.get("name", ""))
            c = {**c, "text": sb.get("base", "")}
        seg, neg = assemble.character_segment(c)
        char_segs.append(seg)
        if neg:
            char_negs.append(neg)
    # per-character injected text, in card order — surfaced in /api/preview so
    # each character card can show exactly what it contributes to the prompt
    j["char_segments"] = [{"segment": s} for s in char_segs]

    # pose / skeleton
    #
    # Two independent things come out of here:
    #   - `frame`: COCO-18 keypoints that become the ControlNet skeleton PNG —
    #     this alone drives the pose.
    #   - `pose_tags`: ONLY orbit-camera facing words (from yaw/pitch). A library
    #     pose's own `tags` (e.g. "standing, arms at sides") are deliberately NOT
    #     put in the prompt — they used to fight an edited skeleton.
    # The editor sends its live 3-D rig projection as `skeleton.people` (already
    # oriented — do not re-project it). An API caller may instead pass only
    # `pose_position` (+ yaw/pitch); then we project the stored rig here.
    pose_tags = ""
    frame = None
    sk = spec.get("skeleton") or {}
    pos_name = spec.get("pose_position")
    yaw = float(spec.get("yaw", 0.0) or 0.0)
    pitch = float(spec.get("pitch", 0.0) or 0.0)
    az, el = yaw * 3.141592653589793, pitch * 1.15   # inverse of editor exportYawPitch
    if yaw or pitch:
        pose_tags = data.orientation_tags(yaw, pitch)
    pk = (spec.get("pose_keywords") or "").strip()
    if pk:
        pose_tags = data.normalize_kw(pose_tags, pk)
    if pos_name and data.cn_position_in_overlay(pos_name):
        nsfw = True
    if pos_name and not sk.get("people"):
        # API fallback: a pose name but no edited skeleton -> project the stored rig.
        _rig, _ = data.cn_canonical_rig(pos_name)
        if _rig is not None:
            frame = rig.project_frame(_rig, az, el, j["width"], j["height"])
    if sk.get("people"):
        frame = {"people": sk["people"],
                 "canvas_width": j["width"], "canvas_height": j["height"]}
    # skip ControlNet if the projected skeleton collapsed to nothing usable
    if frame is not None and _frame_has_joints(frame):
        frame = pose_ops.transform(frame, canvas_w=j["width"], canvas_h=j["height"])
        j["skeleton_png"] = pose_ops.render_png(frame, j["width"], j["height"])
    j["pose_tags"] = pose_tags
    j["scene_tags"] = scene_tags

    # A rear camera contradicts the character's face/front-of-body tokens.
    #  - 3/4 .. near-profile-back (|yaw| > 0.55): just drop gaze-at-camera.
    #  - full back view (|yaw| >= 0.8): strip every face + breast/front token so
    #    "back view" isn't outvoted; skipped if the subject is looking back.
    rear_neg = ""
    ay = abs(yaw)
    if ay > 0.55:
        strip = _rear_strip if ay >= 0.8 else _face_away
        char_segs = [strip(s) for s in char_segs]
        pose_tags = data.normalize_kw(strip(pose_tags),
                                      "(facing away:1.2), back turned to viewer")
        j["pose_tags"] = pose_tags
        j["char_segments"] = [{"segment": s} for s in char_segs]
        if ay >= 0.8 and not any(p in " ".join(char_segs).lower()
                                 for p in _LOOK_BACK):
            rear_neg = ("looking at viewer, eye contact, facing viewer, front view, "
                        "face focus, portrait")

    j["negative"] = data.normalize_kw(
        (spec.get("negative") or "").strip(), rear_neg, *char_negs, th["negative_add"],
        assemble.DEFAULT_NEGATIVE)

    if n == 2:
        j["region_a"] = char_segs[0]
        j["region_b"] = char_segs[1]
        j["base"] = data.normalize_kw(assemble.DEFAULT_QUALITY, scene_tags, pose_tags)
        j["prompt_used"] = {"region_a": j["region_a"], "region_b": j["region_b"],
                            "base": j["base"], "negative": j["negative"]}
    else:
        built = assemble.assemble({
            "characters": chars, "scene_tags": scene_tags, "pose_tags": pose_tags,
            "char_segments": char_segs})
        j["positive"] = built["positive"]
        j["prompt_used"] = {"positive": j["positive"], "negative": j["negative"]}

    if comfy.is_krea2(j["model"]):
        # Krea 2 Turbo is distilled: 8 steps, cfg 1, euler/simple. The SDXL defaults would ruin it, so an unspecified value gets the Krea
        # one and an explicit one is honoured. There is no ControlNet for it: a pose skeleton is dropped, loudly.
        for k, v in (("steps", 8), ("cfg", 1.0), ("sampler", "euler"), ("scheduler", "simple")):
            if spec.get(k) is None:
                j[k] = v
        if j.pop("skeleton_png", None):
            j["warnings"] = ["pose skeleton ignored: Krea 2 has no ControlNet in PromptForge yet"]
        text = (spec.get("prompt_text") or "").strip()
        lead = (spec.get("prompt_lead") or "").strip()
        if lead and not text:
            # a sentence from the caller (a StoryForge shot) + the characters' descriptions + the style, as one natural-language prompt
            text = comfy.krea2_prompt(lead, [x["segment"] for x in j["char_segments"]], j["scene_tags"])
        if text:    # Krea 2 reads natural language: a caller may hand over the sentence prompt instead of the tag soup
            j["positive"] = text
            j["prompt_used"] = {"positive": text, "negative": j["negative"]}

    j["spec"] = spec
    j["nsfw"] = nsfw
    return j
```

Problem:
The `resolve_job` function is not merely long — it is a monolithic orchestrator managing at least eight logically distinct concerns: default merging, model selection, theme/tag resolution, character processing, pose/skeleton handling, rear-view modifications, negative prompt construction, and two-character prompt assembly. Each section has its own edge cases and dependencies (e.g., pose logic modifies `j` and `nsfw`, rear-view logic modifies `char_segments` and `pose_tags`, Krea 2 handling is a conditional side-effect). This entanglement makes the function brittle to change, unreadable without context, and impossible to test in isolation.

Solution:
Extract each concern into a clearly named, self-contained function: `_merge_defaults`, `_select_model`, `_resolve_scene_tags`, `_process_characters`, `_process_pose`, `_build_negative_prompt`, `_assemble_positive_prompt`, `_detect_nsfw`, and `_apply_krea2_config`. Each function should operate on its own inputs and return only its own outputs, with minimal side effects on shared state. The main `resolve_job` should only orchestrate by assigning values to `j` and returning it — no logic should remain in the main body.

Benefits:
Decomposing this function improves testability by enabling unit tests for each concern (e.g., testing `_process_pose` with mocked `j` and `spec`). It improves readability by isolating each concern’s logic into a named, focused block. It improves maintainability by allowing changes to pose logic (e.g., adding new orientation tags) without risking unintended side effects on character segments or negative prompts. Reviewers can now focus on one concern at a time, reducing cognitive load and increasing confidence in changes.

VERDICT CONSTRAINT (mandatory for this task):
- The function snippet IS provided above in this prompt; you do not need to request it or assume it is missing.
- You MUST render exactly one verdict: GENUINE or FALSE POSITIVE.
- Refusals, hedging, or a third outcome (including "Uncertain", "I cannot determine", or any other qualifier) are NOT acceptable. Pick the one verdict that best fits the evidence in the snippet and commit to it.

VERDICT: GENUINE
