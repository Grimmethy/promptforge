// The LoRA picker and model-import panel's pure functions (web/models.js). node --test test/*.test.js
const test = require('node:test');
const assert = require('node:assert');
const M = require('../web/models.js');

const LORAS = [
  { id: 'a', name: 'A', base: 'illustrious', present: true, default_weight: 0.55, triggers: ['ta'], licence: { commercial_use: 'conditional' } },
  { id: 'b', name: 'B', base: 'anima', present: true, default_weight: 0.5, triggers: [], licence: { commercial_use: 'no' } },
  { id: 'c', name: 'C', base: 'illustrious', present: false },
  { id: 'd', name: 'D', base: null, present: true },
];

test('baseOf and fits: an unknown base on either side never hides a LoRA', () => {
  assert.strictEqual(M.baseOf({ 'x.safetensors': 'illustrious' }, 'x.safetensors'), 'illustrious');
  assert.strictEqual(M.baseOf({}, 'x.safetensors'), null);
  assert.strictEqual(M.baseOf(null, null), null);
  assert.ok(M.fits(LORAS[0], 'illustrious') && !M.fits(LORAS[1], 'illustrious') && M.fits(LORAS[3], 'illustrious') && M.fits(LORAS[1], null));
});

test('available offers installed fitting LoRAs that are not already chosen', () => {
  assert.deepStrictEqual(M.available(LORAS, 'illustrious', []).map(l => l.id), ['a', 'd']);
  assert.deepStrictEqual(M.available(LORAS, 'illustrious', ['a']).map(l => l.id), ['d']);
  assert.deepStrictEqual(M.available(LORAS, null, []).map(l => l.id), ['a', 'b', 'd']);
  assert.deepStrictEqual(M.available(null, 'x', null), []);
});

test('a new row takes the registry weight and toSpec sends id and weight only', () => {
  const r = M.newRow(LORAS[0]);
  assert.deepStrictEqual([r.id, r.weight, r.triggers], ['a', 0.55, ['ta']]);
  assert.strictEqual(M.newRow({ id: 'z' }).weight, 1);
  r.weight = '0.8';
  assert.deepStrictEqual(M.toSpec([r, { id: 'q', weight: '' }, { weight: 1 }, null]), [{ id: 'a', weight: 0.8 }, { id: 'q', weight: 1 }]);
  assert.deepStrictEqual(M.toSpec(null), []);
  assert.strictEqual(M.weightOf({ weight: 'abc' }), 1);
  assert.strictEqual(M.weightOf({ weight: 0 }), 0);
});

test('the not-for-sale badge and warning come from the licence verdict', () => {
  assert.ok(M.notForSale(LORAS[1]) && !M.notForSale(LORAS[0]) && !M.notForSale({}) && !M.notForSale(null));
  assert.strictEqual(M.sellWarning([LORAS[0]]), '');
  assert.strictEqual(M.sellWarning([LORAS[0], LORAS[1]]), 'Not cleared for selling: B');
});

test('planRows shows what will be installed and flags the warnings', () => {
  const plan = { name: 'N', type: 'LORA', base_model: 'Illustrious', file: { name: 'f.safetensors', size_bytes: 10485760 }, trained_words: ['x', 'y'],
    licence: { commercial_use: 'conditional', text: 'Image ok' }, destination: '/m/loras/f.safetensors', nsfw: true, warnings: ['the base model is unknown'] };
  const rows = M.planRows(plan);
  const get = l => rows.find(r => r.label === l);
  assert.strictEqual(get('Size').text, '10.0 MB');
  assert.strictEqual(get('Trigger words').text, 'x, y');
  assert.strictEqual(get('Licence').text, 'conditional (Image ok)');
  assert.strictEqual(get('Installs to').text, '/m/loras/f.safetensors');
  assert.ok(get('Note').warn && get('Warning').warn && !get('Name').warn);
  assert.deepStrictEqual(M.planRows(null), []);
  assert.strictEqual(M.planRows({ file: {}, licence: {} }).find(r => r.label === 'Size').text, 'unknown');
});

test('the Import button is enabled only for a checked plan that would not overwrite a file', () => {
  assert.ok(M.canImport({ warnings: [] }));
  assert.ok(M.canImport({ warnings: ['the base model is unknown'] }));
  assert.ok(!M.canImport({ warnings: ['a file named x is already installed and will not be overwritten'] }));
  assert.ok(!M.canImport(null) && !M.canImport({ error: 'bad' }));
});

test('progressText follows the job through its stages', () => {
  assert.strictEqual(M.progressText(null), '');
  assert.strictEqual(M.progressText({ status: 'planning' }), 'Checking the link...');
  assert.strictEqual(M.progressText({ status: 'downloading', bytes: 5242880, total: 10485760 }), 'Downloading 50% of 10 MB');
  assert.strictEqual(M.progressText({ status: 'downloading', bytes: 3145728 }), 'Downloading 3 MB');
  assert.strictEqual(M.progressText({ status: 'verifying' }), 'Verifying the file...');
  assert.strictEqual(M.progressText({ status: 'done', result: { file: 'f.safetensors', registry_id: 'f' } }), 'Installed f.safetensors (registry id f)');
  assert.strictEqual(M.progressText({ status: 'error', error: 'boom' }), 'Failed: boom');
});

test('progressText says when the download goes through the signed-in session', () => {
  assert.strictEqual(M.progressText({ status: 'downloading', via: 'session', bytes: 0 }), 'Downloading through your signed-in Civitai session...');
  assert.strictEqual(M.progressText({ status: 'verifying', via: 'session' }), 'Verifying the file...');
  assert.strictEqual(M.progressText({ status: 'downloading', bytes: 3145728 }), 'Downloading 3 MB');
});

test('sessionLine words the Civitai sign-in state and when the button works', () => {
  assert.deepStrictEqual(M.sessionLine({ available: true, signed_in: true }), { text: 'Civitai sign-in: signed in', canSignIn: true });
  assert.deepStrictEqual(M.sessionLine({ available: true, signed_in: false }), { text: 'Civitai sign-in: not signed in', canSignIn: true });
  assert.deepStrictEqual(M.sessionLine({ available: false, signed_in: null }), { text: 'Civitai sign-in: unavailable', canSignIn: false });
  assert.deepStrictEqual(M.sessionLine(null), { text: 'Civitai sign-in: unavailable', canSignIn: false });
  assert.strictEqual(M.sessionLine({ available: true, signed_in: null, error: 'timeout' }).text, 'Civitai sign-in: unknown (timeout)');
});

test('the page wires the sign-in controls and shows the sign-in text only through textContent', () => {
  const fs = require('node:fs');
  const html = fs.readFileSync(__dirname + '/../web/index.html', 'utf8');
  const js = fs.readFileSync(__dirname + '/../web/app.js', 'utf8');
  assert.ok(html.includes('id="impSignIn"') && html.includes('id="impSessText"'));
  assert.ok(js.includes("'/api/models/session/login'") && js.includes("$('#impSessText').textContent"));
  assert.ok(js.includes('job.needs_signin'));
});
