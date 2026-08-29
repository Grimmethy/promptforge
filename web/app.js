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
    updateOrientLbl(); persist(); schedulePreview();
  });

  $('#addChar').onclick = () => { if (state.chars.length < 2) { state.chars.push(blankChar()); renderChars(); } };
  $('#poseLoad').onclick = loadPose;
  $('#poseSave').onclick = savePose;
  $('#orientReset').onclick = () => editor.resetView();
  $('#generate').onclick = generate;
  $('#reroll').onclick = () => { $('#seed').value = -1; generate(); };
  $$('#inputs select, #inputs input, #inputs textarea').forEach(n => n.addEventListener('change', () => { persist(); schedulePreview(); }));
  $('#theme').addEventListener('change', applyThemeHints);

  restore();
  renderChars();
  await comfyStatus();
  await loadGallery();
  schedulePreview();
  setInterval(comfyStatus, 15000);
}

function fillSelect(sel, opts, keep) {
  const n = $(sel), cur = keep ?? n.value;
  n.innerHTML = '';
  opts.forEach(o => n.append(el('option', {}, o)));
  if (opts.includes(cur)) n.value = cur;
}

async function comfyStatus() {
  try {
    const s = await fetch('/api/comfy/status').then(r => r.json());
    $('#comfyDot').className = 'dot ' + (s.up ? 'up' : 'down');
    $('#comfyMsg').textContent = s.up
      ? `ComfyUI ok · ${(s.vram_free / 1e9).toFixed(1)}/${(s.vram_total / 1e9).toFixed(0)} GB free`
      : 'ComfyUI unreachable';
    if (s.up && s.checkpoints?.length && $('#model').options.length && $('#model').value.startsWith('('))
      fillSelect('#model', s.checkpoints);
  } catch { $('#comfyMsg').textContent = '?'; }
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
  colors: EMPTY_COLORS(), expression: 'flat', weight: 1, text: '',
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
  const expSel = el('select', { onchange: e => { c.expression = e.target.value; schedulePreview(); } }, exprList.map(o => el('option', {}, o)));
  expSel.value = c.expression;
  const exprRow = () => el('div', {}, [el('label', {}, 'Expression'), expSel]);

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

    const gSel = dashSelect(L1, o.gender, v => { o.gender = v; o.genre = o.subgenre = o.key = ''; renderChars(); schedulePreview(); });
    const geSel = dashSelect(L2, o.genre, v => { o.genre = v; o.subgenre = o.key = ''; renderChars(); schedulePreview(); });
    const sgSel = dashSelect(L3, o.subgenre, v => { o.subgenre = v; o.key = ''; renderChars(); schedulePreview(); });
    const kSel = dashSelect(L4, o.key, v => { o.key = v; schedulePreview(); });

    const colorOpts = PRESETS.colors || [];
    const colorCell = slot => {
      const eff = c.colors[slot] || '';
      const hit = colorOpts.includes(eff);
      const sel = el('select', {}, [el('option', { value: '' }, '—'), ...colorOpts.map(x => el('option', {}, x))]);
      sel.value = hit ? eff : '';
      const txt = el('input', { type: 'text', placeholder: 'override', value: hit ? '' : eff });
      const apply = () => { c.colors[slot] = (txt.value.trim() || sel.value || ''); schedulePreview(); };
      sel.onchange = apply; txt.onchange = apply;
      return el('div', {}, [el('label', {}, slot), sel, txt]);
    };

    return [
      el('label', {}, 'Outfit'),
      el('div', { class: 'grid4' }, [gSel, geSel, sgSel, kSel]),
      el('label', {}, 'Clothing colour'),
      el('div', { class: 'grid3' }, ['base', 'contrast', 'detail'].map(colorCell)),
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
        renderChars(); schedulePreview();
      },
    }, names.map(n => el('option', {}, n)));
    nameSel.value = c.name;
    body.push(nameSel, ...dressControls(), exprRow(), presetEditor(c.name));
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
function syncEditor() {
  const n = state.chars.length;
  $('#editorSection').style.display = n ? '' : 'none';
  if (!n || !editor) return;
  const lib = (state._rigs && state._rigs.length === n) ? { rigs: state._rigs }
    : (lastLibFrame && (lastLibFrame.rigs || lastLibFrame.rig) ? lastLibFrame : null);
  editor.setCount(n, lib);
  state._rigs = null;
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
    el('span', { class: 'muted', style: 'font-size:10px;align-self:center' }, 'drag a joint to pose · hands/feet use IK'),
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
  lastLibFrame = d;                 // {rig} | {rigs}
  state.poseName = pos;
  editor.loadRig(d);
  buildEditorTools();
}

