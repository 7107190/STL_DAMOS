"""Check submitted cell evidence against preserved raw BEV and semantic arrays."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from bev_accuracy_batch import load_npz, sha256, write_json
from bev_fusion import fuse_bevs
from bev_protocol import encode_bev

OBSTACLE_CLASSES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 16)


def target_cells(points, pose):
    inverse = np.linalg.inv(pose)
    local = points @ inverse[:3, :3].T + inverse[:3, 3]
    index = np.floor((local[:, :2] + 40) / .4).astype(np.int64)
    inside = ((index >= 0) & (index < 200)).all(1)
    inside &= (local[:, 2] >= -1) & (local[:, 2] < 5.4)
    return np.unique(index[inside, 0] * 200 + index[inside, 1]).tolist()


def predicted_cells(bev):
    labels = bev["probabilities"].argmax(axis=0)
    return np.flatnonzero(bev["valid"].astype(bool) &
                         np.isin(labels, OBSTACLE_CLASSES)).tolist()


def audit(trial, output):
    if output.exists():
        raise FileExistsError("Use a new audit path; preserve the previous audit")
    protocol = json.loads((trial / "protocol.json").read_text(encoding="utf-8"))
    checks, skipped = [], []
    for case in protocol["cases"]:
        folder = trial / "results" / case["case_id"]
        result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        if result["status"] == "data_error":
            skipped.append(case["case_id"])
            continue
        recording = folder / "recording"
        capture = json.loads((recording / "capture.json").read_text(encoding="utf-8"))
        observer = next(node for node in capture["actors"] if node != "ego")
        for sample in sorted(recording.glob("round_*")):
            evidence = json.loads((sample / "evidence.json").read_text(encoding="utf-8"))
            own, fused = load_npz(sample / "ego_own.npz"), load_npz(sample / "ego_fused.npz")
            peer = load_npz(sample / f"ego_received_{observer}.npz")
            regenerated = fuse_bevs(own, [peer], mode="ego_observed")
            for key in ("probabilities", "valid", "pose"):
                np.testing.assert_array_equal(regenerated[key], fused[key])
            points = []
            input_hashes = {}
            for node in capture["actors"]:
                semantic_path = sample / f"{node}_eval_only_semantic.npz"
                semantic = load_npz(semantic_path)
                points.append(semantic["world_xyz"][np.isin(semantic["actor_ids"], capture["target_ids"])])
                packet_path = sample / f"{node}_packet.json"
                packet = json.loads(packet_path.read_text(encoding="utf-8"))
                body = encode_bev(load_npz(sample / f"{node}_own.npz"))
                if hashlib.sha256(body).hexdigest() != packet["manifest"]["sha256"]:
                    raise ValueError("Raw BEV differs from the authenticated body")
                input_hashes[str(semantic_path.name)] = sha256(semantic_path)
                input_hashes[packet_path.name] = sha256(packet_path)
                model_path = sample / f"{node}_model_inputs.npz"
                inputs = load_npz(model_path)
                if (inputs["imgs"].shape != (1, 4, 6, 3, 128, 320)
                        or inputs["lidar_bev"].shape != (1, 6, 200, 200)
                        or inputs["frame_ids"].shape != (4,)
                        or not np.all(np.diff(inputs["frame_ids"]) == 1)
                        or int(inputs["frame_ids"][-1]) != evidence["frame_id"]
                        or abs(float(inputs["timestamps"][-1]) * 1e9 -
                               int(evidence["capture_sim_ns"])) > 1000):
                    raise ValueError("Preserved model input shape or capture clock differs")
                np.testing.assert_array_equal(inputs["ego2globals"][0, -1],
                                              load_npz(sample / f"{node}_own.npz")["pose"])
                if not all(np.isfinite(inputs[key]).all() for key in
                           ("imgs", "lidar_bev", "ego2globals")):
                    raise ValueError("Non-finite original model input")
                input_hashes[model_path.name] = sha256(model_path)
            actual_gt = target_cells(np.concatenate(points), own["pose"])
            if actual_gt != evidence["gt_target_cell_indices"]:
                raise ValueError("JSON GT cells differ from exact actor-ID semantic returns")
            for name, prediction in (("ego", own), ("fused", fused)):
                if predicted_cells(prediction) != evidence[name + "_obstacle_cell_indices"]:
                    raise ValueError("JSON prediction cells differ from preserved raw BEV")
            for path in (sample / "ego_own.npz", sample / "ego_fused.npz",
                         sample / f"ego_received_{observer}.npz", sample / "evidence.json"):
                input_hashes[path.name] = sha256(path)
            checks.append({"case_id": case["case_id"], "sample": sample.name,
                "frame_id": evidence["frame_id"], "gt_cells": len(actual_gt),
                "raw_gt_prediction_fusion_and_authenticated_bytes_match": True,
                "original_model_inputs_and_capture_clock_match": True,
                "source_sha256": input_hashes})
        print(f"[RAW AUDIT] {case['case_id']} all processed samples matched", flush=True)
    audit_source = Path(__file__)
    write_json(output, {"checked_samples": len(checks), "checks": checks,
        "skipped_data_error_cases": skipped, "protocol_sha256": sha256(trial / "protocol.json"),
        "audit_sha256": sha256(audit_source), "audit_source": audit_source.read_text(encoding="utf-8"),
        "metadata_matches_preserved_arrays": True,
        "no_change_to_model_or_case_verdicts": True,
        "scope": "All processed samples of non-data-error cases; unused acquired samples excluded"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.trial, args.output)


if __name__ == "__main__":
    main()
