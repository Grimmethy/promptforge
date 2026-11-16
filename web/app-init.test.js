// web/app-init.js presets loading: never-resolving, later-resolving, rejecting + retry.
// Hand-built fake document, deferred promises, no timers. node --test web/app-init.test.js
const test = require('node:test');
const assert = require('node:assert');

class FakeEl {
  constructor(tag) {
    this.tag = tag.toLowerCase();
    this.children = [];
    this.parentElement = null;
    this.listeners = {};
    this.dataset = {};
    this.style = {};
    this.id = '';
    this.className = '';
    this.textContent = '';
    this.value = '';
    this.disabled = false;
  }
  set innerHTML(v) {
    for (const c of this.children) c.parentElement = null;
    this.children = [];
  }
  get innerHTML() { return ''; }
  get options() { return this.children.filter((c) => c.tag === 'option'); }
  appendChild(el) {
    if (el.parentElement) el.remove();
    el.parentElement = this;
    this.children.push(el);
    return el;
  }
  prepend(el) {
    if (el.parentElement) el.remove();
    el.parentElement = this;
    this.children.unshift(el);
  }
  insertAdjacentElement(pos, el) {
    assert.strictEqual(pos, 'afterend');
    const p = this.parentElement;
    if (el.parentElement) el.remove();
    el.parentElement = p;
    p.children.splice(p.children.indexOf(this) + 1, 0, el);
    return el;
  }
  remove() {
    const p = this.parentElement;
    if (p) p.children.splice(p.children.indexOf(this), 1);
    this.parentElement = null;
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  click() {
    for (const fn of this.listeners.click || []) fn({ target: this });
  }
  matches(sel) {
    const m = /^([a-z]*)(?:#([\w-]+))?(?:\.([\w-]+))?(?:\[data-([\w-]+)\])?$/i.exec(sel);
    if (!m) throw new Error('unsupported selector ' + sel);
    const [, tag, id, cls, data] = m;
    if (tag && this.tag !== tag.toLowerCase()) return false;
    if (id && this.id !== id) return false;
    if (cls && !this.className.split(/\s+/).includes(cls)) return false;
    if (data && this.dataset[data] === undefined) return false;
    return true;
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (el) => {
      for (const c of el.children) {
        if (c.matches(sel)) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function makeDocument() {
  const body = new FakeEl('body');
  for (let i = 0; i < 2; i++) {
    const sel = new FakeEl('select');
    sel.dataset.presets = '';
    body.appendChild(sel);
  }
  return {
    body,
    createElement: (t) => new FakeEl(t),
    querySelector: (s) => body.querySelector(s),
    querySelectorAll: (s) => body.querySelectorAll(s),
  };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const okRes = (data) => ({ ok: true, status: 200, json: async () => data });

async function flush() {
  for (let i = 0; i < 30; i++) await Promise.resolve();
}

function makeFetch(getPresets) {
  return (url) => (url === '/api/presets' ? getPresets() : Promise.resolve(okRes({ text: 'ok' })));
}

const PRESETS = [{ id: 'a', name: 'Alpha' }, 'beta'];

async function load() {
  return import('./app-init.js');
}

test('(a) never-resolving presets: initApp resolves, shell present, selects loading', async () => {
  const { initApp } = await load();
  const doc = makeDocument();
  const d = deferred();
  const result = await initApp({ fetch: makeFetch(() => d.promise), document: doc });
  assert.ok(!result.error, 'initApp should not report an error');
  assert.ok(doc.querySelector('#queueSummary'), 'queue bar summary exists');
  assert.ok(doc.querySelector('#runQueueBtn'), 'Run control exists');
  const selects = doc.querySelectorAll('select[data-presets]');
  assert.strictEqual(selects.length, 2);
  for (const sel of selects) {
    assert.strictEqual(sel.disabled, true);
    assert.strictEqual(sel.options.length, 1);
    assert.strictEqual(sel.options[0].textContent, 'loading...');
  }
});

test('(b) later-resolving presets fill the selects and enable them', async () => {
  const { initApp } = await load();
  const doc = makeDocument();
  const d = deferred();
  await initApp({ fetch: makeFetch(() => d.promise), document: doc });
  d.resolve(okRes(PRESETS));
  await flush();
  for (const sel of doc.querySelectorAll('select[data-presets]')) {
    assert.deepStrictEqual(sel.options.map((o) => o.value), ['a', 'beta']);
    assert.deepStrictEqual(sel.options.map((o) => o.textContent), ['Alpha', 'beta']);
    assert.ok(!sel.options.some((o) => o.textContent === 'loading...'));
    assert.strictEqual(sel.disabled, false);
  }
});

test('(c) rejecting presets show error + retry; retry fills the selects', async () => {
  const { initApp } = await load();
  const doc = makeDocument();
  const d = deferred();
  let getPresets = () => d.promise;
  await initApp({ fetch: makeFetch(() => getPresets()), document: doc });
  d.reject(new Error('boom'));
  await flush();
  const area = doc.querySelector('#presetsError');
  assert.ok(area, 'error area appears');
  assert.match(area.textContent, /boom/);
  const retry = doc.querySelector('#presetsRetryBtn');
  assert.ok(retry, 'retry button appears');
  for (const sel of doc.querySelectorAll('select[data-presets]')) assert.strictEqual(sel.disabled, true);

  getPresets = () => Promise.resolve(okRes(PRESETS));
  retry.click();
  await flush();
  for (const sel of doc.querySelectorAll('select[data-presets]')) {
    assert.deepStrictEqual(sel.options.map((o) => o.value), ['a', 'beta']);
    assert.ok(!sel.options.some((o) => o.textContent === 'loading...'));
    assert.strictEqual(sel.disabled, false);
  }
  assert.strictEqual(doc.querySelector('#presetsError').textContent, '');
});
