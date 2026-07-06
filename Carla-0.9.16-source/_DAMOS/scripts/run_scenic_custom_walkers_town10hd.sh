#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_SH="$ROOT/_DAMOS/custom_walkers/ue4_env.sh"
PYTHON_BIN="${CARLA_PYTHON_BIN:-/home/vvu/anaconda3/envs/carla4/bin/python}"
RUNNER="$SCRIPT_DIR/run_scenic_with_custom_walkers.py"
LOG_DIR="$ROOT/_DAMOS/logs"
LOG_FILE="$LOG_DIR/scenic_custom_walkers_town10hd.log"
UPROJECT="$ROOT/Unreal/CarlaUE4/CarlaUE4.uproject"
MAP="/Game/Carla/Maps/Town10HD_Opt"
SCENIC_CARLA_MAP="Town10HD_Opt"
SCENIC_XODR="$ROOT/Scenic/Maps/Town10HD_Opt.xodr"
WEATHER=""

PORT=2000
HEADLESS=0
OFFSCREEN=0
RESTART=0
SCENIC_TIME=8
N_SCENARIOS=1
RUNS=1
SELECTED_SCENARIO=""
STATIC_EGO=0
EGO_START=""
REALTIME_FACTOR=0
MIN_MOVE_METERS=0.5
OBSERVER_MODE=1
MAX_OBSERVER_ANCHOR_DISTANCE=22.0
MAX_OBSERVER_FACING_ERROR_DEGREES=35.0
OBSERVER_BLUEPRINT="random"
MAX_ANCHOR_PAIRS=""
MAX_DELIVERYBOTS=0
MAX_HUMANOIDS=0
ATTACH_OBSERVER_CAMERAS=1
OBSERVER_CAMERA_CONFIG="/home/vvu/vv/DAMOS/sensor_config.txt"
SAVE_TRAJECTORY_REPORT=1
SAVE_ACTOR_CAMERA_CAPTURES=0
SAVE_OBSERVER_SCENE_CAPTURES=0
SAVE_EGO_FAULT_REPORT=0
EGO_FRONT_CAMERA_FAULT="none"
CAPTURE_IMAGE_WIDTH=1280
CAPTURE_IMAGE_HEIGHT=720
CAPTURE_TIMEOUT_SECONDS=6
VERIFY_S1_CROSSING_AUTOPILOT=0
S1_VERIFY_PER_ANCHOR_SECONDS=15
S1_VERIFY_TRIGGER_DISTANCE=15
S1_VERIFY_PASS_MOVE_METERS=2.0
S1_VERIFY_EGO_UPSTREAM_DISTANCE=12
SERVER_WAIT_SECONDS=180
SCENIC_TIMEOUT_SECONDS=60
RESX=960
RESY=540
SERVER_PID=""
SERVER_STATE="unknown"
VERBOSE=0

