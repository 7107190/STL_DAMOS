#!/usr/bin/env python3
"""Predeclared model/calibration/fusion search, then one sealed S1-S9 confirmation."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np

from audit_bev_adaptation import previous_hidden_detections_retained
from bev_adaptation_data import dataset_scenes
from bev_anchor_evaluation import region_presence
from bev_evaluation import observed_presence_ground_truth,points_mask
from bev_fusion import fuse_bevs
from bev_fusion_diagnostic import comparison_image,load_npz
from bev_lidar_calibration import apply_calibration,fit_calibration,geometry_features
from bev_model_runtime import BEVPredictor,CHECKPOINT
from bev_occupancy import score_scene_presence
from train_bev_adaptation import aggregate,selection_ok


def wire_pose(pose):
    return (np.rint(np.asarray(pose).reshape(-1)*1e6).astype(np.int64).reshape(4,4)/1e6).astype(np.float32)


def build_case(folder,nodes,scenario,group,scene_id,evaluation=None):
    truth = json.loads((folder/"ground_truth.json").read_text())
    evidence = {}
    for path in folder.glob("*_eval_only_semantic.npz"):
        data = load_npz(path)
        evidence[path.stem] = (data["world_xyz"],data["actor_ids"],data["tags"])
    own = next(node for node in nodes if node["name"]=="ego")
    pose = own["pose"]
    gt,gt_valid = observed_presence_ground_truth(evidence,pose)
    groups = {}
    for target in truth["targets"]:
        groups.setdefault((target["scenario"],target["anchor_index"]),[]).append(target["actor_id"])
    anchors,hidden = [],[]
    for (label,index),members in sorted(groups.items()):
        mask = np.zeros((200,200),bool)
        hidden_mask = np.zeros_like(mask)
        hidden_ids = [obj["actor_id"] for obj in evaluation["objects"]
                      if obj["occlusion_verified"] and obj["actor_id"] in members] if evaluation else []
        for xyz,ids,_ in evidence.values():
            mask |= points_mask(xyz[np.isin(ids,members)],pose)
            hidden_mask |= points_mask(xyz[np.isin(ids,hidden_ids)],pose)
        anchors.append({"scenario":label,"anchor_index":index,"member_actor_ids":members,"mask":mask})
        hidden.append({"scenario":label,"anchor_index":index,"mask":hidden_mask})
    return {"folder":str(folder),"nodes":nodes,"scenario":scenario,"group":group,"scene_id":scene_id,
            "frame_id":truth["frame_id"],"gt":gt,"gt_valid":gt_valid,"anchors":anchors,"hidden":hidden}


def manifest_cases(scenes,group):
    cases = []
    for scene in scenes:
        for row in scene["rounds"]:
            nodes = []
            for sample in row["nodes"]:
                arrays = load_npz(sample["inputs"])
                if int(arrays["frame_ids"][-1])!=row["frame_id"]:
                    raise ValueError("Dataset frame mismatch.")
                pose = arrays["ego2globals"][0,-1]
                nodes.append({"name":sample["node"],"key":sample["inputs"],
                    "pose":pose.copy() if sample["node"]=="ego" else wire_pose(pose)})
            cases.append(build_case(Path(row["folder"]),nodes,scene["scenario"],group,scene["scene_id"]))
    return cases


def legacy_cases(summary):
    cases = []
    for run in json.loads(Path(summary).read_text())["rows"]:
        for folder in sorted(Path(run["report"]).glob("round_*")):
            if not (folder/"evaluation.json").exists():
                continue
            paths = [folder/"ego_own.npz",*sorted(folder.glob("ego_received_*.npz"))]
            nodes = []
            for path in paths:
                name = "ego" if path.name=="ego_own.npz" else path.stem.removeprefix("ego_received_")
                nodes.append({"name":name,"key":str(folder/f"{name}_model_inputs.npz"),
                              "pose":load_npz(path)["pose"],"baseline_prediction":str(path)})
            evaluation = json.loads((folder/"evaluation.json").read_text())
            cases.append(build_case(folder,nodes,run["scenario"],"known_driving_regression",run["scenario"],evaluation))
    return cases


def scores_for(prediction,case,hidden=False):
    items = case["hidden"] if hidden else case["anchors"]
    scores = [dict(region_presence(prediction,item["mask"]),**{k:v for k,v in item.items() if k!="mask"}) for item in items]
    return scores if hidden else {"anchors":scores,"scene":score_scene_presence(prediction,case["gt"],case["gt_valid"])}


def evaluate_cases(cases,predictions,geometries,calibration=None,mode="weighted",output=None):
    rows = []
    for index,case in enumerate(cases):
        nodes = {}
        for node in case["nodes"]:
            result = predictions[node["key"]]
            if calibration is not None:
                result = apply_calibration(result,calibration,geometry=geometries[node["key"]])
            nodes[node["name"]] = dict(result,pose=node["pose"].copy())
        own = nodes["ego"]
        fused = fuse_bevs(own,[nodes[name] for name in sorted(nodes) if name!="ego"],mode=mode)
        row = {"scenario":case["scenario"],"scene_id":case["scene_id"],"group":case["group"],
               "frame_id":case["frame_id"],"source_folder":case["folder"],
               "scores":{"ego":scores_for(own,case),"weighted":scores_for(fused,case)},
               "hidden_scores":{"ego":scores_for(own,case,True),"weighted":scores_for(fused,case,True)}}
        if output is not None:
            folder = Path(output)/f"case_{index:03d}"; folder.mkdir(parents=True,exist_ok=False)
            for name,result in nodes.items():
                np.savez_compressed(folder/f"{name}_own.npz",**result)
            np.savez_compressed(folder/"ego_fused.npz",**fused)
            row["prediction_folder"] = str(folder)
        rows.append(row)
    result = {"aggregate":aggregate(rows),
              "per_scenario":{s:aggregate([r for r in rows if r["scenario"]==s]) for s in sorted({r['scenario'] for r in rows})},
              "per_group_scenario":{f"{g}/{s}":aggregate([r for r in rows if r["group"]==g and r["scenario"]==s])
                  for g,s in sorted({(r['group'],r['scenario']) for r in rows})},"rounds":rows}
    if output is not None:
        (Path(output)/"summary.json").write_text(json.dumps(result,indent=2))
    return result


def candidate_gate(candidate,baseline):
    reasons = []
    if not selection_ok(candidate,baseline):
        reasons.append("aggregate/per-scenario nonregression gate")
    for key,old in baseline["per_group_scenario"].items():
        new = candidate["per_group_scenario"][key]
        if (new["regions_detected"]<old["regions_detected"] or new["ground_fp_rate"]>old["ground_fp_rate"]+1e-12
                or new["unknown_fraction"]>old["unknown_fraction"]+1e-12):
            reasons.append(key)
    if len(candidate["rounds"])!=len(baseline["rounds"]):
        raise ValueError("Validation case mismatch.")
    for old,new in zip(baseline["rounds"],candidate["rounds"]):
        if (old["group"],old["scene_id"],old["frame_id"])!=(new["group"],new["scene_id"],new["frame_id"]):
            raise ValueError("Validation identity mismatch.")
        if not previous_hidden_detections_retained(old["hidden_scores"],new["hidden_scores"]):
            reasons.append(f"lost_verified_hidden_part/{old['scenario']}/F{old['frame_id']}")
    return not reasons,reasons


def cache_predictions(checkpoint,keys,output,baseline_refs=None):
    output = Path(output); output.mkdir(parents=True,exist_ok=True)
    digest = hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest()
    manifest_path = output/"manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text())["checkpoint_sha256"]!=digest:
        raise ValueError("Inference cache checkpoint changed.")
    predictor = None
    predictions = {}
    for index,key in enumerate(sorted(keys)):
        path = output/(hashlib.sha256(key.encode()).hexdigest()[:32]+".npz")
        if path.exists():
            predictions[key] = load_npz(path)
        elif baseline_refs and key in baseline_refs:
            predictions[key] = load_npz(baseline_refs[key])
            np.savez_compressed(path,**predictions[key])
        else:
            if predictor is None:
                predictor = BEVPredictor(checkpoint)
            predictions[key],_ = predictor.infer(load_npz(key))
            np.savez_compressed(path,**predictions[key])
        if (index+1)%64==0:
            print(f"[IMPROVE CACHE] {output.name} {index+1}/{len(keys)}",flush=True)
    (output/"manifest.json").write_text(json.dumps({"checkpoint_sha256":digest,"inputs":sorted(keys)},indent=2))
    if predictor is not None:
        del predictor
        import torch
        torch.cuda.empty_cache()
    return predictions


def render_confirmation(output,baseline,candidate,cases):
    tiles = []
    seen = set()
    for old,new,case in zip(baseline["rounds"],candidate["rounds"],cases):
        scenario = old["scenario"]
        if scenario in seen:
            continue
        anchor = next((a for a in case["anchors"] if int(a["mask"].sum())>=3),None)
        if anchor is None:
            continue
        before = load_npz(Path(old["prediction_folder"])/"ego_fused.npz")
        after = load_npz(Path(new["prediction_folder"])/"ego_fused.npz")
        own = load_npz(Path(new["prediction_folder"])/"ego_own.npz")
        image = comparison_image({"original fused":before,"selected ego":own,"selected fused":after},anchor["mask"],
            f"{scenario} F{old['frame_id']} | first assessable fresh confirmation frame")
        cv2.imwrite(str(Path(output)/f"{scenario}_confirmation.png"),image)
        seen.add(scenario)
        # Condensed before/after, no interpolation and no best-frame selection.
        tile = cv2.resize(np.hstack([image[78:,0:380],image[78:,760:1140]]),(760,444),interpolation=cv2.INTER_NEAREST)
        header = np.full((38,760,3),20,np.uint8)
        cv2.putText(header,f"{scenario} F{old['frame_id']} | original / selected fused",(10,25),cv2.FONT_HERSHEY_SIMPLEX,.65,(240,240,240),1)
        tiles.append(np.vstack([header,tile]))
    if len(tiles)==9:
        cv2.imwrite(str(Path(output)/"S1_S9_confirmation.png"),np.vstack([np.hstack(tiles[i:i+3]) for i in (0,3,6)]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-dataset",type=Path,required=True)
    parser.add_argument("--new-dataset",type=Path,required=True)
    parser.add_argument("--training-report",type=Path,required=True)
    parser.add_argument("--previous-candidate",type=Path,required=True)
    parser.add_argument("--driving-summary",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--resume",action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=args.resume)
    if (args.output/"selection.json").exists() or (args.output/"summary.json").exists():
        raise RuntimeError("Candidate selection/test already started; refusing repeat selection.")
    plan = {"families":["foreground/teacher preservation head","foreground head+LiDAR encoder",
                        "original/adapted 50% weight blend","ordinary-LiDAR occupancy calibration","three fusion rules"],
        "calibration_grid":{"foreground_weight":[1,4,12],"C":[1],"strength":[.5,1.]},
        "fusion_search":"weighted all; ego_observed/binary_weighted on top 3 weighted variants and original",
        "ranking":"All nonregression gates first, then region-frame detections, then obstacle cell F1",
        "test":"New seeds 9101-9109 only, one fixed candidate after selection. No reselection using confirmation results.",
        "known_data":"Prior validation, prior test (now disclosed development), and old driving regression are selection data, never training data",
        "production_default_changed":False,"metric2_pass":None}
    plan_path = args.output/"plan.json"
    if plan_path.exists() and json.loads(plan_path.read_text())!=plan:
        raise ValueError("Existing experiment plan changed.")
    plan_path.write_text(json.dumps(plan,indent=2))
    deadline = time.monotonic()+2400
    while True:
        status_path = args.new_dataset/"collection_status.json"
        training_path = args.training_report/"summary.json"
        status = json.loads(status_path.read_text()) if status_path.exists() else {"rows":[]}
        training = json.loads(training_path.read_text()) if training_path.exists() else {}
        known = [row for row in status["rows"] if row["split"]!="test"]
        if len(known)==15 and all(row["capture_complete"] for row in known) and "source_files_unchanged" in training:
            break
        if time.monotonic()>deadline:
            raise TimeoutError("Declared collection/training did not complete; no partial model selection.")
        time.sleep(10)
    if not training["source_files_unchanged"]:
        raise ValueError("Candidate training changed source data.")
    old_scenes = dataset_scenes(args.old_dataset)
    new_scenes = [json.loads(path.read_text()) for split in ("train","val")
                  for path in sorted((args.new_dataset/split).glob("*/manifest.json"))]
    if len(new_scenes)!=15 or any(not scene["capture_complete"] for scene in new_scenes):
        raise ValueError("Declared training/validation scenes are incomplete.")
    identities = [(s["scenario"],s["seed"]) for s in old_scenes+new_scenes]
    if len(identities)!=len(set(identities)):
        raise ValueError("Scene identity leakage between experiments.")
    contents = {}
    for scene in old_scenes+new_scenes:
        role = scene["split"]
        for sample in scene["samples"]:
            digest = sample["content_sha256"]
            if digest in contents and contents[digest]!=role:
                raise ValueError("Cross-role model input leakage.")
            contents[digest] = role
    train = [s for s in old_scenes+new_scenes if s["split"]=="train"]
    training_samples = [sample for scene in train for sample in scene["samples"]]
    cases = manifest_cases([s for s in old_scenes if s["split"]=="val"],"prior_validation")
    cases += manifest_cases([s for s in old_scenes if s["split"]=="test"],"prior_test_now_development")
    cases += manifest_cases([s for s in new_scenes if s["split"]=="val"],"new_validation")
    cases += legacy_cases(args.driving_summary)
    all_keys = {sample["inputs"] for sample in training_samples}|{node["key"] for case in cases for node in case["nodes"]}
    hashes = {str(CHECKPOINT):hashlib.sha256(CHECKPOINT.read_bytes()).hexdigest()}
    for scene in old_scenes+new_scenes:
        for sample in scene["samples"]:
            for name in ("inputs","supervision"):
                path = Path(sample[name]); digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest!=sample[name+"_sha256"]:
                    raise ValueError("Collected input/supervision changed.")
                hashes[str(path)] = digest
    for case in cases:
        for path in Path(case["folder"]).iterdir():
            if path.suffix in (".json",".npz"):
                hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    geometries = {}
    for index,key in enumerate(sorted(all_keys)):
        geometries[key] = geometry_features(load_npz(key)["lidar_bev"])
        if (index+1)%64==0:
            print(f"[IMPROVE GEOMETRY] {index+1}/{len(all_keys)}",flush=True)
    refs = {node["key"]:node["baseline_prediction"] for case in cases for node in case["nodes"] if "baseline_prediction" in node}
    models = [("original",CHECKPOINT),("previous_head",args.previous_candidate)]
    for arm in training["arms"]:
        for path in arm["checkpoints"]:
            models.append((Path(path).stem,Path(path)))
    import torch
    parent = torch.load(CHECKPOINT,map_location="cpu",weights_only=True)
    parent_weights = {k.removeprefix("module."):v for k,v in parent["model"].items()}
    previous = torch.load(args.previous_candidate,map_location="cpu",weights_only=True)
    blend = {k:(v if not v.is_floating_point() or torch.equal(v,previous["model"][k])
                else .5*v+.5*previous["model"][k]) for k,v in parent_weights.items()}
    blend_path = args.output/"weight_blend_50.pt"
    torch.save({"model":blend,"step":int(parent["step"]),"blend_parent_fraction":.5},blend_path)
    models.append(("weight_blend_50",blend_path))
    del parent,parent_weights,previous,blend
    baseline = None
    variants,configs,model_paths = [],{},dict(models)

    def consider(model_name,predictions,calibration,mode,label):
        nonlocal baseline
        result = evaluate_cases(cases,predictions,geometries,calibration,mode)
        if baseline is None:
            if model_name!="original" or calibration is not None or mode!="weighted":
                raise RuntimeError("Original baseline must be first.")
            baseline = result
            (args.output/"baseline_development.json").write_text(json.dumps(result,indent=2))
        passed,reasons = candidate_gate(result,baseline)
        name = f"{model_name}/{label}/{mode}"
        record = {"name":name,"model":model_name,"calibration":label,"fusion_mode":mode,
                  "eligible":passed,"rejection_reasons":reasons,"aggregate":result["aggregate"],
                  "per_scenario":result["per_scenario"],"per_group_scenario":result["per_group_scenario"]}
        variants.append(record); configs[name] = calibration
        (args.output/"variants.json").write_text(json.dumps(variants,indent=2))
        print("[IMPROVE VARIANT] "+json.dumps({k:record[k] for k in ("name","eligible","aggregate","rejection_reasons")}),flush=True)
        return result

    for model_name,path in models:
        predictions = cache_predictions(path,all_keys,args.output/"cache"/model_name,refs if model_name=="original" else None)
        consider(model_name,predictions,None,"weighted","none")
        records = []
        for sample in training_samples:
            gt = load_npz(sample["supervision"])
            records.append({"prediction":predictions[sample["inputs"]],"geometry":geometries[sample["inputs"]],
                            "labels":gt["labels"],"target_mask":gt["target_mask"]})
        for foreground in (1,4,12):
            calibration = fit_calibration(records,foreground_weight=foreground,C=1)
            for strength in (.5,1.):
                config = dict(calibration,strength=strength)
                consider(model_name,predictions,config,"weighted",f"lidar_fg{foreground}_s{strength}")
        del predictions,records
    rank = lambda row:(row["eligible"],row["aggregate"]["regions_detected"],row["aggregate"]["obstacle_f1"])
    top = sorted(variants,key=rank,reverse=True)[:3]
    if not any(row["model"]=="original" and row["calibration"]=="none" for row in top):
        top.append(next(row for row in variants if row["model"]=="original" and row["calibration"]=="none"))
    for row in top:
        predictions = cache_predictions(model_paths[row["model"]],all_keys,args.output/"cache"/row["model"])
        for mode in ("ego_observed","binary_weighted"):
            consider(row["model"],predictions,configs[row["name"]],mode,row["calibration"])
        del predictions
    eligible = [row for row in variants if row["eligible"]]
    # One diagnostic fallback is fixed before test if every candidate fails development gates.
    selected = max(eligible,key=rank) if eligible else max(variants[1:],key=lambda row:(-len(row["rejection_reasons"]),*rank(row)[1:]))
    state = torch.load(model_paths[selected["model"]],map_location="cpu",weights_only=True)
    selected_path = args.output/"selected_candidate.pt"
    torch.save({"model":{k.removeprefix("module."):v for k,v in state["model"].items()},"step":int(state["step"]),
        "occupancy_calibration":configs[selected["name"]],"fusion_mode":selected["fusion_mode"],
        "selection_data":"development only, including disclosed prior test; not fresh confirmation",
        "selection_variant":selected["name"]},selected_path)
    del state
    selection = {"selected":selected,"eligible_candidates":len(eligible),"variants_tested":len(variants),
        "checkpoint":str(selected_path),"checkpoint_sha256":hashlib.sha256(selected_path.read_bytes()).hexdigest(),
        "fresh_test_started":False,"production_default_changed":False}
    (args.output/"selection.json").write_text(json.dumps(selection,indent=2))
    print("[IMPROVE SELECTED] "+json.dumps(selection),flush=True)
    # Fresh test is only opened after the exact checkpoint/algorithm is fixed on disk.
    while True:
        status = json.loads((args.new_dataset/"collection_status.json").read_text())
        if len(status["rows"])==24 and all(row["capture_complete"] for row in status["rows"]):
            break
        if time.monotonic()>deadline:
            raise TimeoutError("Fresh confirmation collection incomplete; fixed candidate preserved.")
        time.sleep(10)
    completed = dataset_scenes(args.new_dataset)
    test_scenes = [scene for scene in completed if scene["split"]=="test"]
    for scene in test_scenes:
        if (scene["scenario"],scene["seed"]) in identities:
            raise ValueError("Fresh scene identity leakage.")
        for sample in scene["samples"]:
            if sample["content_sha256"] in contents:
                raise ValueError("Fresh input content leakage.")
            for name in ("inputs","supervision"):
                path = Path(sample[name]); digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest!=sample[name+"_sha256"]:
                    raise ValueError("Fresh confirmation file changed.")
                hashes[str(path)] = digest
    test_cases = manifest_cases(test_scenes,"fresh_confirmation")
    test_keys = {node["key"] for case in test_cases for node in case["nodes"]}
    original_predictions = cache_predictions(CHECKPOINT,test_keys,args.output/"fresh_cache_original")
    selected_predictions = cache_predictions(selected_path,test_keys,args.output/"fresh_cache_selected")
    baseline_test = evaluate_cases(test_cases,original_predictions,{},mode="weighted",output=args.output/"fresh_original")
    selected_test = evaluate_cases(test_cases,selected_predictions,{},mode=selected["fusion_mode"],output=args.output/"fresh_selected")
    fresh_pass,fresh_reasons = candidate_gate(selected_test,baseline_test)
    render_confirmation(args.output,baseline_test,selected_test,test_cases)
    unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest for path,digest in hashes.items())
    report = dict(selection,fresh_test_started=True,development_gate_pass=selected["eligible"],
        fresh_gate_pass=fresh_pass,fresh_rejection_reasons=fresh_reasons,
        recommended_for_live_verification=selected["eligible"] and fresh_pass and unchanged,
        original_test=baseline_test["aggregate"],selected_test=selected_test["aggregate"],
        per_scenario_test={"original":baseline_test["per_scenario"],"selected":selected_test["per_scenario"]},
        source_files_unchanged=unchanged,source_sha256=hashes,metric2_pass=None,
        limitations=["Same Town10HD, fixed anchor locations overlap; no unseen-map guarantee",
                     "Continuous frames are correlated; unassessable anchors are not passes",
                     "Fresh semantic-return GT does not prove all occlusions; known verified hidden cases were development gates",
                     "No ZKP or full pipeline latency benchmark in offline experiment"])
    (args.output/"summary.json").write_text(json.dumps(report,indent=2))
    print("[IMPROVE FINAL] "+json.dumps({k:report[k] for k in ("variants_tested","eligible_candidates","development_gate_pass","fresh_gate_pass","recommended_for_live_verification","original_test","selected_test","source_files_unchanged")}),flush=True)
    if not unchanged:
        raise RuntimeError("Experiment source files changed.")


if __name__=="__main__":
    main()
