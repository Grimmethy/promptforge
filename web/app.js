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

  editor = new PoseEditor($('#poseCanvas'), () => { schedulePreview(); });

  $('#addChar').onclick = () => { if (state.chars.length < 2) { state.chars.push(blankChar()); renderChars(); } };
  $('#poseLoad').onclick = loadPose;
  $('#poseSave').onclick = savePose;
  const onOrient = () => {
    state.yaw = +$('#yawSlider').value; state.pitch = +$('#pitchSlider').value;
    editor.setOrient(state.yaw, state.pitch); updateOrientLbl(); persist(); schedulePreview();
  };
  $('#yawSlider').addEventListener('input', onOrient);
  $('#pitchSlider').addEventListener('input', onOrient);
  $('#orientReset').onclick = () => {
    $('#yawSlider').value = 0; $('#pitchSlider').value = 0; onOrient();
  };
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

function yawWord(y) {
  if (y <= -0.55) return 'left profile';
  if (y < -0.15) return '3/4 left';
  if (y < 0.15) return 'front';
  if (y < 0.55) return '3/4 from behind';
  return 'facing away';
}
function pitchWord(p) {
  if (p >= 0.4) return 'high angle';
  if (p <= -0.4) return 'low angle';
  return 'eye level';
}
function updateOrientLbl() {
  const yl = $('#yawLbl'), pl = $('#pitchLbl');
  if (yl) yl.textContent = yawWord(state.yaw);
  if (pl) pl.textContent = pitchWord(state.pitch);
}
const blankChar = () => ({ source: 'preset', name: (PRESETS.characters.preset[0] || {}).name || '', outfit: 'default', expression: 'default', weight: 1, text: '' });

function renderChars() {
  const wrap = $('#charCards'); wrap.innerHTML = '';
  state.chars.forEach((c, i) => wrap.append(charCard(c, i)));
  $('#addChar').disabled = state.chars.length >= 2;
  $('#charHint').textContent = state.chars.length === 0 ? '(none → scene only)' : '';
  syncEditor();
  persist();
}

