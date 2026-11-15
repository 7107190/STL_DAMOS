#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_SCRIPT="$SCRIPT_DIR/run_scenic_custom_walkers_town10hd.sh"

if [[ ! -x "$BASE_SCRIPT" ]]; then
  echo "Missing base launcher: $BASE_SCRIPT" >&2
  exit 1
fi

exec "$BASE_SCRIPT" \
  --restart \
  --port 2100 \
  --scenic-time 4 \
  --n-scenarios 1 \
  --min-move-meters 0.1 \
  --resx 800 \
  --resy 450 \
  "$@"
