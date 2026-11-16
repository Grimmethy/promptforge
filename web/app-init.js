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

export async function initApp({ fetch, document } = {}) {
  try {
    const root = resolveRoot(document);
    const bar = ensureQueueBar(document, root);
    const summaryEl = ensureSummaryEl(document, bar);
    const runBtn = ensureRunButton(document, bar);
    setLoading(summaryEl, runBtn);

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
