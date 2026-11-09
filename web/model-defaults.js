/* PromptForge Studio: sampler defaults per model family (an SDXL checkpoint vs Krea 2 Turbo).
   Pure functions, loaded before app.js; node can load it too (module.exports) so the logic is unit tested without a browser.
   The numbers must equal the server's: SDXL = server/app.py _DEFAULTS, Krea 2 = what api_generate / _repair_job_defaults use
   (a test pins both). */
(function (root) {
  const FIELDS = ['cfg', 'steps', 'sampler', 'scheduler'];
  const DEFAULTS = {
    sdxl: { cfg: 6, steps: 30, sampler: 'dpmpp_2m_sde', scheduler: 'karras' },
    krea2: { cfg: 1, steps: 8, sampler: 'euler', scheduler: 'simple' },
  };
  // same test as comfy.is_krea2 on the server (the "krea2:" prefix); the "(ComfyUI down)" placeholder counts as sdxl
  const family = m => (typeof m === 'string' && m.startsWith('krea2:')) ? 'krea2' : 'sdxl';
  const pick = o => { const r = {}; for (const k of FIELDS) r[k] = o[k]; return r; };
  const same = (k, a, b) => (k === 'cfg' || k === 'steps') ? Number(a) === Number(b) : String(a) === String(b);

  // Called when the model changes. Same family: nothing to do (values null). Different family: keep what the user had for the
  // family they are leaving (stash) and return the values for the one they are entering (their earlier values, else the defaults).
  function swap(prevModel, newModel, current, stash) {
    const from = family(prevModel), to = family(newModel);
    const next = { ...(stash || {}) };
    if (from === to) return { values: null, stash: next };
    next[from] = pick(current);
    return { values: { ...(next[to] || DEFAULTS[to]) }, stash: next };
  }

  // A saved state can hold a Krea 2 model with the SDXL numbers (from before this fix): true when exactly that.
  function needsKreaFix(model, current) {
    return family(model) === 'krea2' && FIELDS.every(k => same(k, current[k], DEFAULTS.sdxl[k]));
  }

  const api = { family, DEFAULTS, FIELDS, swap, needsKreaFix };
  root.PFModelDefaults = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
