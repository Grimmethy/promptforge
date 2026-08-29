'use strict';

// AGENT_MANAGER_REGISTER_PATH entry point — intentionally a no-op.
//
// PromptForge is NOT a code plugin in the agent-manager sense. Agent-manager
// plugins register *task sources* (autonomous coding-work generators, like
// agent-manager-hygiene's observability_review / arch_import). PromptForge is a
// standalone local web app (its own Flask server on PROMPTFORGE_URL, default
// :7430) that generates images by driving ComfyUI — a tool Agent Manager *calls*
// and embeds as a dashboard tab, not a source of work for the pipeline.
//
// This file exists only so PromptForge can appear in plugins.json / the Plugins
// tab with a valid `registerPath`, structurally like agent-manager-hygiene. It
// registers nothing and has no side effects, so it is harmless whether an entry
// is enabled or not.
//
// Roadmap for the related (also non-task-source) plugins:
//   SecondBrain/Projects/promptforge-ecosystem.md

module.exports = {};
