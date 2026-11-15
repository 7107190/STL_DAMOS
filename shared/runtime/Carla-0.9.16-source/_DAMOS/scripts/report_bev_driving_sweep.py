#!/usr/bin/env python3
"""Render immutable driving evidence; no simulator, model or actor changes."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from bev_evaluation import points_mask
from bev_occupancy import render_presence
from run_bev_driving_sweep import aggregate


def load_prediction(path):
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def evaluations(report):
    return [(folder, json.loads((folder / "evaluation.json").read_text()))
            for folder in sorted(report.glob("round_*"))
            if (folder / "evaluation.json").exists()]


def region_statistics(items):
    groups = {}
    for _, evaluation in items:
        for anchor in evaluation["anchors"]:
            key = (anchor["scenario"], anchor["anchor_index"])
            row = groups.setdefault(key, {"scenario": key[0], "anchor_index": key[1],
                "assessable_frames": 0, "ego_detected_frames": 0,
                "fused_detected_frames": 0, "hidden_part_recovery_frames": 0,
                "new_detection_frames": 0,
                "member_actor_ids": anchor["member_actor_ids"]})
            if row["member_actor_ids"] != anchor["member_actor_ids"]:
                raise ValueError("Pinned anchor members changed between frames.")
            row["assessable_frames"] += int(anchor["assessable"])
            row["ego_detected_frames"] += int(anchor["ego"]["detected"] is True)
            row["fused_detected_frames"] += int(anchor["fused"]["detected"] is True)
            row["hidden_part_recovery_frames"] += int(anchor["hidden_part_recovered"])
            row["new_detection_frames"] += int(anchor.get("new_situation_presence", False))
    return list(groups.values())


def representative(items):
    # Fixed first-assessable selection, never the best prediction frame.
    for folder, evaluation in items:
        anchor = next((a for a in evaluation["anchors"] if a["assessable"]), None)
        if anchor is not None:
            return folder, evaluation, anchor
    folder, evaluation = items[0]
    return folder, evaluation, evaluation["anchors"][0]


def resolve_attempts(primary, retries):
    rows = {row["scenario"]: row for row in primary}
    if len(rows) != len(primary):
        raise ValueError("Duplicate primary scenarios: no silent successful-run selection.")
    for retry in retries:
        scenario = retry["scenario"]
        if scenario not in rows:
            raise ValueError("Retry requires one matching failed primary attempt.")
        original = rows[scenario]
        if original.get("returncode", 0)==0 or original.get("rounds", 0)!=0:
            raise ValueError("Only pre-evaluation infrastructure failures may be retried here.")
        rows[scenario] = dict(retry, initial_failed_attempt=original,
            retry_reason="Pre-evaluation infrastructure failure; first failed log retained")
    return sorted(rows.values(), key=lambda r: int(r["scenario"][1:]))


def anchor_mask(folder, own, members):
    result = np.zeros((200, 200), bool)
    for path in sorted(folder.glob("*_eval_only_semantic.npz")):
        with np.load(path, allow_pickle=False) as data:
            hits = data["world_xyz"][np.isin(data["actor_ids"], members)]
        result |= points_mask(hits, own["pose"])
    return result


def text(image, line, xy, scale=.6, color=(240, 240, 240)):
    cv2.putText(image, line, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def render_region(folder, evaluation, anchor, size=320, selection_label="First assessable frame"):
    own = load_prediction(folder / "ego_own.npz")
    fused = load_prediction(folder / "ego_fused.npz")
    mask = anchor_mask(folder, own, anchor["member_actor_ids"])
    indices = np.argwhere(mask)
    width = min(200, max(30, int(np.ptp(indices, axis=0).max()) + 12)) if len(indices) else 200
    centre = indices.mean(0).astype(int) if len(indices) else np.array([100, 100])
    x0, y0 = np.clip(centre - width // 2, 0, 200 - width)
    gt = np.full((200, 200, 3), 28, np.uint8)
    gt[mask] = (0, 180, 255)
    images = [np.ascontiguousarray(gt[::-1, ::-1]), render_presence(own, 200), render_presence(fused, 200)]
    panels = []
    for title, full in zip(("TARGET RETURNS: GT", "EGO ONLY", "EGO + ZKP PEERS"), images):
        crop = full[200-x0-width:200-x0, 200-y0-width:200-y0]
        image = cv2.resize(crop, (size, size), interpolation=cv2.INTER_NEAREST)
        local = mask[x0:x0+width, y0:y0+width][::-1, ::-1].astype(np.uint8)
        contours, _ = cv2.findContours(cv2.resize(local, (size, size), interpolation=cv2.INTER_NEAREST),
                                       cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(image, contours, -1, (255, 255, 255), 1)
        header = np.full((34, size, 3), 20, np.uint8)
        text(header, title, (8, 23), .48)
        panels.append(np.vstack([header, image]))
    header = np.full((105, size*3, 3), 20, np.uint8)
    s = anchor["scenario"]
    text(header, f"{s} | anchor {anchor['anchor_index']} | F{evaluation['frame_id']} | same {width*.4:g}m crop", (12, 27), .72)
    text(header, f"{selection_label}: ego={anchor['ego']['detected']} / fused={anchor['fused']['detected']}", (12, 56))
    road = evaluation["ego_road_state"]
    text(header, f"Driving lane={road['on_driving_lane']} | speed={road['speed_mps']*3.6:.1f} km/h", (12, 83))
    footer = np.full((58, size*3, 3), 20, np.uint8)
    text(footer, "Orange=obstacle; gray=ground; purple=uncertain; dark=unobserved", (12, 23), .55)
    text(footer, "White=GT outline only. Raw cells, no interpolation. Not a scenario classifier.", (12, 46), .52)
    return np.vstack([header, np.hstack(panels), footer])


def render_rgb(folder, evaluation, anchor):
    targets = [o for o in evaluation["objects"] if o["actor_id"] in anchor["member_actor_ids"]]
    counts, projected = {}, {}
    for obj in targets:
        for node, cameras in obj["rgb_target_pixels"].items():
            for name, value in cameras.items():
                counts[node, name] = counts.get((node, name), 0) + value
        for name, value in (obj["ego_projected_target_points_in_fov"] or {}).items():
            projected[name] = projected.get(name, 0) + value
    ego = max(((v, name) for (node, name), v in counts.items() if node=="ego"), default=(0, "front"))
    if ego[0]==0 and projected:
        ego = (0, max(projected, key=projected.get))
    peer = max(((v, node, name) for (node, name), v in counts.items() if node!="ego"), default=None)
    if peer is None:
        return None
    panels = []
    for node, name in (("ego", ego[1]), (peer[1], peer[2])):
        path = folder / f"{node}_rgb_{name}.png"
        image = cv2.imread(str(path))
        if image is None:
            return None
        with np.load(folder / f"{node}_eval_only_instance.npz", allow_pickle=False) as data:
            if int(data["frame_id"]) != evaluation["frame_id"]:
                raise ValueError("RGB evaluation frame mismatch.")
            index = list(data["names"]).index(name)
            mask = np.isin(data["ids"][index], anchor["member_actor_ids"]).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(image, contours, -1, (0, 255, 255), 2)
        header = np.full((80, 800, 3), 20, np.uint8)
        text(header, f"{anchor['scenario']} anchor {anchor['anchor_index']} | {node} {name} | F{evaluation['frame_id']}", (12, 29), .64)
        text(header, f"Target pixels={int(mask.sum())}; yellow outline=GT only", (12, 60), .6)
        panels.append(np.vstack([header, image]))
    return np.hstack(panels)


def render_driving_map(report, folder, evaluation, output):
    import carla
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    root = Path(__file__).resolve().parents[2]
    road_map = carla.Map("Town10HD_Opt", (root / "Scenic/Maps/Town10HD_Opt.xodr").read_text())
    lanes = {}
    for point in road_map.generate_waypoints(2.):
        if point.lane_type == carla.LaneType.Driving:
            lanes.setdefault((point.road_id, point.section_id, point.lane_id), []).append(point)
    polygons = []
    for points in lanes.values():
        points.sort(key=lambda w: w.s)
        if len(points) < 2:
            continue
        centres = np.array([[p.transform.location.x, p.transform.location.y] for p in points])
        angles = np.deg2rad([p.transform.rotation.yaw for p in points])
        offsets = np.column_stack([-np.sin(angles), np.cos(angles)]) * np.array([p.lane_width/2 for p in points])[:, None]
        polygons.append(np.vstack([centres+offsets, (centres-offsets)[::-1]]))
    fig, ax = plt.subplots(figsize=(9, 9), layout="constrained")
    ax.add_collection(PolyCollection(polygons, facecolors="#cdd2d2", edgecolors="#b7bbbb", linewidths=.25))
    trace = json.loads((report / "ego_driving_trace.json").read_text())
    xy = np.array([r["location_carla"][:2] for r in trace])
    if len(xy):
        ax.plot(xy[:, 0], xy[:, 1], color="#1461ce", linewidth=2, label="Actual ego trace")
        ax.scatter(*xy[0], marker="o", color="#1461ce", s=45)
        ax.scatter(*xy[-1], marker="x", color="#1461ce", s=65)
    truth = json.loads((folder / "ground_truth.json").read_text())
    for anchor in evaluation["anchors"]:
        members = [t for t in truth["targets"] if t["actor_id"] in anchor["member_actor_ids"]]
        centres = [t["location_model_world"] if "location_model_world" in t else
                   np.asarray(t["vertices_model_world"]).mean(0) for t in members]
        centre = np.asarray(centres)[:, :2].mean(0); centre[1] *= -1
        ax.scatter(*centre, color="#e27416", marker="s", s=42)
        ax.annotate(f"A{anchor['anchor_index']}", centre, xytext=(5, 5), textcoords="offset points")
    ax.autoscale(); ax.set_aspect("equal"); ax.invert_yaxis()
    ax.set_xlabel("CARLA x (m)"); ax.set_ylabel("CARLA y (m)")
    ax.set_title(f"{evaluation['anchors'][0]['scenario']} | whole Town10HD Driving lanes\nOrange=region centre at F{evaluation['frame_id']}; blue=recorded ego trajectory")
    ax.legend(loc="upper left"); ax.grid(alpha=.15)
    fig.savefig(output, dpi=160); plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep-summaries", nargs="+", type=Path, required=True)
    parser.add_argument("--retry-summaries", nargs="+", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = [row for path in args.sweep_summaries for row in json.loads(path.read_text())["rows"]]
    retries = [row for path in args.retry_summaries for row in json.loads(path.read_text())["rows"]]
    rows = resolve_attempts(rows, retries)
    tiles, records = [], []
    for original in rows:
        report = Path(original["report"]) if original.get("report") else None
        items = evaluations(report) if report is not None else []
        if not items:
            tile = np.full((585, 960, 3), 20, np.uint8)
            text(tile, f"{original['scenario']} | NO EVALUATION FRAMES", (24, 60), 1., (90, 90, 255))
            text(tile, "Not a detection success. See original log/summary.", (24, 105), .7)
            tiles.append(tile)
            records.append(dict(original, diagnostic_status="not_evaluated"))
            continue
        result = dict(original, **aggregate(report))
        result["anchors"] = region_statistics(items)
        result["per_round"] = [{"frame_id":e["frame_id"], "assessable":e["anchor_assessable"],
            "ego":e["anchor_ego_detected"], "fused":e["anchor_fused_detected"],
            "hidden_recovery":e["anchor_hidden_parts_recovered"],
            "new_detections":e["anchor_new_detections"],
            "ego_road_state":e["ego_road_state"], "scene_presence":e["scene_presence"]} for _, e in items]
        folder, evaluation, anchor = representative(items)
        result["representative"] = {"folder":str(folder), "frame_id":evaluation["frame_id"],
            "anchor_index":anchor["anchor_index"], "selection":"First assessable frame/anchor, not best prediction"}
        tile = render_region(folder, evaluation, anchor)
        stats = np.full((68, tile.shape[1], 3), 20, np.uint8)
        text(stats, f"Whole run: observed {result['anchors_assessable']}/{result['anchors_total']} regions; ego/fused ever={result['anchors_ego_detected']}/{result['anchors_fused_detected']}", (12, 26), .62)
        text(stats, f"Fused region-frames: {result['region_frames_fused_detected']}/{result['region_frames_assessable']} | mean {result['mean_ms']/1000:.2f}s", (12, 53), .6)
        tiles.append(np.vstack([tile, stats]))
        cv2.imwrite(str(args.output / f"{original['scenario']}_presence.png"), tiles[-1])
        rgb = render_rgb(folder, evaluation, anchor)
        if rgb is not None:
            cv2.imwrite(str(args.output / f"{original['scenario']}_rgb.png"), rgb)
        render_driving_map(report, folder, evaluation, args.output / f"{original['scenario']}_map.png")
        for event_folder,event_evaluation in items:
            regression = next((a for a in event_evaluation["anchors"]
                if a["ego"]["detected"] is True and a["fused"]["detected"] is False), None)
            if regression is not None:
                result["fusion_regression_example"] = {"frame_id":event_evaluation["frame_id"],
                    "anchor_index":regression["anchor_index"],"folder":str(event_folder),
                    "selection":"First ego-success/fused-failure region-frame, separate from representative"}
                cv2.imwrite(str(args.output / f"{original['scenario']}_fusion_regression.png"),
                    render_region(event_folder,event_evaluation,regression,selection_label="Fusion regression frame"))
                break
        records.append(result)
    while len(tiles) % 3:
        tiles.append(np.zeros_like(tiles[0]))
    cv2.imwrite(str(args.output / "S1_S9_presence.png"), np.vstack([
        np.hstack(tiles[index:index+3]) for index in range(0, len(tiles), 3)]))
    (args.output / "summary.json").write_text(json.dumps({"rows":records, "metric2_pass":None,
        "limitations":["Single run per scenario, five correlated frames; not repeated independent trials",
            "GT only for evaluation/visual outlines; not fed to model, packet, verification or fusion",
            "Only observed regions evaluated; partial ROI is not full-map success",
            "Obstacle presence, not abnormal behavior/scenario type classification",
            "Driving lane checks use ego centre, not complete wheel/vehicle footprint",
            "Diagnostic connected-cell threshold is not a validated detection operating point"]}, indent=2))
    print(args.output)


if __name__ == "__main__":
    main()
