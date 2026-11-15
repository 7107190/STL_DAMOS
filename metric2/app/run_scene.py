"""One fresh CARLA/Scenic scene in its own Python/CARLA-client process."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys

from report_data import atomic_json, read
from damos_paths import python_env, runtime_root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--session", type=int, required=True)
    parser.add_argument("--scenario", choices=[f"S{i}" for i in range(1, 10)], required=True)
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / "reports" / args.run
    job = root / ".metric2_ui_jobs" / args.run
    proto = read(output / "protocol.json")
    if (proto.get("source") != "live_carla" or args.scenario not in proto["scenarios"]
            or not 1 <= args.session <= proto["sessions"]):
        raise ValueError("Invalid live scene selection")
    source = runtime_root(root) / "Carla-0.9.16-source"
    sys.path[:0] = [str(Path(__file__).parent / "core_overlay"), str(source / "_DAMOS/scripts")]
    scene_dir = job / f"scene_{args.session:02d}_{args.scenario}"
    scene_dir.mkdir(exist_ok=False)
    completed = (output / "rounds.jsonl").read_text().count("\n") if (output / "rounds.jsonl").exists() else 0
    context = {"root": root, "job": job, "run": args.run, "output": output, "rows": [],
               "session": args.session, "scenario": args.scenario, "scene": f"session{args.session}:{args.scenario}",
               "expected": proto["sessions"] * proto["repeats_per_input_per_session"] * len(proto["scenarios"]),
               "completed_before": completed, "checkpoint_hash": proto["checkpoint_sha256"],
               "tm_port": proto["carla_port"] + 6000}
    def interrupt(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)
    error, code, post_diagnostic = None, 1, None
    try:
        from live_engine import simulation
        from live_plugin import install
        import scenic_custom_walker_injector as injector
        from process_control import identity
        from scenic_custom_walker_injector import ScenicCustomWalkerConfig, run_scenic_custom_walker_integration
        install(context)
        original_launch = injector.launch_scenic_process
        def recorded_launch(*args, **kwargs):
            process = original_launch(*args, **kwargs)
            atomic_json(scene_dir / "scenic_process.json", identity(process.pid))
            return process
        injector.launch_scenic_process = recorded_launch
        final_coverage = {}
        original_check = injector.find_observer_metric_failures
        def recorded_check(metrics, **kwargs):
            failures = original_check(metrics, **kwargs)
            final_coverage.update(metrics=list(metrics), failures=failures, thresholds=kwargs)
            atomic_json(scene_dir / 'post_scene_observer_check.json', final_coverage)
            return failures
        injector.find_observer_metric_failures = recorded_check
        atomic_json(job / "live.json", {"phase": "carla_starting", "source": "live_carla",
                    "current": {"session": args.session, "scenario": args.scenario}})
        with simulation(root, scene_dir, proto["carla_port"], args.offscreen) as client:
            context["client"] = client
            atomic_json(job / "live.json", {"phase": "scene_starting", "source": "live_carla",
                        "current": {"session": args.session, "scenario": args.scenario}})
            config = ScenicCustomWalkerConfig(
                port=proto["carla_port"], carla_map="Town10HD_Opt",
                map_xodr=str(source / "Scenic/Maps/Town10HD_Opt.xodr"),
                scenic_bin=Path(os.environ.get("DAMOS_SCENIC_BIN", python_env(root) / "bin/scenic")),
                scenic_file=source / "_DAMOS/_scenarios/S_.scenic", observer_camera_config=runtime_root(root) / "sensor_config.txt",
                report_dir=output / "scene_diagnostics" / f"session_{args.session:02d}" / args.scenario,
                n_scenarios=1, selected_scenario=args.scenario,
                scene_seed=26110600 + args.session * 10 + int(args.scenario[1:]),
                static_ego=True, realtime_factor=1., scenic_time=600,
                save_trajectory_report=False, attach_bev_sensors=True, live_bev_fusion=True,
                bev_rounds=proto["repeats_per_input_per_session"], bev_radius_m=60., bev_timeout_seconds=60.,
                wait_for_ego_seconds=60., wait_for_support_seconds=40., weather="ClearNoon")
            config.report_dir.mkdir(parents=True, exist_ok=True)
            try:
                run_scenic_custom_walker_integration(config)
            except RuntimeError as exc:
                from live_checks import post_distance_diagnostic_only
                if not post_distance_diagnostic_only(exc, final_coverage.get('metrics'), final_coverage.get('failures'),
                                                     context['rows'], proto['repeats_per_input_per_session']):
                    raise
                post_diagnostic = {**final_coverage, 'message': str(exc),
                    'scope': 'after all timed processing; moving-target distance is not the metric2 latency criterion',
                    'metric3_accuracy_evaluated': False}
                print('[LIVE M2] Timed pipeline completed; post-scene moving-target distance retained as a separate diagnostic.', flush=True)
            finally:
                tracker = context.pop("observer_tracker", None)
                if tracker:
                    tracker[0].set()
                    tracker[1].join(timeout=5)
        code = 0
    except KeyboardInterrupt:
        error, code = "User requested stop", 130
    except BaseException as exc:
        error = type(exc).__name__ + ": " + str(exc)
        print("[LIVE SCENE ERROR] " + error, flush=True)
    finally:
        atomic_json(scene_dir / "result.json", {"session": args.session, "scenario": args.scenario,
                    "source": "live_carla", "rounds": len(context["rows"]), "returncode": code, "error": error,
                    'post_scene_diagnostic': post_diagnostic})
    return code


if __name__ == "__main__":
    raise SystemExit(main())
