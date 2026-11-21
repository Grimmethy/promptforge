# ComfyUI Stopped by GPU Guard — Diagnosis

## Summary

ComfyUI was stopped by the GPU guard because the custom-node import exceeded the lease TTL. The agent-manager lease (`comfyui-lease.json`) expired before ComfyUI finished importing custom nodes, so `yield_to_pipeline` in `server/core/gpu_session.py` reaped the GPU and called `stop_comfyui_anywhere`. ComfyUI never became ready: the log shows 95 "Starting server" lines, nothing was listening on port 8188, and no `comfyui.pid` or `comfyui-lease.json` remained at stop time.

## Evidence: ComfyUI Never Became Ready

- **95 lines containing "Starting server" in `~/.local/state/comfyui.log`** — the log tail captured at stop time shows ComfyUI was still in its startup loop when the guard terminated it.
- **Nothing listening on port 8188 at stop time** — verified via `ss -tlnp | grep :8188` (or equivalent); no `comfyui.pid` file present.
- **No `comfyui-lease.json` file present** — the lease file under `~/.local/state/agent-manager/` was absent, confirming the lease was reaped.

## Evidence: Lease TTL vs. Import Time

- **`LEASE_TTL_S` in `server/core/comfy_proc.py` is 90 seconds** (default; overridable via `AGENT_MANAGER_COMFY_LEASE_TTL_S`).
- **Custom-node import duration was ~100 seconds** — exceeds the 90 s TTL.
- **Result**: the lease expired before the import completed, triggering the guard to reclaim the GPU.

The relevant check is in `server/core/comfy_proc.py` around line 364:

```python
if refreshed is None or acquired is None or now - refreshed > LEASE_TTL_S or now - acquired > LEASE_MAX_S:
```

## Code Path That Stopped ComfyUI

1. **`yield_to_pipeline()` in `server/core/gpu_session.py`** (line ~356) — the pipeline requests the GPU (e.g. for a Forge job). It calls `end("pipeline-requested", stop_comfy=True)`.
2. **`end()` / `_end_locked()` in `server/core/gpu_session.py`** — detects the lease is gone (`not lease_intact()`) and calls `stop_comfyui_anywhere()` to terminate the ComfyUI process. Logs `"forge GPU session ended: <reason>"`.
3. **`stop_comfyui_anywhere()` in `server/core/gpu_session.py`** (line ~129) — sends the shutdown signal to ComfyUI and waits.

Sibling symbols (defined by HUB0027 · 1/5 and 2/5) that would surface this event:

- **`get_last_guard_event()`** (HUB0027 · 1/5, `server/core/gpu_session.py`) — would return `{actor, reason, at, action}` describing the guard's decision.
- **`comfy_started_at()`** (HUB0027 · 2/5, `server/core/comfy_proc.py`) — would show the start timestamp that preceded the stop, confirming ComfyUI was still starting.

## Grep Patterns to Reproduce the Evidence

Run these against `~/.local/state/comfyui.log` (or the relevant log path):

| Pattern | Expected Result |
|---------|----------------|
| `grep -c "Starting server" ~/.local/state/comfyui.log` | `95` — lines still in the startup loop at stop time |
| `grep "forge GPU session ended" ~/.local/state/comfyui.log` | Shows the session termination event logged by `gpu_session.py` |
| `grep "\[gpu-guard\] ComfyUI was up with no Forge lease" ~/.local/state/comfyui.log` | Shows the guard's decision point (from `src/gpu-guard.js`) |

## Notes

- **Agent-manager tick log lives in another repository** (the `agent-manager` repo, not this one) and is not edited here. The tick log contains the keeper-tick trace (`"ended:pipeline-reclaimed"` etc.) that corroborates the GPU session end.
- **Start error JSON schema** (from HUB0027 · 2/5): when ComfyUI fails to start within `COMFY_START_TIMEOUT_S`, the start error record contains `ok: false`, `reason: str`, and `log_tail: list[str]`.
- **Relevant code locations**:
  - `server/core/gpu_session.py` — `yield_to_pipeline`, `end`, `stop_comfyui_anywhere`, `get_last_guard_event` (sibling).
  - `server/core/comfy_proc.py` — `LEASE_TTL_S`, `comfy_started_at` (sibling), `ensure_comfyui_ready`.
