// The Library view (web/library.js helpers, web/app.js wiring): tags, filters, order, saved set, and the structure of the tab and the button.
// node --test test/*.test.js
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { parseTags, filterItems, sortNewest, savedSet, tagCounts, MAX_TAGS } = require('../web/library.js');

const read = (f) => fs.readFileSync(path.join(__dirname, '..', 'web', f), 'utf8');
const items = [
  { id: 'a1', file: '2026-11/aa_0.png', note: 'Red hat', tags: ['hat', 'red'], saved_at: 10 },
  { id: 'b2', file: '2026-11/bb_0.png', note: 'blue boots', tags: ['boots', 'blue'], saved_at: 30 },
  { id: 'c3', file: '2026-10/cc_0.png', note: '', tags: ['hat'], saved_at: 20 },
];

test('parseTags lowercases, trims, drops empties and duplicates and caps at 12', () => {
  assert.deepStrictEqual(parseTags('a, B ,,c, A'), ['a', 'b', 'c']);
  assert.deepStrictEqual(parseTags(['X', ' y ', 'x']), ['x', 'y']);
  assert.deepStrictEqual(parseTags(''), []);
  assert.deepStrictEqual(parseTags(null), []);
  assert.strictEqual(parseTags(Array.from({ length: 30 }, (_, i) => 't' + i)).length, MAX_TAGS);
  assert.strictEqual(parseTags('x'.repeat(100))[0].length, 40);
});

test('filterItems matches note, tags and source name in any case, and one tag', () => {
  assert.deepStrictEqual(filterItems(items, 'RED').map((i) => i.id), ['a1']);
  assert.deepStrictEqual(filterItems(items, 'boots').map((i) => i.id), ['b2']);
  assert.deepStrictEqual(filterItems(items, '2026-10').map((i) => i.id), ['c3']);
  assert.deepStrictEqual(filterItems(items, '', 'hat').map((i) => i.id), ['a1', 'c3']);
  assert.deepStrictEqual(filterItems(items, 'red', 'boots'), []);
  assert.deepStrictEqual(filterItems(items, '  ', '').map((i) => i.id), ['a1', 'b2', 'c3']);
  assert.deepStrictEqual(filterItems(undefined, 'x'), []);
});

test('sortNewest puts the latest saved first, is stable on ties and does not change the input', () => {
  const copy = items.slice();
  assert.deepStrictEqual(sortNewest(items).map((i) => i.id), ['b2', 'c3', 'a1']);
  assert.deepStrictEqual(items, copy);
  assert.deepStrictEqual(sortNewest([{ id: 'a', saved_at: 1 }, { id: 'b', saved_at: 1 }]).map((i) => i.id), ['b', 'a']);
});

test('savedSet holds the gallery files that are in the Library; tagCounts lists the tags most used first', () => {
  assert.deepStrictEqual([...savedSet(items)].sort(), ['2026-10/cc_0.png', '2026-11/aa_0.png', '2026-11/bb_0.png']);
  assert.strictEqual(savedSet(items).has('2026-11/zz_0.png'), false);
  assert.deepStrictEqual(tagCounts(items)[0], { tag: 'hat', count: 2 });
  assert.deepStrictEqual(tagCounts(items).map((t) => t.tag), ['hat', 'blue', 'boots', 'red']);
});

test('index.html has the Library view, its switch on wide screens, the phone tab and the script', () => {
  const h = read('index.html');
  assert.match(h, /<div id="library">/);
  assert.match(h, /id="libSearch"/);
  assert.match(h, /id="libTag"/);
  assert.match(h, /id="libGrid"/);
  assert.match(h, /<div id="areaSwitch" role="tablist">/);
  assert.match(h, /<button data-view="library">📚 Library<\/button>/);
  assert.ok(h.indexOf('/library.js') > 0 && h.indexOf('/library.js') < h.indexOf('/app.js'), 'library.js loads before app.js');
});

test('app.js switches three views and the lightbox gets a Save to Library button that is disabled once saved', () => {
  const a = read('app.js');
  assert.match(a, /classList\.toggle\('view-library', v === 'library'\)/);
  assert.match(a, /if \(v === 'library'\) loadLibrary\(\);/);
  assert.match(a, /saveToLibraryButton\(file\),/);
  const btn = a.slice(a.indexOf('function saveToLibraryButton'), a.indexOf('function markLibraryBadges'));
  assert.match(btn, /'📚 In the Library' : '📚 Save to Library'/);
  assert.match(btn, /if \(saved\) \{ b\.disabled = true; return b; \}/);
  assert.match(btn, /if \(note === null\) return;/);
  assert.match(btn, /fetch\('\/api\/library', \{ method: 'POST'/);
});

test('item text is shown with textContent or attributes, never innerHTML', () => {
  const a = read('app.js');
  const lib = a.slice(a.indexOf('// ---- the Library: pictures worth keeping'), a.indexOf('// ---- paged gallery'));
  assert.ok(lib.length > 500);
  assert.doesNotMatch(lib, /innerHTML/);
  assert.doesNotMatch(lib, /html:/);
});

test('removing asks first and says the picture is deleted after 1 day; the view has an empty state', () => {
  const a = read('app.js');
  assert.match(a, /confirm\('Remove this picture from the Library\? It is deleted after 1 day\./);
  assert.match(a, /Nothing saved yet: open a picture in the Gallery and press Save to Library\./);
});

test('the Library view takes the gallery\'s place and the switch is hidden on phones', () => {
  const c = read('style.css');
  assert.match(c, /body\.view-library #gallery \{ display: none; \}/);
  assert.match(c, /body\.view-library #library \{ display: flex;/);
  assert.match(c, /@media \(max-width: 860px\) \{\s*#areaSwitch \{ display: none; \}/);
  assert.match(c, /body\.view-library #inputs \{ display: none; \}/);
});
