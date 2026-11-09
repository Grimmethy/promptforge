/* PromptForge Studio — SPA */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const el = (t, a = {}, kids = []) => {
  const n = document.createElement(t);
  for (const [k, v] of Object.entries(a)) {
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const c of [].concat(kids)) n.append(c);
  return n;
};
const toast = m => { const t = $('#toast'); t.textContent = m; t.classList.add('on'); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('on'), 2200); };

let PRESETS = null;
let editor = null;
let lastLibFrame = null;   // last library pose loaded, for seeding the editor

// ---- sampler fields follow the model family (SDXL numbers would ruin Krea 2 Turbo): the logic lives in model-defaults.js ----
const _SAMPLER_IDS = ['cfg', 'steps', 'sampler', 'scheduler'];
let _prevModel = '', _famStash = {};
const currentSampler = () => Object.fromEntries(_SAMPLER_IDS.map(k => [k, $('#' + k).value]));
const setSampler = v => { for (const k of _SAMPLER_IDS) $('#' + k).value = v[k]; };
function onModelChange() {
  const r = PFModelDefaults.swap(_prevModel, $('#model').value, currentSampler(), _famStash);
  _famStash = r.stash; _prevModel = $('#model').value;
  if (r.values) { setSampler(r.values); persist(); schedulePreview(); }
}

// ------------------------------------------------------------------ boot
async function boot() {
  PRESETS = await fetch('/api/presets').then(r => r.json());
  fillSelect('#theme', PRESETS.themes.map(t => t.name));
  fillSelect('#setting', ['none', ...PRESETS.backgrounds.filter(x => x !== 'none')]);
  fillSelect('#style', ['none', ...PRESETS.styles.filter(x => x !== 'none')]);
  fillSelect('#model', PRESETS.checkpoints.length ? PRESETS.checkpoints : ['(ComfyUI down)']);
  refreshPoseLib();

  editor = new PoseEditor($('#poseCanvas'), () => {
    const o = editor.exportYawPitch();
    state.yaw = o.yaw; state.pitch = o.pitch;
    state._rigs = editor.getRigs();   // keep the live pose in memory, not just localStorage
    updateOrientLbl(); persist(); schedulePreview();
  });

  $('#addChar').onclick = () => { if (state.chars.length < 2) { state.chars.push(blankChar()); renderChars(); } };
  $('#poseLoad').onclick = loadPose;
  $('#poseSave').onclick = savePose;
  $('#poseReset').onclick = () => {
    if (lastLibFrame && (lastLibFrame.rigs || lastLibFrame.rig)) editor.loadRig(lastLibFrame);
    else { editor.rigs = []; state._rigs = null; syncEditor(true); }   // no base -> default rig
    state._rigs = editor.getRigs(); persist(); schedulePreview();
    toast('pose reset');
  };
  $('#orientReset').onclick = () => editor.resetView();
  // Phone layout: the Create / Gallery bar shows one panel at a time (style.css, body.view-gallery). A choice survives a reload.
  const showView = v => { document.body.classList.toggle('view-gallery', v === 'gallery'); $$('#viewBar button').forEach(b => b.classList.toggle('on', b.dataset.view === v)); try { localStorage.setItem('pfView', v); } catch (e) { /* private mode: not remembered */ } };
  $$('#viewBar button').forEach(b => { b.onclick = () => showView(b.dataset.view); });
  showView((() => { try { return localStorage.getItem('pfView') === 'gallery' ? 'gallery' : 'create'; } catch (e) { return 'create'; } })());
  $('#generate').onclick = generate;
  $('#reroll').onclick = () => { $('#seed').value = -1; generate(); };
  $$('#inputs select, #inputs input, #inputs textarea').forEach(n => n.addEventListener('change', () => { persist(); schedulePreview(); }));
  $('#model').addEventListener('change', onModelChange);
  $('#theme').addEventListener('change', applyThemeHints);
  $('#nsfw').addEventListener('change', onNsfwToggle);
  installAddBoxes();   // after the blanket change-listener so add-box inputs are excluded

  restore();
  _prevModel = $('#model').value;
  if (PFModelDefaults.needsKreaFix(_prevModel, currentSampler())) { setSampler(PFModelDefaults.DEFAULTS.krea2); persist(); }   // saved Krea 2 + SDXL numbers
  $('#nsfw').checked = !!PRESETS.nsfw;   // server flag is the source of truth, not localStorage
  renderChars();
  await comfyStatus();
  await loadGallery();
  connectEvents();
  schedulePreview();
  setInterval(comfyStatus, 15000);
}

function fillSelect(sel, opts, keep) {
  const n = $(sel), cur = keep ?? n.value;
  n.innerHTML = '';
  opts.forEach(o => n.append(el('option', {}, o)));
  if (opts.includes(cur)) n.value = cur;
}

// --------------------------------------------------- inline add / edit / delete
// Every picker gets a small box to add a new entry, or pick an existing one and
// edit / delete its value — written straight into the backing YAML
// (POST /api/entry, comments preserved). On success we re-pull /api/presets.
async function reloadPresets() {
  PRESETS = await fetch('/api/presets').then(r => r.json());
  fillSelect('#theme', PRESETS.themes.map(t => t.name));
  fillSelect('#setting', ['none', ...PRESETS.backgrounds.filter(x => x !== 'none')]);
  fillSelect('#style', ['none', ...PRESETS.styles.filter(x => x !== 'none')]);
  if ($('#nsfw')) $('#nsfw').checked = !!PRESETS.nsfw;
  refreshPoseLib();
  renderChars();
  schedulePreview();
}

// NSFW toggle: flip the process-global server flag, then re-pull /api/presets so
// every dropdown + character card + inline edit-box list gains / loses the
// explicit entries at once. The prompt preview is server-rendered so it follows.
async function onNsfwToggle(e) {
  const enabled = e.target.checked;
  const r = await fetch('/api/nsfw', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  }).then(r => r.json()).catch(() => ({ enabled: !enabled }));
  $('#nsfw').checked = !!r.enabled;
  toast('NSFW content ' + (r.enabled ? 'shown' : 'hidden'));
  await reloadPresets();
  await loadGallery();
}


