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
QUALITY_LEVEL="Low"

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
ATTACH_BEV_SENSORS=0
SAVE_BEV_INPUTS=0
LIVE_BEV_FUSION=0
COMPARE_BEV_INPUTS=0
COLLECT_BEV_DATASET=0
BEV_DATASET_ROOT=""
BEV_DATASET_SPLIT="train"
SCENE_SEED=""
EVALUATE_BEV=0
BEV_SCENE_CAMERAS=0
BEV_VIDEO_SECONDS=0
BEV_VIDEO_ANCHOR=1
BEV_VIDEO_TARGET=""
REPORT_DIR=""
BEV_ROUNDS=3
BEV_RADIUS_M=60
BEV_TIMEOUT_SECONDS=60
LIVE_EGO_SHARING=0
SHARING_ROUNDS=5
SHARING_RADIUS_M=60
SHARING_TIMEOUT_SECONDS=18
SHARING_DURATION_SECONDS=0
SAVE_SHARING_VIDEO=0
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
  --compare-bev-inputs   Paired FOV/history diagnostic only; no ZKP/18s benchmark
  --collect-bev-dataset  Capture new adaptation data; requires root and scene seed
  --bev-dataset-root P   New dataset root, separate from historical reports
  --bev-dataset-split S  train, val or test (default: train)
  --scene-seed N        Explicit Scenic/observer construction seed
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
  --attach-bev-sensors   Attach six 800x450 RGB cameras plus LiDAR to every mobility
  --save-bev-inputs      Save four common frames, calibration, poses and LiDAR
                          model-input NPZs; implies --attach-bev-sensors.
                          Sensor acquisition only; no model inference.
  --live-bev-fusion      Per-mobility BEV inference + real Groth16 sharing + ego fusion.
  --evaluate-bev         Same-frame CARLA GT comparison, evaluation-only sensors.
  --bev-scene-cameras    Same-frame actual overhead RGB; requires --evaluate-bev.
  --bev-video-seconds N  Capture pass-by then replay synchronized BEV/ZKP video; 8..30s.
  --bev-video-anchor N   Pinned scenario anchor for the video (default: 1).
  --bev-video-target X Y Resolve the video anchor near Scenic X Y, then pin IDs.
  --report-dir P         Report root override; default _DAMOS/reports.
  --bev-rounds N        BEV exchange count (default: 3).
  --bev-radius-m N      Ego-neighbour radius (default: 60; 0: all).
  --bev-timeout-seconds N
                        Failure deadline, NOT performance target (default: 60; target: 18s).
  --live-ego-sharing     Front-only live image sharing, real Groth16, ego timings.
                          Detector is not connected. Extends Scenic lifetime
                          until sharing rounds finish; no full 18s pass verdict.
  --sharing-rounds N     Exchange attempt limit (default: 5); idle does not count.
                          0 runs for --sharing-duration-seconds.
  --sharing-radius-m N   Ego-neighbour radius (default: 60; 0: all observers).
                          Observer-to-observer exchange is always disabled.
  --sharing-timeout-seconds N
                          Per-round deadline (default: 18)
  --sharing-duration-seconds N
                          Wall-time session limit; stops between exchanges.
  --save-sharing-video   Save live ego front + map + verification status MP4.
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
  --quality-level LEVEL  Low (default) or Epic; weather-effect trials use Epic.
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
  [[ "$cmdline" == *"$UPROJECT"* ]] && [[ "$cmdline" == *"$MAP"* ]] && [[ "$cmdline" == *"-carla-rpc-port=$PORT"* ]] && [[ "$cmdline" == *"-quality-level=$QUALITY_LEVEL"* ]]
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
    --collect-bev-dataset)
      COLLECT_BEV_DATASET=1
      ATTACH_BEV_SENSORS=1
      shift
      ;;
    --bev-dataset-root)
      BEV_DATASET_ROOT="$2"
      shift 2
      ;;
    --bev-dataset-split)
      BEV_DATASET_SPLIT="$2"
      shift 2
      ;;
    --scene-seed)
      SCENE_SEED="$2"
      shift 2
      ;;
    --compare-bev-inputs)
      COMPARE_BEV_INPUTS=1
      ATTACH_BEV_SENSORS=1
      shift
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
    --attach-bev-sensors)
      ATTACH_BEV_SENSORS=1
      shift
      ;;
    --save-bev-inputs)
      ATTACH_BEV_SENSORS=1
      SAVE_BEV_INPUTS=1
      shift
      ;;
    --live-ego-sharing)
      LIVE_EGO_SHARING=1
      shift
      ;;
    --live-bev-fusion)
      LIVE_BEV_FUSION=1
      ATTACH_BEV_SENSORS=1
      shift
      ;;
    --evaluate-bev)
      EVALUATE_BEV=1
      shift
      ;;
    --bev-scene-cameras)
      BEV_SCENE_CAMERAS=1
      shift
      ;;
    --bev-video-seconds)
      BEV_VIDEO_SECONDS="$2"
      shift 2
      ;;
    --bev-video-anchor)
      BEV_VIDEO_ANCHOR="$2"
      shift 2
      ;;
    --bev-video-target)
      BEV_VIDEO_TARGET="$2 $3"
      shift 3
      ;;
    --report-dir)
      REPORT_DIR="$2"
      shift 2
      ;;
    --bev-rounds)
      BEV_ROUNDS="$2"
      shift 2
      ;;
    --bev-radius-m)
      BEV_RADIUS_M="$2"
      shift 2
      ;;
    --bev-timeout-seconds)
      BEV_TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --sharing-rounds)
      SHARING_ROUNDS="$2"
      shift 2
      ;;
    --sharing-radius-m)
      SHARING_RADIUS_M="$2"
      shift 2
      ;;
    --sharing-timeout-seconds)
      SHARING_TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --sharing-duration-seconds)
      SHARING_DURATION_SECONDS="$2"
      shift 2
      ;;
    --save-sharing-video)
      SAVE_SHARING_VIDEO=1
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
    --quality-level)
      QUALITY_LEVEL="$2"
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

