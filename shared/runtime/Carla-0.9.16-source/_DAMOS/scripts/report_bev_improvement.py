#!/usr/bin/env python3
"""Render fixed confirmation results, including explicit unassessable scenarios."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from bev_evaluation import points_mask
from bev_fusion_diagnostic import comparison_image,load_npz
from bev_occupancy import render_presence


def first_confirmation_rows(rows):
    selected = []
    for scenario in sorted({row["scenario"] for row in rows}):
        candidates = [row for row in rows if row["scenario"]==scenario]
        selected.append(next((row for row in candidates if any(
            anchor["assessable"] for anchor in row["scores"]["weighted"]["anchors"])),candidates[0]))
    return selected


def render_development_regression(report,output):
    import torch
    from bev_lidar_calibration import geometry_features
    from run_bev_improvement_experiment import build_case,evaluate_cases,wire_pose
    selection = json.loads((report/"selection.json").read_text())
    checkpoint = Path(selection["checkpoint"])
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest()!=selection["checkpoint_sha256"]:
        raise ValueError("Fixed candidate checkpoint changed.")
    state = torch.load(checkpoint,map_location="cpu",weights_only=True)
    config,mode = state.get("occupancy_calibration"),state.get("fusion_mode","weighted")
    del state
    original = json.loads((report/"baseline_development.json").read_text())
    for row in original["rounds"]:
        if row["group"]!="new_validation" or row["scenario"]!="S6":
            continue
        folder = Path(row["source_folder"])
        nodes,baseline,candidate,geometries = [],{},{},{}
        for path in sorted(folder.glob("*_inputs.npz")):
            key,name = str(path),path.stem.removesuffix("_inputs")
            inputs = load_npz(path)
            pose = inputs["ego2globals"][0,-1]
            nodes.append({"name":name,"key":key,"pose":pose if name=="ego" else wire_pose(pose)})
            cache_name = hashlib.sha256(key.encode()).hexdigest()[:32]+".npz"
            baseline[key] = load_npz(report/"cache/original"/cache_name)
            candidate[key] = load_npz(report/"cache"/selection["selected"]["model"]/cache_name)
            geometries[key] = geometry_features(inputs["lidar_bev"])
        case = build_case(folder,nodes,"S6",row["group"],row["scene_id"])
        before = evaluate_cases([case],baseline,geometries)
        after = evaluate_cases([case],candidate,geometries,config,mode)
        if after["aggregate"]["ground_fp_rate"]<=before["aggregate"]["ground_fp_rate"]:
            continue
        from bev_fusion import fuse_bevs
        from bev_lidar_calibration import apply_calibration
        predictions = []
        for source,calibration,fusion_mode in ((baseline,None,"weighted"),(candidate,config,mode)):
            aligned = {}
            for node in nodes:
                prediction = source[node["key"]]
                if calibration is not None:
                    prediction = apply_calibration(prediction,calibration,geometry=geometries[node["key"]])
                aligned[node["name"]] = dict(prediction,pose=node["pose"])
            predictions.append(fuse_bevs(aligned["ego"],[aligned[n] for n in sorted(aligned) if n!="ego"],mode=fusion_mode))
        mask = np.zeros((200,200),bool)
        for anchor in case["anchors"]:
            mask |= anchor["mask"]
        image = comparison_image(dict(zip(("Original fused","Candidate fused"),predictions)),mask,
                                 f"S6 DEVELOPMENT F{row['frame_id']} | first FP regression")
        image[36:78] = 20
        cv2.putText(image,"Orange=obstacle; gray=ground; white=GT only. Same raw frame.",
                    (12,58),cv2.FONT_HERSHEY_SIMPLEX,.52,(240,240,240),1)
        footer = np.full((64,image.shape[1],3),20,np.uint8)
        fp_before,fp_after = (100*r["aggregate"]["ground_fp_rate"] for r in (before,after))
        cv2.putText(footer,f"Same frame ground FP: {fp_before:.1f}% -> {fp_after:.1f}% (worse)",
                    (12,25),cv2.FONT_HERSHEY_SIMPLEX,.65,(80,200,255),1)
        cv2.putText(footer,"Known development failure. Fresh S6 test improvement does not erase this failure.",
                    (12,51),cv2.FONT_HERSHEY_SIMPLEX,.52,(240,240,240),1)
        cv2.imwrite(str(output/"S6_development_regression.png"),np.vstack([image,footer]))
        (output/"development_regression.json").write_text(json.dumps({"frame_id":row["frame_id"],
            "source_folder":str(folder),"original":before["aggregate"],"candidate":after["aggregate"],
            "selection":"First higher-FP S6 development frame; no new inference or candidate selection",
            "checkpoint_sha256":selection["checkpoint_sha256"]},indent=2))
        return


def render_report(report,output):
    output.mkdir(parents=True,exist_ok=False)
    original_path,selected_path = report/"fresh_original/summary.json",report/"fresh_selected/summary.json"
    paths = [report/"summary.json",report/"selection.json",original_path,selected_path]
    hashes = {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    originals = json.loads(original_path.read_text())["rounds"]
    candidates = json.loads(selected_path.read_text())["rounds"]
    lookup = {(r["scenario"],r["scene_id"],r["frame_id"]):r for r in candidates}
    if len(lookup)!=len(candidates):
        raise ValueError("Repeated confirmation frame identity.")
    summary = json.loads((report/"summary.json").read_text())
    tiles,rows = [],[]
    for old in first_confirmation_rows(originals):
        new = lookup[(old["scenario"],old["scene_id"],old["frame_id"])]
        if old["source_folder"]!=new["source_folder"]:
            raise ValueError("Cannot compare different source frames.")
        prediction_paths = [Path(old["prediction_folder"])/"ego_fused.npz",
                            Path(new["prediction_folder"])/"ego_fused.npz"]
        for path in prediction_paths:
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        before,after = (load_npz(path) for path in prediction_paths)
        if not np.array_equal(before["pose"],after["pose"]):
            raise ValueError("Confirmation ego pose mismatch.")
        anchor = next((a for a in old["scores"]["weighted"]["anchors"] if a["assessable"]),None)
        if anchor is not None:
            mask = np.zeros((200,200),bool)
            for path in sorted(Path(old["source_folder"]).glob("*_eval_only_semantic.npz")):
                hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
                data = load_npz(path)
                mask |= points_mask(data["world_xyz"][np.isin(data["actor_ids"],anchor["member_actor_ids"])],before["pose"])
            image = comparison_image({"Original fused":before,"Candidate fused":after},mask,
                f"{old['scenario']} F{old['frame_id']} | first assessable frame; raw output")
            panel = image[78:]
        else:
            panels = []
            for name,prediction in (("Original fused",before),("Candidate fused",after)):
                header = np.full((64,380,3),20,np.uint8)
                cv2.putText(header,name,(10,24),cv2.FONT_HERSHEY_SIMPLEX,.62,(240,240,240),1)
                cv2.putText(header,"Target NOT ASSESSABLE",(10,50),cv2.FONT_HERSHEY_SIMPLEX,.57,(80,200,255),1)
                panels.append(np.vstack([header,render_presence(prediction,380)]))
            panel = np.hstack(panels)
        header = np.full((92,760,3),20,np.uint8)
        scenario = old["scenario"]
        baseline = summary["per_scenario_test"]["original"][scenario]
        candidate = summary["per_scenario_test"]["selected"][scenario]
        denominator = baseline["regions_assessable"]
        lines = [f"{scenario} F{old['frame_id']} | Original / Candidate",
                 (f"ALL frames: region hits {baseline['regions_detected']}/{denominator} -> "
                  f"{candidate['regions_detected']}/{denominator}") if denominator else
                 "NO assessable abnormal target; NOT a detection pass",
                 f"Ground FP: {100*baseline['ground_fp_rate']:.1f}% -> {100*candidate['ground_fp_rate']:.1f}%"]
        for index,line in enumerate(lines):
            cv2.putText(header,line,(10,25+28*index),cv2.FONT_HERSHEY_SIMPLEX,.59,(240,240,240),1)
        tile = np.vstack([header,panel])
        cv2.imwrite(str(output/f"{scenario}_confirmation.png"),tile)
        tiles.append(tile)
        rows.append({"scenario":scenario,"scene_id":old["scene_id"],"frame_id":old["frame_id"],
                     "assessable_example":anchor is not None,"source_folder":old["source_folder"]})
    if len(tiles)==9:
        mosaic = np.vstack([np.hstack(tiles[i:i+3]) for i in (0,3,6)])
        legend = np.full((64,mosaic.shape[1],3),20,np.uint8)
        cv2.putText(legend,"Orange=observed obstacle; gray=ground; dark=unobserved, NOT free; white=target GT outline only.",
                    (12,25),cv2.FONT_HERSHEY_SIMPLEX,.7,(240,240,240),1)
        adoption = "Candidate not adopted: development gate failed." if not summary["development_gate_pass"] else "Fixed candidate; inspect all gates before adoption."
        cv2.putText(legend,"Same actor/frame per pair. No interpolation. First assessable frame, NOT best frame. "+adoption,
                    (12,51),cv2.FONT_HERSHEY_SIMPLEX,.66,(240,240,240),1)
        cv2.imwrite(str(output/"S1_S9_confirmation.png"),np.vstack([mosaic,legend]))
    unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest for path,digest in hashes.items())
    (output/"render_audit.json").write_text(json.dumps({"source_files_unchanged":unchanged,
        "selection":"First assessable frame; otherwise first frame with explicit unassessable label",
        "rows":rows,"source_sha256":hashes,"inference_or_reselection":False},indent=2))
    if not unchanged:
        raise RuntimeError("Confirmation source changed during rendering.")
    render_development_regression(report,output)


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    render_report(args.report,args.output)