async function _entryFetch(body) {
  return fetch('/api/entry', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json()).catch(() => ({ error: 'request failed' }));
}

// Mirrors the main picker: whatever entry is selected there loads straight into
// the tag editor below it (no extra dropdown). "＋ new" is only for creating a
// brand-new entry.
// opts: { target, path?, getValue?, current, branchOnly?, nameIsValue?, onDone? }
//   current()   -> the key currently selected in the main picker ('' -> nothing)
//   getValue(k) -> sync value getter (outfit tree / colours); else lazy {action:"get"}
//   branchOnly  -> the current level has no tag value (outfit gender/genre/subgenre):
//                  add-name-only, no edit/delete of a value
function addEntryRow(opts) {
  let adding = false;
  const lbl = el('label', {});
  const nameI = el('input', { type: 'text', placeholder: 'new name' });
  const valI = opts.branchOnly ? null : el('textarea', { class: 'tagbox', rows: 3, placeholder: 'prompt tags (⌃/⌘-Enter to save)' });
  const saveBtn = el('button', { class: 'small' }, 'Save');
  const delBtn = el('button', { class: 'small del', title: 'delete entry' }, '🗑');
  const newBtn = el('button', { class: 'small', title: 'create a new entry' }, '＋ new');

  const curKey = () => {
    const k = (opts.current && opts.current()) || '';
    return (k && k !== 'none' && k !== '—') ? k : '';
  };

  const render = async () => {
    const sel = curKey();
    const editing = !adding && !!sel;
    lbl.textContent = editing ? ('editing: ' + sel) : 'new entry';
    nameI.style.display = editing ? 'none' : '';   // add mode types a name; edit shows the label
    if (valI && editing) valI.value = opts.getValue ? (opts.getValue(sel) || '')
      : ((await _entryFetch({ action: 'get', target: opts.target, key: sel })).value || '');
    saveBtn.textContent = editing ? 'Save' : 'Add';
    delBtn.style.display = editing ? '' : 'none';
    newBtn.style.display = editing ? '' : 'none';   // add mode is already "new"
  };
  const resync = () => { adding = false; nameI.value = ''; if (valI) valI.value = ''; render(); };

  const save = async () => {
    const editing = !adding && !!curKey();
    const key = editing ? curKey() : nameI.value.trim();
    if (!key) return;
    const body = { action: editing ? 'update' : 'add', target: opts.target, path: opts.path || [], key };
    if (valI) body.value = valI.value.trim() || (opts.nameIsValue ? key : '');
    else if (opts.nameIsValue) body.value = key;
    saveBtn.disabled = true;
    const r = await _entryFetch(body);
    saveBtn.disabled = false;
    if (r.error) return toast(r.error);
    toast((editing ? 'saved → ' : 'added → ') + key);
    adding = false; nameI.value = '';
    if (opts.onDone) opts.onDone(key);
    await reloadPresets();
    render();
  };
  const del = async () => {
    const key = curKey();
    if (!key || !confirm(`delete "${key}" from ${opts.target}?`)) return;
    delBtn.disabled = true;
    const r = await _entryFetch({ action: 'delete', target: opts.target, path: opts.path || [], key });
    delBtn.disabled = false;
    if (r.error) return toast(r.error);
    toast('deleted ' + key);
    if (opts.onDone) opts.onDone('');
    await reloadPresets();
    render();
  };
  saveBtn.onclick = save; delBtn.onclick = del;
  newBtn.onclick = () => { adding = true; nameI.value = ''; if (valI) valI.value = ''; render(); nameI.focus(); };
  nameI.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } });
  if (valI) valI.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); } });

  render();
  const kids = valI ? [lbl, nameI, valI, saveBtn, delBtn, newBtn] : [lbl, nameI, saveBtn, newBtn];
  const row = el('div', { class: 'addrow' }, kids);
  row._resync = resync;   // top-level boxes call this when their main picker changes
  return row;
}

function themeEditBox() {
  const F = ['style', 'setting_bias', 'palette', 'lighting', 'negative_add'];
  let adding = false;
  const lbl = el('label', {});
  const nameI = el('input', { type: 'text', placeholder: 'new theme name' });
  const fin = {}; F.forEach(k => fin[k] = el('textarea', { class: 'tagbox', rows: 2, placeholder: k }));
  const saveBtn = el('button', { class: 'small' }, 'Save');
  const delBtn = el('button', { class: 'small del' }, '🗑 delete');
  const newBtn = el('button', { class: 'small' }, '＋ new');

  const curKey = () => { const k = $('#theme').value; return (k && k !== 'none') ? k : ''; };
  const render = () => {
    const sel = curKey(), editing = !adding && !!sel;
    lbl.textContent = editing ? ('editing theme: ' + sel) : 'new theme';
    nameI.style.display = editing ? 'none' : '';
    const t = editing ? ((PRESETS.themes || []).find(x => x.name === sel) || {}) : {};
    if (editing) F.forEach(x => fin[x].value = t[x] || '');
    saveBtn.textContent = editing ? 'Save' : 'Add';
    delBtn.style.display = editing ? '' : 'none';
    newBtn.style.display = editing ? '' : 'none';
  };
  const resync = () => { adding = false; nameI.value = ''; F.forEach(x => fin[x].value = ''); render(); };

  saveBtn.onclick = async () => {
    const editing = !adding && !!curKey();
    const key = editing ? curKey() : nameI.value.trim();
    if (!key) return;
    const fields = {}; F.forEach(k => fields[k] = fin[k].value.trim());
    saveBtn.disabled = true;
    const r = await _entryFetch({ action: editing ? 'update' : 'add', target: 'themes', key, fields });
    saveBtn.disabled = false;
    if (r.error) return toast(r.error);
    toast((editing ? 'saved theme → ' : 'added theme → ') + key);
    adding = false; nameI.value = '';
    await reloadPresets();
    $('#theme').value = key; persist(); schedulePreview();
    render();
  };
  delBtn.onclick = async () => {
    const key = curKey();
    if (!key || !confirm(`delete theme "${key}"?`)) return;
    delBtn.disabled = true;
    const r = await _entryFetch({ action: 'delete', target: 'themes', key });
    delBtn.disabled = false;
    if (r.error) return toast(r.error);
    toast('deleted theme ' + key);
    await reloadPresets();
    render();
  };
  newBtn.onclick = () => { adding = true; nameI.value = ''; F.forEach(x => fin[x].value = ''); render(); nameI.focus(); };
  const box = el('details', { class: 'presetEd' }, [
    el('summary', {}, '✎ theme'), lbl, nameI,
    ...F.flatMap(k => [el('label', {}, k.replace(/_/g, ' ')), fin[k]]),
    el('div', { class: 'row' }, [saveBtn, delBtn, newBtn]),
  ]);
  box._resync = resync;
  return box;
}

