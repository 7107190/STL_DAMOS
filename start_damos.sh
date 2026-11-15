#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -f "$PROJECT_ROOT/shared/env.sh" ]]; then
  source "$PROJECT_ROOT/shared/env.sh"
else
  CARLA_ROOT="$PROJECT_ROOT/Carla-0.9.16-source"
fi
exec bash "$CARLA_ROOT/_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh" "$@"
