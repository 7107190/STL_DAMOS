"""New scene-split adaptation inputs; semantic supervision never enters inference."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np

from bev_evaluation import SemanticEvaluationRig, decode_semantic, local_points, points_mask
from bev_live import task_pose
from bev_model_runtime import INPUT_SHAPES, VENDOR
from bev_sensor_rig import prepare_raw_task


def input_digest(arrays):
    digest = hashlib.sha256()
    for name in sorted(INPUT_SHAPES):
        digest.update(name.encode())
        digest.update(np.ascontiguousarray(arrays[name]).tobytes())
    return digest.hexdigest()


def semantic_labels(evidence, pose, *, exclude_actor_id):
    sys.path.insert(0,str(VENDOR))
    from carla_gt_core import CARLA2CLS, PRIORITY
    xyz,ids,tags = evidence
    p = local_points(xyz,pose)
    ix,iy = np.floor((p[:,:2]+40)/.4).astype(np.int64).T
    ok = (ix>=0)&(ix<200)&(iy>=0)&(iy<200)&(p[:,2]>=-1)&(p[:,2]<5.4)&(ids!=exclude_actor_id)
    classes = np.array([CARLA2CLS.get(int(tag),0) for tag in tags[ok]],np.uint8)
    ranks = np.array([PRIORITY[int(cls)] for cls in classes],np.int32)
    flat = ix[ok]*200+iy[ok]
    best = np.full(40000,999,np.int32)
    np.minimum.at(best,flat,ranks)
    labels = np.full(40000,255,np.uint8)
    for cls,rank in PRIORITY.items():
        labels[best==rank] = cls
    return labels.reshape(200,200)


def collect_scene(rig, root, *, split, seed, rounds, anchor_assignments, scenario_labels, logger=print,on_tick=None):
    if split not in ("train","val","test") or not np.isclose(rig.step,.1,atol=1e-6):
        raise ValueError("Adaptation collection requires a known split and production 0.1s inputs.")
    scenario = scenario_labels[0].split("#")[0]
    scene_id = f"{scenario}_seed{seed}_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output = Path(root)/split/scene_id
    output.mkdir(parents=True,exist_ok=False)
    logger(f"[BEV DATA] split={split} scene={scene_id}; report={output}")
    manifest = {"scene_id":scene_id,"scenario":scenario,"split":split,"seed":seed,
        "capture_complete":False,"rounds_requested":rounds,"samples":[],"rounds":[],
        "sensor_input":"Production FOV=90, history=4x0.1s, current ordinary LiDAR 10Hz",
        "gt_scope":"Same-frame semantic returns only, original taxonomy priority, no boxes or densification",
        "gt_used_as_inference_input":False,"inference_executed":False,"zkp_executed":False,
        "sensors":rig.records,"error":None}
    captured = []
    evaluator = None
    node_labels = {("ego" if label=="ego" else f"observer_{actor.id}"):label for label,actor in rig.actors.items()}
    try:
        evaluator = SemanticEvaluationRig(rig,anchor_assignments,instance_cameras=False)
        minimum = rig.world.get_snapshot().frame+3
        for index in range(rounds):
            if on_tick:
                on_tick()
            window,tasks = rig.freeze_inputs(minimum_frame=minimum,timeout_s=60)
            truth,packets = evaluator.freeze(window[-1],list(rig.actors),timeout_s=30)
            minimum = window[-1]-1
            captured.append((window,tasks,truth,packets))
        manifest["ego_trace"] = evaluator.ego_trace
        evaluator.close(); evaluator = None
        # Freeze the short sequence first; compression cannot move later samples off target.
        for index,(window,tasks,truth,packets) in enumerate(captured):
            folder = output/f"round_{index+1:03d}"; folder.mkdir()
            (folder/"ground_truth.json").write_text(json.dumps(truth,indent=2))
            evidence = {node:decode_semantic(packets[label]) for node,label in node_labels.items()}
            for node,(xyz,ids,tags) in evidence.items():
                np.savez_compressed(folder/f"{node}_eval_only_semantic.npz",world_xyz=xyz,actor_ids=ids,tags=tags)
            round_row = {"frame_id":window[-1],"folder":str(folder),"nodes":[]}
            for node,label in node_labels.items():
                task = tasks[label]
                arrays,_ = prepare_raw_task(task)
                if not np.allclose(np.diff(arrays["timestamps"]),.1,atol=2e-6):
                    raise ValueError("Adaptation input history mismatch.")
                labels = semantic_labels(evidence[node],task_pose(task),exclude_actor_id=task["actor_id"])
                target_ids = [target["actor_id"] for target in truth["targets"]]
                xyz,ids,_ = evidence[node]
                targets = points_mask(xyz[np.isin(ids,target_ids)],task_pose(task))
                inputs_path,gt_path = folder/f"{node}_inputs.npz",folder/f"{node}_supervision.npz"
                np.savez_compressed(inputs_path,**arrays)
                np.savez_compressed(gt_path,labels=labels,target_mask=targets)
                for camera in ("cam_front","cam_back"):
                    image = np.frombuffer(task["packets"][label,camera][-1]["raw"],np.uint8).reshape(450,800,4)[:,:,:3]
                    cv2.imwrite(str(folder/f"{node}_{camera}.png"),image)
                mask = (labels!=255)&(arrays["lidar_bev"][0,0]>0)
                row = {"node":node,"actor_id":task["actor_id"],"actor_type_id":task["actor_type_id"],
                    "frame_id":window[-1],"inputs":str(inputs_path),"supervision":str(gt_path),
                    "content_sha256":input_digest(arrays),"inputs_sha256":hashlib.sha256(inputs_path.read_bytes()).hexdigest(),
                    "supervision_sha256":hashlib.sha256(gt_path.read_bytes()).hexdigest(),
                    "observed_supervision_cells":int(mask.sum()),"target_supervision_cells":int((mask&targets).sum()),
                    "label_counts":np.bincount(labels[mask],minlength=17).tolist()}
                manifest["samples"].append(row); round_row["nodes"].append(row)
            manifest["rounds"].append(round_row)
        manifest["capture_complete"] = len(manifest["rounds"])==rounds
        logger(f"[BEV DATA] Saved {len(manifest['samples'])} samples / {rounds} rounds; report={output}")
    except Exception as exc:
        manifest["error"] = type(exc).__name__+": "+str(exc)
        raise
    finally:
        if evaluator is not None:
            evaluator.close()
        (output/"manifest.json").write_text(json.dumps(manifest,indent=2))
    return manifest


def dataset_scenes(root):
    scenes,identities,contents = [],{},{}
    for path in sorted(Path(root).glob("*/*/manifest.json")):
        manifest = json.loads(path.read_text())
        if manifest["split"]!=path.parent.parent.name or manifest["split"] not in ("train","val","test"):
            raise ValueError("Manifest split does not match its directory.")
        if not manifest["capture_complete"]:
            raise ValueError(f"Incomplete scene: {path}")
        identity = (manifest["scenario"],manifest["seed"])
        if identity in identities:
            raise ValueError("Repeated scene seed or split leakage.")
        identities[identity] = manifest["split"]
        for sample in manifest["samples"]:
            digest = sample["content_sha256"]
            if digest in contents and contents[digest]!=manifest["split"]:
                raise ValueError("Identical model input across splits.")
            contents[digest] = manifest["split"]
        scenes.append(manifest)
    if {scene["split"] for scene in scenes}!={"train","val","test"}:
        raise ValueError("Train/val/test scenes are all required.")
    return scenes