async function newPresetCharacter() {
  const name = (prompt('New character name (saved as <name>.yaml):', '') || '').trim();
  if (!name) return;
  const tpl = await fetch('/api/character-template').then(r => r.json()).catch(() => ({}));
  let body = tpl.yaml || 'name: New Character\nweight: 1.0\nbase: >\n  1girl\nnegative_add: ""\n';
  body = body.replace(/^name:.*$/m, 'name: ' + name);
  const r = await fetch('/api/save-character', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: name, spec_yaml: body }),
  }).then(r => r.json()).catch(() => ({ error: 'request failed' }));
  if (r.error) return toast(r.error);
  const created = r.saved.split('/').pop().replace(/\.ya?ml$/, '');
  toast('created → ' + created + ' (open “✎ edit preset” to flesh it out)');
  await reloadPresets();
  if (state.chars.length < 2) {
    const c = blankChar(); c.source = 'preset'; c.name = created;
    const p = PRESETS.characters.preset.find(x => x.name === created);
    c.outfit = outfitFromPointer(p && p.default_outfit);
    state.chars.push(c); renderChars(); schedulePreview();
  }
}

function installAddBoxes() {
  const themeBox = themeEditBox();
  $('#theme').insertAdjacentElement('afterend', themeBox);
  $('#theme').addEventListener('change', () => themeBox._resync && themeBox._resync());

  const settingBox = addEntryRow({ target: 'backgrounds', current: () => $('#setting').value });
  $('#settingText').insertAdjacentElement('afterend', settingBox);
  $('#setting').addEventListener('change', () => settingBox._resync());

  const styleBox = addEntryRow({ target: 'styles', current: () => $('#style').value });
  $('#styleText').insertAdjacentElement('afterend', styleBox);
  $('#style').addEventListener('change', () => styleBox._resync());

  $('#addChar').insertAdjacentElement('afterend',
    el('button', { class: 'small', style: 'margin-left:6px', onclick: newPresetCharacter }, '＋ new preset'));
}

async function comfyStatus() {
  try {
    const s = await fetch('/api/comfy/status').then(r => r.json());
    $('#comfyDot').className = 'dot ' + (s.up ? 'up' : 'down');
    $('#comfyMsg').textContent = s.up
      ? `ComfyUI ok · ${(s.vram_free / 1e9).toFixed(1)}/${(s.vram_total / 1e9).toFixed(0)} GB free`
      + (s.lease_held ? ' · holding GPU lease' : '')
      : 'ComfyUI unreachable';
    if (s.up && s.checkpoints?.length && $('#model').options.length && $('#model').value.startsWith('('))
      { fillSelect('#model', s.checkpoints); _prevModel = $('#model').value; }
    renderComfyBtn(s);
  } catch { $('#comfyMsg').textContent = '?'; }
}

function renderComfyBtn(s) {
  let b = $('#comfyBtn');
  if (!b) {
    b = el('button', { id: 'comfyBtn', class: 'small', style: 'margin-left:8px' });
    $('#comfyMsg').insertAdjacentElement('afterend', b);
  }
  if (s.autostarting) { b.textContent = 'starting…'; b.disabled = true; return; }
  b.disabled = false;
  if (!s.up) {
    b.textContent = 'Start ComfyUI';
    b.onclick = async () => {
      b.disabled = true; b.textContent = 'starting…';
      const r = await fetch('/api/comfy/start', { method: 'POST' }).then(r => r.json()).catch(() => ({}));
      if (r.error) toast(r.error);
      comfyStatus();
    };
  } else if (s.managed) {
    b.textContent = 'Stop ComfyUI';
    b.onclick = async () => {
      b.disabled = true;
      await fetch('/api/comfy/stop', { method: 'POST' }).catch(() => {});
      comfyStatus();
    };
  } else {
    b.remove();  // running but not ours -- nothing to offer
  }
}

// ------------------------------------------------------------- characters
const state = { chars: [], yaw: 0, pitch: 0 };

// must match server/core/data.orientation_tags(): yaw>0 => subject faces left
function yawWord(y) {
  const a = Math.abs(y);
  if (a <= 0.12) return 'front';
  if (a >= 0.82) return 'from behind';
  return (a <= 0.4 ? '¾ ' : 'profile, ') + (y > 0 ? 'facing left' : 'facing right');
}
function pitchWord(p) {
  if (p >= 0.7) return 'overhead';
  if (p >= 0.3) return 'high angle';
  if (p <= -0.7) return "worm's-eye";
  if (p <= -0.3) return 'low angle';
  return 'eye level';
}
function updateOrientLbl() {
  const yl = $('#yawLbl'), pl = $('#pitchLbl');
  if (yl) yl.textContent = yawWord(state.yaw);
  if (pl) pl.textContent = pitchWord(state.pitch);
}
const EMPTY_OUTFIT = () => ({ gender: '', genre: '', subgenre: '', key: '' });
const EMPTY_COLORS = () => ({ base: '', contrast: '', detail: '' });
const outfitFromPointer = d => (Array.isArray(d) && d.length === 4)
  ? { gender: d[0], genre: d[1], subgenre: d[2], key: d[3] } : EMPTY_OUTFIT();

const blankChar = () => ({
  source: 'preset', name: (PRESETS.characters.preset[0] || {}).name || '',
  outfit: outfitFromPointer((PRESETS.characters.preset[0] || {}).default_outfit),
  colors: EMPTY_COLORS(), expression: 'flat', weight: 1, text: '', condition: '',
});

