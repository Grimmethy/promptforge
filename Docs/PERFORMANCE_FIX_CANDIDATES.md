# Performance Fix Candidates

### AC-1 · Replace JSON round-trip deep clone with structuredClone
Strength: Strong
Files: web/editor.js
Snippet:
```
    while (this.rigs.length < n) this.rigs.push(defaultRig(0.22));
    this._snap(); this.draw(); this._emit();
  }
  getRigs() { return this.rigs.map(r => JSON.parse(JSON.stringify(r))); }

  getFrame(w, h) {
    return {
```

Problem:
The `getRigs()` getter deep-clones each element of `this.rigs` via `JSON.parse(JSON.stringify(r))`. For every rig object this forces a full serialize-to-string allocation, a reparse, and a second object-graph allocation, even though each rig is a small, mostly-flat structure (a few numbers, a couple of strings, and the `draw`/`_snap`/`_emit` method references that JSON silently drops). The cost is sub-microsecond per element and the array is bounded to a handful of entries, so the absolute impact is negligible, but the pattern is strictly more expensive than the native alternative and, more importantly, silently corrupts any `undefined`-valued or `Symbol`-keyed properties and converts `Date` instances to strings, which is a latent correctness hazard the perf cost merely masks.

Solution:
Replace the `JSON.parse(JSON.stringify(r))` expression inside the `.map()` callback with `structuredClone(r)`. `structuredClone` has been baseline in every shipping browser since mid-2022, performs the copy in a single native pass with no intermediate string allocation, and correctly preserves `undefined` properties, `Symbol` keys, `Date` objects, and other structured-cloneable types. No other code in the getter or its callers needs to change; the return shape is identical for the flat numeric/string payloads the rigs actually carry.

Benefits:
Eliminates the two extra heap allocations (the serialized string and the re-parsed object graph) per rig element, shaving a few tens of nanoseconds off each getter call. Removes the silent data-loss edge cases around `undefined` properties, `Symbol` keys, and `Date` serialization so that future rig fields of those types are cloned faithfully without a second code change. The diff is a one-token swap, keeps the defensive-copy contract intact, and aligns the codebase with the 2026 baseline API surface.
