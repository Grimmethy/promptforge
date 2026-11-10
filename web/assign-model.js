'use strict';

// Pure helpers for the lightbox "Assign to..." panel (no DOM, so node tests can run them). The server does the copy (POST /api/gallery/<file>/assign);
// this file decides how the target's entries are grouped, which sections start open, what the role selector says, and how results read.

const NO_GROUP = 'Other';

const keyOf = (e) => e.faction || e.project || NO_GROUP;
const matches = (e, q) => !q || [e.name, e.slug, e.faction, e.project, e.group].some((x) => String(x || '').toLowerCase().includes(q));

// entries: the target's rows [{slug, name, kind, group, faction, project, hasImage, thumb}]. Two separate sections (Characters, Groups); inside each,
// collapsed sub-groups by faction/project. A sub-group starts open when it holds a pre-selected entry or when a search is active. Entries with no
// image come first (marked), then by name; sub-groups are ordered with the pre-selected one first, then by label.
function groupEntries(entries, { preselected = [], query = '' } = {}) {
  const pre = new Set(preselected), q = String(query || '').trim().toLowerCase();
  const section = (title, kind, rows) => {
    const by = new Map();
    for (const e of rows.filter((x) => matches(x, q))) {
      const k = keyOf(e);
      if (!by.has(k)) by.set(k, []);
      by.get(k).push(e);
    }
    const groups = [...by].map(([label, list]) => {
      list.sort((a, b) => (a.hasImage - b.hasImage) || a.name.localeCompare(b.name));
      return { label, open: !!q || list.some((e) => pre.has(e.slug)), hasPre: list.some((e) => pre.has(e.slug)), entries: list.map((e) => ({ ...e, selected: pre.has(e.slug), noImage: !e.hasImage })) };
    }).sort((a, b) => (b.hasPre - a.hasPre) || a.label.localeCompare(b.label));
    return { title, kind, groups, count: groups.reduce((n, g) => n + g.entries.length, 0) };
  };
  const isGroup = (e) => e.kind === 'group';
  return [section('Characters', 'characters', entries.filter((e) => !isGroup(e))), section('Groups', 'groups', entries.filter(isGroup))];
}

// The role selector: every role with its label and n/max (the highest count among the selected entries, since the most-filled one decides whether
// the role is full for somebody).
function roleOptions(categories, roles, countsBySlug, selected, max = 10) {
  const labels = new Map((categories || []).map((c) => [c.id, c.label || c.id]));
  const order = (categories || []).map((c) => c.id);
  const ids = [...new Set([...order.filter((id) => roles.includes(id)), ...roles])];
  return ids.map((id) => {
    const n = Math.max(0, ...selected.map((s) => (countsBySlug[s] || {})[id] || 0));
    return { id, label: labels.get(id) || id, count: n, max, full: n >= max, text: `${labels.get(id) || id} ${n}/${max}` };
  });
}

const buildTargets = (selected, role, names = {}) => ({ targets: selected.map((slug) => ({ slug, role })), names: Object.fromEntries(selected.map((s) => [s, names[s] || s])) });

// One line per result for the panel: ok / already there / role full (needs a replace choice) / failed or unreachable.
function describeResult(r) {
  if (r.ok && r.already) return `${r.slug}: already linked as ${r.role}, nothing sent`;
  if (r.ok) return `${r.slug}: added as ${r.file || r.role}`;
  if (r.status === 409) return `${r.slug}: ${r.role} is full (${r.count == null ? 10 : r.count}/10) - choose a picture to replace; the old one goes to history`;
  return `${r.slug}: ${r.error || 'failed'}`;
}

const badgeText = (link) => (link ? `-> ${link.name} . ${link.role}${link.more ? ` +${link.more}` : ''}` : '');

const api = { groupEntries, roleOptions, buildTargets, describeResult, badgeText, NO_GROUP };
if (typeof window !== 'undefined') window.PFAssign = api;
if (typeof module !== 'undefined' && module.exports) module.exports = api;