// Coerce a possibly-legacy char (string `outfit`, missing `colors`) to the
// current shape. A pre-cascade outfit string can't be mapped into the tree, so
// it's dropped — the character comes up undressed until re-picked.
function normalizeChar(c) {
  c.outfit = (c.outfit && typeof c.outfit === 'object')
    ? { ...EMPTY_OUTFIT(), ...c.outfit } : EMPTY_OUTFIT();
  c.colors = (c.colors && typeof c.colors === 'object')
    ? { ...EMPTY_COLORS(), ...c.colors } : EMPTY_COLORS();
  return c;
}

function renderChars() {
  const wrap = $('#charCards'); wrap.innerHTML = '';
  state.chars.forEach((c, i) => wrap.append(charCard(c, i)));
  $('#addChar').disabled = state.chars.length >= 2;
  $('#charHint').textContent = state.chars.length === 0 ? '(none → scene only)' : '';
  syncEditor();
  persist();
}

// One <select> with a leading "—" (value "") sentinel. `opts` are label strings;
// option value === label (matches the rest of the app), the sentinel is value "".
function dashSelect(opts, val, on) {
  const s = el('select', { onchange: e => on(e.target.value) },
    [el('option', { value: '' }, '—'), ...opts.map(x => el('option', {}, x))]);
  s.value = opts.includes(val) ? val : '';
  return s;
}

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
    const L3 = o.genre ? Object.keys((T[o.gender] || {})[o.genre] || {}) : [];
    if (o.subgenre && !L3.includes(o.subgenre)) { o.subgenre = o.key = ''; }
    const L4 = o.subgenre ? Object.keys(((T[o.gender] || {})[o.genre] || {})[o.subgenre] || {}) : [];
    if (o.key && !L4.includes(o.key)) { o.key = ''; }

    // The tag editor mirrors the cascade: a full path (gender>genre>subgenre>key)
    // loads that outfit's tags to edit; a partial path is "add a child here".
    const oPath = [];
    if (o.gender) oPath.push(o.gender);
    if (o.gender && o.genre) oPath.push(o.genre);
    if (o.gender && o.genre && o.subgenre) oPath.push(o.subgenre);
    const lvl = oPath.length;   // 0..3 -> gender / genre / subgenre / outfit
    const leafMap = () => (((T[o.gender] || {})[o.genre] || {})[o.subgenre]) || {};
    const outfitAdd = addEntryRow({
      target: 'outfits', path: oPath,
      branchOnly: lvl < 3,
      getValue: lvl === 3 ? (k => leafMap()[k] || '') : undefined,
      current: lvl === 3 ? (() => o.key) : (() => ''),
      onDone: k => { [() => o.gender = k, () => o.genre = k, () => o.subgenre = k, () => o.key = k][lvl](); },
    });

    const gSel = dashSelect(L1, o.gender, v => { o.gender = v; o.genre = o.subgenre = o.key = ''; renderChars(); schedulePreview(); });
    const geSel = dashSelect(L2, o.genre, v => { o.genre = v; o.subgenre = o.key = ''; renderChars(); schedulePreview(); });
    const sgSel = dashSelect(L3, o.subgenre, v => { o.subgenre = v; o.key = ''; renderChars(); schedulePreview(); });
    // leaf pick doesn't rebuild the card — just re-point the tag editor at it
    const kSel = dashSelect(L4, o.key, v => { o.key = v; outfitAdd._resync(); schedulePreview(); });

    const colorOpts = PRESETS.colors || [];
    const colorAdd = addEntryRow({ target: 'colors', getValue: k => k, nameIsValue: true,
      current: () => (PRESETS.colors || []).includes(c.colors.base) ? c.colors.base : '' });
    const colorCell = slot => {
      const eff = c.colors[slot] || '';
      const hit = colorOpts.includes(eff);
      const sel = el('select', {}, [el('option', { value: '' }, '—'), ...colorOpts.map(x => el('option', {}, x))]);
      sel.value = hit ? eff : '';
      const txt = el('input', { type: 'text', placeholder: 'override', value: hit ? '' : eff });
      const apply = () => { c.colors[slot] = (txt.value.trim() || sel.value || ''); if (slot === 'base') colorAdd._resync(); schedulePreview(); };
      sel.onchange = apply; txt.onchange = apply;
      return el('div', {}, [el('label', {}, slot), sel, txt]);
    };

    return [
      el('label', {}, 'Outfit'),
      el('div', { class: 'grid4' }, [gSel, geSel, sgSel, kSel]),
      outfitAdd,
      el('label', {}, 'Clothing colour'),
      el('div', { class: 'grid3' }, ['base', 'contrast', 'detail'].map(colorCell)),
      colorAdd,
    ];
  };

  const body = [];
  if (c.source === 'preset') {
    const names = PRESETS.characters.preset.map(p => p.name);
    const nameSel = el('select', {
      onchange: e => {
        c.name = e.target.value;
        const p = PRESETS.characters.preset.find(x => x.name === c.name);
        c.outfit = outfitFromPointer(p && p.default_outfit);
        c.condition = '';   // each character has its own ladder
        renderChars(); schedulePreview();
      },
    }, names.map(n => el('option', {}, n)));
    nameSel.value = c.name;
    // Condition ladder from CharacterForge (e.g. fed -> tired -> bruised -> gaunt); only
    // characters that define one get the dropdown. "(none)" renders the base look.
    const ladder = (PRESETS.characters.preset.find(x => x.name === c.name) || {}).conditions || [];
    if (!ladder.some(r => r.id === c.condition)) c.condition = '';
    const condRow = ladder.length ? [el('label', {}, 'Condition'), (() => {
      const s = el('select', { onchange: e => { c.condition = e.target.value; schedulePreview(); } },
        [el('option', { value: '' }, '(none)'), ...ladder.map(r => el('option', { value: r.id }, r.name))]);
      s.value = c.condition; return s;
    })()] : [];
    body.push(nameSel, ...condRow, ...dressControls(), exprRow(), presetEditor(c.name));
  } else if (c.source === 'secondbrain') {
    const names = PRESETS.characters.secondbrain.map(p => p.name);
    if (!names.length) body.push(el('div', { class: 'muted' }, 'No SECOND_BRAIN_DIR/Characters/*.md found.'));
    else {
      const s = el('select', { onchange: e => { c.name = e.target.value; schedulePreview(); } }, names.map(n => el('option', {}, n)));
      s.value = names.includes(c.name) ? c.name : names[0]; c.name = s.value;
      body.push(s, ...dressControls(), exprRow());
    }
  } else {
    const ta = el('textarea', { placeholder: '1girl, describe the character in danbooru tags…', onchange: e => { c.text = e.target.value; schedulePreview(); } });
    ta.value = c.text; body.push(ta, ...dressControls(), exprRow());
  }

  // the exact text this character injects into the prompt (filled by preview())
  body.push(el('label', {}, 'injected prompt'),
            el('div', { class: 'inject', id: 'inj' + i }, state._segs?.[i]?.segment || '…'));

  const w = el('input', { type: 'range', min: 0.5, max: 1.6, step: 0.05, value: c.weight, oninput: e => { c.weight = +e.target.value; wl.textContent = 'weight ' + c.weight.toFixed(2); }, onchange: schedulePreview });
  const wl = el('label', {}, 'weight ' + (+c.weight).toFixed(2));
  body.push(wl, w);

  return el('div', { class: 'card' }, [hd, ...body]);
}

