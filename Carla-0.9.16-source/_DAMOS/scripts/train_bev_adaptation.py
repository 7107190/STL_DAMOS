#!/usr/bin/env python3
"""Decoder-only transfer learning with scene-disjoint selection and one held-out test."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import time

import cv2
import numpy as np

from bev_adaptation_data import dataset_scenes
from bev_evaluation import points_mask
from bev_fusion import fuse_bevs
from bev_fusion_diagnostic import comparison_image, load_npz, scene_scores
from bev_model_runtime import BEVPredictor, CHECKPOINT, INPUT_SHAPES, validate_inputs
from bev_occupancy import OBSTACLE_CLASSES, GROUND_CLASSES


def aggregate(rows):
    scenes = [row["scores"]["weighted"]["scene"] for row in rows]
    anchors = [anchor for row in rows for anchor in row["scores"]["weighted"]["anchors"]]
    tp = sum(s["true_positive_cells"] for s in scenes)
    fp = sum(s["ground_false_positive_cells"] for s in scenes)
    positives = sum(s["gt_obstacle_cells"] for s in scenes)
    tn = sum(s["true_ground_cells"] for s in scenes)
    unknown = sum(s["unknown_on_ground_cells"]+s["unknown_on_obstacle_cells"] for s in scenes)
    total = sum(s["gt_ground_cells"]+s["gt_obstacle_cells"] for s in scenes)
    return {"regions_detected":sum(a["detected"] is True for a in anchors),
        "regions_assessable":sum(a["assessable"] for a in anchors),"true_positive_cells":tp,
        "ground_false_positive_cells":fp,"gt_obstacle_cells":positives,"true_ground_cells":tn,
        "obstacle_recall":tp/positives if positives else 0.,
        "obstacle_f1":2*tp/(positives+tp+fp) if positives+tp+fp else 0.,
        "ground_fp_rate":fp/(fp+tn) if fp+tn else 1.,
        "unknown_fraction":unknown/total if total else 1.}


def eligible(candidate,baseline):
    return (candidate["regions_assessable"]==baseline["regions_assessable"]
            and candidate["regions_detected"]>=baseline["regions_detected"]
            and candidate["ground_fp_rate"]<=baseline["ground_fp_rate"]
            and candidate["unknown_fraction"]<=baseline["unknown_fraction"]
            and candidate["obstacle_f1"]>baseline["obstacle_f1"])


def selection_ok(candidate,baseline):
    if not eligible(candidate["aggregate"],baseline["aggregate"]):
        return False
    for scenario,original in baseline["per_scenario"].items():
        current = candidate["per_scenario"][scenario]
        if (current["regions_detected"]<original["regions_detected"]
                or current["ground_fp_rate"]>original["ground_fp_rate"]
                or current["unknown_fraction"]>original["unknown_fraction"]):
            return False
    return True


def evaluate(predictor, scenes, output=None):
    rows = []
    for scene in scenes:
        for index,round_row in enumerate(scene["rounds"]):
            predictions = {}
            for sample in round_row["nodes"]:
                arrays = load_npz(sample["inputs"])
                predictions[sample["node"]],_ = predictor.infer(arrays)
            own = predictions["ego"]
            fused = fuse_bevs(own,[predictions[n] for n in sorted(predictions) if n!="ego"])
            folder = Path(round_row["folder"])
            evidence = {}
            for path in folder.glob("*_eval_only_semantic.npz"):
                data = load_npz(path)
                evidence[path.stem] = (data["world_xyz"],data["actor_ids"],data["tags"])
            truth = json.loads((folder/"ground_truth.json").read_text())
            scores = scene_scores({"ego":own,"weighted":fused},evidence,truth)
            row = {"scene_id":scene["scene_id"],"scenario":scene["scenario"],"frame_id":truth["frame_id"],
                   "source_folder":str(folder),"scores":scores}
            if output is not None:
                save = Path(output)/scene["scene_id"]/f"round_{index+1:03d}"; save.mkdir(parents=True)
                for node,prediction in predictions.items():
                    np.savez_compressed(save/f"{node}_own.npz",**prediction)
                np.savez_compressed(save/"ego_fused.npz",**fused)
                row["prediction_folder"] = str(save)
            rows.append(row)
    result = {"aggregate":aggregate(rows),"per_scenario":{scenario:aggregate([row for row in rows if row['scenario']==scenario])
               for scenario in sorted({row['scenario'] for row in rows})},"rounds":rows}
    if output is not None:
        (Path(output)/"summary.json").write_text(json.dumps(result,indent=2))
    return result


def adaptation_loss(logits,labels,observed,target_mask,weights):
    import torch
    import torch.nn.functional as F
    mask = observed & (labels!=255) & (labels!=0)
    if not mask.any():
        raise ValueError("No real observed supervision.")
    ce = F.cross_entropy(logits[:,mask].T,labels[mask],weight=weights)
    margin = logits[list(OBSTACLE_CLASSES)].amax(0)-logits[list(GROUND_CLASSES)].amax(0)
    obstacle = torch.zeros_like(mask)
    for cls in OBSTACLE_CLASSES:
        obstacle |= labels==cls
    group_losses = []
    for group in (mask&obstacle,mask&~obstacle):
        if group.any():
            group_losses.append(F.binary_cross_entropy_with_logits(margin[group],obstacle[group].float()))
    binary = torch.stack(group_losses).mean()
    target = mask&target_mask&obstacle
    foreground = F.cross_entropy(logits[:,target].T,labels[target]) if target.any() else ce*0
    return ce+.5*binary+foreground


def check_decoder_gradients(decoder):
    import torch
    missing = [name for name,param in decoder.named_parameters() if param.requires_grad and param.grad is None]
    nonfinite = [name for name,param in decoder.named_parameters()
                 if param.grad is not None and not torch.isfinite(param.grad).all()]
    if missing or nonfinite:
        raise RuntimeError(f"Invalid decoder gradients: missing={missing}, nonfinite={nonfinite}")


def frozen_state_unchanged(state,parent,step):
    import torch
    if set(state)!=set(parent):
        return False
    for name,tensor in state.items():
        if name.startswith("decoder_v2."):
            continue
        # Original forward synchronizes this persistent buffer to the restored curriculum step.
        expected = torch.full_like(parent[name],step) if name=="z_collapse._step" else parent[name]
        if not torch.equal(tensor.detach().cpu(),expected):
            return False
    return True


def render_test(output,baseline,candidate):
    chosen,regressions = set(),set()
    for old,new in zip(baseline["rounds"],candidate["rounds"]):
        if old["scene_id"]!=new["scene_id"] or old["frame_id"]!=new["frame_id"]:
            raise ValueError("Cannot mix test frames.")
        scenario = old["scenario"]
        anchor = next((a for a in old["scores"]["weighted"]["anchors"] if a["assessable"]),None)
        regressed = next((a for a,b in zip(old["scores"]["weighted"]["anchors"],new["scores"]["weighted"]["anchors"])
                          if a["detected"] is True and b["detected"] is False),None)
        selections = []
        if anchor is not None and scenario not in chosen:
            selections.append((anchor,"heldout","first assessable held-out frame, not best result"))
            chosen.add(scenario)
        if regressed is not None and scenario not in regressions:
            selections.append((regressed,"first_regression","first regressed held-out frame; candidate rejected"))
            regressions.add(scenario)
        if not selections:
            continue
        before = load_npz(Path(old["prediction_folder"])/"ego_fused.npz")
        after = load_npz(Path(new["prediction_folder"])/"ego_fused.npz")
        own = load_npz(Path(new["prediction_folder"])/"ego_own.npz")
        for anchor,suffix,title in selections:
            mask = np.zeros((200,200),bool)
            for path in Path(old["source_folder"]).glob("*_eval_only_semantic.npz"):
                data = load_npz(path)
                mask |= points_mask(data["world_xyz"][np.isin(data["actor_ids"],anchor["member_actor_ids"])],before["pose"])
            cv2.imwrite(str(Path(output)/f"{scenario}_{suffix}.png"),comparison_image(
                {"original fused":before,"adapted ego":own,"adapted fused":after},mask,
                f"{scenario} F{old['frame_id']} | {title}"))


def complete_run(args,splits,source_hashes,history,best,elapsed_training_s):
    import torch
    if (args.output/"summary.json").exists() or (args.output/"heldout_original").exists() or (args.output/"heldout_adapted").exists():
        raise RuntimeError("Test has already started; refusing repeated held-out selection/evaluation.")
    result = {"parent_checkpoint_sha256":source_hashes[str(args.checkpoint)],"production_default_changed":False,
        "training_scope":"decoder_v2 only, full forward/routing recomputed each sample; backbone and other weights frozen",
        "precision":{"training":"FP32 with gradient checks","validation_and_test":"production FP16"},
        "split_scenes":{name:[scene["scene_id"] for scene in items] for name,items in splits.items()},
        "sample_counts":{name:sum(len(scene["samples"]) for scene in items) for name,items in splits.items()},
        "selection":"Validation F1 improvement; no region-detection loss, FP-rate increase or unknown-fraction increase overall and per scenario",
        "history":history,"selected":best,"elapsed_training_s":elapsed_training_s,
        "frozen_weights_unchanged":True,"frozen_state_reference":"Parent checkpoint; z_collapse._step synchronized to restored step exactly as original forward",
        "completion_mode":"resume_finalization" if args.finalize_only else "normal",
        "test_evaluated":False,"test_gate_pass":None,"metric2_pass":None,
        "limitations":["Small scene-disjoint development dataset; same Town10HD and overlapping fixed anchor locations",
                       "Semantic return GT only, no dense free-space truth or verified occlusion evidence",
                       "No ZKP/timing benchmark in training or offline evaluation"]}
    if best is not None:
        baseline_predictor = BEVPredictor(args.checkpoint)
        baseline_test = evaluate(baseline_predictor,splits["test"],args.output/"heldout_original")
        del baseline_predictor; torch.cuda.empty_cache()
        candidate_predictor = BEVPredictor(args.output/"candidate.pt")
        candidate_test = evaluate(candidate_predictor,splits["test"],args.output/"heldout_adapted")
        result.update(test_evaluated=True,test_gate_pass=selection_ok(candidate_test,baseline_test),
                      candidate_checkpoint_sha256=candidate_predictor.checkpoint_hash,
                      baseline_test=baseline_test["aggregate"],candidate_test=candidate_test["aggregate"],
                      per_scenario_test={"original":baseline_test["per_scenario"],"adapted":candidate_test["per_scenario"]})
        render_test(args.output,baseline_test,candidate_test)
    result["source_files_unchanged"] = all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest for path,digest in source_hashes.items())
    (args.output/"summary.json").write_text(json.dumps(result,indent=2))
    print("[ADAPT RESULT] "+json.dumps({key:result[key] for key in ('selected','test_evaluated','test_gate_pass','source_files_unchanged')}),flush=True)
    if not result["source_files_unchanged"]:
        raise RuntimeError("Source files changed during adaptation/evaluation.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--checkpoint",type=Path,default=CHECKPOINT)
    parser.add_argument("--epochs",type=int,default=8)
    parser.add_argument("--lr",type=float,default=1e-4)
    parser.add_argument("--finalize-only",action="store_true")
    args = parser.parse_args()
    if not 1<=args.epochs<=32 or not 0<args.lr<=1e-3:
        parser.error("Invalid bounded learning configuration.")
    if not args.finalize_only:
        args.output.mkdir(parents=True,exist_ok=False)
    scenes = dataset_scenes(args.dataset)
    splits = {name:[scene for scene in scenes if scene['split']==name] for name in ("train","val","test")}
    source_hashes = {str(args.checkpoint):hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()}
    for scene in scenes:
        for sample in scene["samples"]:
            for name in ("inputs","supervision"):
                path = sample[name]
                digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                if digest!=sample[name+"_sha256"]:
                    raise ValueError("Dataset file changed after collection.")
                source_hashes[path] = digest
    import torch
    if args.finalize_only:
        history = json.loads((args.output/"training_history.json").read_text())
        baseline_val = json.loads((args.output/"baseline_val.json").read_text())
        if [item["epoch"] for item in history]!=list(range(1,args.epochs+1)):
            raise ValueError("Finalization requires all declared epochs to be completed.")
        choices = []
        for item in history:
            val = json.loads((args.output/f"val_epoch_{item['epoch']:02d}.json").read_text())
            if item["val"]!=val["aggregate"] or item["eligible"]!=selection_ok(val,baseline_val):
                raise ValueError("Validation selection history mismatch.")
            if item["eligible"]:
                choices.append(item)
        best = max(choices,key=lambda item:item["val"]["obstacle_f1"]) if choices else None
        if best is not None:
            state = torch.load(args.output/"candidate.pt",map_location="cpu",weights_only=True)
            parent = torch.load(args.checkpoint,map_location="cpu",weights_only=True)
            parent_weights = {name.removeprefix("module."):tensor for name,tensor in parent["model"].items()}
            if (state["adaptation_epoch"]!=best["epoch"] or state["selection_split"]!="val"
                    or state["parent_checkpoint_sha256"]!=source_hashes[str(args.checkpoint)]
                    or state["step"]!=parent["step"]
                    or not frozen_state_unchanged(state["model"],parent_weights,state["step"])):
                raise RuntimeError("Selected candidate or frozen state mismatch.")
            del state,parent,parent_weights
        complete_run(args,splits,source_hashes,history,best,None)
        return
    random.seed(7190); np.random.seed(7190); torch.manual_seed(7190); torch.cuda.manual_seed_all(7190)
    predictor = BEVPredictor(args.checkpoint)
    model = predictor.model
    for name,param in model.named_parameters():
        param.requires_grad_(name.startswith("decoder_v2."))
    model.eval()  # Frozen backbone/TC statistics and routing mode remain inference-equivalent.
    original_decoder = {name:tensor.detach().clone() for name,tensor in model.decoder_v2.state_dict().items()}
    baseline_val = evaluate(predictor,splits["val"])
    (args.output/"baseline_val.json").write_text(json.dumps(baseline_val,indent=2))
    print("[ADAPT BASELINE VAL] "+json.dumps(baseline_val['aggregate']),flush=True)
    samples = [sample for scene in splits["train"] for sample in scene["samples"]]
    counts = np.sum([sample["label_counts"] for sample in samples],axis=0).astype(np.float64)
    weights = np.clip(np.sqrt(counts.sum()/(17*np.maximum(counts,1))),.5,20)
    weights[7] = min(20,weights[7]*2)
    weights = torch.tensor(weights,dtype=torch.float32,device="cuda")
    optimizer = torch.optim.AdamW(model.decoder_v2.parameters(),lr=args.lr,weight_decay=1e-4)
    history,best,global_step = [],None,0
    started = time.monotonic()
    for epoch in range(1,args.epochs+1):
        random.shuffle(samples)
        losses = []
        for sample in samples:
            arrays = load_npz(sample["inputs"]); validate_inputs(arrays)
            supervision = load_npz(sample["supervision"])
            tensors = {key:torch.from_numpy(arrays[key]).cuda() for key in INPUT_SHAPES}
            labels = torch.from_numpy(supervision["labels"].astype(np.int64)).cuda()
            observed = tensors["lidar_bev"][0,0]>0
            target = torch.from_numpy(supervision["target_mask"].astype(bool)).cuda()
            optimizer.zero_grad(set_to_none=True)
            # The decoder also runs under no_grad inside routing; FP32 avoids detached autocast-cache reuse.
            logits,_ = model(tensors['imgs'],tensors['rots'],tensors['trans'],tensors['intrins'],
                tensors['post_rots'],tensors['post_trans'],ego2globals=tensors['ego2globals'],
                lidar_bev=tensors['lidar_bev'],return_aux=True)
            loss = adaptation_loss(logits[0,0],labels,observed,target,weights)
            anchor = sum((tensor-original_decoder[name]).square().mean()
                         for name,tensor in model.decoder_v2.named_parameters())
            loss = loss+.01*anchor
            if not torch.isfinite(loss):
                raise ValueError("Nonfinite adaptation loss.")
            loss.backward()
            check_decoder_gradients(model.decoder_v2)
            torch.nn.utils.clip_grad_norm_(model.decoder_v2.parameters(),1.)
            optimizer.step()
            losses.append(float(loss.detach())); global_step += 1
        val = evaluate(predictor,splits["val"])
        qualifies = selection_ok(val,baseline_val)
        item = {"epoch":epoch,"mean_loss":float(np.mean(losses)),"val":val["aggregate"],
                "per_scenario":val["per_scenario"],"eligible":qualifies}
        history.append(item)
        (args.output/f"val_epoch_{epoch:02d}.json").write_text(json.dumps(val,indent=2))
        if qualifies and (best is None or val["aggregate"]["obstacle_f1"]>best["val"]["obstacle_f1"]):
            state = {"model":{name:tensor.detach().cpu() for name,tensor in model.state_dict().items()},
                     "step":predictor.step,"adaptation_epoch":epoch,"adaptation_step":global_step,
                     "parent_checkpoint_sha256":source_hashes[str(args.checkpoint)],
                     "adaptation_scope":"decoder_v2 only; current production rig; GT targets external to forward",
                     "selection_split":"val"}
            torch.save(state,args.output/"candidate.pt")
            best = item
        (args.output/"training_history.json").write_text(json.dumps(history,indent=2))
        print("[ADAPT EPOCH] "+json.dumps(item),flush=True)
    # No model is chosen using test results. If validation gates fail, keep test sealed.
    parent = torch.load(args.checkpoint,map_location="cpu",weights_only=True)["model"]
    parent = {name.removeprefix("module."):tensor for name,tensor in parent.items()}
    unchanged = frozen_state_unchanged(model.state_dict(),parent,predictor.step)
    del parent
    if not unchanged:
        raise RuntimeError("A frozen backbone/refinement parameter changed; refusing candidate.")
    elapsed = time.monotonic()-started
    del optimizer,model,predictor; torch.cuda.empty_cache()
    complete_run(args,splits,source_hashes,history,best,elapsed)


if __name__=="__main__":
    main()
