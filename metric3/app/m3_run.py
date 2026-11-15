"""Freeze independent anchor cases before inference; preserve failed attempts."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys

from m3_rules import rollup, safe_run, select_cases
from report_data import atomic_json, read
from gpu_resources import binding, worker_slot
from damos_paths import runtime_root, shared_root, worker_lock_root


def now():
    return datetime.now(timezone.utc).isoformat()


def verify_runtime(protocol):
    for path, digest in protocol["source_hashes"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen runtime changed: " + path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    safe_run(args.run)
    job, folder = root / "jobs" / args.run, root / "reports" / args.run
    launch = read(job / "launch.json")
    folder.mkdir(exist_ok=True)
    protocol = read(folder / "protocol.json")
    source = runtime_root(root) / "Carla-0.9.16-source"
    from run_metric3_100 import plan_cases, planned_start
    from bev_accuracy_batch import CHECKPOINT_SHA
    checkpoint = Path(os.environ["DAMOS_BEV_CHECKPOINT"])
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != CHECKPOINT_SHA:
        raise ValueError("Unapproved metric3 model")
    if protocol is None:
        import carla
        road_map = carla.Map("Town10HD_Opt", (source / "Scenic/Maps/Town10HD_Opt.xodr").read_text())
        cases = select_cases(plan_cases(), launch["mode"], launch["scenario"])
        for case in cases:
            case["ego_start_scenic"] = planned_start(road_map, case)
        protocol = {"source": "live_carla_sensor_stream", "cases": cases, "created_utc": now(),
            "checkpoint_sha256": CHECKPOINT_SHA, "goal_percent": 70, "carla_port": 2830, "gpu_binding": binding(),
            "minimum_contiguous_span_ns": "500000000", "minimum_connected_overlap_cells": 3,
            "sampling_fps": 5, "sensor_period_seconds": .1, "window_seconds": 6,
            "sensor_input": "RGB 6 directions x 4 frames and current standard LiDAR, each node",
            "gt_used_for_inference": False, "fusion_policy": "ego_observed; evidence_fusion off",
            "queue": "bounded current-run in-memory stream; inference starts before capture completes; no recording-file input",
            "case_origin": "same preplanned 100-case seeds/weather/anchor geometry as existing protocol; new simulation and predictions",
            "error_policy": "execution errors separate from detection failures; same-case attempts retained; no final criterion before all planned cases evaluated",
            "native_carla_window": not args.offscreen, "hostname": platform.node()}
        paths = [*Path(__file__).parent.glob("*.py"),
                 *(source / "_DAMOS/scripts" / name for name in ["bev_accuracy_batch.py", "bev_anchor_evaluation.py", "bev_evaluation.py", "bev_passby_video.py", "run_metric3_100.py", "bev_model_runtime.py", "bev_fusion.py"])]
        common = root.parent / "metric2/app"
        paths += [common / name for name in ("core_overlay/bev_mesh.py", "live_engine.py", "live_scene.py", "live_checks.py", "process_control.py", "gpu_resources.py")]
        paths += [source / "_DAMOS/_scenarios/S_.scenic", source / "Scenic/Maps/Town10HD_Opt.xodr", runtime_root(root) / "sensor_config.txt"]
        paths += [shared_root(root) / name for name in ('damos_paths.py', 'env.sh')]
        paths += sorted((source / "PythonAPI/carla/agents").rglob("*.py"))
        paths += [source / "_DAMOS/scripts" / name for name in ("bev_sensor_rig.py", "bev_protocol.py", "bev_occupancy.py", "bev_ground_guard.py", "bev_lidar_calibration.py", "bev_camera_freshness.py", "scenic_custom_walker_injector.py")]
        paths += sorted((source / "_DAMOS/vendor/yerin_bev").rglob("*.py"))
        protocol["source_hashes"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        atomic_json(folder / "protocol.json", protocol)
        atomic_json(folder / "protocol_lock.json", {"sha256": hashlib.sha256((folder / "protocol.json").read_bytes()).hexdigest()})
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv"], capture_output=True, text=True, timeout=20)
        atomic_json(folder / "environment.json", {"hostname": platform.node(), "python": sys.version, "gpu": gpu.stdout,
                    "platform": platform.platform(), "native_carla_window": not args.offscreen})
    lock = read(folder / "protocol_lock.json")
    if hashlib.sha256((folder / "protocol.json").read_bytes()).hexdigest() != lock["sha256"]:
        raise ValueError("Frozen live protocol changed")
    verify_runtime(protocol)
    state = {"status": "running", "pid": os.getpid(), "started_utc": now(), "source": protocol["source"]}
    atomic_json(job / "state.json", state)
    def interrupt(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)
    code, error = 1, None
    results = {c["case_id"]: read(folder / "cases" / c["case_id"] / "result.json", {}) for c in protocol["cases"]}
    results = {key: value for key,value in results.items() if value}
    with worker_slot(worker_lock_root(root)):
        try:
            for case in protocol["cases"]:
                verify_runtime(protocol)
                case_id = case["case_id"]
                if results.get(case_id, {}).get("status") in ("success", "failure"):
                    continue
                case_dir = folder / "cases" / case_id
                case_dir.mkdir(parents=True, exist_ok=True)
                attempts = [int(p.name.split("_")[1]) for p in case_dir.glob("attempt_*") if p.is_dir()]
                attempt = max(attempts, default=0) + 1
                atomic_json(job / "scene_live.json", {"source": protocol["source"], "scenario": case["scenario"], "case_id": case_id})
                for old in job.glob("scene_live_F*.jpg"):
                    old.unlink(missing_ok=True)
                command = [sys.executable, "-u", str(Path(__file__).with_name("m3_scene.py")), "--root", str(root),
                           "--run", args.run, "--case", case_id, "--attempt", str(attempt)]
                if args.offscreen:
                    command.append("--offscreen")
                process = subprocess.Popen(command, start_new_session=True)
                target = case_dir / f"attempt_{attempt:03d}"
                try:
                    returned = process.wait()
                finally:
                    if process.poll() is None:
                        process.send_signal(signal.SIGINT)
                        process.wait(timeout=90)
                    from process_control import cleanup_scene
                    cleanup_scene(target)
                result = read(target / "result.json", {"case_id": case_id, "scenario": case["scenario"],
                    "status": "execution_error", "successful": False, "error": f"Scene exited {returned}"})
                results[case_id] = result
                atomic_json(case_dir / "result.json", result)
                summary = {"source": protocol["source"], **rollup(protocol["cases"], results)}
                atomic_json(folder / "summary.json", summary)
                print(json.dumps({k:summary[k] for k in ("planned", "evaluated", "successful", "execution_errors")}), flush=True)
                if returned or result["status"] in ("execution_error", "not_evaluable"):
                    raise RuntimeError(result.get("error") or "No assessable anchor ground truth")
            code = 0
        except KeyboardInterrupt:
            code, error = 130, "User requested stop"
        except BaseException as exc:
            error = type(exc).__name__ + ": " + str(exc)
        finally:
            summary = {"source": protocol["source"], **rollup(protocol["cases"], results), "completed_utc": now(), "error": error}
            atomic_json(folder / "summary.json", summary)
            state.update(status="complete" if code == 0 else "cancelled" if code == 130 else "failed", error=error, ended_utc=now())
            atomic_json(job / "state.json", state)
            print(json.dumps(state), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