// collapsible raw-YAML editor for a preset character; saves back to data/characters/
function presetEditor(name) {
  const ta = el('textarea', { class: 'yaml', rows: 14, spellcheck: 'false', placeholder: 'loading…' });
  const load = async () => {
    const d = await fetch(`/api/character/preset/${encodeURIComponent(name)}?raw=1`).then(r => r.json());
    ta.value = d.yaml ?? ('# ' + (d.error || 'not found'));
  };
  const save = async () => {
    const r = await fetch('/api/save-character', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: name, spec_yaml: ta.value }),
    }).then(r => r.json());
    if (r.error) return toast(r.error);
    toast('preset saved → ' + r.saved.split('/').pop());
    PRESETS = await fetch('/api/presets').then(r => r.json());
    renderChars(); schedulePreview();
  };
  const det = el('details', { class: 'presetEd' }, [
    el('summary', {}, '✎ edit preset'),
    ta,
    el('div', { class: 'row' }, [
      el('button', { class: 'small', onclick: save }, 'Save'),
      el('button', { class: 'small', onclick: load }, 'Reload'),
    ]),
  ]);
  det.addEventListener('toggle', () => { if (det.open && !ta.value) load(); });
  return det;
}

// ------------------------------------------------------------- skeleton
// `state._rigs` is the durable in-memory copy of the working pose (kept fresh by the
// editor onChange callback). Ordinary re-renders must NOT re-seed from `lastLibFrame` —
// that discarded manual tweaks on every outfit/name change. `lastLibFrame` is only used
// for an explicit "Reset pose" (fromLib) or a genuinely empty editor.
function syncEditor(fromLib = false) {
  const n = state.chars.length;
  $('#editorSection').style.display = n ? '' : 'none';
  if (!n || !editor) return;
  const havLib = lastLibFrame && (lastLibFrame.rigs || lastLibFrame.rig);
  if (fromLib && havLib) {
    editor.setCount(n, lastLibFrame);
  } else if (state._rigs && state._rigs.length === n) {
    editor.setCount(n, { rigs: state._rigs });          // carry the live pose (normal path)
  } else if (state._rigs && state._rigs.length) {
    editor.setCount(n, null);                           // count changed: keep figure A…
    editor.loadRig({ rigs: state._rigs });              // …then re-apply the saved figures
  } else if (editor.rigs.length === 0 && havLib) {
    editor.setCount(n, lastLibFrame);
  } else {
    editor.setCount(n, null);                           // keep whatever the editor holds
  }
  state._rigs = editor.getRigs();                       // keep it populated, never null
  editor.setCam(state.yaw * Math.PI, state.pitch * 1.15);   // inverse of exportYawPitch
  updateOrientLbl();
  buildEditorTools();
  refreshPoseLib();
}

function buildEditorTools() {
  const wrap = $('#editorTools'); wrap.innerHTML = '';
  wrap.append(el('div', { class: 'undoredo' }, [
    el('button', { class: 'small', onclick: () => editor.undo() }, '↶ undo'),
    el('button', { class: 'small', onclick: () => editor.redo() }, '↷ redo'),
    el('span', { class: 'muted', style: 'font-size:10px;align-self:center' }, 'drag a joint to pose · hands/feet use IK · scroll a joint = depth · drag empty space to orbit'),
  ]));
  editor.rigs.forEach((r, fi) => {
    const head = el('div', { class: 'row' }, [
      el('span', { class: 'muted', style: 'font-size:10px;flex:1' }, editor.rigs.length > 1 ? (fi === 0 ? 'Figure A' : 'Figure B') : 'Figure'),
      el('button', { class: 'small', onclick: () => editor.scaleRig(fi, 1.06) }, 'grow'),
      el('button', { class: 'small', onclick: () => editor.scaleRig(fi, 0.94) }, 'shrink'),
      el('button', { class: 'small', onclick: () => editor.mirror(fi) }, '⇋ mirror'),
    ]);
    const lenRows = RIG.lengthKeys.map(key => {
      const [lo, hi] = RIG.range[key];
      return el('div', { class: 'lenrow' }, [
        el('label', {}, RIG.bonePretty[key]),
        el('input', {
          type: 'range', min: lo, max: hi, step: 0.005, value: r.lengths[key],
          oninput: e => editor.setLength(fi, key, +e.target.value),
        }),
      ]);
    });
    const lens = el('details', { class: 'lens' }, [
      el('summary', {}, 'bone lengths'), ...lenRows,
    ]);
    wrap.append(el('div', { class: 'fig' }, [head, lens]));
  });
}

