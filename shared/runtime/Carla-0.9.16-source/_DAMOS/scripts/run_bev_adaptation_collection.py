#!/usr/bin/env python3
"""Predeclared scene splits; preserve integration failures, never select best runs."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

import carla
from run_bev_driving_sweep import ROOT, REFERENCE_POINTS

PLAN = [
    ("train","S1",4101,25,"ClearNoon"),("train","S8",4102,25,"ClearNoon"),
    ("train","S1",4103,15,"CloudyNoon"),("train","S8",4104,15,"CloudyNoon"),
    ("train","S4",4105,25,"ClearNoon"),("train","S5",4106,25,"CloudyNoon"),
    ("val","S1",5101,20,"ClearNoon"),("val","S8",5102,20,"ClearNoon"),
    ("test","S1",6101,30,"ClearNoon"),("test","S8",6102,30,"ClearNoon"),
]


def load_plan(path=None):
    plan = json.loads(Path(path).read_text()) if path else PLAN
    identities = set()
    for row in plan:
        if (not isinstance(row,(list,tuple)) or len(row)!=5 or row[0] not in ("train","val","test")
                or row[1] not in REFERENCE_POINTS or type(row[2]) is not int or row[2]<0
                or not isinstance(row[3],(int,float)) or not 0<=row[3]<=80
                or row[4] not in ("ClearNoon","CloudyNoon","WetNoon","HardRainNoon")):
            raise ValueError("Invalid declared collection plan.")
        identity = (row[1],row[2])
        if identity in identities:
            raise ValueError("Repeated scene seed in collection plan.")
        identities.add(identity)
    if {row[0] for row in plan}!={"train","val","test"}:
        raise ValueError("Plan requires train/val/test scenes.")
    return plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--port",type=int,default=2663)
    parser.add_argument("--rounds",type=int,default=8)
    parser.add_argument("--plan-file",type=Path)
    parser.add_argument("--limit-scenes",type=int)
    parser.add_argument("--resume",action="store_true")
    args = parser.parse_args()
    declared = load_plan(args.plan_file)
    limit = args.limit_scenes or len(declared)
    if not 1<=args.rounds<=32 or not 1<=limit<=len(declared):
        parser.error("Invalid scene/round count")
    args.output.mkdir(parents=True,exist_ok=True)
    plan = {"plan":[list(item) for item in declared],"rounds":args.rounds,
            "scope":"Scene-level seed split on Town10HD; fixed S8 locations overlap. Not independent-map validation."}
    plan_path = args.output/"plan.json"
    if plan_path.exists():
        if not args.resume or json.loads(plan_path.read_text())!=plan:
            raise ValueError("Existing dataset requires --resume with the identical plan.")
    else:
        plan_path.write_text(json.dumps(plan,indent=2))
    status_path = args.output/"collection_status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {"rows":[]}
    road_map = carla.Map("Town10HD_Opt",(ROOT/"Scenic/Maps/Town10HD_Opt.xodr").read_text())
    for split,scenario,seed,distance,weather in declared[:limit]:
        if any(row["seed"]==seed and row.get("capture_complete") for row in status["rows"]):
            continue
        if shutil.disk_usage(ROOT).free<4*1024**3:
            raise RuntimeError("Less than 4GiB free; refusing more captures.")
        x,y = REFERENCE_POINTS[scenario]
        waypoint = road_map.get_waypoint(carla.Location(x=x,y=-y),True,carla.LaneType.Driving)
        choices = waypoint.previous(distance) or [waypoint]
        start = next((p for p in choices if not p.is_junction),choices[0]).transform
        location = [float(start.location.x),float(-start.location.y),float(-start.rotation.yaw-90)]
        command = [str(ROOT/"_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh"),
            "--restart","--offscreen","--port",str(args.port),"--selected-scenario",scenario,
            "--runs","1","--scenic-time","30","--realtime-factor","1.0","--weather",weather,
            "--ego-start",*[str(v) for v in location],"--collect-bev-dataset",
            "--bev-dataset-root",str(args.output),"--bev-dataset-split",split,
            "--scene-seed",str(seed),"--bev-rounds",str(args.rounds),"--no-trajectory-report"]
        log_path = args.output/f"{split}_{scenario}_{seed}.log"
        print(f"[COLLECT] {split} {scenario} seed={seed} start={location}",flush=True)
        process = subprocess.Popen(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        folder = None
        with log_path.open("w") as log:
            for line in process.stdout:
                log.write(line); log.flush()
                if line.startswith("[BEV DATA]") and "report=" in line:
                    folder = Path(line.rsplit("report=",1)[1].strip())
                if line.startswith(("[BEV DATA]","RuntimeError","Traceback","ERROR")):
                    print(line.rstrip(),flush=True)
        code = process.wait()
        manifest = json.loads((folder/"manifest.json").read_text()) if folder is not None and (folder/"manifest.json").exists() else {}
        row = {"split":split,"scenario":scenario,"seed":seed,"start":location,"weather":weather,
               "report":str(folder) if folder else None,"wrapper_returncode":code,
               "integration_passed":code==0,"capture_complete":manifest.get("capture_complete",False),
               "samples":len(manifest.get("samples",[])),"command":command,"log":str(log_path)}
        status["rows"].append(row)
        status_path.write_text(json.dumps(status,indent=2))
        print("[COLLECT RESULT] "+json.dumps(row),flush=True)
        if not row["capture_complete"]:
            raise RuntimeError("Capture incomplete; preserved log and partial files. Do not train.")
    print(f"[COLLECT] Status: {status_path}",flush=True)


if __name__=="__main__":
    main()
