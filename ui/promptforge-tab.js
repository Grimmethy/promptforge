'use strict';

// PromptForge's own dashboard tab, served by agent-manager's manifest-driven tab
// mechanism (docs/PLUGIN_API.md "Dashboard tab" section in agent-manager;
// Docs/hub-tasks-extraction-plan.md section 5 there). This is agent-manager's first real
// (non-fixture) use of that mechanism -- a live test subject, replacing the tab content
// that used to be hardcoded in agent-manager's own analytics-and-discovery.js
// (renderPromptForgeTab). plugins.json's promptforge entry declares:
//
//   "tab": {
//     "key": "promptforge", "label": "🎨 PromptForge", "kind": "script",
//     "script": "ui/promptforge-tab.js", "replaces": "promptforge"
//   }
//
// `replaces: "promptforge"` swaps this in for agent-manager's own hardcoded core row of
// the same key -- so if this plugin is ever disabled or fails validation, agent-manager's
// original hardcoded tab (and its /api/promptforge/config-driven iframe) reappears
// automatically; nothing here is a one-way replacement.
//
// Behavior is intentionally identical to the code this replaces: fetch
// /api/promptforge/config (an agent-manager core route -- PROMPTFORGE_URL env var, else
// the :7430 convention) and embed it as an iframe, reusing the existing iframe element on
// a re-render instead of reloading it (this tab is re-rendered on every 5s dashboard poll
// cycle, same as any other tab -- see docs/PLUGIN_API.md's "The tab's own script").
//
// Real lazy-start (2026-09-28, agent-manager PR "Wire real lazy-start for
// PromptForge/AdForge/ScriptForge dashboard tabs"): before this, opening this tab just
// pointed an iframe at :7430 with nothing anywhere ensuring PromptForge's own process was
// actually running -- a cold PromptForge showed a silent "localhost refused to connect"
// iframe forever. POST /api/plugins/promptforge/ensure-started (agent-manager core route)
// calls the already-idempotent plugin_process_manager.start() and waits for health when it
// actually had to launch something; a no-op, fast is_running() check when already up. Fire
// this on every render (same 5s cadence as the rest of this function) rather than gating it
// to "first open only" -- the endpoint's own already-running short-circuit makes repeat
// calls cheap, and this way a PromptForge that crashed after the tab was first opened gets
// relaunched on the next poll tick too.
registerPluginTabRenderer('promptforge', async function renderPromptForgeTab() {
  const main = document.getElementById('main');
  try {
    await fetch('/api/plugins/promptforge/ensure-started', { method: 'POST' });
  } catch (e) { /* dashboard core route unreachable -- fall through, the iframe below will just show the connection failure like before this fix */ }
  let url = 'http://' + location.hostname + ':7430/';
  try {
    const cfg = await fetchJson('/api/promptforge/config');
    if (cfg && cfg.url) url = cfg.url.replace('localhost', location.hostname);
  } catch (e) { /* fall back to the default host:7430 */ }
  const existing = main.querySelector('iframe.promptforge-frame');
  if (existing) {
    if (existing.getAttribute('src') !== url) existing.setAttribute('src', url);
    return;
  }
  main.innerHTML = `<iframe class="viz promptforge-frame" src="${escapeAttr(url)}" style="width:100%;height:calc(100vh - 120px);border:2px solid var(--border);border-radius:8px"></iframe>`;
});
