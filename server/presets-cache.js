'use strict';
const fs = require('node:fs');
const path = require('node:path');

// Stat one path. A missing path is a signature entry, not an error.
async function statEntry(p) {
  try {
    const st = await fs.promises.stat(p);
    return {
      line: p + '|' + (st.isDirectory() ? 'd' : 'f') + '|' + st.mtimeMs + '|' + st.size,
      isDir: st.isDirectory(),
    };
  } catch (err) {
    if (err && err.code === 'ENOENT') return { line: p + '|missing', isDir: false };
    throw err;
  }
}

// Signature over every source plus the direct entries of any source directory.
async function computeSignature(sources) {
  const lines = [];
  for (const src of sources) {
    const entry = await statEntry(src);
    lines.push(entry.line);
    if (entry.isDir) {
      const names = (await fs.promises.readdir(src)).sort();
      for (const name of names) {
        lines.push((await statEntry(path.join(src, name))).line);
      }
    }
  }
  return lines.join('\n');
}

function createPresetsCache({ sources = [], load }) {
  if (typeof load !== 'function') throw new TypeError('createPresetsCache: load must be a function');

  let cached = null; // { signature, value } of the last successful build
  let inFlight = null; // { signature, promise } of the build currently running

  async function get() {
    const signature = await computeSignature(sources);
    if (cached && cached.signature === signature) return cached.value;
    if (inFlight && inFlight.signature === signature) return inFlight.promise;

    const build = (async () => {
      await null; // let inFlight be assigned before the build body runs
      try {
        const value = await load();
        cached = { signature, value };
        return value;
      } catch (err) {
        if (cached) return cached.value; // keep serving the last good value
        throw err;
      } finally {
        if (inFlight && inFlight.promise === build) inFlight = null;
      }
    })();

    inFlight = { signature, promise: build };
    return build;
  }

  return { get };
}

module.exports = { createPresetsCache };