usage() {
  cat <<'EOF'
Usage: run_scenic_custom_walkers_town10hd.sh [options]

Options:
  --port N               CARLA RPC port (default: 2000)
  --scenic-time N        Scenic simulation time cap in seconds (default: 8)
  --n-scenarios N        N_SCENARIOS override for S_.scenic (default: 1)
  --runs N               Repeat full Scenic/custom observer execution N times
                          (default: 1). Each run samples scenarios again.
  --selected-scenario S  Force one abnormal scenario: S1..S9
  --static-ego           Spawn the ego vehicle stopped for manual inspection
  --ego-start X Y H      Spawn ego at fixed Scenic coordinate (X, Y) and
                          heading H degrees instead of random lane placement
  --realtime-factor N    Pace Scenic/CARLA ticks; 1.0 is approximate real time
  --observer-mode        Place custom walkers as static observers (default)
  --walker-mode          Route custom walkers and require movement
  --min-move-meters N    Walker-mode movement requirement in meters
  --max-observer-anchor-distance N
                          Max observer-to-anchor distance in meters (default: 22)
  --max-observer-facing-error-degrees N
                          Max observer yaw error toward anchor (default: 35)
  --observer-blueprint T Observer type per anchor: deliverybot, humanoid, or
                          random (default: random)
  --max-anchor-pairs N    Max semantic anchors to cover; each gets one observer
                          of the chosen type. Default: all candidates
  --max-deliverybots N   Legacy compatibility cap; ignored
  --max-humanoids N      Legacy compatibility cap; ignored
  --attach-observer-cameras
                          Attach six RGB cameras to each observer (default)
  --no-observer-cameras  Disable observer camera sensor attachment
  --observer-camera-config PATH
                          sensor_config.txt-style camera mount log
  --no-trajectory-report Skip PNG/JSON report generation
  --save-actor-camera-captures
                          Save one RGB frame from every ego/custom observer
                          camera attached during the Scenic run
  --ego-front-camera-fault MODE
                          Apply a fault only to ego cam_front when actor camera
                          captures are saved. MODE: none, random, blackout,
                          blur, occlusion, color_failure, misalignment,
                          shaking, freeze_cycle
  --save-ego-fault-report Save ego-centric report images for LiDAR noise,
                          RGB sensor delay, and module stop/freeze
  --save-observer-scene-captures
                          Save external observer-anchor and observer cam_front
                          RGB captures during the Scenic run
  --capture-image-width N Capture image width (default: 1280)
  --capture-image-height N
                          Capture image height (default: 720)
  --capture-timeout-seconds N
                          Seconds to wait for each camera frame (default: 6)
  --verify-s1-crossing-autopilot
                          For S1, move the Scenic ego upstream of each pedestrian,
                          enable autopilot, and verify that crossing starts
  --s1-verify-per-anchor-seconds N
                          Seconds to observe each S1 pedestrian (default: 15)
  --s1-verify-trigger-distance N
                          Ego-pedestrian trigger distance threshold (default: 13)
  --s1-verify-pass-move-meters N
                          Required pedestrian movement for pass (default: 2.0)
  --s1-verify-ego-upstream-distance N
                          Place ego this many meters upstream before autopilot
                          approach (default: 16)
  --scenic-timeout N     Timeout passed to Scenic's CARLA model (default: 60)
  --map-name NAME        CARLA map name for server and Scenic (default: Town10HD_Opt)
  --map-xodr PATH        Scenic .xodr path override
  --weather NAME         Scenic weather override. Omit for random S_.scenic weather.
  --verbose              Print detailed anchor, observer, metric, and report logs
  --resx N               GUI width when not headless (default: 960)
  --resy N               GUI height when not headless (default: 540)
  --restart              Restart an existing matching Town10HD source server
  --headless             Start server with -nullrhi -nosound
  --offscreen            Start server with -RenderOffScreen -nosound for RGB captures
  -h, --help             Show this help
EOF
}

any_listener_pid() {
  ss -ltnp 2>/dev/null | awk -v port=":$PORT" '$4 ~ port"$"' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | head -n 1
}

listener_pid() {
  ss -ltnp 2>/dev/null | awk -v port=":$PORT" '$4 ~ port"$" && $0 ~ /UE4Editor/' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | head -n 1
}

process_cmdline() {
  local pid="$1"
  tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true
}

is_ignorable_listener() {
  local pid="$1"
  local cmdline
  cmdline="$(process_cmdline "$pid")"
  [[ "$cmdline" == *"CrashReportClient"* ]]
}

is_same_source_server() {
  local pid="$1"
  local cmdline
  cmdline="$(process_cmdline "$pid")"
  [[ "$cmdline" == *"$UPROJECT"* ]]
}

is_matching_source_server() {
  local pid="$1"
  local cmdline
  cmdline="$(process_cmdline "$pid")"
  [[ "$cmdline" == *"$UPROJECT"* ]] && [[ "$cmdline" == *"$MAP"* ]] && [[ "$cmdline" == *"-carla-rpc-port=$PORT"* ]]
}

