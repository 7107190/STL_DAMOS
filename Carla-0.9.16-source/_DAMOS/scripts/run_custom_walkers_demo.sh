#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_SH="$ROOT/_DAMOS/custom_walkers/ue4_env.sh"
SPAWN_SCRIPT="$SCRIPT_DIR/spawn_custom_walkers.py"
DEFAULT_PYTHON_BIN="/home/vvu/anaconda3/envs/carla4/bin/python"
LOG_DIR="$ROOT/_DAMOS/logs"
LOG_FILE="$LOG_DIR/custom_walkers_demo.log"
UPROJECT="$ROOT/Unreal/CarlaUE4/CarlaUE4.uproject"
MAP="/Game/Carla/Maps/Town01"

PORT=2000
KEEP_SECONDS=300
RANDOM_SPAWN=0
RESTART=0
HEADLESS=0
SERVER_WAIT_SECONDS=180
SERVER_PID=""
SERVER_STATE="unknown"
MIN_MOVE_METERS=0

usage() {
  cat <<'EOF'
Usage: run_custom_walkers_demo.sh [options]

Options:
  --keep-seconds N   Keep walkers alive for N seconds (default: 300)
  --min-move-meters N  Fail if any walker moves less than N meters
  --random-spawn     Use random navigation spawns instead of Town01 demo layout
  --restart          Restart an existing matching source-build server on the port
  --headless         Start the server with -nullrhi -nosound instead of GUI
  --port N           CARLA RPC port (default: 2000)
  -h, --help         Show this help message
EOF
}

listener_pid() {
  ss -ltnp 2>/dev/null | awk -v port=":$PORT" '$4 ~ port"$"' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | head -n 1
}

process_cmdline() {
  local pid="$1"
  tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true
}

is_same_source_server() {
  local pid="$1"
  local cmdline
  cmdline="$(process_cmdline "$pid")"
  [[ "$cmdline" == *"$UPROJECT"* ]]
}

is_matching_town01_server() {
  local pid="$1"
  local cmdline
  cmdline="$(process_cmdline "$pid")"
  [[ "$cmdline" == *"$UPROJECT"* ]] && [[ "$cmdline" == *"$MAP"* ]] && [[ "$cmdline" == *"-carla-rpc-port=$PORT"* ]]
}

wait_for_port_to_clear() {
  for _ in $(seq 1 30); do
    if [[ -z "$(listener_pid)" ]]; then
      return 0
    fi
    sleep 1
  done
  echo "Port $PORT did not clear in time." >&2
  return 1
}

stop_server_pid() {
  local pid="$1"

  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 1
    done
  fi

  if kill -0 "$pid" 2>/dev/null; then
    echo "Server pid $pid did not exit after SIGTERM; sending SIGKILL."
    kill -9 "$pid" 2>/dev/null || true
  fi

  wait_for_port_to_clear
}

wait_for_server_port() {
  local pid="$1"
  for _ in $(seq 1 "$SERVER_WAIT_SECONDS"); do
    if [[ -n "$(listener_pid)" ]]; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Source server exited before opening port $PORT." >&2
      echo "Recent log output:" >&2
      tail -n 40 "$LOG_FILE" >&2 || true
      return 1
    fi
    sleep 1
  done
  echo "Timed out waiting for source server to open port $PORT." >&2
  echo "Recent log output:" >&2
  tail -n 40 "$LOG_FILE" >&2 || true
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-seconds)
      KEEP_SECONDS="$2"
      shift 2
      ;;
    --min-move-meters)
      MIN_MOVE_METERS="$2"
      shift 2
      ;;
    --random-spawn)
      RANDOM_SPAWN=1
      shift
      ;;
    --restart)
      RESTART=1
      shift
      ;;
    --headless)
      HEADLESS=1
      shift
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "$ENV_SH" ]]; then
  echo "Missing environment script: $ENV_SH" >&2
  exit 1
fi

source "$ENV_SH"

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/run/user/1000/gdm/Xauthority}"
PYTHON_BIN="${CARLA_PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"

mkdir -p "$LOG_DIR"
touch "$LOG_FILE"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing Python interpreter for spawn script: $PYTHON_BIN" >&2
  echo "Set CARLA_PYTHON_BIN to override it." >&2
  exit 1
fi

existing_pid="$(listener_pid)"
if [[ -n "$existing_pid" ]]; then
  if is_matching_town01_server "$existing_pid"; then
    if [[ "$RESTART" -eq 1 ]]; then
      echo "Restarting matching Town01 source server on port $PORT (pid=$existing_pid)."
      stop_server_pid "$existing_pid"
      existing_pid=""
    else
      SERVER_PID="$existing_pid"
      SERVER_STATE="reused"
    fi
  elif is_same_source_server "$existing_pid"; then
    if [[ "$RESTART" -eq 1 ]]; then
      echo "Restarting non-Town01 source server on port $PORT (pid=$existing_pid)."
      stop_server_pid "$existing_pid"
      existing_pid=""
    else
      echo "Port $PORT is already used by this source build, but not with Town01." >&2
      echo "Use --restart to replace it with the Town01 demo server." >&2
      exit 1
    fi
  else
    echo "Port $PORT is already in use by another process." >&2
    echo "Refusing to replace it automatically." >&2
    local_cmdline="$(process_cmdline "$existing_pid")"
    if [[ -n "$local_cmdline" ]]; then
      echo "Listener pid=$existing_pid cmdline=$local_cmdline" >&2
    fi
    exit 1
  fi
fi

if [[ -z "$SERVER_PID" ]]; then
  SERVER_STATE="started"
  echo "Starting Town01 source server on port $PORT..."
  server_cmd=(
    "$UE4_ROOT/Engine/Binaries/Linux/UE4Editor"
    "$UPROJECT"
    "$MAP"
    -game
    "-carla-rpc-port=$PORT"
    -quality-level=Low
  )

  if [[ "$HEADLESS" -eq 1 ]]; then
    server_cmd+=(-nullrhi -nosound)
  else
    server_cmd+=(-windowed -ResX=1280 -ResY=720)
  fi

  nohup setsid "${server_cmd[@]}" </dev/null >>"$LOG_FILE" 2>&1 &
  SERVER_PID="$!"
  wait_for_server_port "$SERVER_PID"
fi

echo "Server state: $SERVER_STATE"
echo "Server pid: $SERVER_PID"
echo "Log file: $LOG_FILE"

spawn_cmd=(
  "$PYTHON_BIN"
  "$SPAWN_SCRIPT"
  --host 127.0.0.1
  --port "$PORT"
  --keep-seconds "$KEEP_SECONDS"
  --wait-for-server-seconds "$SERVER_WAIT_SECONDS"
  --min-move-meters "$MIN_MOVE_METERS"
)

if [[ "$RANDOM_SPAWN" -eq 1 ]]; then
  spawn_cmd+=(--random-spawn)
fi

"${spawn_cmd[@]}"

echo "Demo complete."
echo "Server state: $SERVER_STATE"
echo "Server pid: $SERVER_PID"
echo "Log file: $LOG_FILE"