if [[ "$QUALITY_LEVEL" != "Low" && "$QUALITY_LEVEL" != "Epic" ]]; then
  echo "--quality-level must be Low or Epic." >&2
  exit 1
fi

sharing_args=()
bev_args=()
seed_args=()
if [[ -n "$SCENE_SEED" ]]; then
  seed_args=(--scene-seed "$SCENE_SEED")
fi
if [[ "$COLLECT_BEV_DATASET" -eq 1 ]]; then
  if [[ "$COMPARE_BEV_INPUTS" -eq 1 || "$LIVE_BEV_FUSION" -eq 1 || "$SAVE_BEV_INPUTS" -eq 1 || "$EVALUATE_BEV" -eq 1 || -z "$BEV_DATASET_ROOT" || -z "$SCENE_SEED" ]]; then
    echo "Dataset collection needs its own mode, --bev-dataset-root and --scene-seed." >&2
    exit 1
  fi
fi
if [[ "$COMPARE_BEV_INPUTS" -eq 1 && ( "$LIVE_BEV_FUSION" -eq 1 || "$SAVE_BEV_INPUTS" -eq 1 || "$EVALUATE_BEV" -eq 1 ) ]]; then
  echo "Run --compare-bev-inputs separately from live fusion/evaluation/input-saving modes." >&2
  exit 1
fi
if [[ "$EVALUATE_BEV" -eq 1 && "$LIVE_BEV_FUSION" -ne 1 ]]; then
  echo "--evaluate-bev requires --live-bev-fusion." >&2
  exit 1
fi
if [[ "$BEV_SCENE_CAMERAS" -eq 1 && "$EVALUATE_BEV" -ne 1 ]]; then
  echo "--bev-scene-cameras requires --evaluate-bev." >&2
  exit 1
fi
if [[ "$LIVE_BEV_FUSION" -eq 1 || "$COMPARE_BEV_INPUTS" -eq 1 || "$COLLECT_BEV_DATASET" -eq 1 ]]; then
  if [[ "$SAVE_BEV_INPUTS" -eq 1 ]]; then
    echo "Run --live-bev-fusion separately from --save-bev-inputs." >&2
    exit 1
  fi
  PYTHON_BIN="${DAMOS_BEV_PYTHON:-/home/vvu/vv/DAMOS/.venvs/bev/bin/python}"
  export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
