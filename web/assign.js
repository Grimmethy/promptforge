'use strict';

// Lightbox "Assign to..." panel: pre-selection, badge text and the DOM builder. Pure helpers come from assign-model.js (PFAssign).
// The server does the copy (POST /api/gallery/<relpath>/assign); this file only collects choices and shows the per-target results.

const model = (typeof window !== 'undefined' && window.PFAssign) || (typeof require === 'function' ? require('./assign-model.js') : null);

const norm = (s) => String(s == null ? '' : s).trim().toLowerCase();

// All-or-nothing: select the entries only when every spec name resolves to exactly one entry.
function preselectEntries(specCharacters, entries) {
  const names = (specCharacters || []).map(norm).filter(Boolean);
  if (!names.length) return [];
  const found = [];
  for (const n of new Set(names)) {
    const hits = (entries || []).filter((e) => norm(e.name) === n);
    if (hits.length !== 1) return [];
    found.push(hits[0]);
  }
  return found;
}

// links: sidecar links [{forge, slug, name?, role}]. '-> Name · role', plus ' +n' for the other links.
function badgeLabel(links) {
  if (!Array.isArray(links) || !links.length) return '';
  const l = links[0];
  const more = links.length > 1 ? ` +${links.length - 1}` : '';
  return `-> ${l.name || l.slug} · ${l.role}${more}`;
}

function el(doc, tag, props = {}, kids = []) {
  const n = doc.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'text') n.textContent = v;
    else if (k === 'on') for (const [ev, fn] of Object.entries(v)) n.addEventListener(ev, fn);
    else if (k in n) n[k] = v;
    else n.setAttribute(k, v);
  }
  for (const c of kids) n.appendChild(c);
  return n;
}

const totalCount = (e) => Object.values(e.counts || {}).reduce((a, b) => a + (Number(b) || 0), 0);
const prepare = (e) => ({ ...e, name: String(e.name || e.slug), hasImage: totalCount(e) > 0, faction: e.faction || e.group || '' });

function entryRow(doc, e, st, refresh) {
  const box = el(doc, 'input', { type: 'checkbox', checked: st.selected.has(e.slug), on: { change: () => {
    if (box.checked) st.selected.add(e.slug); else st.selected.delete(e.slug);
    refresh();
  } } });
  const label = el(doc, 'label', { className: 'assign-row' }, [box, el(doc, 'span', { text: ` ${e.name}` })]);
  if (e.noImage) label.appendChild(el(doc, 'span', { className: 'assign-noimage', text: ' (no image)' }));
  return label;
}

function renderList(doc, host, st, refresh) {
  host.textContent = '';
  const sections = model.groupEntries(st.entries, { preselected: st.pre, query: st.query });
  for (const s of sections) {
    if (!s.count) continue;
    host.appendChild(el(doc, 'h4', { text: `${s.title} (${s.count})` }));
    for (const g of s.groups) {
      const det = el(doc, 'details', { open: g.open }, [el(doc, 'summary', { text: `${g.label} (${g.entries.length})` })]);
      for (const e of g.entries) det.appendChild(entryRow(doc, { ...e, selected: st.selected.has(e.slug) }, st, refresh));
      host.appendChild(det);
    }
  }
  if (!host.firstChild) host.appendChild(el(doc, 'p', { text: 'No matching entries.' }));
}

function renderRoles(doc, sel, st) {
  const counts = Object.fromEntries(st.entries.map((e) => [e.slug, e.counts || {}]));
  const opts = model.roleOptions(null, st.roles, counts, [...st.selected]);
  sel.textContent = '';
  if (!st.role && opts.length) st.role = opts[0].id;
  for (const o of opts) sel.appendChild(el(doc, 'option', { value: o.id, text: o.text, selected: o.id === st.role }));
}

