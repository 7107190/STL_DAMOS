"""Paired same-actor/end-frame FOV x history-spacing diagnostic, not a benchmark."""
from datetime import datetime, timezone
import json
from pathlib import Path

import cv2
import numpy as np

from bev_fusion_diagnostic import comparison_image, scene_scores, summarize
from bev_fusion import fuse_bevs
from bev_sensor_rig import CAMERAS, FOV, REFERENCE_FOVS, REFERENCE_PREFIX, prepare_raw_task

CONDITIONS = {"current_100ms":("current",2),"reference_100ms":("reference",2),
              "current_50ms":("current",1),"reference_50ms":("reference",1)}


def variant_task(task, profile, stride):
    if profile not in ("current","reference") or stride not in (1,2) or len(task["window"])!=7:
        raise ValueError("Comparison requires seven common 50ms frames and a known condition.")
    indices = list(range(6-3*stride,7,stride))
    label = task["label"]
    prefix = REFERENCE_PREFIX if profile=="reference" else ""
    names = {name:prefix+name for name in CAMERAS}
    names["lidar"] = "lidar"
    return dict(task,window=[task["window"][i] for i in indices],
                packets={(label,name):[task["packets"][label,source][i] for i in indices]
                         for name,source in names.items()},
                sensor_matrices={(label,name):task["sensor_matrices"][label,source] for name,source in names.items()},
                camera_fovs=REFERENCE_FOVS if profile=="reference" else {name:FOV for name in CAMERAS})


def run_input_comparison(rig, report_dir, *, rounds, anchor_assignments, scenario_labels=(), logger=print, on_tick=None):
    from bev_evaluation import SemanticEvaluationRig, decode_semantic, points_mask
    from bev_model_runtime import BEVPredictor
    if not np.isclose(rig.step,.05,atol=1e-6):
        raise ValueError("Paired comparison requires a 0.05s Scenic timestep.")
    output = Path(report_dir)/("bev_input_comparison_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"))
    output.mkdir(parents=True,exist_ok=False)
    logger(f"[BEV INPUT] Paired capture, no ZKP/18s benchmark; report={output}")
    node_labels = {("ego" if label=="ego" else f"observer_{actor.id}"):label for label,actor in rig.actors.items()}
    captures, results = [], []
    evaluator = SemanticEvaluationRig(rig,anchor_assignments,instance_cameras=False)
    try:
        minimum = rig.world.get_snapshot().frame+7
        for index in range(rounds):
            if on_tick is not None:
                on_tick()
            window,tasks = rig.freeze_inputs(minimum_frame=minimum,history_frames=7,timeout_s=60)
            truth,packets = evaluator.freeze(window[-1],list(rig.actors),timeout_s=30)
            minimum = window[-1]+20
            folder = output/f"round_{index+1:03d}"
            folder.mkdir()
            (folder/"ground_truth.json").write_text(json.dumps(truth,indent=2))
            evidence = {node:decode_semantic(packets[label]) for node,label in node_labels.items()}
            for node,(xyz,ids,tags) in evidence.items():
                np.savez_compressed(folder/f"{node}_eval_only_semantic.npz",world_xyz=xyz,actor_ids=ids,tags=tags)
            variants = {}
            for condition,(profile,stride) in CONDITIONS.items():
                variants[condition] = {}
                for node,label in node_labels.items():
                    task = variant_task(tasks[label],profile,stride)
                    arrays,record = prepare_raw_task(task)
                    times = arrays["timestamps"]
                    if not np.allclose(np.diff(times),.05*stride,atol=2e-6):
                        raise ValueError("History spacing differs from condition.")
                    path = folder/f"{condition}_{node}_model_inputs.npz"
                    np.savez_compressed(path,**arrays)
                    variants[condition][node] = str(path)
                    if stride==1:
                        for name in CAMERAS:
                            rgb = np.frombuffer(task["packets"][label,name][-1]["raw"],np.uint8).reshape(450,800,4)[:,:,:3]
                            cv2.imwrite(str(folder/f"{profile}_{node}_{name}.png"),rgb)
            for node in node_labels:
                reference = np.load(variants["current_100ms"][node])
                try:
                    for condition in CONDITIONS:
                        with np.load(variants[condition][node]) as data:
                            if not np.array_equal(data["lidar_bev"],reference["lidar_bev"]) or not np.array_equal(data["ego2globals"][0,-1],reference["ego2globals"][0,-1]):
                                raise ValueError("Paired conditions have different current LiDAR/poses.")
                finally:
                    reference.close()
            captures.append({"folder":str(folder),"truth":truth,"variants":variants})
            logger(f"[BEV INPUT] Captured {index+1}/{rounds} F{window[-1]} | same actors/current LiDAR/end pose; four conditions")
    finally:
        evaluator.close()
    # All inputs are frozen before model loading; no recapture during inference.
    predictor = BEVPredictor()
    first = next(iter(captures[0]["variants"].values()))["ego"]
    with np.load(first) as data:
        predictor.infer({key:data[key] for key in data.files})
    representative = False
    for capture in captures:
        folder,truth = Path(capture["folder"]),capture["truth"]
        evidence = {}
        for node in node_labels:
            with np.load(folder/f"{node}_eval_only_semantic.npz") as data:
                evidence[node] = (data["world_xyz"],data["actor_ids"],data["tags"])
        row = {"frame_id":truth["frame_id"],"folder":str(folder),"conditions":{}}
        display_predictions = {}
        for condition,paths in capture["variants"].items():
            predictions,timings = {},{}
            for node,path in paths.items():
                with np.load(path) as data:
                    prediction,timing = predictor.infer({key:data[key] for key in data.files})
                predictions[node],timings[node] = prediction,timing
                np.savez_compressed(folder/f"{condition}_{node}_prediction.npz",**prediction)
            own = predictions["ego"]
            peers = [predictions[node] for node in sorted(predictions) if node!="ego"]
            fused,candidate = fuse_bevs(own,peers),fuse_bevs(own,peers,mode="ego_observed")
            scores = scene_scores({"ego":own,"weighted":fused,"ego_observed":candidate},evidence,truth)
            node_scores = {node:scene_scores({"ego":prediction},{node:evidence[node]},truth)["ego"] for node,prediction in predictions.items()}
            row["conditions"][condition] = {"scores":scores,"per_node":node_scores,"timings":timings}
            display_predictions[condition] = fused
        if not representative:
            anchor = next((a for a in row["conditions"]["current_100ms"]["scores"]["ego"]["anchors"] if a["assessable"]),None)
            if anchor is not None:
                mask = np.zeros((200,200),bool)
                for xyz,ids,_ in evidence.values():
                    mask |= points_mask(xyz[np.isin(ids,anchor["member_actor_ids"])],own["pose"])
                cv2.imwrite(str(output/"input_comparison.png"),comparison_image(display_predictions,mask,
                    f"First assessable F{truth['frame_id']} anchor {anchor['anchor_index']} | same scene, actor IDs, end frame and LiDAR"))
                representative = True
        results.append(row)
        (folder/"comparison.json").write_text(json.dumps(row,indent=2))
        logger(f"[BEV INPUT] Inferred F{truth['frame_id']}: "+"; ".join(
            f"{name} regions={sum(a['detected'] is True for a in item['scores']['weighted']['anchors'])}"
            for name,item in row["conditions"].items()))
    report = {"experimental":True,"scenario_labels":list(scenario_labels),"checkpoint_sha256":predictor.checkpoint_hash,
              "conditions":{name:{"fov":REFERENCE_FOVS if profile=="reference" else {n:FOV for n in CAMERAS},"history_interval_s":.05*stride}
                            for name,(profile,stride) in CONDITIONS.items()},
              "conditions_held_equal":["same scene and original actor IDs","same end frame and current ordinary LiDAR","same mounts and model weights"],
              "capture_timestep_s":rig.step,"lidar_rotation_hz":1/rig.step,
              "caveats":["Reference FOV/spacing only; mobility mounts differ from original vehicle training rigs.",
                         "All conditions use a shared 20Hz current LiDAR scan, unlike production 10Hz; not an exact production before/after replay.",
                         "GT used only for scoring. No ZKP exchange; sequential inference timing is not the 18s metric.",
                         "Single scene development comparison, not independent accuracy validation."],
              "aggregate":{name:summarize([{"scores":row["conditions"][name]["scores"]} for row in results]) for name in CONDITIONS},
              "rounds":results,"metric2_pass":None}
    (output/"summary.json").write_text(json.dumps(report,indent=2))
    (output/"sensor_manifest.json").write_text(json.dumps(rig.records,indent=2))
    logger(f"[BEV INPUT] Summary: {output/'summary.json'}")
    render_saved_results(output)
    return report