function refreshPoseLib() {
  const n = state.chars.length || 1;
  const opts = PRESETS.cn_positions.filter(p => p.people === n);
  const sel = $('#poseLib'); sel.innerHTML = '';
  opts.forEach(p => sel.append(el('option', { value: p.name }, p.name)));
  if (!opts.length) sel.append(el('option', {}, '(no library poses for ' + n + ')'));
  // Drop a remembered pose that no longer fits the current character count.
  if (state.poseName && [...sel.options].some(o => o.value === state.poseName)) sel.value = state.poseName;
  else state.poseName = null;
}

async function loadPose() {
  const pos = $('#poseLib').value || '';
  if (!pos) return;
  const d = await fetch(`/api/pose/${encodeURIComponent(pos)}`).then(r => r.json());
  if (d.error) return toast(d.error);
  lastLibFrame = d;                 // {rig} | {rigs} — the base for "Reset pose"
  state.poseName = pos;
  editor.loadRig(d);
  if ($('#poseKeywords')) $('#poseKeywords').value = d.tags || '';   // pose's saved keywords
  state._rigs = editor.getRigs(); persist();
  buildEditorTools();
}

async function savePose() {
  const name = prompt('Save pose as (name — the pose only, no camera angle):',
    state.poseName || 'my_pose');
  if (!name) return;
  const tags = ($('#poseKeywords')?.value || '').trim();   // stored as the pose's keywords
  const rigs = editor.getRigs();
  const body = { position: name, position_tags: tags, people: rigs.length, overwrite: true };
  if (rigs.length > 1) body.rigs = rigs; else body.rig = rigs[0];
  const r = await fetch('/api/save-pose', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json());
  if (r.error) return toast(r.error);
  toast('saved → ' + r.saved.split('/').pop());
  PRESETS = await fetch('/api/presets').then(r => r.json());
  refreshPoseLib();
}

// ------------------------------------------------------------- spec + preview
function buildSpec() {
  const chars = state.chars.map(c => ({
    source: c.source, name: c.name,
    outfit: { ...c.outfit }, colors: { ...c.colors },
    expression: c.expression, weight: c.weight, text: c.text, condition: c.condition || '',
  }));
  const spec = {
    characters: chars,
    theme: $('#theme').value,
    setting: [$('#setting').value === 'none' ? '' : $('#setting').value, $('#settingText').value].filter(Boolean).join(', '),
    style: [$('#style').value === 'none' ? '' : $('#style').value, $('#styleText').value].filter(Boolean).join(', '),
    negative: $('#negative').value,
    model: $('#model').value.startsWith('(') ? undefined : $('#model').value,
    cfg: +$('#cfg').value, steps: +$('#steps').value,
    sampler: $('#sampler').value, scheduler: $('#scheduler').value,
    seed: +$('#seed').value, batch: +$('#batch').value,
    width: +$('#width').value, height: +$('#height').value,
    cn_strength: +$('#cn_strength').value, cn_end: +$('#cn_end').value,
    layout: $('#layout').value,
  };
  if (state.chars.length && editor) {
    // the editor already projected its 3-D rig through the orbit camera
    spec.skeleton = { people: editor.getFrame(spec.width, spec.height).people };
    spec.pose_rigs = editor.getRigs();   // so a re-used image restores the rig
  }
  // carry the pose name so a re-used image / API caller can resolve the stored
  // rig; its tags are no longer injected into the prompt
  if (state.poseName) spec.pose_position = state.poseName;
  const pk = ($('#poseKeywords')?.value || '').trim();
  if (pk) spec.pose_keywords = pk;   // the only pose text that reaches the prompt
  // orbit camera -> exported facing (skeleton is pre-oriented; these drive tags)
  if (state.yaw) spec.yaw = state.yaw;
  if (state.pitch) spec.pitch = state.pitch;
  return spec;
}

let _pt;
function schedulePreview() { clearTimeout(_pt); _pt = setTimeout(preview, 350); }
async function preview() {
  try {
    const r = await fetch('/api/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildSpec()),
    }).then(r => r.json());
    if (r.error) { $('#preview').textContent = 'error: ' + r.error; return; }
    state._segs = r.char_segments || [];
    state._segs.forEach((s, i) => { const n = $('#inj' + i); if (n) n.textContent = s.segment || '(empty)'; });
    const p = r.prompt_used;
    const pose = r.pose_tags ? `POSE TAGS\n${r.pose_tags}\n\n` : '';
    $('#preview').textContent = pose + (p.positive
      ? `POSITIVE\n${p.positive}\n\nNEGATIVE\n${p.negative}`
      : `A (${r.n_chars} chars)\n${p.region_a}\n\nB\n${p.region_b}\n\nBASE\n${p.base}\n\nNEGATIVE\n${p.negative}`);
  } catch { }
}

function applyThemeHints() {
  const t = PRESETS.themes.find(x => x.name === $('#theme').value);
  if (!t) return;
  // only fill the free-text boxes as hints; the explicit dropdowns still win
  if (t.style && $('#style').value === 'none' && !$('#styleText').value) $('#styleText').value = t.style;
  if (t.setting_bias && $('#setting').value === 'none' && !$('#settingText').value) $('#settingText').value = t.setting_bias;
  schedulePreview();
}

// ------------------------------------------------------------- generate
async function generate() {
  const btn = $('#generate'); btn.disabled = true; btn.textContent = 'Queued…';
  try {
    const r = await fetch('/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildSpec()),
    }).then(r => r.json());
    if (r.error) { toast(r.error); return; }
    const tile = runningTile(r.job_id);
    $('#gallery').prepend(tile);
    pollJob(r.job_id, tile, r);
  } finally { btn.disabled = false; btn.textContent = 'Generate'; }
}

function runningTile(jid) {
  const t = el('div', { class: 'tile', 'data-job': jid },
    [el('div', { class: 'spin' }, 'generating…'), el('div', { class: 'bar', style: 'width:5%' })]);
  return t;
}

