#!/usr/bin/env bash
SHARED_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SHARED_ROOT/.." && pwd -P)"
SOURCE_ROOT="$SHARED_ROOT/runtime/Carla-0.9.16-source"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONHOME
export DAMOS_RUNTIME_ROOT="$SHARED_ROOT/runtime"
export DAMOS_PYTHON="$SHARED_ROOT/venvs/simulation/bin/python"
export DAMOS_SCENIC_BIN="$SHARED_ROOT/venvs/simulation/bin/scenic"
export PYTHONPATH="$SHARED_ROOT:$PROJECT_ROOT/metric/metric2/app/core_overlay:$PROJECT_ROOT/metric/metric2/app:$SOURCE_ROOT/_DAMOS/scripts:$SHARED_ROOT/runtime_overlay"
export CARLA_CACHE_DIR="$SHARED_ROOT/cache/carla"
export PATH="$SHARED_ROOT/venvs/simulation/bin:$SHARED_ROOT/tools/node-v22.22.2-linux-x64/bin:$PATH"
export V2V_NODE_CMD="$SHARED_ROOT/tools/node-v22.22.2-linux-x64/bin/node"
export V2V_ZKP_BACKEND=snarkjs V2V_REQUIRE_BINDING_GATE=1
export DAMOS_BEV_CHECKPOINT="$SHARED_ROOT/models/scenic_presence_ground_guard_20261008.pt"
export DAMOS_BEV_EVIDENCE_FUSION=off
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export UE4_ROOT="$SHARED_ROOT/runtime/UnrealEngine_4.26"
export CARLA_ROOT="$SOURCE_ROOT"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
