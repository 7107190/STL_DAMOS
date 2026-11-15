#!/usr/bin/env python3
"""Bounded foreground-preserving candidates; no validation/test data enters training."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import time

import numpy as np

from bev_fusion_diagnostic import load_npz
from bev_model_runtime import BEVPredictor, CHECKPOINT, INPUT_SHAPES
from bev_occupancy import OBSTACLE_CLASSES, GROUND_CLASSES

ARMS = (("foreground_head",("decoder_v2.",),4,5e-5,(2,4)),
        ("foreground_lidar",("decoder_v2.","lidar_encoder."),2,3e-5,(2,)))


def presence_loss(logits,labels,observed,targets,teacher):
    import torch
    import torch.nn.functional as F
    mask = observed&(labels!=255)&(labels!=0)
    if not mask.any():
        raise ValueError("No observed training supervision.")
    positive = torch.zeros_like(mask)
    for cls in OBSTACLE_CLASSES:
        positive |= labels==cls
    margin = logits[list(OBSTACLE_CLASSES)].amax(0)-logits[list(GROUND_CLASSES)].amax(0)
    binary = F.binary_cross_entropy_with_logits(margin,positive.float(),reduction="none")
    balanced = torch.stack([binary[mask&group].mean() for group in (positive,~positive) if (mask&group).any()]).mean()
    foreground = mask&targets&positive
    target_loss = binary[foreground].mean() if foreground.any() else balanced*0
    p = margin[mask].sigmoid()
    y = positive[mask].float()
    dice = 1-(2*(p*y).sum()+1)/(p.sum()+y.sum()+1)
    teacher_margin = (teacher[list(OBSTACLE_CLASSES)].amax(0).clamp_min(1e-7).log()
                      -teacher[list(GROUND_CLASSES)].amax(0).clamp_min(1e-7).log())
    correct_teacher = mask&positive&(teacher_margin>0)
    preserve = F.relu(teacher_margin[correct_teacher]-margin[correct_teacher]).mean() if correct_teacher.any() else balanced*0
    ce = F.cross_entropy(logits[:,mask].T,labels[mask])
    return .25*ce+balanced+4*target_loss+dice+.5*preserve


def train_scenes(root):
    items = []
    for path in sorted(Path(root).glob("train/*/manifest.json")):
        scene = json.loads(path.read_text())
        if scene["split"]!="train" or not scene["capture_complete"]:
            raise ValueError("Only complete training scenes may be used.")
        items.append(scene)
    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-dataset",type=Path,required=True)
    parser.add_argument("--new-dataset",type=Path,required=True)
    parser.add_argument("--initial-checkpoint",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--wait-for-new-scenes",type=int,default=6)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    deadline = time.monotonic()+1800
    while len(train_scenes(args.new_dataset))<args.wait_for_new_scenes:
        if time.monotonic()>deadline:
            raise TimeoutError("Training scenes did not finish; refusing partial training.")
        time.sleep(10)
    scenes = train_scenes(args.old_dataset)+train_scenes(args.new_dataset)
    identities = [(s["scenario"],s["seed"]) for s in scenes]
    if len(set(identities))!=len(identities):
        raise ValueError("Repeated training scene.")
    samples = [sample for scene in scenes for sample in scene["samples"]]
    hashes = {}
    for sample in samples:
        for name in ("inputs","supervision"):
            path = Path(sample[name]); digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest!=sample[name+"_sha256"]:
                raise ValueError("Training source changed.")
            hashes[str(path)] = digest
    import torch
    random.seed(7190); np.random.seed(7190); torch.manual_seed(7190); torch.cuda.manual_seed_all(7190)
    teacher = BEVPredictor(CHECKPOINT)
    teachers = {}
    for index,sample in enumerate(samples):
        prediction,_ = teacher.infer(load_npz(sample["inputs"]))
        teachers[sample["inputs"]] = prediction["probabilities"]
        if (index+1)%32==0:
            print(f"[PRESENCE TEACHER] {index+1}/{len(samples)}",flush=True)
    del teacher; torch.cuda.empty_cache()
    results = {"training_samples":len(samples),"training_scenes":[s["scene_id"] for s in scenes],
        "validation_or_test_used":False,"arms":[],"source_sha256":hashes}
    for name,prefixes,epochs,lr,save_epochs in ARMS:
        record = {"name":name,"prefixes":prefixes,"epochs":epochs,"lr":lr,"checkpoints":[],"error":None}
        predictor,optimizer,model = None,None,None
        try:
            predictor = BEVPredictor(args.initial_checkpoint); model = predictor.model
            model.eval()
            for key,param in model.named_parameters():
                param.requires_grad_(key.startswith(prefixes))
            initial = {key:value.detach().cpu().clone() for key,value in model.state_dict().items()}
            initial["z_collapse._step"].fill_(predictor.step)
            trainable = [(key,param) for key,param in model.named_parameters() if param.requires_grad]
            optimizer = torch.optim.AdamW([param for _,param in trainable],lr=lr,weight_decay=1e-4)
            record["trainable_parameters"] = sum(p.numel() for _,p in trainable)
            record["history"] = []
            for epoch in range(1,epochs+1):
                random.shuffle(samples); losses=[]
                for index,sample in enumerate(samples):
                    arrays = load_npz(sample["inputs"]); gt = load_npz(sample["supervision"])
                    tensors = {key:torch.from_numpy(arrays[key]).cuda() for key in INPUT_SHAPES}
                    labels = torch.from_numpy(gt["labels"].astype(np.int64)).cuda()
                    target = torch.from_numpy(gt["target_mask"].astype(bool)).cuda()
                    teacher_p = torch.from_numpy(teachers[sample["inputs"]].astype(np.float32)).cuda()
                    optimizer.zero_grad(set_to_none=True)
                    logits,_ = model(tensors['imgs'],tensors['rots'],tensors['trans'],tensors['intrins'],
                        tensors['post_rots'],tensors['post_trans'],ego2globals=tensors['ego2globals'],
                        lidar_bev=tensors['lidar_bev'],return_aux=True)
                    loss = presence_loss(logits[0,0],labels,tensors['lidar_bev'][0,0]>0,target,teacher_p)
                    if not torch.isfinite(loss):
                        raise RuntimeError("Nonfinite presence loss.")
                    loss.backward()
                    missing = [key for key,param in trainable if param.grad is None or not torch.isfinite(param.grad).all()]
                    if missing:
                        raise RuntimeError(f"Invalid gradients: {missing}")
                    torch.nn.utils.clip_grad_norm_([param for _,param in trainable],1.)
                    optimizer.step(); losses.append(float(loss.detach()))
                unchanged = all(torch.equal(value.detach().cpu(),initial[key])
                    for key,value in model.state_dict().items() if not key.startswith(prefixes))
                if not unchanged:
                    raise RuntimeError("A frozen parameter/buffer changed.")
                item = {"epoch":epoch,"loss":float(np.mean(losses)),"frozen_unchanged":unchanged}
                record["history"].append(item)
                if epoch in save_epochs:
                    path = args.output/f"{name}_epoch{epoch}.pt"
                    torch.save({"model":{key:value.detach().cpu() for key,value in model.state_dict().items()},
                        "step":predictor.step,"training_objective":"presence, target foreground, correct-teacher obstacle preservation",
                        "training_scope":list(prefixes),"training_epoch":epoch,
                        "initial_checkpoint_sha256":predictor.checkpoint_hash},path)
                    record["checkpoints"].append(str(path))
                print(f"[PRESENCE TRAIN] {name} "+json.dumps(item),flush=True)
                (args.output/"progress.json").write_text(json.dumps(dict(results,current=record),indent=2))
        except Exception as exc:
            record["error"] = type(exc).__name__+": "+str(exc)
            print(f"[PRESENCE TRAIN FAILED] {name}: {record['error']}",flush=True)
        finally:
            results["arms"].append(record)
            del optimizer,model,predictor
            torch.cuda.empty_cache()
            (args.output/"summary.json").write_text(json.dumps(results,indent=2))
    results["source_files_unchanged"] = all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest for path,digest in hashes.items())
    (args.output/"summary.json").write_text(json.dumps(results,indent=2))
    if not results["source_files_unchanged"]:
        raise RuntimeError("Training source files changed.")


if __name__=="__main__":
    main()
