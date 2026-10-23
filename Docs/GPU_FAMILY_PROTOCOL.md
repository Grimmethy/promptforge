# GPU family protocol: Forge vs. Agent Manager pipeline

**Rule (owner, 2026-10-13):** the Agent Manager pipeline (Ollama's local model) and the Forge family
(ComfyUI and everything that drives it: PromptForge, CharacterForge, StoryForge, SceneForge, VideoForge,
CutForge's GPU-free assembly excluded) never have models loaded at the same time. Starting one stops the
other and clears the GPU, so the active family has the whole card and the machine's RAM.

## Who does what
| Piece | Role |
|---|---|
| `comfyui-lease.json` (`~/.local/state/agent-manager/`) | the single shared flag. Present and fresh = Forge family holds the GPU |
| PromptForge `server/core/gpu_session.py` | the Forge family's **GPU broker**. HTTP API below; every other Forge component uses it over HTTP (no shared code) |
| Agent Manager lanes (`local-worker.sh`, `review-runner.sh`, `gpu-guard.js`) | already yield their ticks while the lease is fresh |
| KaggleForge `server/gpu_lease.py` | reserves ONE card for an eval with an endpoint-scoped lease (`holder: "kaggleforge"`, `endpoints: [<that card's Ollama URL>]`); see below |
| `scripts/launch.sh` (Agent Manager) | explicit pipeline start deletes the lease and calls `/api/gpu/yield`: the pipeline wins |
| `gpu-guard.js` (Agent Manager, per tick) | with no lease and ComfyUI still up, calls `/api/gpu/yield` (branch `feat/forge-gpu-yield`, pending merge) |

## Forge session (what "taking the GPU" means)
`POST /api/gpu/acquire {owner}` (PromptForge `:7430`) does, in order:
1. write the lease (the pipeline's lanes stop starting ticks);
2. wait up to 90 s for the pipeline's in-flight model calls (`instances/.model-locks`) to finish;
3. evict **every** resident Ollama model (not only when ComfyUI wouldn't fit) and wait for the card to clear;
4. start ComfyUI if it is down; check host RAM.

While the session lasts PromptForge's keeper thread refreshes the lease every 20 s and renews `acquiredAt`
(so the pipeline's fixed 15-minute ceiling cannot wake it mid-render; the session's own bound is 4 h).
Activity = any PromptForge job, `POST /api/gpu/heartbeat {owner}`, or another `acquire`.

A session **ends** (lease deleted **and ComfyUI stopped**, so VRAM and RAM are fully free) when:
- a component calls `POST /api/gpu/release`; or
- 20 minutes pass with no activity and no job running; or
- the lease disappears: the pipeline was started explicitly. The keeper notices within 20 s; a running job
  is told "the agent-manager pipeline reclaimed the GPU"; or
- 4 hours pass; or
- the pipeline asks: `POST /api/gpu/yield` is granted unless a Forge job is running right now (a PromptForge job, or anything in ComfyUI's own queue).

The pipeline reloads its model by itself on its next tick once the lease is gone.

**Concurrent starts.** `acquire` / a job's `begin` is idempotent, but a job that arrives while another is still clearing the
GPU or cold-starting ComfyUI (2-5 min) now **waits** for that start (up to `FORGE_START_WAIT_S`, default 600) instead of taking
the "session already live" shortcut. Before this, four quick Regenerate presses all but the first failed their first upload with
"Connection refused". If the starting job fails, the waiting ones fail with "the Forge GPU session did not start: ...".

## The lease file: fields and the one-holder rule
`{holder, family: "forge", owner, acquiredAt, refreshedAt, ...}`. Fresh = `refreshedAt` within 90 s and `acquiredAt` within 900 s
(Agent Manager's `readFreshLease`; both writers renew both stamps every 20 s).

- **`endpoints`** (optional, Agent Manager `3a1b58eb`): an array of Ollama URLs. Absent or empty = the whole pipeline yields. Present = only
  callers of those endpoints yield (drafts, reviewer votes, the lane-health probe); the other cards keep working. PromptForge's lease is
  scoped to `FORGE_LEASE_ENDPOINTS` (default: the Ollama it evicts from, `OLLAMA_URL`, i.e. the card ComfyUI takes), so a Forge render no
  longer pauses lanes on other cards. `FORGE_LEASE_ENDPOINTS=all` restores the old unscoped lease. Extra URLs in the list pause their lanes
  but nothing is evicted from them: eviction still clears only `OLLAMA_URL`.
- **One holder at a time.** The file has room for one lease, so nobody writes over another holder's *fresh* lease. PromptForge refuses a
  session (`begin` raises, a job fails with "the GPU is reserved by kaggleforge (KAG-...)") and will not start ComfyUI while a fresh
  foreign lease exists, even on a card with room; it also never refreshes or deletes a lease it does not hold. A stale lease may be taken over.
  KaggleForge likewise refuses to start while PromptForge's lease is fresh.
- **Losing the lease is preemption.** The file vanishing (an explicit pipeline start) or another holder in it ends the session / eval.

## API (PromptForge `:7430`)
`GET /api/gpu/status` · `POST /api/gpu/acquire {owner}` · `POST /api/gpu/heartbeat {owner}` ·
`POST /api/gpu/release` · `POST /api/gpu/yield`

## What each Forge component must do
- **Anything that generates through PromptForge** (CharacterForge, StoryForge, SceneForge): nothing. A PromptForge
  job begins a session automatically.
- **Anything that talks to ComfyUI directly** (VideoForge, scripts): `acquire` first, `heartbeat` at least every
  few minutes while working, `release` when the batch is done.
- Never start ComfyUI by hand while the pipeline is running; use `acquire`.

## Tunables (env, PromptForge)
`FORGE_SESSION_IDLE_S` (1200) · `FORGE_SESSION_MAX_S` (14400) · `FORGE_KEEPER_TICK_S` (20) ·
`FORGE_LOCK_WAIT_S` (90) · `FORGE_EVICT_WAIT_S` (45) · `FORGE_START_WAIT_S` (600) · `FORGE_LEASE_ENDPOINTS` (default `OLLAMA_URL`; `all` = unscoped) · `AGENT_MANAGER_MODEL_LOCKS_DIR`.

## Known edges
- Evicting while a pipeline call is mid-flight is avoided by step 2; a call that starts in the same instant fails
  and is retried by the pipeline like any other transient failure.
- The pipeline's own `gpu-guard.js` `/free` path still reclaims a ComfyUI that PromptForge cannot be asked about.
- If PromptForge is down, Forge components cannot take the GPU; ComfyUI started by hand has no session and is only
  stopped when the pipeline asks (`/api/gpu/yield`) or via PromptForge's Stop button.
