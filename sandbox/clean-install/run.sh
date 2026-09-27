#!/usr/bin/env bash
# One command: build the current wheel, build the clean-install image, run the
# core-only verification with the wheel mounted in.
#
#   ./sandbox/clean-install/run.sh
#
# Exits non-zero if any check fails, so it is usable as a pre-ship gate / CI step.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

echo ">> building wheel from $ROOT"
rm -rf "$ROOT/dist"
( cd "$ROOT" && uv build --wheel -o dist >/dev/null )
ls "$ROOT"/dist/modelith_dbt-*.whl >/dev/null || { echo "no wheel built"; exit 1; }
echo "   $(basename "$(ls "$ROOT"/dist/modelith_dbt-*.whl | head -1)")"

echo ">> building clean-install image"
docker build -q -t modelith-clean-install "$HERE" >/dev/null

echo ">> running core-only verification"
docker run --rm -v "$ROOT/dist:/tmp/wheels:ro" modelith-clean-install
