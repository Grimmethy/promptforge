'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createPresetsCache } = require('./presets-cache.js');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'presets-cache-test-'));
after(() => fs.rmSync(root, { recursive: true, force: true }));

function fixture(name, obj) {
  const file = path.join(root, name);
  fs.writeFileSync(file, JSON.stringify(obj));
  return file;
}

function countingLoader(file) {
  const load = async () => {
    load.calls += 1;
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  };
  load.calls = 0;
  return load;
}

test('second call is served from cache', async () => {
  const file = fixture('hit.json', { a: 1 });
  const load = countingLoader(file);
  const cache = createPresetsCache({ sources: [file], load });
  assert.deepEqual(await cache.get(), { a: 1 });
  assert.deepEqual(await cache.get(), { a: 1 });
  assert.equal(load.calls, 1);
});

test('changed content and touch-only edits trigger a rebuild', async () => {
  const file = fixture('change.json', { a: 1 });
  const load = countingLoader(file);
  const cache = createPresetsCache({ sources: [file], load });
  await cache.get();

  fs.writeFileSync(file, JSON.stringify({ a: 22 })); // size changes
  assert.deepEqual(await cache.get(), { a: 22 });
  assert.equal(load.calls, 2);

  const later = new Date(Date.now() + 3600 * 1000);
  fs.utimesSync(file, later, later); // same content, new mtime only
  assert.deepEqual(await cache.get(), { a: 22 });
  assert.equal(load.calls, 3);
});

test('concurrent cold calls share one build', async () => {
  const file = fixture('cold.json', { c: 1 });
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let calls = 0;
  const cache = createPresetsCache({
    sources: [file],
    load: async () => {
      calls += 1;
      await gate;
      return JSON.parse(fs.readFileSync(file, 'utf8'));
    },
  });
  const pending = [1, 2, 3, 4, 5].map(() => cache.get());
  release();
  const results = await Promise.all(pending);
  assert.equal(calls, 1);
  for (const r of results) assert.deepEqual(r, { c: 1 });
});

test('failed rebuild keeps serving the last good value', async () => {
  const file = fixture('fail.json', { v: 1 });
  let fail = false;
  const load = async () => {
    if (fail) throw new Error('boom');
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  };
  const cache = createPresetsCache({ sources: [file], load });
  assert.deepEqual(await cache.get(), { v: 1 });

  fail = true;
  fs.writeFileSync(file, JSON.stringify({ v: 222 }));
  assert.deepEqual(await cache.get(), { v: 1 });

  fail = false;
  assert.deepEqual(await cache.get(), { v: 222 });
});

test('cold failure rethrows and the next call retries', async () => {
  const file = fixture('cold-fail.json', { x: 1 });
  let fail = true;
  const load = async () => {
    if (fail) throw new Error('boom');
    return { x: 1 };
  };
  const cache = createPresetsCache({ sources: [file], load });
  await assert.rejects(cache.get(), /boom/);
  fail = false;
  assert.deepEqual(await cache.get(), { x: 1 });
});