wait_for_port_to_clear() {
  for _ in $(seq 1 30); do
    if [[ -z "$(any_listener_pid)" ]]; then
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

cleanup_started_server() {
  local status=$?
  if [[ "${SERVER_STATE:-unknown}" == "started" && -n "${SERVER_PID:-}" ]]; then
    echo "Stopping $SCENIC_CARLA_MAP source server on port $PORT (pid=$SERVER_PID)."
    stop_server_pid "$SERVER_PID" || true
  fi
  return "$status"
}

wait_for_server_rpc() {
  local pid="$1"
  for _ in $(seq 1 "$SERVER_WAIT_SECONDS"); do
    if "$PYTHON_BIN" - <<PY >/dev/null 2>&1
import sys
import carla

client = carla.Client("127.0.0.1", int("$PORT"))
client.set_timeout(2.0)
world = client.get_world()
name = world.get_map().name.split("/")[-1]
if name != "$SCENIC_CARLA_MAP":
    raise RuntimeError(f"unexpected map {name}")
PY
    then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Source server exited before CARLA RPC became ready on port $PORT." >&2
      tail -n 40 "$LOG_FILE" >&2 || true
      return 1
    fi
    sleep 1
  done
  echo "Timed out waiting for CARLA RPC readiness on port $PORT." >&2
  tail -n 40 "$LOG_FILE" >&2 || true
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="$2"
      shift 2
      ;;
    --scenic-time)
      SCENIC_TIME="$2"
      shift 2
      ;;
    --n-scenarios)
      N_SCENARIOS="$2"
      shift 2
      ;;
    --runs|--count)
      RUNS="$2"
      shift 2
      ;;
    --selected-scenario)
      SELECTED_SCENARIO="$2"
      shift 2
      ;;
    --static-ego)
      STATIC_EGO=1
      shift
      ;;
    --ego-start)
      EGO_START="$2 $3 $4"
      shift 4
      ;;
    --realtime-factor)
      REALTIME_FACTOR="$2"
      shift 2
      ;;
    --min-move-meters)
      MIN_MOVE_METERS="$2"
      shift 2
      ;;
    --observer-mode)
      OBSERVER_MODE=1
      shift
      ;;
    --walker-mode)
      OBSERVER_MODE=0
      shift
      ;;
    --max-observer-anchor-distance)
      MAX_OBSERVER_ANCHOR_DISTANCE="$2"
      shift 2
      ;;
    --max-observer-facing-error-degrees)
      MAX_OBSERVER_FACING_ERROR_DEGREES="$2"
      shift 2
      ;;
    --observer-blueprint)
      OBSERVER_BLUEPRINT="$2"
      shift 2
      ;;
    --max-anchor-pairs)
      MAX_ANCHOR_PAIRS="$2"
      shift 2
      ;;
    --max-deliverybots)
      MAX_DELIVERYBOTS="$2"
      shift 2
      ;;
    --max-humanoids)
      MAX_HUMANOIDS="$2"
      shift 2
      ;;
    --attach-observer-cameras)
      ATTACH_OBSERVER_CAMERAS=1
      shift
      ;;
    --no-observer-cameras)
      ATTACH_OBSERVER_CAMERAS=0
      shift
      ;;
    --observer-camera-config)
      OBSERVER_CAMERA_CONFIG="$2"
      shift 2
      ;;
    --no-trajectory-report)
      SAVE_TRAJECTORY_REPORT=0
      shift
      ;;
    --save-actor-camera-captures)
      SAVE_ACTOR_CAMERA_CAPTURES=1
      shift
      ;;
    --ego-front-camera-fault)
      EGO_FRONT_CAMERA_FAULT="$2"
      shift 2
      ;;
    --save-observer-scene-captures)
      SAVE_OBSERVER_SCENE_CAPTURES=1
      shift
      ;;
    --save-ego-fault-report)
      SAVE_EGO_FAULT_REPORT=1
      shift
      ;;
    --capture-image-width)
      CAPTURE_IMAGE_WIDTH="$2"
      shift 2
      ;;
    --capture-image-height)
      CAPTURE_IMAGE_HEIGHT="$2"
      shift 2
      ;;
    --capture-timeout-seconds)
      CAPTURE_TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --verify-s1-crossing-autopilot)
      VERIFY_S1_CROSSING_AUTOPILOT=1
      shift
      ;;
    --s1-verify-per-anchor-seconds)
      S1_VERIFY_PER_ANCHOR_SECONDS="$2"
      shift 2
      ;;
    --s1-verify-trigger-distance)
      S1_VERIFY_TRIGGER_DISTANCE="$2"
      shift 2
      ;;
    --s1-verify-pass-move-meters)
      S1_VERIFY_PASS_MOVE_METERS="$2"
      shift 2
      ;;
    --s1-verify-ego-upstream-distance)
      S1_VERIFY_EGO_UPSTREAM_DISTANCE="$2"
      shift 2
      ;;
    --scenic-timeout)
      SCENIC_TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --map-name)
      SCENIC_CARLA_MAP="$2"
      MAP="/Game/Carla/Maps/$2"
      shift 2
      ;;
    --map-xodr)
      SCENIC_XODR="$2"
      shift 2
      ;;
    --weather)
      WEATHER="$2"
      shift 2
      ;;
    --verbose)
      VERBOSE=1
      shift
      ;;
    --resx)
      RESX="$2"
      shift 2
      ;;
    --resy)
      RESY="$2"
      shift 2
      ;;
    --restart)
      RESTART=1
      shift
      ;;
    --headless)
      HEADLESS=1
      shift
      ;;
    --offscreen)
      OFFSCREEN=1
      shift
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

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing Python interpreter: $PYTHON_BIN" >&2
  exit 1
