// Hand-offs from the library forges (web/app.js applies them; server/core/handoff.py stores them for an hour). Pure helpers, no DOM. node --test test/*.test.js
(function (root) {
  const ID = /^[0-9a-f]{32}$/;

  const isId = (v) => typeof v === 'string' && ID.test(v);

  // The Create form's spec for a hand-off payload: its own spec when it has one (a saved recipe, or the one the server built for an empty slot),
  // otherwise a single custom character card carrying the prompt, plus the model, seed, style and negative text when the payload has them.
  const specFromPayload = (payload) => {
    const p = payload || {};
    if (p.spec && typeof p.spec === 'object' && Object.keys(p.spec).length) return p.spec;
    const slug = (p.source && p.source.slug) || 'character';
    const spec = { characters: [{ source: 'custom', name: slug, expression: 'none', weight: 1, outfit: {}, colors: {}, text: p.prompt || '' }], theme: 'none' };
    for (const k of ['model', 'style', 'negative']) if (typeof p[k] === 'string' && p[k]) spec[k] = p[k];
    if (typeof p.seed === 'number') spec.seed = p.seed;
    return spec;
  };

  // The line shown after a hand-off is applied.
  const describe = (payload) => {
    const p = payload || {};
    const from = p.kind === 'slot' ? p.slot || {} : p.source || {};
    const where = [from.forge, from.slug].filter(Boolean).join(': ');
    const role = from.role ? ` (${from.role})` : '';
    return (p.kind === 'slot' ? 'Empty slot from ' : 'Loaded from ') + (where || 'a library forge') + role;
  };

  // The query parameter or message carrying a hand-off id; null for anything that is not one.
  const idFromSearch = (search) => {
    const v = new URLSearchParams(search || '').get('handoff');
    return isId(v) ? v : null;
  };
  const idFromMessage = (data) => (data && data.type === 'pf-handoff' && isId(data.id)) ? data.id : null;

  const api = { isId, specFromPayload, describe, idFromSearch, idFromMessage };
  root.PFHandoff = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
