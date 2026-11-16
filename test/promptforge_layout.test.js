// Static checks for the PromptForge left-column reflow fix. node --test test/promptforge_layout.test.js
// jsdom is not installed and layout is not computed here, so a real-browser check of the shift is still needed.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const app = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'web', 'style.css'), 'utf8');
const LABEL = 'ComfyUI is not running: picking a model and Generate are off; model import still works';

function rule(re) {
  const m = css.match(re);
  return m ? m[0] : '';
}

test('ComfyUI-down Model label explains that import still works', () => {
  assert.ok(app.includes(LABEL));
  assert.ok(!app.includes('(ComfyUI down)'));
  assert.ok(/fillSelect\('#model'[^\n]*\n\s*decorateModels\(\)/.test(app));
});

test('left column and selects reserve their height', () => {
  assert.match(rule(/#inputs \.scroll \{[^}]*\}/), /min-height/);
  assert.match(rule(/#inputs select \{[^}]*height[^}]*\}/), /height: 28px/);
  assert.match(rule(/#inputs select, #inputs input\[type=text\] \{[^}]*\}/), /min-height: 28px/);
});

test('gallery container has a bounded height', () => {
  assert.match(rule(/\n#gallery \{[^}]*\}/), /height: 100%/);
});