fi

if ! [[ "$RUNS" =~ ^[0-9]+$ ]] || [[ "$RUNS" -lt 1 ]]; then
  echo "--runs must be a positive integer." >&2
  exit 1
fi

source "$ENV_SH"

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/run/user/1000/gdm/Xauthority}"

trap cleanup_started_server EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$LOG_DIR"
touch "$LOG_FILE"

existing_any_pid="$(any_listener_pid)"
if [[ -n "$existing_any_pid" ]] && is_ignorable_listener "$existing_any_pid"; then
  echo "Removing stale CrashReportClient on port $PORT (pid=$existing_any_pid)."
  stop_server_pid "$existing_any_pid"
  existing_any_pid=""
fi

existing_pid="$(listener_pid)"
if [[ -n "$existing_pid" ]]; then
  if is_matching_source_server "$existing_pid"; then
    if [[ "$RESTART" -eq 1 ]]; then
      echo "Restarting matching source server on port $PORT (pid=$existing_pid)."
      stop_server_pid "$existing_pid"
      existing_pid=""
    else
      SERVER_PID="$existing_pid"
      SERVER_STATE="reused"
    fi
  elif is_same_source_server "$existing_pid"; then
    if [[ "$RESTART" -eq 1 ]]; then
      echo "Restarting source server on port $PORT with map $SCENIC_CARLA_MAP (pid=$existing_pid)."
      stop_server_pid "$existing_pid"
      existing_pid=""
    else
      echo "Port $PORT is already used by this source build, but not with $SCENIC_CARLA_MAP." >&2
      echo "Use --restart to replace it with the Scenic integration server for $SCENIC_CARLA_MAP." >&2
      exit 1
    fi
  else
    echo "Port $PORT is already in use by another process." >&2
    exit 1
  fi
elif [[ -n "$existing_any_pid" ]]; then
  echo "Port $PORT is already in use by another process." >&2
  exit 1
fi

if [[ -z "$SERVER_PID" ]]; then
  SERVER_STATE="started"
  echo "Starting $SCENIC_CARLA_MAP source server on port $PORT..."
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
  elif [[ "$OFFSCREEN" -eq 1 ]]; then
    server_cmd+=(-RenderOffScreen -nosound)
  else
    server_cmd+=(-windowed "-ResX=$RESX" "-ResY=$RESY")
  fi

  nohup setsid "${server_cmd[@]}" </dev/null >>"$LOG_FILE" 2>&1 &
  SERVER_PID="$!"
  wait_for_server_rpc "$SERVER_PID"
fi

echo "Server state: $SERVER_STATE"
echo "Server pid: $SERVER_PID"
echo "Log file: $LOG_FILE"

mode_args=(--observer-mode)
if [[ "$OBSERVER_MODE" -eq 0 ]]; then
  mode_args=(--walker-mode)
fi

anchor_pair_args=()
if [[ -n "$MAX_ANCHOR_PAIRS" ]]; then
  anchor_pair_args=(--max-anchor-pairs "$MAX_ANCHOR_PAIRS")
fi

camera_args=(--attach-observer-cameras)
if [[ "$ATTACH_OBSERVER_CAMERAS" -eq 0 ]]; then
  camera_args=(--no-observer-cameras)
fi

report_args=()
if [[ "$SAVE_TRAJECTORY_REPORT" -eq 0 ]]; then
  report_args=(--no-trajectory-report)
fi

actor_camera_capture_args=()
if [[ "$SAVE_ACTOR_CAMERA_CAPTURES" -eq 1 ]]; then
  actor_camera_capture_args=(
    --save-actor-camera-captures
    --ego-front-camera-fault "$EGO_FRONT_CAMERA_FAULT"
  )
fi

ego_fault_report_args=()
if [[ "$SAVE_EGO_FAULT_REPORT" -eq 1 ]]; then
  ego_fault_report_args=(--save-ego-fault-report)
fi

