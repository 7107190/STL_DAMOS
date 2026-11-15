#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAMOS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$DAMOS_ROOT/.." && pwd)"
WORKSPACE_ROOT="$(cd "$ROOT/.." && pwd)"
UE4_ROOT="$WORKSPACE_ROOT/UnrealEngine_4.26"
UPROJECT="$ROOT/Unreal/CarlaUE4/CarlaUE4.uproject"
SCRIPT="$ROOT/_DAMOS/unreal/import_damos_walkers.py"
MAP="/Engine/Maps/Entry"

"$UE4_ROOT/Engine/Binaries/Linux/UE4Editor-Cmd" \
  "$UPROJECT" \
  "$MAP" \
  -nullrhi \
  -unattended \
  -nop4 \
  -nosplash \
  -stdout \
  -FullStdOutLogOutput \
  -ExecutePythonScript="$SCRIPT"
