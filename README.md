# PromptForge Studio

A local web app for building and generating single still images with ComfyUI —
character / theme / setting / style inputs, 0–2 characters with regional
prompting, and a skeleton editor. Replaces the `ComfyUI-PromptForge` node graph
as the primary interface (the node graph stays as a debug mode).

- **Backend:** Flask, `server/app.py`, binds `127.0.0.1:7430`.
- **Engine:** ComfyUI's HTTP API on `:8188`. This app builds the API-format graph
  (using the `ComfyUI-PromptForge` node pack) and submits it.
- **Presets:** YAML/JSON under `data/` — the source of truth. Point the ComfyUI
  node pack at it with `PROMPTFORGE_DATA_DIR=<repo>/data`.
- **Second Brain (read-only):** if `SECOND_BRAIN_DIR` is set, character notes
  under `$SECOND_BRAIN_DIR/Characters/*.md` appear in the character picker.

## Run

```
./run.sh          # creates .venv, installs deps, starts on :7430
```

Env: `PROMPTFORGE_PORT` (7430), `COMFY_URL` (http://127.0.0.1:8188),
`SECOND_BRAIN_DIR`, `PROMPTFORGE_DATA_DIR`.

### ComfyUI auto-start (GPU-coordinated)

If ComfyUI is down when a generation starts, PromptForge brings it up itself. It
first measures per-GPU free VRAM (`nvidia-smi`); if a GPU already has room it just
starts ComfyUI there and the agent-manager pipeline is never touched. Only when
ComfyUI genuinely can't fit does it take a shared lease file, evict Ollama's
resident model(s), and start ComfyUI — the pipeline then yields its local-model
ticks until the lease clears (released ~60 s after the last job, or stomped by an
explicit `bash scripts/launch.sh` on the agent-manager side).

| Env | Default | Meaning |
| --- | --- | --- |
| `COMFY_AUTOSTART` | `true` | set falsey to disable; a generation with ComfyUI down then errors as before |
| `COMFY_LAUNCH_CMD` | `bash /media/model-cache/github/TheAgent/app/tools/ComfyUI/launch-comfyui.sh` | detached launch command (the browserless inner script) |
| `COMFY_LOG` | `~/.local/state/comfyui.log` | ComfyUI stdout/stderr is appended here; `/api/comfy/status` tails it |
| `COMFY_PID_PATH` | `~/.local/state/promptforge/comfyui.pid` | pid of a ComfyUI PromptForge started (drives the Stop button) |
| `COMFY_START_TIMEOUT_S` | `300` | how long to wait for `/system_stats` after spawn |
| `COMFY_REQUIRED_VRAM_MB` | `9000` | VRAM ComfyUI needs to be considered "fits" |
| `COMFY_CUDA_DEVICE` | `0` | ComfyUI's target GPU index; another GPU with room is preferred over evicting |
| `GPU_BROKER_URL` | unset | set (e.g. `http://localhost:7449`) to take the GPU from the standalone GPU broker instead of running the session in-process; `/api/gpu/*` then proxy to it. See `Docs/GPU_FAMILY_PROTOCOL.md`, Broker mode |
| `FORGE_BROKER_STEP_POLL_S` | `1.0` | broker mode: how often a blocked acquire polls the broker for its progress step |
| `FORGE_BROKER_HEARTBEAT_S` | `60` | broker mode: heartbeat interval while a job runs |
| `FORGE_BROKER_ACQUIRE_TIMEOUT_S` | `900` | broker mode: how long one acquire may block |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | queried for resident models (`/api/ps`) and eviction (`/api/generate` `keep_alive:0`) |
| `COMFY_LEASE_PATH` | `~/.local/state/agent-manager/comfyui-lease.json` | shared lease file; must match agent-manager's `AGENT_MANAGER_COMFY_LEASE_PATH` |
| `PROMPTFORGE_GALLERY_DIR` | `<checkout>/gallery` | where generated images live. Images are gitignored, so a second checkout/worktree must point this at the real gallery or it shows an empty grid |
| `COMFY_MIN_AVAILABLE_RAM_MB` | `2048` | a generation first claims the GPU (shared lease + evicts the pipeline's model if ComfyUI would not fit), then waits `COMFY_RAM_WAIT_S` (20) for host RAM; below this it refuses instead of thrashing the box. `0` disables the RAM check |

## Layout

```
server/app.py          Flask routes
server/core/
  data.py              preset loaders + kw helpers   (ported from ComfyUI-PromptForge)
  randomizer.py        seeded coherent character roll (ported verbatim)
  assemble.py          shot spec -> BREAK-chunked positive/negative
  pose_ops.py          keypoint transforms + render_png()
  themes.py            theme presets (data/themes.yaml)
  secondbrain.py       read-only Characters/*.md
  comfy.py             (next) build + submit the ComfyUI graph
  vendor/dwpose_draw.py  vendored OpenPose skeleton drawing (CMU non-commercial)
data/                  characters, styles, backgrounds, positions, poses,
                       supporting, themes, randomizer, cn_positions + keypoints
web/                   the SPA (scaffold placeholder for now)
gallery/               generated PNGs + sidecar JSON
```

## Ecosystem

Story generation and video generation are **separate future plugins** — see
`SecondBrain/Projects/promptforge-ecosystem.md`. This app produces one still per
`POST /api/generate`; that endpoint is the contract those plugins call.

## Status

**v1 working end-to-end.**

- Backend: `/api/presets`, `/api/character`, `/api/theme`, `/api/pose/*`,
  `/api/pose/render`, `/api/save-{pose,character}`, `/api/randomize-character`,
  `/api/preview`, `/api/generate` + `/api/job/<id>`, `/api/gallery`,
  `/api/image/<name>` (GET+DELETE), `/api/comfy/status`.
- `core/comfy.py` builds + submits the graph in all three shapes
  (0 = scene-only, 1 = +ControlNet if a skeleton, 2 = regional + ControlNet).
  Verified against live ComfyUI on every shape.
- UI (`web/`): character cards (0–2, +Add / remove, preset·SB·custom source),
  theme / setting / style, model + advanced, live prompt preview, image grid
  with per-tile menu + lightbox, localStorage persistence.
- Editor (`web/editor.js` + `server/core/rig.py`): a named 3-D stick rig per
  character (head / chest / shoulders / elbows / hands / waist / hips / knees /
  feet), editable per-bone rest lengths, FABRIK IK on the four limbs, hover
  tooltips. Drag empty space to orbit — the orbit camera is the exported facing
  (yaw = azimuth/π, pitch = elevation/(π/2)) and drives the camera prompt tags.
  Poses are stored as rigs (`cn_positions/keypoints/<pos>.json`) and projected
  to COCO-18 for ControlNet on both client and server.
- Browser QA done via headless CDP (page render, add-character, pose load,
  full Generate → gallery loop; no JS errors).

Not built (deliberately — separate future plugins): story / shot sequencing,
video. WebSocket generation progress is coarse (status polling) for now.

## Characters come from CharacterForge

CharacterForge (`../characterforge`, :7433) is the canonical character store and an earlier
step in the design process; PromptForge only renders characters. When it is present,
`server/core/cf_characters.py` reads its `characters/` and `samples/` directories (a character's
`## Keywords` block is the prompt base; `weight`, `default_outfit`, `negative_add` come from its
frontmatter) and `/api/presets` keeps its shape (`characters.preset`; `characters.secondbrain`
is empty since vault notes are imported into CharacterForge instead). The old preset editor
refuses with a pointer to CharacterForge. Characters without a keyword block are not listed.

Config: `CHARACTERFORGE_ROOT` (default: a sibling `characterforge` checkout),
`CHARACTERFORGE_DIR` (override the user-characters dir). `PROMPTFORGE_CHARACTER_SOURCE=local`
restores the legacy local YAML presets + Second Brain reader. Tests:
`python3 -m unittest discover -s test -p 'test_*.py'`.
