/* PromptForge Studio: which of these gallery files still need a tile?
   One source of truth for the two paths that add tiles (the gallery-new push and the job poll), so a finished image gets exactly one tile
   whichever of them reaches the page first. Pure function, loaded before app.js; node can load it too (module.exports) for the unit tests. */
(function (root) {
  // The entries of `files` that are not in the `shown` set, in their original order. Does not change the set; tolerates a missing list.
  const freshFiles = (shown, files) => (files || []).filter(f => !shown.has(f));

  const api = { freshFiles };
  root.PFTileDedupe = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
