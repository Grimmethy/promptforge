/* PromptForge Studio: the render job queue in the header. Pure functions only (no DOM, no fetch, no timers), loaded before app.js; node can load it too
   (module.exports) for the unit tests. The wiring lives in app.js (queueStatus). */
(function (root) {
  // Same wording as scripts/jobs/runner.py format_seconds.
  const formatDuration = (seconds) => {
    const n = Math.round(Number(seconds) || 0);
    if (n < 60) return n + 's';
    if (n < 3600) return Math.floor(n / 60) + 'm';
    const m = Math.floor((n % 3600) / 60);
    return Math.floor(n / 3600) + 'h ' + (m < 10 ? '0' : '') + m + 'm';
  };

  // 'Queue: 3 jobs · 12 pictures · ~4m (rough)', or '' when there is nothing queued.
  const summaryText = (est) => {
    if (!est || !est.queued) return '';
    const jobs = est.queued + (est.queued === 1 ? ' job' : ' jobs');
    const pics = est.pictures + (est.pictures === 1 ? ' picture' : ' pictures');
    return 'Queue: ' + jobs + ' · ' + pics + ' · ~' + formatDuration(est.total_seconds) + (est.rough ? ' (rough)' : '');
  };

  // [{requester, jobs, pictures}] sorted by requester; jobs keep their input order.
  const groupByRequester = (jobs) => {
    const by = new Map();
    for (const j of jobs || []) {
      if (!by.has(j.requester)) by.set(j.requester, { requester: j.requester, jobs: [], pictures: 0 });
      const g = by.get(j.requester);
      g.jobs.push(j);
      g.pictures += (j.seeds || []).length;
    }
    return [...by.values()].sort((a, b) => (a.requester < b.requester ? -1 : a.requester > b.requester ? 1 : 0));
  };

  const jobRow = (job, perJob) => ({
    id: job.id, purpose: job.purpose || '', model: job.model, seeds: (job.seeds || []).length, priority: job.priority, state: job.state,
    seconds: perJob && perJob[job.id] != null ? perJob[job.id] : null,
    canHold: job.state === 'queued', canRelease: job.state === 'held', canRemove: job.state === 'queued' || job.state === 'held',
  });

  // A stop time between 0.25 and 12 hours, from what was typed; null when it is anything else.
  const parseHours = (text) => {
    const s = String(text == null ? '' : text).trim();
    if (!/^\d+(\.\d+)?$/.test(s)) return null;
    const n = Number(s);
    return n >= 0.25 && n <= 12 ? n : null;
  };

  // The Run queue button is offered when something is queued and no window is running.
  const canRun = (est, run) => !!(est && est.queued > 0) && !(run && run.running);

  // What the dialog says before anything starts (it must say what happens to the machine).
  const confirmText = (est, hours) => {
    const work = est && est.queued
      ? ' and renders ' + est.pictures + (est.pictures === 1 ? ' picture' : ' pictures') + ' (about ' + formatDuration(est.total_seconds) + (est.rough ? ', rough' : '') + ')'
      : '';
    return 'This pauses the Agent Manager pipeline, unloads the local model, takes the GPU' + work + ', and stops by itself after at most '
      + hours + (Number(hours) === 1 ? ' hour' : ' hours') + '. Nothing else can use the GPU until it ends.';
  };

  // The header text while a window runs, or why the last one did not. fmtTime turns the ISO stop time into a clock time (the page passes a local formatter).
  const PHASES = { 'forge-on': 'entering Forge mode', rendering: 'rendering', leaving: 'leaving Forge mode' };
  const runLabel = (run, fmtTime) => {
    if (!run) return '';
    if (run.running) {
      const until = run.until ? ' · until ' + (fmtTime ? fmtTime(run.until) : run.until) : '';
      return 'Window: ' + (PHASES[run.phase] || run.phase) + ' · ' + (run.pictures || 0) + (run.pictures === 1 ? ' picture' : ' pictures') + until;
    }
    if (run.phase === 'refused' || run.phase === 'forge-failed') return 'Last window did not start: ' + ((run.reasons || []).join('; ') || run.phase);
    if (run.phase === 'ended') return 'Last window ended unexpectedly';
    return '';
  };

  // The one header line: while a window runs it is the window's progress; otherwise the queue summary, followed by why the last window did not start (if so).
  const headerText = (summary, run, fmtTime) => {
    const label = runLabel(run, fmtTime);
    return run && run.running ? label : [summary, label].filter(Boolean).join(' · ');
  };

  const api = { formatDuration, summaryText, groupByRequester, jobRow, parseHours, canRun, confirmText, runLabel, headerText };
  root.PFQueue = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
