#!/usr/bin/env bash
# Render the Modelith CLI demo tapes to MP4 under demos/out/.
#
# Requirements: vhs, ffmpeg, the JetBrains Mono font, and the project venv:
#   brew install vhs ffmpeg && brew install --cask font-jetbrains-mono && uv sync
# The tapes put a fast `mdl` on PATH from .venv/bin so recorded commands start quickly and
# show a clean `mdl`.
#
# Usage:
#   demos/render.sh            # every tape
#   demos/render.sh a-ibor     # one series (matches the path)
#   demos/render.sh a3         # one chapter (matches the filename)
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root, so each tape's demos/... paths resolve

filter="${1:-}"
shopt -s nullglob
tapes=(demos/tapes/films/[0-9]*.tape)

for tape in "${tapes[@]}"; do
  if [[ -n "$filter" && "$tape" != *"$filter"* ]]; then continue; fi
  echo "▶ rendering $tape"
  vhs "$tape"
done

echo "✓ done — outputs in demos/out/"
ls -lh demos/out/*.mp4 2>/dev/null | awk '{print $9, $5}' || true
