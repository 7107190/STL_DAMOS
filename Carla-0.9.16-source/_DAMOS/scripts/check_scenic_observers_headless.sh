#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$SCRIPT_DIR/run_scenic_custom_walkers_town10hd.sh"
VERIFIER="$SCRIPT_DIR/verify_observer_report.py"
PYTHON_BIN="${CARLA_PYTHON_BIN:-/home/vvu/anaconda3/envs/carla4/bin/python}"

PORT="${PORT:-2212}"
SCENIC_TIME="${SCENIC_TIME:-8}"
PORT_PROVIDED=0
SCENIC_TIME_PROVIDED=0

usage() {
  cat <<'EOF'
Usage: check_scenic_observers_headless.sh [--port N] [--scenic-time N] [runner options...]

Runs the Town10HD Scenic observer integration without GUI, then verifies the
latest JSON report for the selected port.

Environment overrides:
  PORT=2212
  SCENIC_TIME=8
  CARLA_PYTHON_BIN=/path/to/python
EOF
}

runner_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="$2"
      PORT_PROVIDED=1
      runner_args+=("$1" "$2")
      shift 2
      ;;
    --scenic-time)
      SCENIC_TIME="$2"
      SCENIC_TIME_PROVIDED=1
      runner_args+=("$1" "$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      runner_args+=("$1")
      shift
      ;;
  esac
done

if [[ "$PORT_PROVIDED" -eq 0 ]]; then
  runner_args=(--port "$PORT" "${runner_args[@]}")
fi

if [[ "$SCENIC_TIME_PROVIDED" -eq 0 ]]; then
  runner_args=(--scenic-time "$SCENIC_TIME" "${runner_args[@]}")
fi

"$RUNNER" --restart --headless "${runner_args[@]}"
"$PYTHON_BIN" "$VERIFIER" --port "$PORT"