async function pollJob(jid, tile, meta) {
  for (let i = 0; i < 400; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const j = await fetch('/api/job/' + jid).then(r => r.json());
    const bar = tile.querySelector('.bar');
    if (j.status === 'running' && bar) bar.style.width = Math.min(90, 10 + i * 4) + '%';
    if (j.status === 'starting-comfyui') {
      tile.querySelector('.spin').textContent = j.comfy_step || 'starting ComfyUI…';
      continue;
    }
    if (j.status === 'error') { tile.querySelector('.spin').textContent = '⚠ ' + (j.error || 'error'); return; }
    if (j.status === 'done') {
      // a regenerate job has no form spec to carry: let the tile fetch the real sidecar lazily instead
      tile.replaceWith(...j.images.map(f => imgTile(f, meta.lazy ? null : { ...meta, seed: j.seed, spec: buildSpec(), prompt_used: j.prompt_used })));
      return;
    }
  }
  tile.querySelector('.spin').textContent = 'timed out';
}

// `meta` may be pre-supplied (fresh generation); otherwise it's fetched from
// /api/gallery-meta only when a menu action or the lightbox actually needs it.
const _shown = new Set();   // gallery files that have a tile on the page (so a pushed event never doubles one)
function imgTile(file, meta = null) {
  _shown.add(file);
  let _m = meta;
  const getMeta = async () => _m || (_m = await fetch('/api/gallery-meta/' + file).then(r => r.json()).catch(() => ({})));
  const menu = el('div', { class: 'menu row' }, [
    mBtn('♻', 'reuse settings', async () => { const m = await getMeta(); if (m.spec) applySpec(m.spec); }),
    mBtn('🎨', 'regenerate from this image', async () => openRegen(file, await getMeta())),
    mBtn('⧉', 'copy prompt', async () => { const m = await getMeta(); navigator.clipboard.writeText(JSON.stringify(m.prompt_used || {}, null, 1)); toast('prompt copied'); }),
    mBtn('🦴', 'send pose to editor', async () => {
      const rigs = (await getMeta()).spec?.pose_rigs;
      if (rigs?.length && editor) { editor.loadRig({ rigs }); toast('pose loaded'); }
      else toast('no rig saved for this image');
    }),
    mBtn('⬇', 'download', () => { const a = el('a', { href: '/api/image/' + file, download: file.split('/').pop() }); a.click(); }),
    mBtn('🗑', 'delete', async () => { await fetch('/api/image/' + file, { method: 'DELETE' }); _shown.delete(file); tile.remove(); _galOffset = Math.max(0, _galOffset - 1); _galTotal = Math.max(0, _galTotal - 1); }),
  ]);
  const img = el('img', { src: '/api/thumb/' + file, loading: 'lazy', onclick: async () => openLB(file, await getMeta()) });
  const done = () => tile.classList.remove('loading');
  img.addEventListener('load', done); img.addEventListener('error', done);
  const tile = el('div', { class: 'tile loading', 'data-file': file }, [img, menu]);
  return tile;
}
const mBtn = (t, title, fn) => el('button', { class: 'x', title, onclick: fn }, t);

function openLB(file, meta) {
  $('#lbImg').src = '/api/image/' + file;               // full-res in the lightbox
  $('#lbInfo').innerHTML = '';
  const p = (meta || {}).prompt_used || {};
  $('#lbInfo').append(
    el('div', {}, `seed ${meta?.seed ?? '?'} · ${meta?.model || ''}`),
    el('button', { class: 'x', onclick: () => { $('#lightbox').classList.remove('on'); openRegen(file, meta); } }, '🎨 Regenerate from this image'),
    el('pre', { html: (p.positive || [p.region_a, p.region_b, p.base].filter(Boolean).join('\n\nBREAK\n')) || '' }),
    el('pre', { html: p.negative || '' }));
  $('#lightbox').classList.add('on');
}
$('#lightbox').onclick = e => { if (e.target.id === 'lightbox') e.currentTarget.classList.remove('on'); };

// ---- regenerate from a gallery image (image-to-image through /api/regenerate, the picture is read on the server) ----
let _regenFile = null;
function openRegen(file, meta) {
  _regenFile = file;
  const m = meta || {};
  const p = m.prompt_used || {};
  $('#regenImg').src = '/api/image/' + file;
  $('#regenPrompt').value = p.positive || [p.region_a, p.region_b, p.base].filter(Boolean).join('\n') || '';
  const sel = $('#regenModel');
  sel.innerHTML = '';
  for (const o of $('#model').options) sel.append(el('option', { value: o.value }, o.textContent));
  const wanted = m.model || m.spec?.model;
  const mainModel = $('#model').value;
  sel.value = [...sel.options].some(o => o.value === wanted) ? wanted : (mainModel.startsWith('(') ? '' : mainModel);
  $('#regenDenoise').value = '0.65'; $('#regenDenoiseVal').textContent = '0.65';
  $('#regenBg').value = ''; $('#regenSeed').value = '-1';
  $('#regenPanel').classList.add('on');
  $('#regenPrompt').focus();
}
function closeRegen() { $('#regenPanel').classList.remove('on'); _regenFile = null; }
async function runRegen() {
  const prompt = $('#regenPrompt').value.trim();
  if (!_regenFile) return;
  if (!prompt) { toast('write a prompt first'); return; }
  const fd = new FormData();
  fd.append('from_gallery', _regenFile);
  fd.append('prompt', prompt);
  fd.append('denoise', $('#regenDenoise').value);
  fd.append('seed', $('#regenSeed').value || '-1');
  if ($('#regenModel').value) fd.append('model', $('#regenModel').value);
  if ($('#regenBg').value.trim()) fd.append('background', $('#regenBg').value.trim());
  const btn = $('#regenRun'); btn.disabled = true;
  try {
    const r = await fetch('/api/regenerate', { method: 'POST', body: fd }).then(r => r.json());
    if (r.error) { toast(r.error); return; }
    const tile = runningTile(r.job_id);
    $('#gallery').prepend(tile);
    pollJob(r.job_id, tile, { ...r, lazy: true });
    closeRegen();
    toast('regenerating…');
  } catch (e) { toast('regenerate failed: ' + e); }
  finally { btn.disabled = false; }
}
$('#regenRun').onclick = runRegen;
$('#regenCancel').onclick = closeRegen;
$('#regenDenoise').oninput = e => { $('#regenDenoiseVal').textContent = Number(e.target.value).toFixed(2); };
$('#regenPanel').onclick = e => { if (e.target.id === 'regenPanel') closeRegen(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('#regenPanel').classList.contains('on')) closeRegen(); });

