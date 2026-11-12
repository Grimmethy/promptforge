'use strict';

// The expanded picture (the lightbox) closes with a corner X, a backdrop tap and Escape, all through closeLB (web/app.js).
// The wiring is checked structurally here; the behaviour was checked in a real browser (see the task text).

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const read = (f) => fs.readFileSync(path.join(__dirname, '..', 'web', f), 'utf8');

test('index.html has the close button inside the lightbox with an aria-label', () => {
  const h = read('index.html');
  const box = h.slice(h.indexOf('<div id="lightbox">'), h.indexOf('</div>', h.indexOf('<div class="info" id="lbInfo">')) + 6);
  assert.match(box, /<button id="lbClose" type="button" aria-label="Close">×<\/button>/);
  assert.ok(box.indexOf('lbClose') < box.indexOf('id="lbImg"'), 'the button comes first so it is above the picture');
});

test('closeLB is the single close path: backdrop, the X button and Escape all call it', () => {
  const a = read('app.js');
  assert.match(a, /function closeLB\(\) \{ \$\('#lightbox'\)\.classList\.remove\('on'\); \}/);
  assert.match(a, /\$\('#lightbox'\)\.onclick = e => \{ if \(e\.target\.id === 'lightbox'\) closeLB\(\); \};/);
  assert.match(a, /\$\('#lbClose'\)\.onclick = closeLB;/);
  assert.match(a, /else if \(\$\('#lightbox'\)\.classList\.contains\('on'\) && !\$\('#assignPanel'\)\.classList\.contains\('on'\)\) closeLB\(\);/);
});

test('Escape still closes the regenerate panel first and ignores other keys', () => {
  const a = read('app.js');
  const h = a.slice(a.indexOf("document.addEventListener('keydown', e => {\n  if (e.key !== 'Escape') return;"));
  assert.ok(h.length > 0, 'the combined handler exists');
  assert.ok(h.indexOf("$('#regenPanel').classList.contains('on')) closeRegen();") > 0);
  assert.ok(h.indexOf('closeRegen();') < h.indexOf('closeLB();'), 'regenerate is handled before the lightbox');
});

test('opening a picture scrolls the overlay to the top, and the in-lightbox buttons are unchanged', () => {
  const a = read('app.js');
  const open = a.slice(a.indexOf('function openLB'), a.indexOf('function closeLB'));
  assert.match(open, /\$\('#lightbox'\)\.scrollTop = 0;/);
  assert.match(open, /'🎨 Regenerate from this image'/);
  assert.match(open, /'➕ Assign to…'/);
  assert.match(open, /'🧼 Download clean copy'/);
});

test('the X is fixed in the top right corner, at least 44px, above the picture', () => {
  const c = read('style.css');
  const rule = c.slice(c.indexOf('#lbClose {'), c.indexOf('}', c.indexOf('#lbClose {')));
  assert.match(rule, /position: fixed;/);
  assert.match(rule, /top: calc\(10px/);
  assert.match(rule, /right: calc\(10px/);
  assert.match(rule, /z-index: 60;/);
  assert.match(rule, /width: 44px; height: 44px;/);
  assert.match(rule, /border-radius: 50%/);
});
