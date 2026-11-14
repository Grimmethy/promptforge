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

  const api = { formatDuration, summaryText, groupByRequester, jobRow };
  root.PFQueue = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