async function postAssign(st, body) {
  const url = `/api/gallery/${String(st.relpath).split('/').map(encodeURIComponent).join('/')}/assign`;
  const res = await st.fetchFn(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (Array.isArray(data)) return data;
  if (Array.isArray(data.results)) return data.results;
  if (Array.isArray(data.targets)) return data.targets;
  return [{ slug: '', role: st.role, ok: false, error: data.error || `HTTP ${res.status}`, status: res.status }];
}

function replaceControls(doc, r, st, send) {
  const choices = r.images || r.entries || [];
  const field = choices.length
    ? el(doc, 'select', {}, choices.map((c) => el(doc, 'option', { value: c.id || c.imageId, text: c.file || c.id || c.imageId })))
    : el(doc, 'input', { type: 'text', placeholder: 'image id to replace' });
  const btn = el(doc, 'button', { type: 'button', text: 'Replace', on: { click: () => {
    if (!field.value) return;
    send({ targets: [{ slug: r.slug, role: r.role }], tag: st.tag, note: st.note, replace: { [r.slug]: field.value } });
  } } });
  return [field, btn];
}

function renderResults(doc, host, results, st, send) {
  host.textContent = '';
  for (const r of results) {
    const row = el(doc, 'div', { className: r.ok ? 'assign-ok' : 'assign-err' }, [el(doc, 'span', { text: model.describeResult(r) })]);
    if (!r.ok && r.status === 409) for (const n of replaceControls(doc, r, st, send)) row.appendChild(n);
    host.appendChild(row);
  }
}

function pickTarget(data) {
  const list = Array.isArray(data) ? data : (data && data.targets) || [];
  return list.find((t) => t && !t.error && Array.isArray(t.entries)) || null;
}

// opts: {relpath, specCharacters?, doc?, fetchFn?, onDone?(results)}. Returns the panel element; it fills in after GET /api/link-targets.
function buildAssignPanel(opts) {
  const doc = opts.doc || document;
  const st = { relpath: opts.relpath, fetchFn: opts.fetchFn || ((...a) => fetch(...a)), entries: [], pre: [], selected: new Set(), query: '', role: '', roles: [], tag: '', note: '' };
  const list = el(doc, 'div', { className: 'assign-list' });
  const roleSel = el(doc, 'select', { className: 'assign-role', on: { change: () => { st.role = roleSel.value; } } });
  const results = el(doc, 'div', { className: 'assign-results' });
  const status = el(doc, 'p', { className: 'assign-status', text: 'Loading targets...' });
  const refresh = () => { renderRoles(doc, roleSel, st); };
  const search = el(doc, 'input', { type: 'search', placeholder: 'Search', on: { input: () => { st.query = search.value; renderList(doc, list, st, refresh); } } });
  const tag = el(doc, 'input', { type: 'text', placeholder: 'Tag (optional)', on: { input: () => { st.tag = tag.value; } } });
  const note = el(doc, 'input', { type: 'text', placeholder: 'Note (optional)', on: { input: () => { st.note = note.value; } } });
  const send = async (body) => {
    status.textContent = 'Sending...';
    try {
      const res = await postAssign(st, body);
      renderResults(doc, results, res, st, send);
      status.textContent = '';
      if (opts.onDone) opts.onDone(res);
    } catch (err) { status.textContent = `Failed: ${err.message || err}`; }
  };
  const confirm = el(doc, 'button', { type: 'button', text: 'Confirm', on: { click: () => {
    if (!st.selected.size || !st.role) { status.textContent = 'Pick at least one target and a role.'; return; }
    const body = { targets: [...st.selected].map((slug) => ({ slug, role: st.role })) };
    if (st.tag) body.tag = st.tag;
    if (st.note) body.note = st.note;
    send(body);
  } } });
  const panel = el(doc, 'div', { className: 'assign-panel' }, [search, list, roleSel, tag, note, confirm, status, results]);

  st.fetchFn('/api/link-targets').then((r) => r.json()).then((data) => {
    const t = pickTarget(data);
    if (!t) { status.textContent = 'No reachable link target.'; return; }
    st.entries = t.entries.map(prepare);
    st.roles = t.roles || [];
    st.pre = preselectEntries(opts.specCharacters, st.entries).map((e) => e.slug);
    st.selected = new Set(st.pre);
    status.textContent = '';
    renderList(doc, list, st, refresh);
    refresh();
  }).catch((err) => { status.textContent = `Could not load targets: ${err.message || err}`; });
  return panel;
}

// Link-list renderer: one row per sidecar link (forge, slug, role, tag, note, verdict, ts). Hidden when there are no links.
function renderLinkList(doc, host, links) {
  host.textContent = '';
  const list = Array.isArray(links) ? links : [];
  host.hidden = !list.length;
  for (const l of list) {
    host.appendChild(el(doc, 'div', { className: 'assign-link' }, [
      el(doc, 'span', { text: `${l.forge || ''} ${l.slug || ''} . ${l.role || ''}` }),
      el(doc, 'span', { text: l.tag ? ` tag:${l.tag}` : '' }),
      el(doc, 'span', { text: l.note ? ` note:${l.note}` : '' }),
      el(doc, 'span', { text: l.verdict ? ` verdict:${l.verdict}` : '' }),
      el(doc, 'span', { text: l.ts ? ` ${l.ts}` : '' }),
    ]));
  }
  return host;
}

// GET /api/link-targets, one call per invocation. Error entries are kept and tagged with a '<forge> unreachable' message.
async function fetchLinkTargets(fetchFn = (...a) => fetch(...a)) {
  const res = await fetchFn('/api/link-targets');
  const data = await res.json().catch(() => ({}));
  const list = Array.isArray(data) ? data : (data && data.targets) || [];
  return list.map((t) => (t && t.error ? { ...t, unreachable: true, message: `${t.forge || t.label || 'target'} unreachable` } : t));
}

// Set of forge names whose target is unreachable (error entry).
function unreachableForges(targets) {
  return new Set((targets || []).filter((t) => t && t.error).map((t) => t.forge));
}

// Renders a disabled '<forge> unreachable' line per error entry. Returns the forges it disabled.
function renderUnreachable(doc, host, targets) {
  const bad = (targets || []).filter((t) => t && t.error);
  for (const t of bad) {
    host.appendChild(el(doc, 'div', { className: 'assign-unreachable', text: `${t.forge || t.label || 'target'} unreachable` }));
  }
  return bad.map((t) => t.forge);
}

// POST /api/gallery/<relpath>/assign. Targets on an unreachable forge are never sent; they come back as local error results.
async function sendAssign(fetchFn, relpath, body, unreachable = new Set()) {
  const all = (body && body.targets) || [];
  const blocked = all.filter((t) => t && unreachable.has(t.forge));
  const allowed = all.filter((t) => !(t && unreachable.has(t.forge)));
  const blockedResults = blocked.map((t) => ({ slug: t.slug, role: t.role, ok: false, error: `${t.forge} unreachable` }));
  if (!allowed.length) return blockedResults;
  const url = `/api/gallery/${String(relpath).split('/').map(encodeURIComponent).join('/')}/assign`;
  const res = await fetchFn(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, targets: allowed }) });
  const data = await res.json().catch(() => ({}));
  let results;
  if (Array.isArray(data)) results = data;
  else if (Array.isArray(data.results)) results = data.results;
  else if (Array.isArray(data.targets)) results = data.targets;
  else results = [{ slug: '', role: body.role || '', ok: false, error: data.error || `HTTP ${res.status}`, status: res.status }];
  return [...blockedResults, ...results];
}

const api = { preselectEntries, badgeLabel, buildAssignPanel, renderLinkList, fetchLinkTargets, unreachableForges, renderUnreachable, sendAssign };
if (typeof window !== 'undefined') window.PFAssignPanel = api;
if (typeof module !== 'undefined' && module.exports) module.exports = api;
