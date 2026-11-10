/* PromptForge Studio: model licence badges (what a model lets us SELL). Pure functions, loaded before app.js; node can load it too (module.exports)
   so the labels are unit tested. The verdicts come from the server (data/model-licenses.json via /api/presets and each image's sidecar). */
(function (root) {
  const BADGES = {
    yes: { label: '✔ can sell', short: '✔', color: '#2e9e5b' },
    conditional: { label: '⚠ conditional', short: '⚠', color: '#d99a1c' },
    unverified: { label: '? unverified', short: '?', color: '#7d838c' },
    no: { label: '✖ not for sale', short: '✖', color: '#d04545' },
  };
  const badge = cu => BADGES[cu] || BADGES.unverified;
  const optionText = (name, rec) => `${name}   [${badge(rec && rec.commercial_use).label}]`;
  const tooltip = rec => {
    const r = rec || {};
    return [badge(r.commercial_use).label, r.licence, r.note, r.checked ? 'checked ' + r.checked : ''].filter(Boolean).join(' · ');
  };
  const api = { BADGES, badge, optionText, tooltip };
  root.PFLicences = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
