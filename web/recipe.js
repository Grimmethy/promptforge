/* PromptForge Studio: the lightbox "Recipe" section. Pure functions, loaded before app.js; node can load it so the text and the spec are unit
   tested. The data comes from /api/gallery-recipe/<file> (server/core/imgmeta.py: the checkpoint, LoRAs and settings embedded in the image). */
(function (root) {
  const num = v => (v === null || v === undefined || v === '' ? '' : String(v));
  const loraLine = r => {
    const w = r.weight === null || r.weight === undefined ? '' : ' @ ' + r.weight;
    const where = !r.known ? 'not in the registry' : r.installed === false ? 'known, file not installed' : 'known';
    return `${r.name}${w} (${where})`;
  };
  // [{label, text}] rows for the section; empty when the image carries no recipe
  const summary = rec => {
    const meta = (rec && rec.meta) || {}, match = (rec && rec.match) || {};
    if (!meta.source || meta.source === 'none') return [];
    const rows = [['Source', meta.source], ['Checkpoint', (meta.checkpoint || '?') + (match.checkpoint_base ? ' (' + match.checkpoint_base + ')' : '')]];
    (match.loras || []).forEach((r, i) => rows.push(['LoRA ' + (i + 1), loraLine(r)]));
    const bits = [['sampler', meta.sampler], ['scheduler', meta.scheduler], ['steps', meta.steps], ['cfg', meta.cfg], ['clip skip', meta.clip_skip],
      ['seed', meta.seed], ['size', meta.width && meta.height ? meta.width + 'x' + meta.height : '']].filter(b => num(b[1]) !== '');
    if (bits.length) rows.push(['Settings', bits.map(b => b[0] + ' ' + b[1]).join(' · ')]);
    return rows.map(([label, text]) => ({ label, text }));
  };
  // the generate-form spec for "Copy as spec": the model, the settings that map onto the form, and the LoRA stack ({name, weight}; the registry file when known)
  const spec = rec => {
    const meta = (rec && rec.meta) || {}, match = (rec && rec.match) || {};
    if (!meta.source || meta.source === 'none') return null;
    const out = {};
    if (meta.checkpoint) out.model = meta.checkpoint;
    for (const k of ['steps', 'cfg', 'seed']) if (meta[k] !== null && meta[k] !== undefined) out[k] = meta[k];
    if (meta.sampler) out.sampler = meta.sampler;
    if (meta.scheduler) out.scheduler = meta.scheduler;
    const loras = (match.loras || []).map(r => ({ name: r.file || r.name, weight: r.weight === null || r.weight === undefined ? 1 : r.weight, known: !!r.known }));
    if (loras.length) out.loras = loras;
    return out;
  };
  const api = { summary, spec, loraLine };
  root.PFRecipe = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
