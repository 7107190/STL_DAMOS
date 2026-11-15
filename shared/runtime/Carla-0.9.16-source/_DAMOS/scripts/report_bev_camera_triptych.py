#!/usr/bin/env python3
"""Same-frame 2x3 evidence: ego/observer/fused BEVs above actual RGB views."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

from audit_bev_candidate_live import audit_round
from bev_evaluation import camera_projection_counts, local_points
from bev_fusion import warp_to_ego
from bev_fusion_diagnostic import load_npz
from bev_occupancy import (draw_ego_pose, ego_visible_crop, project_ego_pose,
                           render_presence, render_presence_focus)
from bev_sensor_rig import CAMERAS
from m2x.security import real_backend
from report_bev_occlusion_triptych import (BACKGROUND, BLUE, MUTED, ORANGE, TEXT,
    collect_candidates, focused_mask, label, select_candidate, target_crop)

SIZE, GAP, MARGIN = 640, 10, 12
WIDTH, HEIGHT = 3 * SIZE + 2 * GAP + 2 * MARGIN, 1320


def project_overhead(world_model, metadata):
    points = np.asarray(world_model, np.float64).reshape(-1, 3) * [1, -1, 1]
    inverse = np.linalg.inv(np.asarray(metadata["camera_to_world_carla"]))
    camera = points @ inverse[:3, :3].T + inverse[:3, 3]
    optical = camera[:, [1, 2, 0]] * [1, -1, 1]
    projected = optical @ np.asarray(metadata["intrinsic"]).T
    uv = projected[:, :2] / np.maximum(projected[:, 2:3], 1e-8)
    valid = ((optical[:, 2] > .1) & (uv[:, 0] >= 0) & (uv[:, 0] < metadata["width"])
             & (uv[:, 1] >= 0) & (uv[:, 1] < metadata["height"]))
    return uv, valid


def context_bounds(points, width, height):
    points = np.asarray(points)
    low, high = points.min(0) - 45, points.max(0) + 45
    span = high - low
    w = max(240., span[0], span[1] * 16 / 9)
    h = w * 9 / 16
    if w > width or h > height:
        return 0, 0, width, height
    centre = (low + high) / 2
    x = np.clip(centre[0] - w / 2, 0, width - w)
    y = np.clip(centre[1] - h / 2, 0, height - h)
    return int(x), int(y), int(x + w), int(y + h)


def choose_camera(counts, visible):
    order = ["cam_front", *[name for name in CAMERAS if name != "cam_front"]]
    geometry = counts or {}
    if any(geometry.values()):
        return max(order, key=lambda name: geometry.get(name, 0)), "target geometry in FOV"
    if any(visible.values()):
        return max(order, key=lambda name: visible.get(name, 0)), "target instance visible"
    return "cam_front", "no target FOV evidence; front fallback"


def camera_view(folder, node, obj, hits, pose, frame):
    visible = obj["rgb_target_pixels"].get(node, {})
    projected = camera_projection_counts(folder, node, hits, pose, frame)
    name, basis = choose_camera(projected, visible)
    path = folder / f"{node}_rgb_{name}.png"
    image = cv2.imread(str(path))
    if image is None or image.shape[:2] != (450, 800):
        raise ValueError("Missing actual same-frame actor RGB.")
    instances = load_npz(folder / f"{node}_eval_only_instance.npz")
    if int(instances["frame_id"]) != frame:
        raise ValueError("RGB instance frame does not match BEV.")
    index = list(instances["names"]).index(name)
    mask = (instances["ids"][index] == obj["actor_id"]).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(image, contours, -1, (0, 225, 255), 2)
    if not mask.any() and len(hits):
        inputs = load_npz(folder / f"{node}_model_inputs.npz")
        k = list(CAMERAS).index(name)
        optical = (local_points(hits, pose) - inputs["trans"][0, -1, k]) @ inputs["rots"][0, -1, k]
        points = optical @ inputs["intrins"][0, -1, k].T
        uv = points[:, :2] / np.maximum(points[:, 2:3], 1e-8)
        valid = ((optical[:, 2] > .1) & (uv[:, 0] >= 0) & (uv[:, 0] < 800)
                 & (uv[:, 1] >= 0) & (uv[:, 1] < 450))
        if valid.any():
            u, v = np.median(uv[valid], axis=0).round().astype(int)
            cv2.drawMarker(image, (int(u), int(v)), (255, 185, 74), cv2.MARKER_CROSS, 24, 2)
    rgb = Image.fromarray(image[:, :, ::-1]).resize((SIZE, 360), Image.Resampling.LANCZOS)
    return rgb, {"node": node, "camera": name, "frame_id": frame, "selection": basis,
        "target_pixels": int(mask.sum()), "image": str(path),
        "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def bev_view(prediction, mask, crop, *, context=False, ego_pose=None):
    if context:
        return Image.fromarray(render_presence_focus(prediction, mask, crop, SIZE, ego_pose=ego_pose)[:, :, ::-1])
    x0, y0, width = crop
    full = render_presence(prediction, size=200)
    image = full[200 - x0 - width:200 - x0, 200 - y0 - width:200 - y0]
    image = cv2.resize(image, (SIZE, SIZE), interpolation=cv2.INTER_NEAREST)
    local = mask[x0:x0 + width, y0:y0 + width][::-1, ::-1].astype(np.uint8)
    local = cv2.resize(local, (SIZE, SIZE), interpolation=cv2.INTER_NEAREST)
    contours, _ = cv2.findContours(local, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(image, contours, -1, (255, 255, 255), 1)
    if ego_pose is not None:
        image, _ = draw_ego_pose(image, ego_pose, prediction["pose"], crop)
    return Image.fromarray(image[:, :, ::-1])


def overhead_view(folder, own, peer, obj, frame, stamp):
    metadata = json.loads((folder / "overhead_capture.json").read_text())
    path = folder / metadata["image"]
    if (metadata["frame_id"] != frame or abs(metadata["capture_sim_ns"] - stamp) > 1000
            or hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]
            or metadata["fed_to_model_or_fusion"]):
        raise ValueError("Wrong-frame or modified overhead camera.")
    image = Image.open(path).convert("RGB")
    if image.size != (metadata["width"], metadata["height"]):
        raise ValueError("Overhead image dimensions changed.")
    positions = np.asarray([own["pose"][:3, 3], peer["pose"][:3, 3], obj["location_model_world"]])
    uv, valid = project_overhead(positions, metadata)
    if not valid[2]:
        raise ValueError("Selected target outside overhead camera; not a valid context view.")
    bounds = context_bounds(uv[valid], *image.size)
    draw = ImageDraw.Draw(image)
    for name, point, inside, color, offset in zip(("E", "O", "T"), uv, valid,
            (BLUE, (65, 215, 183), ORANGE), ((-25, 10), (12, -30), (12, 10))):
        if inside:
            x, y = point
            draw.ellipse((x - 8, y - 8, x + 8, y + 8), outline=color, width=2)
            label(image, (x + offset[0], y + offset[1]), name, 22, color, True)
    image = ImageOps.pad(image.crop(bounds), (SIZE, 360), color=BACKGROUND, method=Image.Resampling.LANCZOS)
    return image, dict(metadata, crop_pixels=list(bounds), pose_markers="GT/saved pose projections only",
                       ego_in_frame=bool(valid[0]), observer_in_frame=bool(valid[1]), target_in_frame=bool(valid[2]))


def render(folder, row, summary):
    own = load_npz(folder / "ego_own.npz")
    peer = load_npz(folder / f"ego_received_{row['observer']}.npz")
    fused = load_npz(folder / "ego_fused.npz")
    evaluation = json.loads((folder / "evaluation.json").read_text())
    obj = next(obj for obj in evaluation["objects"] if obj["actor_id"] == row["actor_id"])
    mask = focused_mask(folder, own, row["actor_id"])
    if mask.any():
        crop = target_crop(mask)
    else:
        local = local_points(np.asarray([obj["location_model_world"]]), own["pose"])[0]
        start = np.clip(np.floor((local[:2] + 40) / .4).astype(int) - 25, 0, 150)
        crop = int(start[0]), int(start[1]), 50
    hits = []
    for path in sorted(folder.glob("*_eval_only_semantic.npz")):
        data = load_npz(path)
        hits.append(data["world_xyz"][data["actor_ids"] == row["actor_id"]])
    hits = np.concatenate(hits)
    own_rgb, ego_record = camera_view(folder, "ego", obj, hits, own["pose"], row["frame_id"])
    original_peer_pose = load_npz(folder / f"{row['observer']}_own.npz")["pose"]
    peer_rgb, peer_record = camera_view(folder, row["observer"], obj, hits, original_peer_pose, row["frame_id"])
    overhead, overhead_record = overhead_view(folder, own, peer, obj, row["frame_id"], evaluation["capture_sim_ns"])
    probs, valid = warp_to_ego(peer, own["pose"])
    aligned = {"probabilities": probs, "valid": valid, "pose": own["pose"]}
    crop = ego_visible_crop(crop, own["pose"], own["pose"])
    views = [bev_view(prediction, mask, crop, ego_pose=own["pose"]) for prediction in (own, aligned, fused)]
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    label(canvas, (MARGIN, 10), f"{row['scenario']}  |  frame {row['frame_id']}  |  anchor {row['anchor_index']}  |  target actor {row['actor_id']}", 29, bold=True)
    label(canvas, (MARGIN, 52), f"Same-frame BEV + RGB  |  target {row['distance_m']:.2f} m  |  ego {row['speed_kmh']:.1f} km/h  |  BEV crop {crop[2] * .4:g} m", 22, MUTED)
    status = "OCCLUSION VERIFIED" if row["occlusion_verified"] else "OCCLUSION NOT VERIFIED"
    label(canvas, (MARGIN, 87), status, 22, (65, 215, 183) if row["occlusion_verified"] else (255, 198, 96), True)
    actor = summary["actors"][row["observer"]]
    titles = ("EGO BEV", f"OBSERVER {actor['actor_id']} BEV", "FUSED BEV")
    camera_titles = (f"EGO RGB / {ego_record['camera']}", f"OBSERVER RGB / {peer_record['camera']}", "ACTUAL OVERHEAD RGB / SAME FRAME")
    camera_notes = (f"Visible target pixels: {ego_record['target_pixels']}",
                    f"Visible target pixels: {peer_record['target_pixels']}", "E=ego / O=observer / T=target (GT markers)")
    for index, (title, bev, rgb, rgb_title, note) in enumerate(zip(titles, views,
            (own_rgb, peer_rgb, overhead), camera_titles, camera_notes)):
        x = MARGIN + index * (SIZE + GAP)
        label(canvas, (x, 127), title, 23, bold=True)
        detail = "Own prediction" if index == 0 else "Verified received BEV / ego axes" if index == 1 else "All verified nearby observers / ego axes"
        label(canvas, (x, 160), detail, 19, MUTED)
        canvas.paste(bev, (x, 192))
        label(canvas, (x, 846), rgb_title, 19, bold=True)
        label(canvas, (x, 876), note, 18, MUTED)
        canvas.paste(rgb, (x, 908))
    label(canvas, (MARGIN, 1279), "Orange=all predicted obstacles; gray=ground; dark=unobserved; red EGO+arrow=recorded ego pose. White/yellow outlines=GT, not detections.", 18, MUTED)
    return canvas, {"ego_camera": ego_record, "observer_camera": peer_record,
        "overhead_camera": overhead_record, "crop_grid": list(crop), "raw_target_return_cells": int(mask.sum()),
        "ego_marker": project_ego_pose(own["pose"], own["pose"], crop, SIZE),
        "source_run_fatal_error": summary["fatal_error"], "source_rounds_completed": len(summary["rounds"]),
        "source_rounds_requested": summary["rounds_requested"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweeps", nargs="+", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--retain-root", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    args.retain_root.mkdir(parents=True, exist_ok=False)
    checkpoint_hash = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    backend = real_backend(60)
    source_hashes, records, seen = {}, [], set()
    for sweep_path in args.sweeps:
        source_hashes[str(sweep_path)] = hashlib.sha256(sweep_path.read_bytes()).hexdigest()
        for run in json.loads(sweep_path.read_text())["rows"]:
            scenario = run["scenario"]
            if scenario in seen:
                raise ValueError("Duplicate scenarios; do not silently select the best run.")
            seen.add(scenario)
            report = Path(run["report"])
            for filename in ("summary.json", "ego_driving_trace.json"):
                path = report / filename
                source_hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            candidates, summary, source = collect_candidates(report, scenario, include_unassessable=True)
            source_hashes.update(source)
            row = select_candidate(candidates)
            folder = Path(row["folder"])
            audit = audit_round(folder, summary, checkpoint_hash, backend, fusion_mode="ego_observed", calibrated=True)
            image, camera_records = render(folder, row, summary)
            for key in ("ego_camera", "observer_camera"):
                camera = camera_records[key]
                source_hashes[camera["image"]] = camera["image_sha256"]
            camera = camera_records["overhead_camera"]
            source_hashes[str(folder / camera["image"])] = camera["sha256"]
            output_name = f"{scenario}_bev_rgb_2x3.png"
            image.save(args.output / output_name)
            retained = args.retain_root / report.name
            retained.mkdir()
            shutil.copytree(folder, retained / folder.name)
            for filename in ("summary.json", "ego_driving_trace.json"):
                shutil.copy2(report / filename, retained / filename)
            (retained / "frame_selection.json").write_text(json.dumps(candidates, indent=2))
            record = dict(row, **camera_records, image=output_name, source_run_returncode=run["returncode"],
                retained_folder=str(retained / folder.name), historical_proof_audit=audit,
                selection="Closest saved occlusion-verified target; otherwise closest saved target with its assigned received observer. Prediction success is not a selection input.",
                candidates=candidates)
            records.append(record)
            print(f"[CAMERA FIGURE] {scenario} F{row['frame_id']} ego={camera_records['ego_camera']['camera']} "
                  f"observer={camera_records['observer_camera']['camera']} overhead=same-frame", flush=True)
    unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in source_hashes.items())
    if not unchanged:
        raise RuntimeError("Capture sources changed during rendering.")
    result = {"rows": records, "source_files_unchanged": unchanged, "source_sha256": source_hashes,
        "checkpoint_sha256": checkpoint_hash, "metric2_pass": None,
        "source": "New actual synchronized CARLA captures, not synthetic/reconstructed old topdown images",
        "limitations": ["Nearest among saved input frames, not continuous closest-approach measurement",
            "RGB camera direction chosen from target geometry/FOV, not necessarily cam_front",
            "GT overlays are visualization only; no model, packet, fusion or scores changed",
            "Full scene/occlusion claim applies only where explicitly verified",
            "Partial failed runs are reported as such; valid earlier same-frame images do not imply complete-run success"]}
    (args.output / "summary.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"scenarios": len(records), "source_files_unchanged": unchanged,
                      "output": str(args.output), "retained_source": str(args.retain_root)}), flush=True)


if __name__ == "__main__":
    main()