fi
if [[ "$ATTACH_BEV_SENSORS" -eq 1 ]]; then
  if [[ "$HEADLESS" -eq 1 || "$ATTACH_OBSERVER_CAMERAS" -ne 1 || "$OBSERVER_MODE" -ne 1 || "$REALTIME_FACTOR" == "0" ]]; then
    echo "BEV sensors need RGB rendering, observer mode and --realtime-factor 1.0; use --offscreen, not --headless." >&2
    exit 1
  fi
  if [[ "$LIVE_EGO_SHARING" -eq 1 || "$EGO_FRONT_CAMERA_FAULT" != "none" || "$SAVE_EGO_FAULT_REPORT" -eq 1 || "$SAVE_ACTOR_CAMERA_CAPTURES" -eq 1 || "$SAVE_OBSERVER_SCENE_CAPTURES" -eq 1 || "$VERIFY_S1_CROSSING_AUTOPILOT" -eq 1 ]]; then
    echo "Run BEV acquisition separately from legacy sharing/fault/capture modes." >&2
    exit 1
  fi
  bev_args=(--attach-bev-sensors)
  if [[ "$COLLECT_BEV_DATASET" -eq 1 ]]; then
    bev_args+=(--collect-bev-dataset --bev-dataset-root "$BEV_DATASET_ROOT"
      --bev-dataset-split "$BEV_DATASET_SPLIT" --bev-rounds "$BEV_ROUNDS")
  fi
  if [[ "$COMPARE_BEV_INPUTS" -eq 1 ]]; then
    bev_args+=(--compare-bev-inputs --bev-rounds "$BEV_ROUNDS")
  fi
  if [[ "$SAVE_BEV_INPUTS" -eq 1 ]]; then
    bev_args+=(--save-bev-inputs)
  fi
  if [[ "$LIVE_BEV_FUSION" -eq 1 ]]; then
    bev_args+=(--live-bev-fusion --bev-rounds "$BEV_ROUNDS"
      --bev-radius-m "$BEV_RADIUS_M" --bev-timeout-seconds "$BEV_TIMEOUT_SECONDS")
    if [[ "$EVALUATE_BEV" -eq 1 ]]; then
      bev_args+=(--evaluate-bev)
    fi
    if [[ "$BEV_SCENE_CAMERAS" -eq 1 ]]; then
      bev_args+=(--bev-scene-cameras)
    fi
    bev_args+=(--bev-video-seconds "$BEV_VIDEO_SECONDS" --bev-video-anchor "$BEV_VIDEO_ANCHOR")
    if [[ -n "$BEV_VIDEO_TARGET" ]]; then
      read -r video_x video_y <<< "$BEV_VIDEO_TARGET"
      bev_args+=(--bev-video-target "$video_x" "$video_y")
    fi
  fi
fi
if [[ "$SAVE_SHARING_VIDEO" -eq 1 && "$LIVE_EGO_SHARING" -ne 1 ]]; then
  echo "--save-sharing-video requires --live-ego-sharing." >&2
  exit 1
fi
if [[ "$LIVE_EGO_SHARING" -eq 1 ]]; then
  if [[ "$HEADLESS" -eq 1 || "$ATTACH_OBSERVER_CAMERAS" -ne 1 || "$REALTIME_FACTOR" == "0" ]]; then
    echo "Live sharing needs RGB rendering, cameras and --realtime-factor 1.0; use --offscreen, not --headless." >&2
    exit 1
  fi
  if [[ "$EGO_FRONT_CAMERA_FAULT" != "none" || "$SAVE_EGO_FAULT_REPORT" -eq 1 || "$SAVE_ACTOR_CAMERA_CAPTURES" -eq 1 || "$SAVE_OBSERVER_SCENE_CAPTURES" -eq 1 || "$VERIFY_S1_CROSSING_AUTOPILOT" -eq 1 ]]; then
    echo "Live sharing saves front images itself. Run other capture/fault modes separately." >&2
    exit 1
  fi
  sharing_args=(--live-ego-sharing --sharing-rounds "$SHARING_ROUNDS"
    --sharing-radius-m "$SHARING_RADIUS_M" --sharing-timeout-seconds "$SHARING_TIMEOUT_SECONDS"
    --sharing-duration-seconds "$SHARING_DURATION_SECONDS")
  if [[ "$SAVE_SHARING_VIDEO" -eq 1 ]]; then
    sharing_args+=(--save-sharing-video)
  fi
fi

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
      echo "Port $PORT is already used by this source build, but not with $SCENIC_CARLA_MAP / $QUALITY_LEVEL." >&2
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
    "-quality-level=$QUALITY_LEVEL"
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
if [[ -n "$REPORT_DIR" ]]; then
  report_args+=(--report-dir "$REPORT_DIR")
fi
if [[ "$SAVE_TRAJECTORY_REPORT" -eq 0 ]]; then
  report_args+=(--no-trajectory-report)
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
  "$PYTHON_BIN" -u "$RUNNER" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --wait-for-server-seconds "$SERVER_WAIT_SECONDS" \
    --scenic-time "$SCENIC_TIME" \
    --n-scenarios "$N_SCENARIOS" \
    "${selected_scenario_args[@]}" \
    "${static_ego_args[@]}" \
    "${ego_start_args[@]}" \
    "${realtime_args[@]}" \
    "${seed_args[@]}" \
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
    "${sharing_args[@]}" \
    "${bev_args[@]}" \
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
