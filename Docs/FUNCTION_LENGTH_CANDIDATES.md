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
