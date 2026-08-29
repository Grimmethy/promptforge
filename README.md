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
