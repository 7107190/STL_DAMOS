#!/usr/bin/env python3
"""Fixed-checkpoint regression audit on immutable earlier S1-S9 inputs."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from bev_fusion import fuse_bevs
from bev_fusion_diagnostic import load_npz, scene_scores, score_hidden_parts
from bev_model_runtime import BEVPredictor
from train_bev_adaptation import aggregate, selection_ok


def hidden_recoveries(scores):
    return sum(old["assessable"] and old["detected"] is False and new["detected"] is True
               for old,new in zip(scores["ego"],scores["weighted"]))


def previous_hidden_detections_retained(original,adapted):
    if len(original["ego"])!=len(adapted["weighted"]):
        raise ValueError("Hidden-anchor identity mismatch.")
    for own,old,new in zip(original["ego"],original["weighted"],adapted["weighted"]):
        if (old["scenario"],old["anchor_index"])!=(new["scenario"],new["anchor_index"]):
            raise ValueError("Hidden-anchor identity mismatch.")
        if own["assessable"] and own["detected"] is False and old["detected"] is True:
            if new["detected"] is not True:
                return False
    return True


def replay_pose_and_coverage(prediction,reference):
    if not np.allclose(prediction["pose"],reference["pose"],atol=1e-5,rtol=0):
        raise ValueError("Replay input pose does not match the original frame.")
    if not np.array_equal(prediction["valid"],reference["valid"]):
        raise ValueError("Replay sensor coverage differs.")
    # Preserve the earlier peer's fixed-point transport rounding, not an unquantized alternative.
    prediction["pose"] = reference["pose"].copy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-summary",type=Path,required=True)
    parser.add_argument("--checkpoint",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    hashes = {}

    def record(path):
        path = Path(path)
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return path

    predictor = BEVPredictor(record(args.checkpoint))
    source = json.loads(record(args.source_summary).read_text())
    rows = {"original":[],"adapted":[]}
    for run in source["rows"]:
        for folder in sorted(Path(run["report"]).glob("round_*")):
            if not (folder/"evaluation.json").exists():
                continue
            own = load_npz(record(folder/"ego_own.npz"))
            peer_paths = sorted(folder.glob("ego_received_*.npz"))
            peers = [load_npz(record(path)) for path in peer_paths]
            saved_fused = load_npz(record(folder/"ego_fused.npz"))
            replay = fuse_bevs(own,peers)
            if not np.array_equal(replay["probabilities"],saved_fused["probabilities"]):
                raise ValueError("Earlier baseline does not replay exactly.")
            truth = json.loads(record(folder/"ground_truth.json").read_text())
            evaluation = json.loads(record(folder/"evaluation.json").read_text())
            for path in folder.glob("*_packet.json"):
                record(path)
            evidence = {}
            for path in folder.glob("*_eval_only_semantic.npz"):
                data = load_npz(record(path))
                evidence[path.stem] = (data["world_xyz"],data["actor_ids"],data["tags"])
            nodes = ["ego",*[path.stem.removeprefix("ego_received_") for path in peer_paths]]
            references = dict(zip(nodes,[own,*peers]))
            adapted = {}
            for node in nodes:
                arrays = load_npz(record(folder/f"{node}_model_inputs.npz"))
                adapted[node],_ = predictor.infer(arrays)
                if int(arrays["frame_ids"][-1])!=truth["frame_id"]:
                    raise ValueError("Regression frame mismatch.")
                replay_pose_and_coverage(adapted[node],references[node])
            adapted_fused = fuse_bevs(adapted["ego"],[adapted[node] for node in nodes[1:]])
            predictions = {"original":{"ego":own,"weighted":saved_fused},
                           "adapted":{"ego":adapted["ego"],"weighted":adapted_fused}}
            for name,items in predictions.items():
                rows[name].append({"scenario":run["scenario"],"frame_id":truth["frame_id"],
                    "source_folder":str(folder),"scores":scene_scores(items,evidence,truth),
                    "hidden_scores":score_hidden_parts(items,evidence,evaluation)})
        print(f"[ADAPT AUDIT] {run['scenario']} complete",flush=True)
    results = {}
    for name,items in rows.items():
        results[name] = {"aggregate":aggregate(items),
            "per_scenario":{s:aggregate([row for row in items if row["scenario"]==s])
                            for s in sorted({row["scenario"] for row in items})},
            "hidden_part_recovery_frames":sum(hidden_recoveries(row["hidden_scores"]) for row in items),
            "rounds":items}
    recovery_retained = all(previous_hidden_detections_retained(old["hidden_scores"],new["hidden_scores"])
                           for old,new in zip(rows["original"],rows["adapted"]))
    unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest for path,digest in hashes.items())
    result = {"candidate_checkpoint_sha256":predictor.checkpoint_hash,
        "production_default_changed":False,"source_files_unchanged":unchanged,"source_sha256":hashes,
        "regression_gate_pass":selection_ok(results["adapted"],results["original"]) and recovery_retained,
        "every_previous_hidden_part_recovery_retained":recovery_retained,
        "pose_replay":"Exact original ego and verified peer poses/coverage; same fixed-point transport rounding, not GT poses",
        "scope":"Earlier development scenes, not independent test or checkpoint selection. Fixed candidate; weighted fusion unchanged. No new ZKP proof or timing claim.",
        "metric2_pass":None,**results}
    (args.output/"summary.json").write_text(json.dumps(result,indent=2))
    print("[ADAPT AUDIT RESULT] "+json.dumps({key:result[key] for key in
        ("regression_gate_pass","every_previous_hidden_part_recovery_retained","source_files_unchanged")}),flush=True)
    if not unchanged:
        raise RuntimeError("Regression source files changed.")


if __name__=="__main__":
    main()
