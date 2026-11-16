// Decoupled app initialisation: loading shell plus queue summary / Run estimate.
// No timers: this module performs one pass of requests and never polls.

const ROOT_SELECTORS = ['#queueSummary', '#runQueueBtn', 'select'];

function resolveRoot(document) {
  for (const sel of ROOT_SELECTORS) {
    const el = document.querySelector(sel);
    if (el) return el.parentElement || el;
  }
  return document.body;
}

function ensureQueueBar(document, root) {
  let bar = document.querySelector('#queueSummary')?.parentElement || null;
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'queueBar';
    root.appendChild(bar);
  }
  return bar;
}

function ensureSummaryEl(document, bar) {
  let el = document.querySelector('#queueSummary');
  if (!el) {
    el = document.createElement('a');
    el.id = 'queueSummary';
    el.className = 'muted';
    el.href = '#';
    bar.prepend(el);
  }
  return el;
}

function ensureRunButton(document, bar) {
  let btn = document.querySelector('#runQueueBtn');
  if (!btn) {
    btn = document.createElement('button');
    btn.id = 'runQueueBtn';
    btn.className = 'small';
    btn.textContent = 'Run queue';
    bar.appendChild(btn);
  }
  return btn;
}

function ensureRunError(document, btn) {
  let err = btn.parentElement?.querySelector('.runError') || null;
  if (!err) {
    err = document.createElement('span');
    err.className = 'runError muted';
    btn.insertAdjacentElement('afterend', err);
  }
  return err;
}

async function fetchJson(fetch, url) {
  const res = await fetch(url);
  if (!res || !res.ok) {
    throw new Error(`${url} failed${res ? ` (${res.status})` : ''}`);
  }
  return res.json();
}

function setLoading(summaryEl, runBtn) {
  summaryEl.textContent = 'Loading queue…';
  runBtn.disabled = true;
  runBtn.style.display = '';
}

function renderSummary(summaryEl, result) {
  if (result.status === 'fulfilled') {
    const d = result.value;
    const text = typeof d === 'string'
      ? d
      : (d && typeof d.text === 'string' ? d.text : JSON.stringify(d));
    summaryEl.textContent = text;
    summaryEl.style.display = '';
  } else {
    summaryEl.textContent = 'Queue summary unavailable: ' + String(result.reason?.message || result.reason);
    summaryEl.style.display = '';
  }
}

function renderEstimate(document, runBtn, result) {
  const errEl = ensureRunError(document, runBtn);
  if (result.status === 'fulfilled') {
    const d = result.value || {};
    errEl.textContent = '';
    const label = typeof d.text === 'string' ? d.text : (typeof d.estimate === 'string' ? d.estimate : '');
    if (label) runBtn.title = label;
    runBtn.disabled = false;
    runBtn.style.display = '';
  } else {
    runBtn.disabled = true;
    errEl.textContent = 'Estimate unavailable: ' + String(result.reason?.message || result.reason);
    runBtn.style.display = '';
  }
}

const PRESETS_URL = '/api/presets';

function errMsg(err) {
  return String(err?.message || err);
}

function findPresetSelects(document) {
  return Array.from(document.querySelectorAll('select[data-presets]'));
}

function clearPresetsError(document) {
  const area = document.querySelector('#presetsError');
  if (area) area.textContent = '';
}

// Error display step. Its own catch cannot write to the error area it failed to build,
// so that fallback goes to the console.
function showPresetsError(document, message, onRetry) {
  try {
    let area = document.querySelector('#presetsError');
    if (!area) {
      area = document.createElement('div');
      area.id = 'presetsError';
      area.className = 'runError muted';
      const first = findPresetSelects(document)[0];
      if (first) first.insertAdjacentElement('afterend', area);
      else document.body.appendChild(area);
    }
    area.textContent = message;
    if (onRetry) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'small';
      btn.id = 'presetsRetryBtn';
      btn.textContent = 'Retry';
      btn.addEventListener('click', () => onRetry());
      area.appendChild(btn);
    }
  } catch (e) {
    console.error('Presets error display failed:', e);
  }
}

// Render step 1: replace each presets select's contents with one 'loading...' option.
function insertLoadingOption(document) {
  try {
    for (const sel of findPresetSelects(document)) {
      sel.innerHTML = '';
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'loading...';
      opt.dataset.loading = 'true';
      sel.appendChild(opt);
    }
  } catch (err) {
    showPresetsError(document, 'Presets render failed: ' + errMsg(err));
  }
}

// Render step 2: remove the loading option and add one option per preset.
function populatePresets(document, data) {
  try {
    const list = Array.isArray(data)
      ? data
      : (data && Array.isArray(data.presets) ? data.presets : []);
    for (const sel of findPresetSelects(document)) {
      for (const opt of Array.from(sel.querySelectorAll('option[data-loading]'))) opt.remove();
      for (const p of list) {
        const isStr = typeof p === 'string';
        const opt = document.createElement('option');
        opt.value = isStr ? p : String(p.id ?? p.name ?? '');
        opt.textContent = isStr ? p : String(p.name ?? p.id ?? '');
        sel.appendChild(opt);
      }
    }
  } catch (err) {
    showPresetsError(document, 'Presets render failed: ' + errMsg(err));
  }
}

// Render step 3: enable or disable every presets select.
function setPresetSelectsDisabled(document, disabled) {
  try {
    for (const sel of findPresetSelects(document)) sel.disabled = disabled;
  } catch (err) {
    showPresetsError(document, 'Presets render failed: ' + errMsg(err));
  }
}

// One fetch per call. Retry re-invokes this function, so each click makes exactly one request.
function loadPresets(fetch, document) {
  clearPresetsError(document);
  return fetchJson(fetch, PRESETS_URL).then(
    (data) => {
      populatePresets(document, data);
      setPresetSelectsDisabled(document, false);
    },
    (err) => {
      setPresetSelectsDisabled(document, true);
      showPresetsError(
        document,
        'Presets unavailable: ' + errMsg(err),
        () => loadPresets(fetch, document),
      );
    },
  );
}

// Starts the presets chain. Not awaited by initApp, so initApp does not wait for it.
function startPresets(fetch, document) {
  insertLoadingOption(document);
  setPresetSelectsDisabled(document, true);
  loadPresets(fetch, document);
}

export async function initApp({ fetch, document } = {}) {
  try {
    const root = resolveRoot(document);
    const bar = ensureQueueBar(document, root);
    const summaryEl = ensureSummaryEl(document, bar);
    const runBtn = ensureRunButton(document, bar);
    setLoading(summaryEl, runBtn);

    // Presets run on their own chain. Not awaited, so initApp does not wait for /api/presets.
    startPresets(fetch, document);

    const [summaryRes, estimateRes] = await Promise.allSettled([
      fetchJson(fetch, '/api/jobs/summary'),
      fetchJson(fetch, '/api/jobs/estimate'),
    ]);

    renderSummary(summaryEl, summaryRes);
    renderEstimate(document, runBtn, estimateRes);
    return { root, bar, summaryEl, runBtn };
  } catch (err) {
    return { error: err };
  }
}