function charCard(c, i) {
  const setSrc = s => { c.source = s; renderChars(); schedulePreview(); };
  const hd = el('div', { class: 'hd' }, [
    el('span', { class: 'tag ' + (i === 0 ? 'A' : 'B') }, i === 0 ? 'A · left' : 'B · right'),
    el('div', { class: 'seg' }, ['preset', 'secondbrain', 'custom'].map(s =>
      el('button', { class: c.source === s ? 'on' : '', onclick: () => setSrc(s) }, s === 'secondbrain' ? 'SB' : s))),
    el('span', { style: 'flex:1' }),
    el('button', { class: 'x', onclick: () => { state.chars.splice(i, 1); renderChars(); schedulePreview(); } }, '✕'),
  ]);

  const body = [];
  if (c.source === 'preset') {
    const names = PRESETS.characters.preset.map(p => p.name);
    const p = PRESETS.characters.preset.find(x => x.name === c.name) || PRESETS.characters.preset[0] || { outfits: ['default'], expressions: ['default'] };
    const nameSel = el('select', { onchange: e => { c.name = e.target.value; c.outfit = 'default'; c.expression = 'default'; renderChars(); schedulePreview(); } }, names.map(n => el('option', {}, n)));
    nameSel.value = c.name;
    const outSel = el('select', { onchange: e => { c.outfit = e.target.value; schedulePreview(); } }, (p.outfits || ['default']).map(o => el('option', {}, o)));
    outSel.value = c.outfit;
    const expSel = el('select', { onchange: e => { c.expression = e.target.value; schedulePreview(); } }, (p.expressions || ['default']).map(o => el('option', {}, o)));
    expSel.value = c.expression;
    body.push(nameSel, el('div', { class: 'grid2' }, [
      el('div', {}, [el('label', {}, 'Outfit'), outSel]),
      el('div', {}, [el('label', {}, 'Expression'), expSel]),
    ]), presetEditor(c.name));
  } else if (c.source === 'secondbrain') {
    const names = PRESETS.characters.secondbrain.map(p => p.name);
    if (!names.length) body.push(el('div', { class: 'muted' }, 'No SECOND_BRAIN_DIR/Characters/*.md found.'));
    else {
      const s = el('select', { onchange: e => { c.name = e.target.value; schedulePreview(); } }, names.map(n => el('option', {}, n)));
      s.value = names.includes(c.name) ? c.name : names[0]; c.name = s.value;
      body.push(s);
    }
  } else {
    const ta = el('textarea', { placeholder: '1girl, describe the character in danbooru tags…', onchange: e => { c.text = e.target.value; schedulePreview(); } });
    ta.value = c.text; body.push(ta);
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
  editor.setCount(n, lastLibFrame && lastLibFrame.people?.length === n ? lastLibFrame : null);
  $('#yawSlider').value = state.yaw; $('#pitchSlider').value = state.pitch;
  editor.setOrient(state.yaw, state.pitch);
  updateOrientLbl();
  buildEditorTools();
  refreshPoseLib();
}

function buildEditorTools() {
  const wrap = $('#editorTools'); wrap.innerHTML = '';
  wrap.append(el('div', { class: 'undoredo' }, [
    el('button', { class: 'small', onclick: () => editor.undo() }, '↶ undo'),
    el('button', { class: 'small', onclick: () => editor.redo() }, '↷ redo'),
    el('span', { class: 'muted', style: 'font-size:10px;align-self:center' }, 'drag joints · right-click a joint to toggle'),
  ]));
  const aspect = (+$('#width').value) / (+$('#height').value);
  editor.figures.forEach((_, fi) => {
    wrap.append(el('div', { class: 'fig' }, [
      el('div', { class: 'muted', style: 'font-size:10px' }, (fi === 0 ? 'Figure A' : 'Figure B')),
      el('div', { class: 'row' }, [
        el('button', { class: 'small', onclick: () => editor.mirror(fi) }, '⇋ mirror'),
        el('button', { class: 'small', onclick: () => editor.nudge(fi, -0.03, 0) }, '←'),
        el('button', { class: 'small', onclick: () => editor.nudge(fi, 0.03, 0) }, '→'),
        el('button', { class: 'small', onclick: () => editor.nudge(fi, 0, -0.03) }, '↑'),
        el('button', { class: 'small', onclick: () => editor.nudge(fi, 0, 0.03) }, '↓'),
      ]),
      el('label', {}, 'scale'),
      el('input', { type: 'range', min: 0.6, max: 1.5, step: 0.02, value: 1, onchange: e => { editor.scale(fi, +e.target.value); e.target.value = 1; } }),
      el('label', {}, 'dutch'),
      el('input', { type: 'range', min: -30, max: 30, step: 1, value: 0, onchange: e => { editor.dutch(fi, +e.target.value, aspect); e.target.value = 0; } }),
    ]));
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
  lastLibFrame = d.frame;
  state.poseName = pos;
  editor.loadFrame(d.frame);
  buildEditorTools();
}

async function savePose() {
  const name = prompt('Save pose as (name — the pose only, no camera angle):',
    state.poseName || 'my_pose');
  if (!name) return;
  const tags = prompt('Pose tags for the prompt (e.g. "kneeling, hands on thighs"):', '') || '';
  const r = await fetch('/api/save-pose', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      position: name, position_tags: tags, people: editor.figures.length, overwrite: true,
      keypoints: editor.getFrame(+$('#width').value, +$('#height').value),
    }),
  }).then(r => r.json());
  if (r.error) return toast(r.error);
  toast('saved → ' + r.saved.split('/').pop());
  PRESETS = await fetch('/api/presets').then(r => r.json());
  refreshPoseLib();
}

// ------------------------------------------------------------- spec + preview
function buildSpec() {
  const chars = state.chars.map(c => ({
    source: c.source, name: c.name, outfit: c.outfit, expression: c.expression,
    weight: c.weight, text: c.text,
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
  if (state.chars.length && editor)
    spec.skeleton = { people: editor.getFrame(spec.width, spec.height).people };
  // Carry the chosen library pose so the backend can add its pose/action words
  // to the prompt — the edited skeleton only drives ControlNet, which a
  // voluminous outfit can hide.
  if (state.poseName) spec.pose_position = state.poseName;
  // facing sliders: procedural yaw/pitch, applied to the skeleton + as camera tags
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
    mBtn('🦴', 'send pose to editor', () => { const sk = m.spec?.skeleton; if (sk?.people && editor) { editor.loadFrame({ people: sk.people }); toast('pose loaded'); } }),
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
  state.chars = (spec.characters || []).map(c => ({ ...blankChar(), ...c }));
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
  renderChars();
  if (spec.skeleton?.people && editor) { lastLibFrame = { people: spec.skeleton.people }; editor.loadFrame(lastLibFrame); }
  toast('settings restored'); schedulePreview();
}

function persist() {
  try {
    localStorage.pf_state = JSON.stringify({
      chars: state.chars, form: formValues(),
      poseName: state.poseName, yaw: state.yaw, pitch: state.pitch,
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
    state.chars = s.chars || [];
    state.poseName = s.poseName || null;
    state.yaw = s.yaw || 0;
    state.pitch = s.pitch || 0;
  } catch { state.chars = []; }
}

boot();
