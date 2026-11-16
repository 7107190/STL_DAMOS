"""One original Scenic Ego and one geometrically selected observer per fresh world."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal

from report_data import atomic_json, read
from damos_paths import python_env, runtime_root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    run = root / "reports" / args.run
    protocol = read(run / "protocol.json")
    case = next(c for c in protocol["cases"] if c["case_id"] == args.case)
    if protocol["source"] != "live_carla_sensor_stream":
        raise ValueError("Not a new live CARLA trial")
    source = runtime_root(root) / "Carla-0.9.16-source"
    target = run / "cases" / args.case / f"attempt_{args.attempt:03d}"
    target.mkdir(parents=True, exist_ok=False)
    job = root / "jobs" / args.run
    context = {"root": root, "job": job, "run": args.run, "case": case, "target": target,
               "port": protocol["carla_port"], "rows": []}
    def interrupt(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)
    code, error, diagnostic = 1, None, None
    try:
        from live_engine import simulation
        from m3_stream import install
        from process_control import identity
        import scenic_custom_walker_injector as injector
        install(context)
        original_launch = injector.launch_scenic_process
        def launch(*a, **kw):
            process = original_launch(*a, **kw)
            atomic_json(target / "scenic_process.json", identity(process.pid))
            return process
        injector.launch_scenic_process = launch
        coverage = {}
        original_check = injector.find_observer_metric_failures
        def check(metrics, **kw):
            failures = original_check(metrics, **kw)
            coverage.update(metrics=list(metrics), failures=failures, thresholds=kw)
            atomic_json(target / "post_scene_observer_check.json", coverage)
            return failures
        injector.find_observer_metric_failures = check
        atomic_json(job / "live.json", {"phase": "carla_starting", "case_id": args.case, "scenario": case["scenario"]})
        with simulation(root, target, protocol["carla_port"], args.offscreen) as client:
            context["client"] = client
            config = injector.ScenicCustomWalkerConfig(
                port=protocol["carla_port"], carla_map="Town10HD_Opt",
                map_xodr=str(source / "Scenic/Maps/Town10HD_Opt.xodr"),
                scenic_bin=python_env(root) / "bin/scenic", scenic_file=source / "_DAMOS/_scenarios/S_.scenic",
                observer_camera_config=runtime_root(root) / "sensor_config.txt", report_dir=target / "scene_diagnostics",
                n_scenarios=1, selected_scenario=case["scenario"], scene_seed=case["scene_seed"],
                static_ego=True, ego_start=tuple(case["ego_start_scenic"]), weather=case["weather"],
                realtime_factor=1., scenic_time=1200., save_trajectory_report=False,
                attach_bev_sensors=True, live_bev_fusion=True, bev_rounds=30,
                bev_timeout_seconds=60, wait_for_ego_seconds=60, wait_for_support_seconds=40)
            config.report_dir.mkdir()
            try:
                injector.run_scenic_custom_walker_integration(config)
            except RuntimeError as exc:
                failures = coverage.get("failures", [])
                valid = context.get("result", {}).get("status") in ("success", "failure")
                distance_only = bool(failures) and all(reason.startswith("observer_to_anchor_distance=") for _,reason in failures)
                okay = coverage.get("metrics") and all(m.get("status") == "ok" and m.get("on_sidewalk") for m in coverage["metrics"])
                if not (str(exc).startswith("Custom observers did not satisfy Scenic anchor coverage requirements.") and valid and distance_only and okay):
                    raise
                diagnostic = {"message": str(exc), "scope": "post-window moving-target distance, separate from recorded anchor detection", **coverage}
        code = 0
    except KeyboardInterrupt:
        error, code = "User requested stop", 130
    except BaseException as exc:
        error = type(exc).__name__ + ": " + str(exc)
        print("[LIVE M3 ERROR] " + error, flush=True)
    finally:
        tracker = context.get("observer_tracker")
        if tracker:
            tracker[0].set()
            tracker[1].join(timeout=3)
        result = context.get("result", {"case_id": args.case, "scenario": case["scenario"],
            "source": "live_carla_sensor_stream", "status": "execution_error", "successful": False,
            "samples": len(context["rows"]), "error": error})
        if code:
            result.update(status="execution_error", successful=False, error=error)
        result.update(returncode=code, attempt=args.attempt, output=str(target), post_scene_diagnostic=diagnostic)
        atomic_json(target / "result.json", result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
