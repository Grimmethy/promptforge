// The Gallery sweep box on the page (web/index.html, web/app.js): a read-only plan with the owner's on/off switch; text only. node --test test/*.test.js
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const read = (f) => fs.readFileSync(path.join(__dirname, '..', 'web', f), 'utf8');

test('index.html has the sweep box with its text, state and the two buttons', () => {
  const h = read('index.html');
  const box = h.slice(h.indexOf('<details id="sweepBox">'), h.indexOf('</details>', h.indexOf('<details id="sweepBox">')));
  assert.match(box, /id="sweepState"/);
  assert.match(box, /id="sweepText"/);
  assert.match(box, /id="sweepEnable"/);
  assert.match(box, /id="sweepDisable"/);
  assert.match(box, /nothing is deleted by looking/i);
});

test('the plan is read when the box opens and shown as text, never as html', () => {
  const a = read('app.js');
  const code = a.slice(a.indexOf('// ---- the gallery sweep box'));
  assert.match(code, /addEventListener\('toggle', \(\) => \{ if \(\$\('#sweepBox'\)\.open\) loadSweepPlan\(\); \}\)/);
  assert.match(code, /fetch\('\/api\/sweep\/plan'\)/);
  assert.match(code, /\$\('#sweepText'\)\.textContent = /);
  assert.doesNotMatch(code, /innerHTML/);
  assert.match(code, /\$\('#sweepState'\)\.textContent = p\.enabled \? '\(ON\)' : '\(off\)'/);
});

test('enabling asks first and says what happens; the buttons follow the state', () => {
  const code = read('app.js').slice(read('app.js').indexOf('// ---- the gallery sweep box'));
  assert.match(code, /confirm\('Switch the 7-day gallery sweep ON\? Pictures older than 7 days that are not in the Library go to the trash \(deleted after 1 day\)\.'\)/);
  assert.match(code, /fetch\('\/api\/sweep\/enable', \{ method: 'POST' \}\)/);
  assert.match(code, /fetch\('\/api\/sweep\/disable', \{ method: 'POST' \}\)/);
  assert.match(code, /\$\('#sweepEnable'\)\.disabled = !!p\.enabled; \$\('#sweepDisable'\)\.disabled = !p\.enabled;/);
});

test('the page never calls the run route by itself: only enable, disable and plan', () => {
  assert.doesNotMatch(read('app.js'), /api\/sweep\/run/);
});
