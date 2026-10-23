# GPU family protocol: moved

The Forge/pipeline GPU protocol (the one-holder lease, what `acquire` does, when a session ends, cards, preferences) is owned by the GPU broker and
lives in its repo: **`agent-manager-gpu-broker`, `Docs/GPU_FAMILY_PROTOCOL.md`** (`/media/model-cache/github/agent-manager-gpu-broker/Docs/GPU_FAMILY_PROTOCOL.md`
on this machine). It moved there on 2026-10-23 (extraction piece G7); this file stays so older links still resolve.

## What stays true here
- PromptForge is a **client** of the broker when `GPU_BROKER_URL` is set (the production setting, `http://localhost:7449` in the `promptforge` entry of
  agent-manager's `plugins.json`): a job acquires from the broker (`server/core/gpu_client.py`), a heartbeat thread keeps the session alive while a job runs,
  and PromptForge's own `/api/gpu/*` routes proxy to the broker so there is never a second session.
- **Unset, PromptForge runs the same session in-process** (`server/core/gpu_session.py`), exactly as the broker does. That is the legacy fallback; it must
  never run against the same lease file as a live broker. Cutover and rollback steps are in the broker's doc.
- A job may name the card it runs on: `POST /api/generate` accepts an optional `card` (a broker card id such as `3090`); `GET /api/gpu/cards` lists the
  broker's cards (in-process answers `{cards: [], broker: false}`).
- Client-side tunables read by `gpu_client.py`: `FORGE_BROKER_STEP_POLL_S` (1.0), `FORGE_BROKER_HEARTBEAT_S` (60), `FORGE_BROKER_ACQUIRE_TIMEOUT_S` (900).
  The session tunables (`FORGE_SESSION_IDLE_S`, `FORGE_LEASE_ENDPOINTS`, ...) are documented with the broker; in-process mode reads the same names.
