"""Paired, scene-held-out road-FP experiment on unmodified saved sensor frames."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
from scipy.ndimage import minimum_filter, maximum_filter

from bev_fusion import warp_to_ego
from bev_ground_guard import VERSION, ground_candidates, lower_obstacle_evidence
from bev_lidar_calibration import geometry_features
from bev_occupancy import obstacle_presence, OBSTACLE_CLASSES

OBSTACLE_TAGS = (3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 26, 28)
GROUND_TAGS = (1, 2, 10, 23, 24, 25, 27)
DEVELOPMENT = ("S1", "S3", "S5", "S7", "S9")
CONFIRMATION = ("S2", "S4", "S6", "S8")


def load(path):
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def points_mask(xyz, pose):
    inv = np.linalg.inv(pose)
    p = xyz @ inv[:3, :3].T + inv[:3, 3]
    ix, iy = np.floor((p[:, :2] + 40) / .4).astype(int).T
    ok = ((ix >= 0) & (ix < 200) & (iy >= 0) & (iy < 200)
          & (p[:, 2] >= -1) & (p[:, 2] < 5.4))
    result = np.zeros((200, 200), bool)
    result[ix[ok], iy[ok]] = True
    return result


def masks(evidence, pose, target_ids):
    result = {key: np.zeros((200, 200), bool) for key in ("road", "ground", "obstacle", "target")}
    for data in evidence:
        for key, selector in (("road", np.isin(data["tags"], (1, 24))),
                              ("ground", np.isin(data["tags"], GROUND_TAGS)),
                              ("obstacle", np.isin(data["tags"], OBSTACLE_TAGS)),
                              ("target", np.isin(data["actor_ids"], target_ids))):
            result[key] |= points_mask(data["world_xyz"][selector], pose)
    result["road"] &= ~result["obstacle"]
    result["ground"] &= ~result["obstacle"]
    return result


def candidates():
    rows = [{"name": "baseline", "kind": "baseline"}]
    rows += [{"name": f"confidence_{p:g}", "kind": "confidence", "threshold": p}
             for p in (.55, .65, .75, .85)]
    rows += [{"name": f"component_{n}", "kind": "component", "minimum_cells": n} for n in (2, 3, 5)]
    rows += [{"name": "causal_temporal", "kind": "temporal"}]
    rows += [{"name": f"plane_{h:g}", "kind": "plane", "version": VERSION,
              "height_m": h, "span_m": .08} for h in (.04, .06, .08, .10, .12, .15)]
    rows += [{"name": f"surfaces_{h:g}", "kind": "surfaces", "band_m": h} for h in (.03, .05, .08)]
    rows += [{"name": f"local_{h:g}", "kind": "local", "height_m": h} for h in (.06, .10, .15)]
    rows += [{"name": f"guard_{h:g}_below{below:g}_r{radius}", "kind": "guard", "version": VERSION,
              "height_m": h, "span_m": .08, "below_plane_m": below,
              "protect_radius_cells": radius}
             for h in (.04, .08, .12) for below in (.12, .25) for radius in (1, 2)]
    rows += [{"name": f"guard_{mode}_{h:g}_r{radius}", "kind": "guard", "version": VERSION,
              "height_m": h, "span_m": .08, "below_plane_m": .12,
              "protect_radius_cells": radius, "protect_mode": mode}
             for h in (.04, .08, .12) for radius in (1, 2) for mode in ("foreground", "vulnerable")]
    return rows


def candidate_mask(prediction, lidar, geometry, config, previous):
    p, valid = prediction["probabilities"], prediction["valid"].astype(bool)
    obstacle = obstacle_presence(p, valid)["occupancy"] == 2
    kind = config["kind"]
    if kind == "baseline":
        return np.zeros_like(valid)
    if kind in ("plane", "guard"):
        return ground_candidates(lidar, config, geometry=geometry, prediction=prediction)
    if kind == "confidence":
        score = p[list(OBSTACLE_CLASSES)].max(0).astype(np.float32)
        ground = p[[11, 12, 13, 14]].max(0).astype(np.float32)
        return score / np.maximum(score + ground, 1e-7) < config["threshold"]
    if kind == "component":
        count, labels, stats, _ = cv2.connectedComponentsWithStats(obstacle.astype(np.uint8), 8)
        small = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] < config["minimum_cells"])
        return np.isin(labels, small[small != 0])
    if kind == "temporal":
        if previous is None:
            return np.zeros_like(valid)
        probs, observed = warp_to_ego(previous, prediction["pose"])
        old = (obstacle_presence(probs, observed)["occupancy"] == 2).astype(np.uint8)
        return obstacle & ~cv2.dilate(old, np.ones((3, 3), np.uint8)).astype(bool)
    if kind == "surfaces":
        flat = valid & (lidar[4] <= .05)
        heights = geometry[1]
        modes = []
        for centre in np.arange(-.025, .351, .025):
            support = flat & (np.abs(heights - centre) <= .015)
            coords = np.argwhere(support)
            if len(coords) >= 200 and np.ptp(coords, axis=0).max() * .4 >= 12:
                modes.append(float(np.median(heights[support])))
        return flat & np.any([np.abs(geometry[0] - h) <= config["band_m"]
                              for h in modes], axis=0) if modes else np.zeros_like(valid)
    if kind == "local":
        plane = lidar[3] - geometry[1]
        floor = np.where(valid, lidar[3], plane)
        opened = maximum_filter(minimum_filter(floor, 11, mode="nearest"), 11, mode="nearest")
        local = np.clip(opened, plane - .1, plane + .25)
        return (valid & (np.abs(lidar[2] - local) <= config["height_m"])
                & (lidar[4] <= .08))
    raise ValueError(kind)


def add_metrics(total, occupancy, truth, crop):
    obs, valid = occupancy == 2, occupancy != 0
    for prefix, roi in (("full", np.ones((200, 200), bool)), ("crop", crop)):
        for group in ("road", "ground", "obstacle", "target"):
            mask = truth[group] & roi
            total[prefix + "_" + group + "_cells"] += int(mask.sum())
            total[prefix + "_" + group + "_obstacle"] += int((mask & obs).sum())
            total[prefix + "_" + group + "_unknown"] += int((mask & ~valid).sum())
        target = truth["target"] & roi
        assessable = target.sum() >= 3
        total[prefix + "_assessable_frames"] += int(assessable)
        total[prefix + "_detected_frames"] += int(assessable and (target & obs).sum() / target.sum() >= .5)
        _, _, stats, _ = cv2.connectedComponentsWithStats((target & obs).astype(np.uint8), 8)
        largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if len(stats) > 1 else 0
        total[prefix + "_region_detected_frames"] += int(assessable and largest >= 3)
    total["frames"] += 1


def ratios(total):
    result = dict(total)
    for prefix in ("full", "crop"):
        for group in ("road", "ground", "obstacle", "target"):
            count = total[prefix + "_" + group + "_cells"]
            result[prefix + "_" + group + "_obstacle_rate"] = total[prefix + "_" + group + "_obstacle"] / count if count else None
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenarios", nargs="+", default=list(DEVELOPMENT))
    parser.add_argument("--configs", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    configs = json.loads(args.configs.read_text()) if args.configs else candidates()
    protocol = {"development_scenarios": DEVELOPMENT, "confirmation_scenarios": CONFIRMATION,
        "selection": "Development only: no loss of per-scenario target-detected frames under either >=50% overlap or >=3 connected overlap criterion; at most 1% relative target-hit-cell loss and 2% all-obstacle-hit loss. Prefer >=80% full-road-FP reduction. Initial 33 methods failed that goal; practical fallback requires >=50% without relaxing any detection/unknown constraint. Choose fewest full-road FP cells, then fewest changed cells. Freeze before confirmation. No tuning on confirmation.",
        "scope": "Paired saved input; GT for scoring only; correlated video frames, one anchor/scene, not independent trials or full-map GT. Ordinary LiDAR only enters corrections; no actor IDs, map, semantic sensor or target ROI.",
        "configs": configs, "input_index_sha256": hashlib.sha256(args.index.read_bytes()).hexdigest()}
    (args.output / "protocol.json").write_text(json.dumps(protocol, indent=2))
    totals, frames = defaultdict(lambda: defaultdict(int)), []
    start = time.monotonic()
    for row in json.loads(args.index.read_text())["rows"]:
        scenario = row["scenario"]
        if scenario not in args.scenarios:
            continue
        root = Path(row["source_archive"])
        observer = next(n for n in row["actors"] if n != "ego")
        previous = {}
        for folder in sorted(root.glob("round_*")):
            metadata = json.loads((folder / "video_frame.json").read_text())
            own, peer = load(folder / "ego_own.npz"), load(folder / f"ego_received_{observer}.npz")
            original_fused = load(folder / "ego_fused.npz")
            peer_occ, peer_valid = warp_to_ego(peer, own["pose"])
            evidence = [load(folder / f"{n}_eval_only_semantic.npz") for n in ("ego", observer)]
            truth_metadata = json.loads((folder / "ground_truth.json").read_text())
            if truth_metadata["frame_id"] != metadata["frame_id"] or truth_metadata["capture_sim_ns"] != metadata["capture_sim_ns"]:
                raise ValueError("Semantic/source frame mismatch.")
            truth = masks(evidence, own["pose"], row["target_ids"])
            x, y, width = metadata["crop_grid"]
            crop = np.zeros((200, 200), bool); crop[x:x+width, y:y+width] = True
            inputs, geo = {}, {}
            for node in ("ego", observer):
                with np.load(folder / f"{node}_model_inputs.npz", allow_pickle=False) as data:
                    if int(data["frame_ids"][-1]) != metadata["frame_id"]:
                        raise ValueError("Model/source frame mismatch.")
                    inputs[node] = data["lidar_bev"][0]
                geo[node] = geometry_features(inputs[node])
            for config in configs:
                predictions, changes = [], []
                tick = time.perf_counter_ns()
                for node, pred in (("ego", own), (observer, peer)):
                    mask = candidate_mask(pred, inputs[node], geo[node], config, previous.get(node))
                    corrected, changed = lower_obstacle_evidence(pred, mask)
                    predictions.append(corrected); changes.append(int(changed.sum()))
                ego, remote = predictions
                remote_p, remote_v = warp_to_ego(remote, ego["pose"])
                ego_o = obstacle_presence(ego["probabilities"], ego["valid"])["occupancy"]
                remote_o = obstacle_presence(remote_p, remote_v)["occupancy"]
                fused_o = np.where(ego["valid"].astype(bool), ego_o, remote_o)
                if config["kind"] == "baseline":
                    expected = obstacle_presence(original_fused["probabilities"], original_fused["valid"])["occupancy"]
                    if not np.array_equal(fused_o, expected):
                        raise ValueError("Baseline label replay not equal to actual saved fusion.")
                key = (config["name"], scenario)
                add_metrics(totals[key], fused_o, truth, crop)
                totals[key]["changed_cells"] += sum(changes)
                totals[key]["postprocess_ns_excluding_geometry"] += time.perf_counter_ns() - tick
                target = truth["target"] & crop
                frames.append({"scenario": scenario, "folder": str(folder), "candidate": config["name"],
                    "frame_id": metadata["frame_id"], "target_cells": int(target.sum()),
                    "target_hit": int(((fused_o == 2) & target).sum()),
                    "road_fp": int(((fused_o == 2) & truth["road"] & crop).sum())})
            previous = {"ego": own, observer: peer}
            if int(folder.name[-3:]) % 20 == 0:
                print(f"{scenario} {folder.name} elapsed={time.monotonic()-start:.1f}s", flush=True)
        print(f"{scenario} complete", flush=True)
    output = {"protocol": protocol, "elapsed_seconds": time.monotonic()-start,
        "rows": [{"candidate": name, "scenario": scenario, **ratios(total)}
                 for (name, scenario), total in totals.items()]}
    (args.output / "results.json").write_text(json.dumps(output, indent=2))
    (args.output / "frames.json").write_text(json.dumps(frames, indent=2))
    print(args.output / "results.json", flush=True)


if __name__ == "__main__":
    main()