capture_args=()
if [[ "$SAVE_ACTOR_CAMERA_CAPTURES" -eq 1 || "$SAVE_OBSERVER_SCENE_CAPTURES" -eq 1 || "$SAVE_EGO_FAULT_REPORT" -eq 1 ]]; then
  capture_args=(
    --capture-image-width "$CAPTURE_IMAGE_WIDTH"
    --capture-image-height "$CAPTURE_IMAGE_HEIGHT"
    --capture-timeout-seconds "$CAPTURE_TIMEOUT_SECONDS"
  )
fi

observer_scene_capture_args=()
if [[ "$SAVE_OBSERVER_SCENE_CAPTURES" -eq 1 ]]; then
  observer_scene_capture_args=(--save-observer-scene-captures)
fi

s1_verify_args=()
if [[ "$VERIFY_S1_CROSSING_AUTOPILOT" -eq 1 ]]; then
  s1_verify_args=(
    --verify-s1-crossing-autopilot
    --s1-verify-per-anchor-seconds "$S1_VERIFY_PER_ANCHOR_SECONDS"
    --s1-verify-trigger-distance "$S1_VERIFY_TRIGGER_DISTANCE"
    --s1-verify-pass-move-meters "$S1_VERIFY_PASS_MOVE_METERS"
    --s1-verify-ego-upstream-distance "$S1_VERIFY_EGO_UPSTREAM_DISTANCE"
  )
fi

selected_scenario_args=()
if [[ -n "$SELECTED_SCENARIO" ]]; then
  selected_scenario_args=(--selected-scenario "$SELECTED_SCENARIO")
fi

static_ego_args=()
if [[ "$STATIC_EGO" -eq 1 ]]; then
  static_ego_args=(--static-ego)
fi

ego_start_args=()
if [[ -n "$EGO_START" ]]; then
  read -r EGO_START_X EGO_START_Y EGO_START_HEADING <<<"$EGO_START"
  ego_start_args=(--ego-start "$EGO_START_X" "$EGO_START_Y" "$EGO_START_HEADING")
fi

realtime_args=()
if [[ "$REALTIME_FACTOR" != "0" ]]; then
  realtime_args=(--realtime-factor "$REALTIME_FACTOR")
fi

weather_args=()
if [[ -n "$WEATHER" ]]; then
  weather_args=(--weather "$WEATHER")
fi

verbose_args=()
if [[ "$VERBOSE" -eq 1 ]]; then
  verbose_args=(--verbose)
fi

runner_status=0
for run_index in $(seq 1 "$RUNS"); do
  echo "=== DAMOS run $run_index/$RUNS: $N_SCENARIOS abnormal scenario(s) per Scenic execution ==="
  set +e
  "$PYTHON_BIN" "$RUNNER" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --wait-for-server-seconds "$SERVER_WAIT_SECONDS" \
    --scenic-time "$SCENIC_TIME" \
    --n-scenarios "$N_SCENARIOS" \
    "${selected_scenario_args[@]}" \
    "${static_ego_args[@]}" \
    "${ego_start_args[@]}" \
    "${realtime_args[@]}" \
    "${mode_args[@]}" \
    --min-move-meters "$MIN_MOVE_METERS" \
    --max-observer-anchor-distance "$MAX_OBSERVER_ANCHOR_DISTANCE" \
    --max-observer-facing-error-degrees "$MAX_OBSERVER_FACING_ERROR_DEGREES" \
    --observer-blueprint "$OBSERVER_BLUEPRINT" \
    "${anchor_pair_args[@]}" \
    --max-deliverybots "$MAX_DELIVERYBOTS" \
    --max-humanoids "$MAX_HUMANOIDS" \
    "${camera_args[@]}" \
    --observer-camera-config "$OBSERVER_CAMERA_CONFIG" \
    --scenic-timeout-seconds "$SCENIC_TIMEOUT_SECONDS" \
    --carla-map "$SCENIC_CARLA_MAP" \
    --map-xodr "$SCENIC_XODR" \
    "${weather_args[@]}" \
    "${verbose_args[@]}" \
    "${report_args[@]}" \
    "${actor_camera_capture_args[@]}" \
    "${ego_fault_report_args[@]}" \
    "${observer_scene_capture_args[@]}" \
    "${capture_args[@]}" \
    "${s1_verify_args[@]}"
  runner_status=$?
  set -e
  if [[ "$runner_status" -ne 0 ]]; then
    echo "DAMOS run $run_index/$RUNS failed with status $runner_status." >&2
    break
  fi
done
exit "$runner_status"