def render_saved_results(output):
    """Re-render every assessable round; never rerun or replace model predictions."""
    from bev_evaluation import points_mask
    from bev_fusion_diagnostic import load_npz
    output = Path(output)
    report = json.loads((output/"summary.json").read_text())
    rows = []
    first_difference = False
    for row in report["rounds"]:
        folder = Path(row["folder"])
        anchor = next((a for a in row["conditions"]["current_100ms"]["scores"]["ego"]["anchors"] if a["assessable"]),None)
        if anchor is None:
            continue
        own,fused = {},{}
        for condition in CONDITIONS:
            paths = sorted(folder.glob(f"{condition}_*_prediction.npz"))
            own[condition] = load_npz(folder/f"{condition}_ego_prediction.npz")
            peers = [load_npz(path) for path in paths if path.name!=f"{condition}_ego_prediction.npz"]
            fused[condition] = fuse_bevs(own[condition],peers)
        mask = np.zeros((200,200),bool)
        for path in folder.glob("*_eval_only_semantic.npz"):
            data = load_npz(path)
            mask |= points_mask(data["world_xyz"][np.isin(data["actor_ids"],anchor["member_actor_ids"])],own["current_100ms"]["pose"])
        title = f"F{row['frame_id']} anchor {anchor['anchor_index']} | same actors/end frame/LiDAR; development comparison"
        cv2.imwrite(str(folder/"ego_input_comparison.png"),comparison_image(own,mask,"EGO ONLY | "+title))
        cv2.imwrite(str(folder/"fused_input_comparison.png"),comparison_image(fused,mask,"WEIGHTED FUSION | "+title))
        current = row["conditions"]["current_100ms"]["scores"]["ego"]["anchors"]
        reference = row["conditions"]["reference_100ms"]["scores"]["ego"]["anchors"]
        if not first_difference and any(a["detected"]!=b["detected"] for a,b in zip(current,reference)):
            cv2.imwrite(str(output/"first_fov_difference.png"),comparison_image(own,mask,"FIRST FOV DETECTION DIFFERENCE | "+title))
            first_difference = True
        rows.append({"frame_id":row["frame_id"],"anchor_index":anchor["anchor_index"],"folder":str(folder)})
    (output/"render_manifest.json").write_text(json.dumps({"all_assessable_rounds":rows,
        "selection":"All assessable rounds rendered; separate first differing FOV event, not best-case selection",
        "source_predictions_modified":False},indent=2))


if __name__=="__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--render-report",type=Path,required=True)
    render_saved_results(parser.parse_args().render_report)