async function savePose() {
  const name = prompt('Save pose as (name — the pose only, no camera angle):',
    state.poseName || 'my_pose');
  if (!name) return;
  const tags = prompt('Pose tags for the prompt (e.g. "kneeling, hands on thighs"):', '') || '';
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
    expression: c.expression, weight: c.weight, text: c.text,
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
  // carry the pose name for its prompt tags even when an edited skeleton is sent
  if (state.poseName) spec.pose_position = state.poseName;
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
    if (j.status === 'error') { tile.querySelector('.spin').textContent = '⚠ ' + (j.error || 'error'); return; }
    if (j.status === 'done') {
      tile.replaceWith(...j.images.map(f => imgTile(f, { meta: { ...meta, seed: j.seed, spec: (buildSpec()), prompt_used: j.prompt_used } })));
      return;
    }
  }
  tile.querySelector('.spin').textContent = 'timed out';
}

function imgTile(file, item) {
  const m = item.meta || {};
  const menu = el('div', { class: 'menu row' }, [
    mBtn('♻', 'reuse settings', () => { if (m.spec) applySpec(m.spec); }),
    mBtn('⧉', 'copy prompt', () => { navigator.clipboard.writeText(JSON.stringify(m.prompt_used || {}, null, 1)); toast('prompt copied'); }),
    mBtn('🦴', 'send pose to editor', () => {
      const rigs = m.spec?.pose_rigs;
      if (rigs?.length && editor) { editor.loadRig({ rigs }); toast('pose loaded'); }
      else toast('no rig saved for this image');
    }),
    mBtn('⬇', 'download', () => { const a = el('a', { href: '/api/image/' + file, download: file }); a.click(); }),
    mBtn('🗑', 'delete', async () => { await fetch('/api/image/' + file, { method: 'DELETE' }); tile.remove(); }),
  ]);
  const img = el('img', { src: '/api/image/' + file, loading: 'lazy', onclick: () => openLB(file, m) });
  const tile = el('div', { class: 'tile' }, [img, menu]);
  return tile;
}
const mBtn = (t, title, fn) => el('button', { class: 'x', title, onclick: fn }, t);

function openLB(file, meta) {
  $('#lbImg').src = '/api/image/' + file;
  $('#lbInfo').innerHTML = '';
  const p = meta.prompt_used || {};
  $('#lbInfo').append(
    el('div', {}, `seed ${meta.seed ?? '?'} · ${meta.model || ''}`),
    el('pre', { html: (p.positive || [p.region_a, p.region_b, p.base].filter(Boolean).join('\n\nBREAK\n')) || '' }),
    el('pre', { html: p.negative || '' }));
  $('#lightbox').classList.add('on');
}
$('#lightbox').onclick = e => { if (e.target.id === 'lightbox') e.currentTarget.classList.remove('on'); };

async function loadGallery() {
  const items = await fetch('/api/gallery').then(r => r.json());
  const g = $('#gallery'); g.innerHTML = '';
  items.forEach(it => g.append(imgTile(it.file, it)));
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
  if (spec.layout) $('#layout').value = spec.layout;
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
    });
  } catch { }
}
function formValues() {
  const o = {};
  $$('#inputs select, #inputs input, #inputs textarea').forEach(n => { if (n.id) o[n.id] = n.type === 'checkbox' ? n.checked : n.value; });
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
  } catch { state.chars = []; }
}

boot();
