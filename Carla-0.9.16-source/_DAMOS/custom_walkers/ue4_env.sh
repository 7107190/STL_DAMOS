#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAMOS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_ROOT="$(cd "$DAMOS_ROOT/.." && pwd)"
WORKSPACE_ROOT="$(cd "$SOURCE_ROOT/.." && pwd)"

export UE4_ROOT="$WORKSPACE_ROOT/UnrealEngine_4.26"
export CARLA_ROOT="$SOURCE_ROOT"
export PATH=/home/vvu/anaconda3/envs/carla4/bin:$PATH

echo "UE4_ROOT=$UE4_ROOT"
echo "CARLA_ROOT=$CARLA_ROOT"
