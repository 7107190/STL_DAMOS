"""Actual CUDA/Groth16/TCP confirmation on pinned saved inputs, not live CARLA."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

import numpy as np

from audit_bev_candidate_live import audit_round
from bev_ground_guard import apply_ground_guard
from bev_mesh import BEVMesh
from bev_occupancy import obstacle_presence
from bev_anchor_evaluation import region_presence
from bev_fusion import fuse_bevs
from benchmark_bev_ground_guard import masks
from m2x.security import real_backend


def load(path):
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ["DAMOS_BEV_CHECKPOINT"] = str(args.checkpoint)
    os.environ["DAMOS_BEV_AUDIT_GROUND_GUARD"] = "1"
    profile = json.loads(args.profile.read_text())
    digest = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    report = {"source": "Actual CUDA inference + real Groth16 + loopback TCP on saved sensor arrays",
        "live_CARLA": False, "formal_metric2_pass": None,
        "checkpoint_sha256": digest, "ground_guard": profile, "scenarios": [], "error": None}
    backend = real_backend(60)
    try:
        for scene in json.loads(args.index.read_text())["rows"]:
            source = args.sources / scene["source_report"]
            actors = scene["actors"]
            folders = sorted(source.glob("round_*"))
            if len(folders) != 3:
                raise ValueError("Expected pre-pinned first/middle/last input frames.")
            def tasks(folder):
                return {node: {"arrays": load(folder / f"{node}_model_inputs.npz"),
                    "window": load(folder / f"{node}_model_inputs.npz")["frame_ids"].tolist(),
                    "actor_id": actor["actor_id"], "label": node} for node, actor in actors.items()}
            mesh = None
            result = {"scenario": scene["scenario"], "frames": [], "actors": actors}
            try:
                mesh = BEVMesh({node: actor["actor_id"] for node, actor in actors.items()}, 60)
                mesh.warmup(tasks(folders[0]))
                result["setup"] = mesh.setup
                for i, folder in enumerate(folders, 1):
                    output = args.output / scene["scenario"] / folder.name
                    rows = mesh.exchange(tasks(folder), i, output, save_inputs=False)
                    (output / "peers.json").write_text(json.dumps(rows, indent=2))
                    if any(row["status"] != "fusion_complete" or row.get("artifact_error")
                           or not row["inference"].get("ground_guard_enabled") for row in rows.values()):
                        raise RuntimeError("Corrected actual BEV pipeline failed.")
                    audit = audit_round(output, {"actors": actors, "setup": mesh.setup}, digest,
                                        backend, fusion_mode="ego_observed", calibrated=True)
                    comparisons = {}
                    for node in actors:
                        old = load(folder / f"{node}_own.npz")
                        with np.load(folder / f"{node}_model_inputs.npz", allow_pickle=False) as data:
                            lidar = data["lidar_bev"]
                        expected, _ = apply_ground_guard(old, lidar, profile)
                        actual = load(output / f"{node}_own.npz")
                        valid = old["valid"].astype(bool)
                        delta = np.abs(expected["probabilities"].astype(np.float32)
                                       - actual["probabilities"].astype(np.float32))
                        label_difference = int(((obstacle_presence(expected["probabilities"], valid)["occupancy"]
                            != obstacle_presence(actual["probabilities"], actual["valid"])["occupancy"]) & valid).sum())
                        np.testing.assert_array_equal(actual["valid"], old["valid"])
                        comparisons[node] = {"max_probability_difference": float(delta.max()),
                            "observed_occupancy_label_difference": label_difference,
                            "observed_cells": int(valid.sum()),
                            "ground_guard_ms": rows[node]["inference"]["ground_guard_ms"]}
                        # Historical inference is a diagnostic, not an equality oracle.
                        # Compare the correction with this exact CUDA forward instead.
                        reference = dict(actual, probabilities=actual["pre_ground_guard_probabilities"])
                        same_forward, _ = apply_ground_guard(reference, lidar, profile)
                        np.testing.assert_array_equal(same_forward["probabilities"], actual["probabilities"])
                        comparisons[node]["same_forward_postprocess_bitwise_equal"] = True
                    actual_ego = load(output / "ego_own.npz")
                    actual_fused = load(output / "ego_fused.npz")
                    old_fused = load(folder / "ego_fused.npz")
                    evidence = [load(folder / f"{node}_eval_only_semantic.npz") for node in actors]
                    target = masks(evidence, actual_ego["pose"], scene["target_ids"])["target"]
                    before, after = region_presence(old_fused, target), region_presence(actual_fused, target)
                    references = {}
                    for node in actors:
                        prediction = load(output / f"{node}_own.npz")
                        prediction["probabilities"] = prediction["pre_ground_guard_probabilities"]
                        if node != "ego":
                            prediction["pose"] = load(output / f"ego_received_{node}.npz")["pose"]
                        references[node] = prediction
                    raw_fused = fuse_bevs(references["ego"], [references[n] for n in actors if n != "ego"], mode="ego_observed")
                    paired_before = region_presence(raw_fused, target)
                    if paired_before["detected"] is True and after["detected"] is not True:
                        raise AssertionError("Actual corrected CUDA target-region regression.")
                    record = {"source_folder": str(folder), "output": str(output),
                        "frame_id": rows["ego"]["frame_id"], "elapsed_ms": rows["ego"]["elapsed_ms"],
                        "audit": audit, "comparisons": comparisons,
                        "GT_evaluation_only_target": {"historical_before": before,
                            "same_forward_before": paired_before, "after": after}}
                    result["frames"].append(record)
                    print(f"{scene['scenario']} F{record['frame_id']} verified=2/2 elapsed={record['elapsed_ms']:.1f}ms", flush=True)
            finally:
                if mesh is not None:
                    mesh.close()
                report["scenarios"].append(result)
                (args.output / "summary.json").write_text(json.dumps(report, indent=2))
    except BaseException as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        (args.output / "summary.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
