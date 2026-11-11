// The lightbox recipe section's pure functions (web/recipe.js): the rows shown and the spec copied. node --test test/*.test.js
const test = require('node:test');
const assert = require('node:assert');
const { summary, spec, loraLine } = require('../web/recipe.js');

const rec = {
  meta: { source: 'tungsten', checkpoint: 'Nova Furry XL', sampler: 'dpmpp_2m_karras', steps: 29, cfg: 4.5, seed: 7, width: 832, height: 1216, clip_skip: 2 },
  match: { checkpoint_base: 'illustrious', loras: [
    { name: '[Style] SSB - v1', weight: 0.55, known: true, installed: true, file: 'SSB_Illustrious-XL_v1.safetensors' },
    { name: 'Huffslove Style', weight: 0.5, known: false, installed: null, file: null },
    { name: 'Pose', weight: 0.55, known: true, installed: false, file: 'Pose.safetensors' }] },
};

test('summary lists the source, checkpoint, each LoRA with where it stands, and the settings', () => {
  const rows = summary(rec);
  assert.deepStrictEqual(rows.map(r => r.label), ['Source', 'Checkpoint', 'LoRA 1', 'LoRA 2', 'LoRA 3', 'Settings']);
  assert.strictEqual(rows[1].text, 'Nova Furry XL (illustrious)');
  assert.strictEqual(rows[2].text, '[Style] SSB - v1 @ 0.55 (known)');
  assert.strictEqual(rows[3].text, 'Huffslove Style @ 0.5 (not in the registry)');
  assert.strictEqual(rows[4].text, 'Pose @ 0.55 (known, file not installed)');
  assert.strictEqual(rows[5].text, 'sampler dpmpp_2m_karras · steps 29 · cfg 4.5 · clip skip 2 · seed 7 · size 832x1216');
});

test('an image without a recipe gives no rows and no spec', () => {
  for (const r of [null, {}, { meta: { source: 'none' } }]) {
    assert.deepStrictEqual(summary(r), []);
    assert.strictEqual(spec(r), null);
  }
});

test('spec carries the model, settings and a LoRA stack using the registry file when known', () => {
  const s = spec(rec);
  assert.strictEqual(s.model, 'Nova Furry XL');
  assert.deepStrictEqual([s.steps, s.cfg, s.seed, s.sampler], [29, 4.5, 7, 'dpmpp_2m_karras']);
  assert.deepStrictEqual(s.loras, [
    { name: 'SSB_Illustrious-XL_v1.safetensors', weight: 0.55, known: true },
    { name: 'Huffslove Style', weight: 0.5, known: false },
    { name: 'Pose.safetensors', weight: 0.55, known: true }]);
});

test('spec leaves out what the image does not say and defaults a missing weight to 1', () => {
  const s = spec({ meta: { source: 'a1111', checkpoint: null, steps: 0 }, match: { loras: [{ name: 'x', weight: null, known: false }] } });
  assert.deepStrictEqual(s, { steps: 0, loras: [{ name: 'x', weight: 1, known: false }] });
  assert.strictEqual(loraLine({ name: 'y', weight: null, known: false }), 'y (not in the registry)');
});