// ---- paged gallery with a shimmering skeleton loader ----
let _galOffset = 0, _galTotal = 0, _galLoading = false;
const _GAL_PAGE = 48;
let _galObserver = null;

function _skelTiles(n) {
  return Array.from({ length: n }, () => el('div', { class: 'tile skel' }));
}

async function loadGallery(reset = true) {
  if (_galLoading) return;
  const g = $('#gallery');
  if (reset) {
    _galOffset = 0; _galTotal = 0;
    g.innerHTML = '';
    g.append(..._skelTiles(12));   // placeholders while page 1 loads
  }
  if (_galOffset && _galOffset >= _galTotal) return;
  _galLoading = true;
  try {
    const r = await fetch(`/api/gallery?offset=${_galOffset}&limit=${_GAL_PAGE}`)
      .then(r => r.json()).catch(err => { console.error('loadGallery fetch failed:', err); return { items: [], total: 0 }; });
    if (reset) g.innerHTML = '';
    const old = $('#gallerySentinel'); if (old) old.remove();
    (r.items || []).forEach(it => g.append(imgTile(it.file)));
    _galOffset += (r.items || []).length;
    _galTotal = r.total || _galOffset;
    if (_galOffset < _galTotal) {
      const s = el('div', { id: 'gallerySentinel', style: 'grid-column:1/-1;height:1px' });
      g.append(s);
      (_galObserver || (_galObserver = new IntersectionObserver(
        es => { if (es.some(e => e.isIntersecting)) loadGallery(false); },
        { root: g, rootMargin: '800px' }))).observe(s);
    }
  } finally { _galLoading = false; }
}

// ---- live gallery: the server pushes finished and deleted images, so no refresh and no polling ----
function addNewTiles(files) {
  const g = $('#gallery');
  const fresh = files.filter(f => !_shown.has(f));
  if (!fresh.length) return;
  for (const f of fresh.slice().reverse()) g.prepend(imgTile(f));   // keep the batch's own order, newest group first
  _galOffset += fresh.length; _galTotal += fresh.length;
}

async function reconcileGallery() {   // once after a dropped stream comes back: pick up anything missed meanwhile
  const r = await fetch(`/api/gallery?offset=0&limit=${_GAL_PAGE}`).then(r => r.json()).catch(() => null);
  if (r && r.items) addNewTiles(r.items.map(it => it.file));
}

function connectEvents() {
  if (!window.EventSource) return;
  const es = new EventSource('/api/events');
  let dropped = false;
  es.addEventListener('gallery-new', e => {
    const d = JSON.parse(e.data);
    if (d.nsfw && !$('#nsfw').checked) return;
    addNewTiles(d.images || []);
  });
  es.addEventListener('gallery-removed', e => {
    const f = JSON.parse(e.data).file;
    _shown.delete(f);
    const gone = $$('#gallery .tile').filter(n => n.dataset.file === f);
    gone.forEach(n => n.remove());
    _galOffset = Math.max(0, _galOffset - gone.length); _galTotal = Math.max(0, _galTotal - gone.length);   // keep paging in step
  });
  es.onerror = () => { dropped = true; };      // EventSource reconnects by itself (server sends retry: 3000)
  es.onopen = () => { if (dropped) { dropped = false; reconcileGallery(); } };
}

// ------------------------------------------------------------- persistence
function applySpec(spec) {
  state.chars = (spec.characters || []).map(c => normalizeChar({ ...blankChar(), ...c }));
  for (const k of ['theme', 'model', 'sampler', 'scheduler'])
    if (spec[k] != null && $('#' + k)) $('#' + k).value = spec[k];
  for (const k of ['cfg', 'steps', 'seed', 'batch', 'width', 'height', 'cn_strength', 'cn_end'])
    if (spec[k] != null && $('#' + k)) $('#' + k).value = spec[k];
  if (spec.style) $('#styleText').value = spec.style;
  if (spec.setting) $('#settingText').value = spec.setting;
  if (spec.negative) $('#negative').value = spec.negative;
  _prevModel = $('#model').value;   // the model was set without a change event: keep the family tracker in step (the spec carries its own numbers, so no swap)
  if (spec.layout) $('#layout').value = spec.layout;
  if ($('#poseKeywords')) $('#poseKeywords').value = spec.pose_keywords || '';
  state.poseName = spec.pose_position || null;
  state.yaw = spec.yaw || 0;
  state.pitch = spec.pitch || 0;
  state._rigs = (spec.pose_rigs && spec.pose_rigs.length) ? spec.pose_rigs : null;
  renderChars();   // syncEditor() picks up state._rigs + camera
  toast('settings restored'); schedulePreview();
}

function persist() {
  try {
    localStorage.pf_state = JSON.stringify({
      chars: state.chars, form: formValues(),
      poseName: state.poseName, yaw: state.yaw, pitch: state.pitch,
      rigs: editor ? editor.getRigs() : null,
      libFrame: lastLibFrame,
    });
  } catch { }
}
function formValues() {
  const o = {};
  $$('#inputs select, #inputs input, #inputs textarea').forEach(n => {
    if (n.id && n.id !== 'nsfw') o[n.id] = n.type === 'checkbox' ? n.checked : n.value;   // #nsfw lives on the server
  });
  return o;
}
function restore() {
  try {
    const s = JSON.parse(localStorage.pf_state || '{}');
    if (s.form) for (const [k, v] of Object.entries(s.form)) { const n = $('#' + k); if (n) n.value = v; }
    state.chars = (s.chars || []).map(normalizeChar);
    state.poseName = s.poseName || null;
    state.yaw = s.yaw || 0;
    state.pitch = s.pitch || 0;
    state._rigs = (s.rigs && s.rigs.length) ? s.rigs : null;
    lastLibFrame = s.libFrame || null;
  } catch { state.chars = []; }
}

boot();
