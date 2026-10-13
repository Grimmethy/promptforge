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
| `scripts/launch.sh` (Agent Manager) | explicit pipeline start deletes the lease: the pipeline wins |

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
- the pipeline asks: `POST /api/gpu/yield` is granted unless a Forge job is running right now.

The pipeline reloads its model by itself on its next tick once the lease is gone.

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
`FORGE_LOCK_WAIT_S` (90) · `FORGE_EVICT_WAIT_S` (45) · `AGENT_MANAGER_MODEL_LOCKS_DIR`.

## Known edges
- Evicting while a pipeline call is mid-flight is avoided by step 2; a call that starts in the same instant fails
  and is retried by the pipeline like any other transient failure.
- The pipeline's own `gpu-guard.js` `/free` path still reclaims an idle ComfyUI that has no session.
- If PromptForge is down, Forge components cannot take the GPU; ComfyUI started by hand has no session and is only
  stopped when the pipeline asks (`/api/gpu/yield`) or via PromptForge's Stop button.
