"""Freeze and execute a separate CARLA S5 fault-response matrix without replacing metrics."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from fault_catalog import PIPELINE_CASE_NAMES
from bev_fault_quality import INJECTION_SETTINGS, QUALITY_LIMITS, INFERENCE_REPLAY_LIMITS
from bev_model_runtime import CUDA_INFERENCE_POLICY

CASE_NAMES = list(PIPELINE_CASE_NAMES)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def freeze_source(files, output):
    sources = [(Path(p).resolve(), Path(p).read_bytes()) for p in files]
    if len({p.name for p, _ in sources}) != len(sources):
        raise ValueError("Frozen source filenames must be unique")
    target = Path(output) / "frozen_source"
    target.mkdir(exist_ok=False)
    for path, data in sources:
        (target / path.name).write_bytes(data)
    return {str(p): hashlib.sha256(data).hexdigest() for p, data in sources}


def completed_successfully(summary):
    return (summary["planned"] > 0 and summary["completed"] == summary["planned"]
            and summary["successful"] == summary["planned"]
            and all(not e["returncode"] and not e["summary"].get("fatal_error")
                    for e in summary["executions"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--port", type=int, default=2690)
    parser.add_argument("--cases", nargs="+", choices=CASE_NAMES, default=CASE_NAMES)
    parser.add_argument("--weathers", nargs="+", choices=["ClearNoon", "HardRainNoon", "ClearNight"],
                        default=["ClearNoon", "HardRainNoon", "ClearNight"])
    parser.add_argument("--receive-timeout-s", type=float, default=8.)
    args = parser.parse_args()
    if (not 1 <= args.receive_timeout_s <= 15 or len(set(args.cases)) != len(args.cases)
            or len(set(args.weathers)) != len(args.weathers)):
        parser.error("Unique cases/weathers and 1..15s receive timeout required")
    root, output = args.root.resolve(), args.output.resolve()
    checkpoint = root / "_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt"
    if not checkpoint.is_file():
        raise FileNotFoundError(checkpoint)
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            raise RuntimeError("Port occupied; will not restart somebody else's CARLA")
    if shutil.disk_usage(root).free < 3 * 1024 ** 3:
        raise RuntimeError("Need at least 3GiB free on the runtime filesystem")
    output.mkdir(parents=True, exist_ok=False)
    files = [Path(__file__), *[root / "_DAMOS/scripts" / n for n in
        ("bev_resilience.py", "carla_resilience_trial.py", "bev_mesh.py", "bev_live.py",
         "bev_protocol.py", "bev_fusion.py", "bev_model_runtime.py", "bev_sensor_rig.py",
         "fault_catalog.py", "bev_fault_quality.py", "audit_carla_resilience.py",
         "render_carla_resilience.py", "bev_occupancy.py", "bev_ground_guard.py")],
         root.parent / "sensor_config.txt", root / "_DAMOS/vendor/yerin_bev/models_bev.py",
         root / "_DAMOS/vendor/yerin_bev/prism_v2.json"]
    source = freeze_source(files, output)
    write_json(output / "protocol.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
        "cases": args.cases, "weathers": args.weathers, "scenario": "S5", "ego_only": True,
        "source_sha256": source, "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "service_definition": "fresh authenticated cooperative monitoring or explicit stop; no guaranteed all-object recognition",
        "phases": {"baseline_rounds": 3, "fault_rounds": 1, "recovery_rounds": 3},
        "quality_limits": QUALITY_LIMITS, "quality_injection_settings": INJECTION_SETTINGS,
        "inference_replay_limits": INFERENCE_REPLAY_LIMITS,
        "inference_policy": CUDA_INFERENCE_POLICY,
        "camera_degradation_variant": "severe defocus blur; other camera degradation variants not covered",
        "receive_timeout_s": args.receive_timeout_s, "maximum_round_age_s": 20.,
        "brake_check": "separate controlled initial 2m/s to <=0.2m/s within 5 simulation seconds",
        "scope": "CARLA only, one pinned observer, stationary monitoring plus actuator check; not autonomous-driving recovery",
        "formal_metric2_or_3_case": False, "no_failed_case_replacement": True})
    results = []
    for index, weather in enumerate(args.weathers):
        target = output / weather
        target.mkdir()
        plan = {"schema": "DAMOS_RESILIENCE_TRIAL_V2", "cases": args.cases, "weather": weather,
                "port": args.port, "receive_timeout_s": args.receive_timeout_s,
                "maximum_round_age_s": 20.}
        plan_path = target / "plan.json"
        write_json(plan_path, plan)
        env = dict(os.environ, DAMOS_RESILIENCE_PLAN=str(plan_path), DAMOS_BEV_CHECKPOINT=str(checkpoint))
        command = [str(root / "_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh"),
            "--restart", "--offscreen", "--port", str(args.port), "--selected-scenario", "S5",
            "--runs", "1", "--scenic-time", "1800", "--realtime-factor", "1.0",
            "--ego-start", "-59", "26", "0", "--weather", weather,
            "--scene-seed", str(2026101400 + index), "--observer-blueprint", "deliverybot",
            "--live-bev-fusion", "--bev-rounds", str(7 * len(args.cases)),
            "--report-dir", str(target), "--no-trajectory-report"]
        write_json(target / "invocation.json", {"command": command, "environment": {
            "DAMOS_RESILIENCE_PLAN": str(plan_path), "DAMOS_BEV_CHECKPOINT": str(checkpoint)}})
        print(f"[RESILIENCE RUN] {weather}: {len(args.cases)} planned cases", flush=True)
        with (target / "runtime.log").open("w") as log:
            process = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                log.write(line)
                log.flush()
                if line.startswith(("[RESILIENCE", "Traceback", "RuntimeError", "ValueError", "TimeoutError")):
                    print(line.rstrip(), flush=True)
            code = process.wait()
        summary_path = target / "resilience/summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else {"cases": [], "fatal_error": "No trial summary"}
        results.append({"weather": weather, "returncode": code, "summary": summary})
        cases = [c for r in results for c in r["summary"]["cases"]]
        aggregate = {"planned": len(args.cases) * len(args.weathers),
            "completed": len(cases), "successful": sum(c["criterion_pass"] for c in cases),
            "executions": results, "formal_metric2_or_3_case": False}
        aggregate["criterion_pass"] = completed_successfully(aggregate)
        write_json(output / "summary.json", aggregate)
    if any(r["returncode"] or r["summary"].get("fatal_error") for r in results):
        raise RuntimeError("Execution errors retained; see the distinct resilience output")
    print(f"[RESILIENCE SUMMARY] {output / 'summary.json'}", flush=True)
    if not completed_successfully(aggregate):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
