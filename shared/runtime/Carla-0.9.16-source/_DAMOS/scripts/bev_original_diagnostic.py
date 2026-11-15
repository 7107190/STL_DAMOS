"""Compare unchanged author evaluation and live adapter on fixed original inputs."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np

from bev_model_runtime import BEVPredictor, CHECKPOINT, INPUT_SHAPES, VENDOR, render_bev
from bev_sensor_rig import preprocess_rgb, rasterize_lidar


def confusion(prediction, target, mask):
    valid = mask.astype(bool)&(target>=0)&(target<17)
    return np.bincount(target[valid]*17+prediction[valid],minlength=289).reshape(17,17)


def metrics(matrix):
    from bev_model_runtime import CLASS_NAMES
    union = matrix.sum(0)+matrix.sum(1)-np.diag(matrix)
    return {name:{"iou":float(matrix[i,i]/union[i]) if union[i] else None,
                  "gt_cells":int(matrix[i].sum()),"predicted_cells":int(matrix[:,i].sum())}
            for i,name in enumerate(CLASS_NAMES)}


def prepare_ground_truth(root, core):
    for path in root.glob("*/npz/*.npz"):
        with np.load(path,allow_pickle=False) as data:
            arrays = {k:data[k] for k in data.files}
        if arrays.pop("densify_pending",False):
            bev,mask = core.densify_ground(arrays["bev_cls_future"][0],arrays["bev_mask_future"][0])
            arrays["bev_cls_future"],arrays["bev_mask_future"] = bev[None],mask[None]
            np.savez_compressed(path,**arrays)


def draw_comparison(folder, sample, original, runtime, viz):
    labels = sample["bev_cls_future"][0].numpy()
    mask = sample["bev_mask_future"][0].numpy().astype(bool)
    gt_probabilities = np.eye(17,dtype=np.float32)[np.clip(labels,0,16)].transpose(2,0,1)
    panels = []
    for name,probabilities in [("ORIGINAL GT (dilated)",gt_probabilities),
                               ("AUTHOR EVAL / step=0",original),
                               ("RUNTIME / step=100000",runtime)]:
        header = np.full((40,400,3),20,np.uint8)
        cv2.putText(header,name,(8,27),cv2.FONT_HERSHEY_SIMPLEX,.52,(255,255,255),1)
        panels.append(np.vstack([header,render_bev(probabilities,mask,size=400)]))
    cameras = viz._cam_montage(sample["imgs"][-1],1200)
    cv2.imwrite(str(folder/"original_vs_runtime.png"),np.vstack([cameras,np.hstack(panels)]))


def main():
    import torch
    from PIL import Image
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--live-input",type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    # These environment flags match the handoff evaluation command.
    import os
    os.environ["CARLA_LIDAR"],os.environ["CARLA_DILATE"] = "1","1"
    sys.path.insert(0,str(VENDOR))
    import viz_carla as viz
    import carla_gt_core as core
    from data_bev_carla import CarlaBEVDataset
    selection = json.loads((args.dataset/"selection.json").read_text())
    prepare_ground_truth(args.dataset/"gt",core)
    cfg = json.loads((VENDOR/"prism_v2.json").read_text())
    torch.set_num_threads(2)
    model,aug,grid = viz.build_model(cfg,torch.device("cuda"),past_len=4)
    checkpoint = torch.load(CHECKPOINT,map_location="cpu",weights_only=True)
    weights = {k.removeprefix("module."):v for k,v in checkpoint["model"].items()}
    missing,unexpected = model.load_state_dict(weights,strict=False)
    model_settings = {"step":int(model._step.item()),"tc_enabled":model.tc_enabled,
                      "missing_keys":list(missing),"unexpected_keys":list(unexpected)}
    dataset = CarlaBEVDataset(str(args.dataset/"gt"),"val","v1.0-mini",str(args.dataset/"images"),
                              aug,grid,past_len=4,future_len=1,is_train=False)
    agents = {Path(n.dataroot).name:i for i,n in enumerate(dataset.nuscs)}
    # The selection was made from the FULL temporal sequence before inference.
    # Use unchanged __getitem__ for these explicit windows; do not resplit the
    # small downloaded subset, which would produce a different holdout.
    dataset.ixes = [(agents[row["uid"]],
        [dataset.nuscs[agents[row["uid"]]].get("sample",t) for t in row["past_tokens"]],row["token"])
        for row in selection["samples"]]
    samples,original_probabilities,checks = [],[],[]
    original_conf,original_lidar_conf = np.zeros((17,17),np.int64),np.zeros((17,17),np.int64)
    runtime_conf,runtime_lidar_conf = original_conf.copy(),original_conf.copy()
    for index,row in enumerate(selection["samples"]):
        sample = dataset[index]
        inputs = {name:sample[name].numpy()[None].astype(np.float32) for name in INPUT_SHAPES}
        if not inputs["lidar_bev"][0,0].any():
            raise ValueError("Original loader returned empty LiDAR; no silent fallback allowed.")
        samples.append((sample,inputs))
        folder = args.output/f"sample_{index:02d}"
        folder.mkdir(exist_ok=True)
        np.savez_compressed(folder/"original_model_inputs.npz",**inputs)
        ai,past,_ = dataset.ixes[index]
        nusc = dataset.nuscs[ai]
        errors = []
        for t,rec in enumerate(past):
            for k,channel in enumerate(dataset.cams):
                sd = nusc.get("sample_data",rec["data"][channel])
                with Image.open(Path(nusc.dataroot)/sd["filename"]) as image:
                    converted,pr,pt = preprocess_rgb(np.array(image.convert("RGB")))
                errors.append(float(np.max(np.abs(converted-inputs["imgs"][0,t,k]))))
                np.testing.assert_allclose(pr,inputs["post_rots"][0,t,k],atol=1e-7)
                np.testing.assert_allclose(pt,inputs["post_trans"][0,t,k],atol=1e-7)
        sd = nusc.get("sample_data",past[-1]["data"]["LIDAR_TOP"])
        cs = nusc.get("calibrated_sensor",sd["calibrated_sensor_token"])
        raw = np.fromfile(Path(nusc.dataroot)/sd["filename"],np.float32)
        if raw.size%5:
            raise ValueError("Original LiDAR is not an exact five-float record.")
        matrix = np.eye(4); matrix[:3,:3] = core.quat2rot(cs["rotation"]); matrix[:3,3] = cs["translation"]
        adapted = rasterize_lidar(raw.reshape(-1,5)[:,:4],matrix)
        lid_error = float(np.max(np.abs(adapted-inputs["lidar_bev"][0])))
        checks.append({"rgb_max_abs_error":max(errors),"lidar_max_abs_error":lid_error})
        np.testing.assert_allclose(adapted,inputs["lidar_bev"][0],atol=1e-6)
        np.testing.assert_allclose(max(errors),0,atol=1e-6)
        tensors = {k:torch.from_numpy(v).cuda() for k,v in inputs.items()}
        with torch.no_grad(),torch.autocast("cuda"):
            logits,_ = model(*[tensors[k] for k in ("imgs","rots","trans","intrins","post_rots","post_trans")],
                ego2globals=tensors["ego2globals"],lidar_bev=tensors["lidar_bev"],return_aux=True)
            probabilities = logits[0,0].float().softmax(0).cpu().numpy()
        original_probabilities.append(probabilities)
        target,mask = sample["bev_cls_future"][0].numpy(),sample["bev_mask_future"][0].numpy()
        original_conf += confusion(probabilities.argmax(0),target,mask)
        original_lidar_conf += confusion(probabilities.argmax(0),target,mask&(inputs["lidar_bev"][0,0]>0))
        print(f"Original evaluation {index+1}/{len(dataset)}",flush=True)
    del model,tensors,logits,checkpoint,weights
    gc.collect(); torch.cuda.empty_cache()
    predictor = BEVPredictor()
    rows = []
    for index,((sample,inputs),row,original) in enumerate(zip(samples,selection["samples"],original_probabilities)):
        result,_ = predictor.infer(inputs)
        target,mask = sample["bev_cls_future"][0].numpy(),sample["bev_mask_future"][0].numpy()
        runtime_conf += confusion(result["probabilities"].argmax(0),target,mask)
        runtime_lidar_conf += confusion(result["probabilities"].argmax(0),target,mask&result["valid"].astype(bool))
        folder = args.output/f"sample_{index:02d}"
        draw_comparison(folder,sample,original,result["probabilities"],viz)
        rows.append({"uid":row["uid"],"token":row["token"],**checks[index]})
        print(f"Runtime evaluation {index+1}/{len(dataset)}",flush=True)
    report = {"scope":selection["limit"],"selection":selection["selection"],"sample_count":len(rows),
        "checkpoint_sha256":predictor.checkpoint_hash,"checkpoint_step":predictor.step,
        "original_eval_settings":model_settings,"runtime_settings":{"step":predictor.step,"tc_enabled":predictor.model.tc_enabled},
        "gt_recipe":"Original hybrid GT regenerated; ground densify + CARLA_DILATE=1; not original cached GT files",
        "ground_truth_is_model_input":False,"production_settings_changed":False,
        "original_dense_gt_iou":metrics(original_conf),"runtime_dense_gt_iou":metrics(runtime_conf),
        "original_lidar_cells_iou":metrics(original_lidar_conf),"runtime_lidar_cells_iou":metrics(runtime_lidar_conf),
        "preprocessing_checks":rows,
        "source_hashes":{name:hashlib.sha256((VENDOR/name).read_bytes()).hexdigest() for name in
            ("viz_carla.py","data_bev.py","data_bev_carla.py","carla_gt_core.py","models_bev.py","prism_v2.json")}}
    if args.live_input:
        with np.load(args.live_input,allow_pickle=False) as data:
            report["current_live_dt_seconds"] = np.diff(data["timestamps"]).tolist()
            report["current_live_camera_fovs"] = np.degrees(2*np.arctan(800/(2*data["intrins"][0,-1,:,0,0]))).tolist()
    (args.output/"diagnostic.json").write_text(json.dumps(report,indent=2))
    print(json.dumps({key:{c:report[key][c]["iou"] for c in ("pedestrian","object","car","road")} for key in
        ("original_dense_gt_iou","runtime_dense_gt_iou")}),flush=True)


if __name__=="__main__":
    main()
