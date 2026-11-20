// web/queue.js run-outcome logic: refused reasons, stale reasons, started-toast gating. node --test web/queue.test.js
const test = require('node:test');
const assert = require('node:assert');
const { runOutcome, startedToastAllowed, summaryText } = require('./queue.js');

const st = (phase, reason, exit) => ({ phase, reason, exit });

const IO = 'I/O pressure is too high to start a window';
const MEM = 'MemAvailable 7853 MB is below the 8192 MB needed';

// Phases in the order scripts/jobs/window_run.py writes them: forge-on, then either refused / forge-failed (run ends) or rendering, leaving, done.
const THROUGH_FORGE_ON = ['forge-on', 'refused', 'forge-failed'];
const LATER = ['rendering', 'leaving', 'done'];

test('(a) a refused state renders its reason in the summary', () => {
  const s = st('refused', IO, 4);
  assert.ok(summaryText(s).includes(IO));
  const o = runOutcome(s);
  assert.strictEqual(o.phase, 'refused');
  assert.strictEqual(o.reason, IO);
  assert.strictEqual(o.failed, true);
  assert.strictEqual(o.started, false);
});

test('(b) a stale reason is replaced by the new one', () => {
  const first = summaryText(st('refused', IO, 4));
  const second = summaryText(st('refused', MEM, 4));
  assert.ok(first.includes('I/O pressure'));
  assert.ok(second.includes('MemAvailable 7853 MB'));
  assert.ok(!second.includes('I/O pressure'));
  assert.strictEqual(runOutcome(st('refused', MEM, 4)).reason, MEM);
});

test('(c) startedToastAllowed is false through forge-on and for refused/forge-failed', () => {
  for (const p of THROUGH_FORGE_ON) {
    assert.strictEqual(startedToastAllowed(st(p, '', null)), false, p);
  }
  assert.strictEqual(startedToastAllowed(st('refused', MEM, 4)), false);
  assert.strictEqual(startedToastAllowed(st('forge-failed', 'timed out', 5)), false);
});

test('(d) startedToastAllowed is true only for later phases', () => {
  for (const p of LATER) {
    assert.strictEqual(startedToastAllowed(st(p, '', null)), true, p);
  }
  assert.strictEqual(startedToastAllowed(null), false);
  assert.strictEqual(startedToastAllowed({}), false);
});

test('(e) a new run clears the old reason', () => {
  assert.strictEqual(runOutcome(st('refused', IO, 4)).reason, IO);
  const fresh = st('forge-on', undefined, null);
  assert.strictEqual(runOutcome(fresh).reason, '');
  assert.ok(!summaryText(fresh).includes('I/O pressure'));
  const rendering = st('rendering', undefined, null);
  assert.strictEqual(runOutcome(rendering).reason, '');
  assert.ok(!summaryText(rendering).includes('I/O pressure'));
});
