const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const LINK = 'https://civitai.com/models/123';

// A canvas context that accepts any call or property write, so the pose editor can start under jsdom.
const noopCtx = new Proxy({}, {
  get: (t, k) => (k in t ? t[k] : () => noopCtx),
  set: () => true,
});

function openApp() {
  const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf-8');
  const js = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf-8');
  const dom = new JSDOM(html, { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  window.HTMLCanvasElement.prototype.getContext = () => noopCtx;
  window.eval(js);
  return window;
}

// Lets the boot chain (promises only, no timers) run far enough to reach initModelsPanel().
async function settle() {
  await null; await null; await null; await null; await null;
  await null; await null; await null; await null; await null;
}

test('focusing the import link box selects its whole value', async () => {
  const window = openApp();
  try {
    await settle();
    const input = window.document.querySelector('#impUrl');
    input.value = LINK;
    input.focus();
    assert.equal(input.selectionStart, 0);
    assert.equal(input.selectionEnd, input.value.length);
  } finally {
    window.close();
  }
});

test('Escape in the import link box clears it', async () => {
  const window = openApp();
  try {
    await settle();
    const input = window.document.querySelector('#impUrl');
    input.value = LINK;
    input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    assert.equal(input.value, '');
  } finally {
    window.close();
  }
});
