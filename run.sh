#!/usr/bin/env bash
# PromptForge Studio — start the local server on 127.0.0.1:7430
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

export PROMPTFORGE_PORT="${PROMPTFORGE_PORT:-7430}"
# SECOND_BRAIN_DIR / COMFY_URL / PROMPTFORGE_DATA_DIR are read from the environment
exec ./.venv/bin/python server/app.py
