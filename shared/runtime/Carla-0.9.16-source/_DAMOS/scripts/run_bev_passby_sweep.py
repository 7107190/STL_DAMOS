#!/usr/bin/env python3
"""Explicit, seeded video staging; never teleports a running Scenic ego."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess

import carla
from bev_passby_video import ROOT, video_waypoint

PRESETS = {"S1": (39.3, -148.5, 2), "S2": (-34.467, 14.7802, 2),
           "S3": (14, -144, 1), "S4": (-45.0981, 49.8706, 1),
           "S5": (-59, 10, 1), "S6": (-98, -121.7, 1),
           "S7": (57, -57, 1), "S8": (-95, -90, 1), "S9": (-3.8, 75.4, 1)}


def require_tmpfs_headroom(report_root):
    meminfo = Path("/proc/meminfo")
    if not report_root.resolve().is_relative_to(Path("/dev/shm")) or not meminfo.is_file():
        return
    values = {parts[0].rstrip(":"): int(parts[1]) * 1024
              for line in meminfo.read_text().splitlines() if (parts := line.split())}
    if values["MemAvailable"] < 24 * 1024**3:
        raise RuntimeError("RAM-backed video reports require >=24GiB available memory before each run. "
                           "Archive and verify earlier reports, then remove only the preserved copies "
                           "from /dev/shm; alternatively use a sufficiently large disk-backed report path.")


def spawn_plan(road_map, scenario):
    x, y, anchor = PRESETS[scenario]
    goal = video_waypoint(road_map, x, -y, scenario)
    distances = (35,) if scenario == "S2" else (35, 40, 45, 55, 65)
    candidates = [p for distance in distances for p in goal.previous(distance)
                  if (not p.is_junction or scenario == "S2")
                  and p.transform.location.distance(goal.transform.location) > (25 if scenario == "S2" else 30)]
    if not candidates and scenario == "S6":
        candidates = [p for p in goal.previous(25) if not p.is_junction]
    if not candidates:
        raise ValueError("No safe upstream Driving-lane spawn for the video.")
    start = min(candidates, key=lambda p: (round(abs((p.transform.rotation.yaw - goal.transform.rotation.yaw + 180) % 360 - 180), 1),
                                           abs(p.transform.location.distance(goal.transform.location) - 45)))
    tr = start.transform
    return [float(tr.location.x), float(-tr.location.y), float(-tr.rotation.yaw - 90)], anchor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", nargs="+", choices=list(PRESETS), default=["S1"])
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=24)
    parser.add_argument("--port", type=int, default=2670)
    args = parser.parse_args()
    if not 8 <= args.seconds <= 30:
        parser.error("seconds must be 8..30")
    args.report_root.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Install ffmpeg before video collection.")
    output = args.report_root / ("passby_sweep_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    output.mkdir()
    road_map = carla.Map("Town10HD_Opt", (ROOT / "Scenic/Maps/Town10HD_Opt.xodr").read_text())
    rows = []
    for scenario in args.scenarios:
        require_tmpfs_headroom(args.report_root)
        if shutil.disk_usage(args.report_root).free < 4 * 1024**3 or shutil.disk_usage(ROOT).free < 1024**3:
            raise RuntimeError("Video collection needs >=4GiB report space and >=1GiB server root space.")
        start, anchor = spawn_plan(road_map, scenario)
        command = [str(ROOT / "_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh"),
            "--restart", "--offscreen", "--port", str(args.port), "--selected-scenario", scenario,
            "--runs", "1", "--scenic-time", "30", "--realtime-factor", "1.0",
            "--ego-start", *map(str, start), "--weather", "ClearNoon", "--scene-seed", str(12000 + int(scenario[1:])),
            "--live-bev-fusion", "--evaluate-bev", "--bev-scene-cameras",
            "--bev-video-seconds", str(args.seconds), "--bev-video-anchor", str(anchor),
            "--bev-video-target", str(PRESETS[scenario][0]), str(PRESETS[scenario][1]),
            "--report-dir", str(args.report_root), "--no-trajectory-report"]
        print(f"[VIDEO SWEEP] {scenario} spawn={start} anchor={anchor}", flush=True)
        report = None
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        with (output / f"{scenario}.log").open("w") as log:
            for line in process.stdout:
                log.write(line); log.flush()
                if line.startswith("[BEV VIDEO] Recording"):
                    report = line.rsplit("report=", 1)[1].strip()
                if line.startswith(("[BEV VIDEO]", "Traceback", "RuntimeError", "ValueError", "ERROR")):
                    print(f"[{scenario}] {line.rstrip()}", flush=True)
        code = process.wait()
        row = {"scenario": scenario, "returncode": code, "report": report, "ego_spawn_scenic": start,
               "anchor_index": anchor, "scene_seed": 12000 + int(scenario[1:])}
        if report and (Path(report) / "summary.json").exists():
            video = json.loads((Path(report) / "summary.json").read_text())
            row["video_complete"] = video.get("error") is None and bool(video.get("video_sha256"))
            row["video_frames"] = video.get("frame_count", 0)
        else:
            row["video_complete"] = False
        rows.append(row)
        (output / "summary.json").write_text(json.dumps({"rows": rows, "live_latency_benchmark": False,
            "metric2_pass": None, "source": "Controlled low-speed Driving-lane pass-by sensor replay"}, indent=2))
        print("[VIDEO SWEEP RESULT] " + json.dumps(row), flush=True)
    print(f"[VIDEO SWEEP] Report: {output}", flush=True)
    return 0 if all(r["returncode"] == 0 for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
