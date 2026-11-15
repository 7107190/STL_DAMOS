"""Engineering-only acquisition checks; explicitly excluded from the 100 cases."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from bev_accuracy_batch import write_json
from run_metric3_100 import owned_server, planned_start, plan_cases


def main():
    import carla
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=2680)
    parser.add_argument("--scenarios", nargs="+", choices=[f"S{i}" for i in range(1, 10)],
                        default=["S1", "S2", "S6", "S9"])
    args = parser.parse_args()
    root, output = args.root, args.output
    output.mkdir(parents=True, exist_ok=False)
    road_map = carla.Map("Town10HD_Opt", (root / "Scenic/Maps/Town10HD_Opt.xodr").read_text())
    rows = []
    with owned_server(root, output, args.port) as client:
        for scenario in args.scenarios:
            index = int(scenario[1:]) - 1
            case = plan_cases()[index]
            case.update(case_id=f"ENGINEERING-{case['scenario']}", scene_seed=2026109902 + index)
            if case["scenario"] == "S1":
                case["target_scenic_xy"] = [39.3, -148.5]
            case["ego_start_scenic"] = planned_start(road_map, case)
            path = output / (case["case_id"] + ".json")
            write_json(path, case)
            target = output / case["case_id"]
            target.mkdir()
            client.load_world("Town10HD_Opt")
            env = dict(os.environ,
                DAMOS_BEV_CHECKPOINT=str(root / "_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt"),
                DAMOS_BEV_ACCURACY_CASE=str(path))
            command = [str(root / "_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh"),
                "--offscreen", "--port", str(args.port), "--selected-scenario", case["scenario"],
                "--runs", "1", "--scenic-time", "30", "--realtime-factor", "1.0",
                "--ego-start", *map(str, case["ego_start_scenic"]), "--weather", case["weather"],
                "--scene-seed", str(case["scene_seed"]), "--live-bev-fusion", "--evaluate-bev",
                "--bev-scene-cameras", "--bev-video-seconds", "30", "--report-dir", str(target),
                "--no-trajectory-report"]
            print(f"[ENGINEERING] {case['case_id']}; NOT an official accuracy case", flush=True)
            with (target / "runtime.log").open("w") as log:
                process = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True)
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    if line.startswith(("[ACCURACY", "Traceback", "RuntimeError", "ValueError", "TimeoutError")):
                        print(line.rstrip(), flush=True)
                code = process.wait()
            rows.append({"case": case, "returncode": code, "excluded_from_official_100": True})
            write_json(output / "engineering_checks.json", rows)
            if code:
                raise RuntimeError("Engineering execution failed; retained original evidence: " + str(target))


if __name__ == "__main__":
    main()
