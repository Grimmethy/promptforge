// The Library view's pure helpers (no DOM): tags, filtering, order and the set of saved gallery files. node --test test/*.test.js
(function (root) {
  const MAX_TAGS = 12;

  // 'a, B ,,c' (or a list) -> ['a', 'b', 'c']: lowercase, trimmed, no duplicates, at most MAX_TAGS, each at most 40 characters.
  const parseTags = (input) => {
    const raw = Array.isArray(input) ? input : String(input == null ? '' : input).split(',');
    const out = [];
    for (const t of raw) {
      const v = String(t).trim().toLowerCase().slice(0, 40);
      if (v && !out.includes(v)) out.push(v);
    }
    return out.slice(0, MAX_TAGS);
  };

  // Items whose note, tags or source name contain the text (any case) and, when a tag is given, that tag.
  const filterItems = (items, query, tag) => {
    const q = String(query || '').trim().toLowerCase(), t = String(tag || '').trim().toLowerCase();
    return (items || []).filter((it) => {
      const hay = `${it.note || ''} ${(it.tags || []).join(' ')} ${it.file || ''}`.toLowerCase();
      return (!q || hay.includes(q)) && (!t || (it.tags || []).includes(t));
    });
  };

  // Newest saved first (ties by id so the order is stable); the input is not changed.
  const sortNewest = (items) => (items || []).slice().sort((a, b) => ((b.saved_at || 0) - (a.saved_at || 0)) || String(b.id).localeCompare(String(a.id)));

  // The gallery files that are already in the Library.
  const savedSet = (items) => new Set((items || []).map((it) => it.file));

  // Every tag in use, most used first, then alphabetical (for the tag filter).
  const tagCounts = (items) => {
    const n = {};
    for (const it of items || []) for (const t of it.tags || []) n[t] = (n[t] || 0) + 1;
    return Object.entries(n).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([tag, count]) => ({ tag, count }));
  };

  const api = { parseTags, filterItems, sortNewest, savedSet, tagCounts, MAX_TAGS };
  root.PFLibrary = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
