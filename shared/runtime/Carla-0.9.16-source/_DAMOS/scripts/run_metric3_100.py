"""Freeze 100 anchor cases before outcomes; retain every planned case."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time

from bev_accuracy_batch import CHECKPOINT_SHA, sha256, write_json

TARGETS = {
    "S1": [(-81, -121.7), (39.3, -148.5), (37.6, 75.3)],
    "S2": [(-81, -121.7), (39.3, -148.5), (37.6, 75.3), (-59, 10), (57, -57)],
    "S3": [(-14.5, -127.5), (14, -144), (107.5, -115)],
    "S4": [(-81, -121.7), (39.3, -148.5), (37.6, 75.3), (-59, 10), (57, -57)],
    "S5": [(-59, 10)], "S6": [(-98, -121.7)], "S7": [(57, -57)],
    "S8": [(-95, -90), (37.6, 75.3), (92, -85)],
    "S9": [(-3.8, 75.4), (-59.6, -47.1), (32.5, -44.1), (92.1, 12.1), (16.5, -123.1)],
}
WEATHERS = ["ClearNoon", "WetCloudyNoon", "HardRainNoon", "ClearNight"]
CORE_FILES = ["bev_accuracy_batch.py", "run_metric3_100.py", "verify_metric3_100.py",
    "prepare_metric3_100.py", "test_metric3_100.py",
    "audit_metric3_zkp.py",
    "bev_passby_video.py", "scenic_custom_walker_injector.py",
    "run_scenic_custom_walkers_town10hd.sh", "bev_model_runtime.py", "bev_sensor_rig.py",
    "bev_camera_freshness.py", "bev_mesh.py", "bev_protocol.py", "bev_fusion.py",
    "bev_evaluation.py", "bev_anchor_evaluation.py", "bev_occupancy.py",
    "bev_ground_guard.py", "bev_lidar_calibration.py", "bev_ground_guard_20261008.json"]


def plan_cases():
    rows = []
    for repetition in range(11):
        for number in range(1, 10):
            scenario = f"S{number}"
            rows.append({"case_id": f"M3-{len(rows)+1:03d}-{scenario}", "scenario": scenario,
                "repetition": repetition + 1, "scene_seed": 2026101200 + len(rows) + 1,
                "weather": WEATHERS[(repetition + number - 1) % 4],
                "target_scenic_xy": list(TARGETS[scenario][repetition % len(TARGETS[scenario])]),
                "upstream_distance_m": [35, 40, 45][repetition % 3],
                "entry_radius_m": 20, "window_seconds": 6, "approach_timeout_seconds": 240,
                "early_success_stop": True})
    rows.append({**rows[8], "case_id": "M3-100-S9", "repetition": 12,
        "scene_seed": 2026101300, "weather": "ClearNight",
        "target_scenic_xy": list(TARGETS["S9"][1]), "upstream_distance_m": 45})
    return rows


def planned_start(road_map, case):
    import carla
    from bev_passby_video import video_waypoint
    x, y = case["target_scenic_xy"]
    # Random S4 anchors do not yet exist; only use the preplanned reference road.
    scenario = "S2" if case["scenario"] == "S4" else case["scenario"]
    waypoint = video_waypoint(road_map, x, -y, scenario)
    candidates = waypoint.previous(case["upstream_distance_m"])
    if not candidates:
        raise ValueError("No upstream Driving-lane spawn for preplanned case")
    candidates.sort(key=lambda p: (p.is_junction,
        abs((p.transform.rotation.yaw - waypoint.transform.rotation.yaw + 180) % 360 - 180),
        p.road_id, p.lane_id, p.s))
    transform = candidates[0].transform
    return [float(transform.location.x), float(-transform.location.y), float(-transform.rotation.yaw - 90)]


def freeze(root, output):
    if output.exists():
        raise FileExistsError("Use a new trial folder; do not overwrite existing evidence")
    import carla
    scripts = root / "_DAMOS/scripts"
    checkpoint = root / "_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt"
    if sha256(checkpoint) != CHECKPOINT_SHA:
        raise ValueError("Checkpoint differs from the frozen preliminary model")
    paths = [scripts / name for name in CORE_FILES] + [checkpoint,
        root / "_DAMOS/_scenarios/S_.scenic", root / "Scenic/Maps/Town10HD_Opt.xodr",
        root.parent / "sensor_config.txt"]
    paths += [scripts / "m2x/security.py", scripts / "m2x/transport.py", scripts / "bev_live.py"]
    vendor = root / "_DAMOS/vendor"
    paths += sorted((vendor / "yerin_bev").rglob("*.py"))
    paths += [vendor / "yerin_bev/prism_v2.json",
              vendor / "damos_zkp/crypto/groth16_v2v.py", vendor / "damos_zkp/app/config.py",
              vendor / "damos_zkp/perception/canonical.py"]
    for extension in ("*.wasm", "*.zkey", "*.json"):
        paths += sorted((vendor / "damos_zkp/assets/perception_v2").rglob(extension))
    source_hashes = {str(path): {"sha256": sha256(path), "bytes": path.stat().st_size,
        "snapshot_path": str(path.relative_to(root.parent))} for path in paths}
    road_map = carla.Map("Town10HD_Opt", (root / "Scenic/Maps/Town10HD_Opt.xodr").read_text())
    cases = plan_cases()
    for case in cases:
        case["ego_start_scenic"] = planned_start(road_map, case)
    output.mkdir(parents=True)
    (output / "cases").mkdir()
    for case in cases:
        write_json(output / "cases" / (case["case_id"] + ".json"), case)
    protocol = {"created_utc": datetime.now(timezone.utc).isoformat(),
        "criterion": "anchor-duration-v1", "planned_cases": 100, "goal_percent": 70,
        "scenario_counts": dict(Counter(c["scenario"] for c in cases)),
        "additional_case_assigned_before_outcomes": "S9",
        "checkpoint_sha256": CHECKPOINT_SHA, "minimum_contiguous_span_ns": "500000000",
        "minimum_connected_overlap_cells": 3, "minimum_gt_cells": 3, "connectivity": 8,
        "expected_period_ns": "200000000", "expected_frame_stride": 2, "tolerance_ns": 1000,
        "sampling_fps": 5, "selection": "one geometry-selected anchor per newly generated scene",
        "acquisition": "first entry within 20m, fixed 6s/30-sample window; approach limit 240s",
        "processing": "stop after the first sufficient 0.5s success witness; otherwise process all 30 samples",
        "sequential_success_witness_equivalent_to_full_window_binary_criterion": True,
        "denominator": "all 100 preplanned cases; missing, failure, data error retained",
        "no_outcome_based_replacement": True, "gt_used_for_inference": False,
        "case_paths": ["cases/" + c["case_id"] + ".json" for c in cases],
        "case_sha256": {c["case_id"]: sha256(output / "cases" / (c["case_id"] + ".json")) for c in cases},
        "source_hashes": source_hashes, "cases": cases,
        "environment": {"hostname": platform.node(), "platform": platform.platform(),
            "python": sys.version,
            "gpu": subprocess.check_output(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader"], text=True).strip(),
            "cpu": json.loads(subprocess.check_output(["lscpu", "-J"], text=True)),
            "packages": sorted(({"name": d.metadata["Name"], "version": d.version}
                for d in distributions()), key=lambda d: (d["name"].lower(), d["version"]))}}
    write_json(output / "protocol.json", protocol)
    write_json(output / "protocol_lock.json", {"sha256": sha256(output / "protocol.json")})
    print(f"[PLAN] Frozen 100 cases: {output}", flush=True)


def verify_freeze(output, *, check_code=True):
    protocol_path = output / "protocol.json"
    lock = json.loads((output / "protocol_lock.json").read_text())
    if sha256(protocol_path) != lock["sha256"]:
        raise ValueError("Frozen protocol changed")
    protocol = json.loads(protocol_path.read_text())
    for case, path in zip(protocol["cases"], protocol["case_paths"]):
        if sha256(output / path) != protocol["case_sha256"][case["case_id"]]:
            raise ValueError("Preplanned case changed")
    if check_code:
        for path, record in protocol["source_hashes"].items():
            if sha256(path) != record["sha256"]:
                raise ValueError("Frozen runtime changed: " + path)
    return protocol


def aggregate(output, protocol):
    cases = []
    for case in protocol["cases"]:
        path = output / "results" / case["case_id"] / "result.json"
        row = json.loads(path.read_text()) if path.exists() else {
            "case_id": case["case_id"], "scenario": case["scenario"],
            "status": "not_run", "successful": False, "samples": 0}
        cases.append(row)
    successes = sum(c["successful"] is True for c in cases)
    completed = sum(c["status"] != "not_run" for c in cases)
    summary = {"planned_cases": 100, "completed_cases": completed,
        "successful_cases": successes, "success_rate_percent": successes,
        "goal_percent": 70, "all_cases_complete": completed == 100,
        "criterion_pass": completed == 100 and successes >= 70,
        "statuses": dict(Counter(c["status"] for c in cases)),
        "samples": sum(c.get("samples", 0) for c in cases),
        "per_scenario": {s: {"planned": sum(c["scenario"] == s for c in cases),
            "completed": sum(c["scenario"] == s and c["status"] != "not_run" for c in cases),
            "successful": sum(c["scenario"] == s and c["successful"] for c in cases)} for s in TARGETS},
        "cases": cases, "protocol_sha256": sha256(output / "protocol.json")}
    write_json(output / "summary.json", summary)
    return summary


@contextmanager
def owned_server(root, output, port):
    import carla
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError("Trial port is already occupied; refusing to replace another server")
    editor = root.parent / "UnrealEngine_4.26/Engine/Binaries/Linux/UE4Editor"
    command = [str(editor), str(root / "Unreal/CarlaUE4/CarlaUE4.uproject"),
        "/Game/Carla/Maps/Town10HD_Opt", "-game", f"-carla-rpc-port={port}",
        "-quality-level=Low", "-RenderOffScreen", "-nosound"]
    env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0"),
               XAUTHORITY=os.environ.get("XAUTHORITY", "/run/user/1000/gdm/Xauthority"))
    with (output / "server.log").open("a") as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, cwd=root)
        try:
            deadline = time.monotonic() + 1200
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Owned CARLA server exited before readiness")
                try:
                    # A client created before RPC startup may retain a failed episode stream.
                    client = carla.Client("127.0.0.1", port)
                    client.set_timeout(3)
                    client.get_world()
                    break
                except RuntimeError:
                    time.sleep(1)
            else:
                raise TimeoutError("Owned CARLA server readiness timeout")
            client.set_timeout(60)
            yield client
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def run_cases(root, output, port, client, limit=None):
    protocol = verify_freeze(output)
    checkpoint = root / "_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt"
    executed = 0
    for case in protocol["cases"]:
        target = output / "results" / case["case_id"]
        if (target / "result.json").exists():
            continue
        if target.exists():
            raise RuntimeError("Interrupted case evidence exists; explicit audit required: " + str(target))
        if shutil.disk_usage(output).free < 8 * 1024**3 or shutil.disk_usage(root).free < 3 * 1024**3:
            raise RuntimeError("Insufficient space; existing evidence retained")
        verify_freeze(output)
        # Reset actors, sensor buffers and simulation clock for every new scene.
        client.load_world("Town10HD_Opt")
        target.mkdir(parents=True)
        case_path = output / "cases" / (case["case_id"] + ".json")
        env = dict(os.environ, DAMOS_BEV_CHECKPOINT=str(checkpoint), DAMOS_BEV_ACCURACY_CASE=str(case_path))
        command = [str(root / "_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh"),
            "--offscreen", "--port", str(port), "--selected-scenario", case["scenario"],
            "--runs", "1", "--scenic-time", "30", "--realtime-factor", "1.0",
            "--ego-start", *map(str, case["ego_start_scenic"]), "--weather", case["weather"],
            "--scene-seed", str(case["scene_seed"]), "--live-bev-fusion", "--evaluate-bev",
            "--bev-scene-cameras", "--bev-video-seconds", "30",
            "--report-dir", str(target), "--no-trajectory-report"]
        started = datetime.now(timezone.utc).isoformat()
        write_json(target / "invocation.json", {"started_utc": started, "command": command,
            "environment": {key: env[key] for key in ("DAMOS_BEV_CHECKPOINT", "DAMOS_BEV_ACCURACY_CASE")},
            "case_sha256": sha256(case_path), "protocol_sha256": sha256(output / "protocol.json")})
        print(f"[TRIAL] {case['case_id']} seed={case['scene_seed']} weather={case['weather']}", flush=True)
        with (target / "runtime.log").open("w") as log:
            process = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                log.write(line)
                log.flush()
                if line.startswith(("[ACCURACY", "Traceback", "RuntimeError", "ValueError", "TimeoutError")):
                    print(line.rstrip(), flush=True)
            code = process.wait()
        summary_path = target / "recording/summary.json"
        result = json.loads(summary_path.read_text()) if summary_path.exists() else {
            "case_id": case["case_id"], "scenario": case["scenario"], "status": "data_error",
            "successful": False, "samples": 0, "error": "Acquisition or execution failed; see runtime.log"}
        if code != 0:
            result.update(status="data_error", successful=False,
                          execution_error=f"Runtime exit {code}; no outcome-based replacement")
        result.update(returncode=code, finished_utc=datetime.now(timezone.utc).isoformat())
        write_json(target / "result.json", result)
        summary = aggregate(output, protocol)
        print(f"[TRIAL TOTAL] completed={summary['completed_cases']}/100 success={summary['successful_cases']} status={result['status']}", flush=True)
        executed += 1
        if limit is not None and executed >= limit:
            break
    aggregate(output, protocol)


def run(root, output, port, limit=None):
    verify_freeze(output)
    with owned_server(root, output, port) as client:
        run_cases(root, output, port, client, limit)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("freeze", "run", "summary"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=2680)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("limit must be positive")
    if args.mode == "freeze":
        freeze(args.root.resolve(), args.output.resolve())
    elif args.mode == "run":
        run(args.root.resolve(), args.output.resolve(), args.port, args.limit)
    else:
        print(json.dumps(aggregate(args.output, verify_freeze(args.output, check_code=False)), indent=2))


if __name__ == "__main__":
    main()
