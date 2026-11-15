#!/usr/bin/env python3
"""Bounded S1-S9 driving diagnostic; CARLA/Scenic remains the only tick owner."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess
import carla

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_POINTS = {"S1":(-81,-121.7),"S2":(0,0),"S3":(-14.5,-127.5),"S4":(0,0),
                    "S5":(-59,10),"S6":(-98,-121.7),"S7":(57,-57),"S8":(-95,-90),"S9":(-3.8,75.4)}


def road_start(road_map, scenario):
    x,y = REFERENCE_POINTS[scenario]
    waypoint = road_map.get_waypoint(carla.Location(x=x,y=-y),True,carla.LaneType.Driving)
    choices = [p for distance in (25,20,35,45,15,10,5,55) for p in waypoint.previous(distance)]
    choices = choices or [waypoint]
    start = next((p for p in choices if not p.is_junction),choices[0])
    tr = start.transform
    return [float(tr.location.x),float(-tr.location.y),float(-tr.rotation.yaw-90)]


def aggregate(report):
    summary = json.loads((report/"summary.json").read_text())
    regions,ego,fused,hidden,new = set(),set(),set(),set(),set()
    evaluations = []
    for folder in sorted(report.glob("round_*")):
        path = folder/"evaluation.json"
        if not path.exists():
            continue
        evaluation = json.loads(path.read_text()); evaluations.append(evaluation)
        for anchor in evaluation["anchors"]:
            key = (anchor["scenario"],anchor["anchor_index"])
            if anchor["assessable"]:
                regions.add(key)
            if anchor["ego"]["detected"] is True:
                ego.add(key)
            if anchor["fused"]["detected"] is True:
                fused.add(key)
            if anchor["hidden_part_recovered"]:
                hidden.add(key)
            if anchor.get("new_situation_presence"):
                new.add(key)
    return {"report":str(report),"rounds":len(evaluations),
        "anchors_total":max((e["anchor_total"] for e in evaluations),default=0),
        "anchors_assessable":len(regions),"anchors_ego_detected":len(ego),
        "anchors_fused_detected":len(fused),"anchors_hidden_parts_recovered":len(hidden),
        "anchors_new_detections":len(new),
        "region_frames_assessable":sum(e["anchor_assessable"] for e in evaluations),
        "region_frames_ego_detected":sum(e["anchor_ego_detected"] for e in evaluations),
        "region_frames_fused_detected":sum(e["anchor_fused_detected"] for e in evaluations),
        "mean_ms":summary["mean_ms"],"max_ms":summary["max_ms"],
        "ego_driving":summary["ego_driving"],"fatal_error":summary["fatal_error"],
        "captures_on_driving_lane":all(e["ego_road_state"]["on_driving_lane"] for e in evaluations) if evaluations else None,
        "scope":"Union over observed frames of this single run, not independent repeated trials or all-map coverage"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios",nargs="+",choices=list(REFERENCE_POINTS),default=list(REFERENCE_POINTS))
    parser.add_argument("--port",type=int,default=2663)
    parser.add_argument("--rounds",type=int,default=5)
    parser.add_argument("--scene-cameras",action="store_true")
    parser.add_argument("--report-root",type=Path)
    parser.add_argument("--seed-base",type=int)
    args = parser.parse_args()
    if args.rounds<1:
        parser.error("rounds must be positive")
    reports = args.report_root.resolve() if args.report_root else ROOT/"_DAMOS/reports"
    reports.mkdir(parents=True,exist_ok=True)
    output = reports/("bev_driving_sweep_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    output.mkdir()
    road_map = carla.Map("Town10HD_Opt",(ROOT/"Scenic/Maps/Town10HD_Opt.xodr").read_text())
    rows = []
    for scenario in args.scenarios:
        if shutil.disk_usage(ROOT).free<3*1024**3:
            raise RuntimeError("Less than 3GiB free; refusing another capture run.")
        if shutil.disk_usage(reports).free<3*1024**3:
            raise RuntimeError("Less than 3GiB free in the requested report filesystem.")
        start = road_start(road_map,scenario)
        command = [str(ROOT/"_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh"),
            "--restart","--offscreen","--port",str(args.port),"--selected-scenario",scenario,
            "--runs","1","--scenic-time","30","--realtime-factor","1.0",
            "--ego-start",*[str(v) for v in start],"--weather","ClearNoon",
            "--live-bev-fusion","--bev-rounds",str(args.rounds),"--bev-radius-m","60",
            "--evaluate-bev","--no-trajectory-report"]
        if args.scene_cameras:
            command.append("--bev-scene-cameras")
        if args.report_root:
            command.extend(["--report-dir",str(reports)])
        if args.seed_base is not None:
            command.extend(["--scene-seed",str(args.seed_base+int(scenario[1:]))])
        print(f"[SWEEP] Starting {scenario}; driving-lane spawn={start}",flush=True)
        process = subprocess.Popen(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        report = None
        with (output/f"{scenario}.log").open("w") as log:
            for line in process.stdout:
                log.write(line); log.flush()
                if line.startswith("[BEV] Enrolling") and "report=" in line:
                    report = Path(line.rsplit("report=",1)[1].strip())
                if line.startswith(("[BEV]","[BEV GT]","Traceback","RuntimeError","ERROR")):
                    print(f"[{scenario}] {line.rstrip()}",flush=True)
        code = process.wait()
        row = {"scenario":scenario,"returncode":code,"start_scenic":start,
               "scene_seed":args.seed_base+int(scenario[1:]) if args.seed_base is not None else None,
               "overhead_scene_camera_enabled":args.scene_cameras,
               "vehicle_control":"Unmodified Scenic AutopilotBehavior; no teleport, GT routing or manual tick"}
        if report is not None and (report/"summary.json").exists():
            row.update(aggregate(report))
        rows.append(row)
        (output/"summary.json").write_text(json.dumps({"rows":rows,"metric2_pass":None,
            "criterion":"Raw situation-region obstacle presence, not scenario/action classification",
            "test_conditions":"ClearNoon; single-host GPU; Scenic autopilot from a Driving lane; nearby observers only"},indent=2))
        print("[SWEEP RESULT] "+json.dumps(row),flush=True)
    print(f"[SWEEP] Report: {output}",flush=True)


if __name__=="__main__":
    main()
