#!/usr/bin/env bash
M3_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$M3_ROOT/../shared/env.sh"
export PYTHONPATH="$M3_ROOT/app:$PYTHONPATH"
export DAMOS_BEV_EVIDENCE_FUSION=off
unset DAMOS_BEV_ACCURACY_CASE
