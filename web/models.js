/* PromptForge Studio: the LoRA picker and the model importer panel. Pure functions, loaded before app.js; node can load it so the choices, the
   spec and the badge decision are unit tested. The data comes from GET /api/loras and POST /api/models/plan (server/core/loras.py, model_importer.py). */
(function (root) {
  const GB = 1e9;
  const MB = 1e6;
  // one shared size formatter: decimal GB from 1 GB up, whole decimal MB below; 'unknown' for a missing or zero size
  const formatSize = n => {
    if (!n || !Number.isFinite(Number(n))) return 'unknown';
    return Number(n) >= GB ? (Number(n) / GB).toFixed(1) + ' GB' : Math.round(Number(n) / MB) + ' MB';
  };
  // does this registry entry fit the selected model's base? (an unknown base on either side never hides a LoRA)
  const fits = (lora, base) => !base || !lora.base || lora.base === base;
  // the base the registry lists for a checkpoint file, else null
  const baseOf = (checkpoints, model) => (checkpoints && model && checkpoints[model]) || null;
  // LoRAs the picker offers: installed, a fitting base, not already chosen
  const available = (loras, base, chosenIds) => (loras || []).filter(l => l.present && fits(l, base) && !(chosenIds || []).includes(l.id));
  // a new picker row for a registry entry
  const newRow = l => ({ id: l.id, name: l.name, weight: l.default_weight == null ? 1 : l.default_weight, triggers: l.triggers || [], licence: l.licence || null, base: l.base || null });
  const weightOf = r => (Number.isFinite(Number(r.weight)) && String(r.weight).trim() !== '' ? Number(r.weight) : 1);
  // spec.loras for the generate call: [{id, weight}] (the server fills in the file and triggers from the registry)
  const toSpec = rows => (rows || []).filter(r => r && r.id).map(r => ({ id: r.id, weight: weightOf(r) }));
  // a LoRA whose licence says we may not sell images made with it
  const notForSale = r => !!(r && r.licence && r.licence.commercial_use === 'no');
  const sellWarning = rows => {
    const bad = (rows || []).filter(notForSale).map(r => r.name || r.id);
    return bad.length ? 'Not cleared for selling: ' + bad.join(', ') : '';
  };
  // [{label, text, warn}] for the import plan table
  const planRows = plan => {
    if (!plan) return [];
    const f = plan.file || {}, lic = plan.licence || {};
    const rows = [['Name', plan.name], ['Type', plan.type], ['Base model', plan.base_model || 'unknown'],
      ['Size', formatSize(f.size_bytes)], ['File', f.name],
      ['Trigger words', (plan.trained_words || []).join(', ') || 'none'],
      ['Licence', (lic.commercial_use || 'unverified') + (lic.text ? ' (' + lic.text + ')' : '')],
      ['Installs to', plan.destination]].map(([label, text]) => ({ label, text: String(text == null ? '' : text), warn: false }));
    if (plan.nsfw) rows.push({ label: 'Note', text: 'Civitai marks this model as adult content', warn: true });
    (plan.warnings || []).forEach(w => rows.push({ label: 'Warning', text: w, warn: true }));
    return rows;
  };
  // the Import button is enabled only for a checked plan that would not overwrite an installed file
  const canImport = plan => !!plan && !plan.error && !(plan.warnings || []).some(w => /already installed/i.test(w));
  const progressText = job => {
    if (!job) return '';
    if (job.status === 'error') return 'Failed: ' + (job.error || 'unknown error');
    if (job.status === 'done') return 'Installed ' + ((job.result || {}).file || '') + (job.result && job.result.registry_id ? ' (registry id ' + job.result.registry_id + ')' : '');
    const pct = job.total ? Math.min(100, Math.round(100 * (job.bytes || 0) / job.total)) + '% of ' + formatSize(job.total) : (job.bytes ? formatSize(job.bytes) : '0 MB');
    if (job.status === 'downloading' && job.via === 'session') return 'Downloading through your signed-in Civitai session...';
    return { planning: 'Checking the link...', downloading: 'Downloading ' + pct, verifying: 'Verifying the file...' }[job.status] || job.status;
  };
  const sessionLine = s => {
    if (!s || s.available === false) return { text: 'Civitai sign-in: unavailable', canSignIn: false };
    if (s.signed_in === true) return { text: 'Civitai sign-in: signed in', canSignIn: true };
    if (s.signed_in === false) return { text: 'Civitai sign-in: not signed in', canSignIn: true };
    return { text: 'Civitai sign-in: unknown' + (s.error ? ' (' + s.error + ')' : ''), canSignIn: true };
  };
  // the hosts we can check a pasted model link from; a subdomain of any of them counts
  const MODEL_HOSTS = ['civitai.com', 'civitai.red', 'huggingface.co'];
  // is the pasted text an http(s) link to one of those hosts? (a bare word, another site, or a lookalike like evilcivitai.com is not)
  const isModelLink = text => {
    if (typeof text !== 'string') return false;
    let u;
    try { u = new URL(text.trim()); } catch (e) { return false; }
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
    const h = u.hostname.toLowerCase();
    return MODEL_HOSTS.some(d => h === d || h.endsWith('.' + d));
  };
  // a paste starts the check only when it is a model link
  const shouldAutoCheck = pastedText => isModelLink(pastedText);
  // after a check, focus Import only when the check result would allow an import (the same gate as canImport); otherwise no focus move
  const nextFocusAfterCheck = checkResult => (canImport(checkResult) ? 'impGo' : null);
  const api = { sessionLine, fits, baseOf, available, newRow, toSpec, notForSale, sellWarning, planRows, canImport, progressText, weightOf, formatSize, isModelLink, shouldAutoCheck, nextFocusAfterCheck };
  root.PFModels = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
