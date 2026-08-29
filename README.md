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

Scaffold: core ported, `/api/presets`, `/api/pose/*`, `/api/assemble`,
`/api/randomize-character`, `/api/comfy/status` live. `/api/generate` +
`/api/job` + the real UI + the editor are next.
