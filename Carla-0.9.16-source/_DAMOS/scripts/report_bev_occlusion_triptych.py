#!/usr/bin/env python3
"""Render frozen ego/verified-observer/fused BEVs at geometry-selected frames."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from audit_bev_candidate_live import audit_round
from bev_anchor_evaluation import region_presence
from bev_evaluation import local_points, points_mask
from bev_fusion import warp_to_ego
from bev_fusion_diagnostic import load_npz
from bev_occupancy import render_presence
from m2x.security import real_backend

SIZE, GAP, MARGIN = 520, 10, 12
WIDTH = 3 * SIZE + 2 * GAP + 2 * MARGIN
BACKGROUND, TEXT = (18, 22, 26), (237, 241, 244)
MUTED, ORANGE, BLUE = (172, 184, 193), (255, 180, 0), (74, 185, 255)
FONT_ROOT = Path("/usr/share/fonts/truetype/dejavu")


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONT_ROOT / name), size)


def label(canvas, xy, value, size=20, color=TEXT, bold=False):
    ImageDraw.Draw(canvas).text(xy, str(value), font=font(size, bold), fill=color)


def select_candidate(candidates):
    if not candidates:
        raise ValueError("No assessable anchor with its verified assigned observer.")
    if any(not np.isfinite(row["distance_m"]) or row["distance_m"] < 0 for row in candidates):
        raise ValueError("Invalid target distance.")
    hidden = [row for row in candidates if row["occlusion_verified"]]
    pool = hidden or candidates
    return min(pool, key=lambda row: (row["distance_m"], row["frame_id"], row["anchor_index"], row["actor_id"]))


def target_crop(mask):
    indices = np.argwhere(mask)
    if not len(indices):
        raise ValueError("Focused target has no exact actor-ID returns in ego ROI.")
    low, high = indices.min(0), indices.max(0)
    width = min(200, max(50, int((high - low + 1).max()) + 16))
    start = np.clip((low + high + 1 - width) // 2, 0, 200 - width)
    return int(start[0]), int(start[1]), width


def collect_candidates(report, scenario, *, include_unassessable=False):
    summary = json.loads((report / "summary.json").read_text())
    if summary["backend"] != "REAL_GROTH16":
        raise ValueError("Expected actual live Groth16 evidence.")
    candidates, hashes = [], {}
    folders = sorted(report.glob("round_*"))
    for folder in folders:
        if not (folder / "evaluation.json").exists():
            continue
        for path in folder.iterdir():
            if path.suffix in (".npz", ".json"):
                hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        evaluation = json.loads((folder / "evaluation.json").read_text())
        truth = json.loads((folder / "ground_truth.json").read_text())
        peers = json.loads((folder / "peers.json").read_text())
        frame = evaluation["frame_id"]
        if truth["frame_id"] != frame or any(row["frame_id"] != frame for row in peers.values()):
            raise ValueError("Mixed source frames.")
        if not evaluation["ego_road_state"]["on_driving_lane"]:
            continue
        ego_pose = load_npz(folder / "ego_own.npz")["pose"]
        objects = {row["actor_id"]: row for row in evaluation["objects"]}
        targets = {row["actor_id"]: row for row in truth["targets"]}
        for anchor in evaluation["anchors"]:
            if anchor["scenario"] != scenario or (not anchor["assessable"] and not include_unassessable):
                continue
            suffix = f":anchor{anchor['anchor_index']}"
            assigned = [name for name, actor in summary["actors"].items()
                        if name != "ego" and actor["track_label"].endswith(suffix)]
            if len(assigned) != 1:
                raise ValueError("Ambiguous assigned observer.")
            observer = assigned[0]
            if observer not in peers["ego"]["verified_peer_ids"]:
                continue
            members = [objects[actor] for actor in anchor["member_actor_ids"] if objects[actor]["in_ego_roi"]]
            hidden = [row for row in members if row["occlusion_verified"]]
            for obj in hidden or members:
                distance = float(np.linalg.norm(np.asarray(targets[obj["actor_id"]]["location_model_world"][:2])
                                                - ego_pose[:2, 3]))
                candidates.append({"folder": str(folder), "scenario": scenario, "frame_id": frame,
                    "anchor_index": anchor["anchor_index"], "actor_id": obj["actor_id"],
                    "distance_m": distance, "occlusion_verified": bool(obj["occlusion_verified"]),
                    "observer": observer, "anchor": anchor,
                    "speed_kmh": evaluation["ego_road_state"]["speed_mps"] * 3.6})
    return candidates, summary, hashes


def focused_mask(folder, own, actor):
    mask = np.zeros((200, 200), bool)
    for path in sorted(folder.glob("*_eval_only_semantic.npz")):
        data = load_npz(path)
        mask |= points_mask(data["world_xyz"][data["actor_ids"] == actor], own["pose"])
    return mask


def raw_images(prediction, mask, crop):
    full = render_presence(prediction, size=200)
    outlined = full.copy()
    contours, _ = cv2.findContours(mask[::-1, ::-1].astype(np.uint8), cv2.RETR_EXTERNAL,
                                  cv2.CHAIN_APPROX_SIMPLE)
    # Enlarge first so an outline cannot paint over a small target's entire cell.
    full = cv2.resize(full, (SIZE, SIZE), interpolation=cv2.INTER_NEAREST)
    for contour in contours:
        points = np.rint((contour + .5) * SIZE / 200).astype(np.int32)
        cv2.drawContours(full, [points], -1, (255, 255, 255), 1)
    x0, y0, width = crop
    selected = outlined[200 - x0 - width:200 - x0, 200 - y0 - width:200 - y0]
    zoom = cv2.resize(selected, (SIZE, SIZE), interpolation=cv2.INTER_NEAREST)
    local = mask[x0:x0 + width, y0:y0 + width][::-1, ::-1].astype(np.uint8)
    contour_mask = cv2.resize(local, (SIZE, SIZE), interpolation=cv2.INTER_NEAREST)
    contours, _ = cv2.findContours(contour_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(zoom, contours, -1, (255, 255, 255), 1)
    start = (round((200 - y0 - width) * SIZE / 200), round((200 - x0 - width) * SIZE / 200))
    end = (round((200 - y0) * SIZE / 200), round((200 - x0) * SIZE / 200))
    cv2.rectangle(full, start, end, (255, 185, 74), 1)
    return Image.fromarray(full[:, :, ::-1]), Image.fromarray(zoom[:, :, ::-1])


def add_poses(image, own, peer):
    draw = ImageDraw.Draw(image)
    draw.polygon([(SIZE // 2, SIZE // 2 - 7), (SIZE // 2 - 6, SIZE // 2 + 5),
                  (SIZE // 2 + 6, SIZE // 2 + 5)], fill=BLUE)
    position = local_points(peer["pose"][:3, 3][None], own["pose"])[0]
    x, y = (40 - position[1]) * SIZE / 80, (40 - position[0]) * SIZE / 80
    if 5 <= x < SIZE - 5 and 5 <= y < SIZE - 5:
        draw.rectangle((x - 4, y - 4, x + 4, y + 4), fill=(65, 215, 183))


def legend(canvas, y):
    draw = ImageDraw.Draw(canvas)
    entries = [("Obstacle", ORANGE), ("Ground", (185, 185, 185)),
               ("Uncertain", (155, 100, 180)), ("Unobserved", (28, 28, 28)),
               ("GT outline only", (255, 255, 255))]
    for index, (name, color) in enumerate(entries):
        x = MARGIN + index * 312
        draw.rectangle((x, y + 5, x + 19, y + 24), fill=color)
        label(canvas, (x + 28, y), name, 19, MUTED)


def render(row, summary, own, peer, fused, mask):
    probs, valid = warp_to_ego(peer, own["pose"])
    aligned = {"probabilities": probs, "valid": valid, "pose": own["pose"]}
    crop = target_crop(mask)
    predictions = (own, aligned, fused)
    scores = [region_presence(pred, mask) for pred in predictions]
    pairs = [raw_images(pred, mask, crop) for pred in predictions]
    hidden = row["occlusion_verified"]
    status = "OCCLUSION VERIFIED" if hidden else "OCCLUSION NOT VERIFIED"
    color = (75, 218, 182) if hidden else (255, 198, 96)
    observer_type = summary["actors"][row["observer"]]["type_id"].removeprefix("walker.pedestrian.damos_")
    titles = ("EGO ONLY", f"OBSERVER {summary['actors'][row['observer']]['actor_id']} ({observer_type})", "FUSED BEV")
    full = Image.new("RGB", (WIDTH, 1340), BACKGROUND)
    compact = Image.new("RGB", (WIDTH, 758), BACKGROUND)
    for canvas in (full, compact):
        label(canvas, (MARGIN, 10), f"{row['scenario']}  |  anchor {row['anchor_index']}  |  frame {row['frame_id']}", 27, bold=True)
        label(canvas, (MARGIN, 49), f"Target actor {row['actor_id']}  |  distance {row['distance_m']:.2f} m  |  {row['speed_kmh']:.1f} km/h", 21, MUTED)
        label(canvas, (MARGIN, 80), status, 21, color, True)
        label(canvas, (910, 80), f"Same ego axes / focused window {crop[2] * .4:g} m", 20, MUTED)
    for index, (title, pair, score) in enumerate(zip(titles, pairs, scores)):
        x = MARGIN + index * (SIZE + GAP)
        detail = "Own prediction" if index == 0 else "Received via ZKP; ego-aligned" if index == 1 else "All verified nearby observers"
        for canvas in (full, compact):
            label(canvas, (x, 120), title, 21, bold=True)
            label(canvas, (x, 152), detail, 18, MUTED)
        add_poses(pair[0], own, peer)
        full.paste(pair[0], (x, 184))
        label(full, (x, 713), "FULL BEV 80 m / blue box = zoom below", 18, MUTED)
        full.paste(pair[1], (x, 746))
        compact.paste(pair[1], (x, 184))
        detail = f"Focused target obstacle cells: {score['obstacle_overlap_cells']}/{score['gt_return_cells']}"
        label(full, (x, 1275), detail, 18, MUTED)
        label(compact, (x, 713), detail, 18, MUTED)
    legend(full, 1308)
    # Separate target outlines never modify the BEV data or turn GT into predictions.
    return full, compact, scores, crop


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    checkpoint_hash = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    backend = real_backend(60)
    hashes = {str(args.sweep): hashlib.sha256(args.sweep.read_bytes()).hexdigest(),
              str(args.checkpoint): checkpoint_hash}
    records, compacts, seen = [], [], set()
    for run in json.loads(args.sweep.read_text())["rows"]:
        scenario = run["scenario"]
        if scenario in seen:
            raise ValueError("Duplicate scenario runs; no silent best-run selection.")
        seen.add(scenario)
        report = Path(run["report"])
        candidates, summary, source_hashes = collect_candidates(report, scenario)
        hashes.update(source_hashes)
        hashes[str(report / "summary.json")] = hashlib.sha256((report / "summary.json").read_bytes()).hexdigest()
        row = select_candidate(candidates)
        folder = Path(row["folder"])
        audit = audit_round(folder, summary, checkpoint_hash, backend, fusion_mode="ego_observed", calibrated=True)
        own = load_npz(folder / "ego_own.npz")
        peer = load_npz(folder / f"ego_received_{row['observer']}.npz")
        fused = load_npz(folder / "ego_fused.npz")
        mask = focused_mask(folder, own, row["actor_id"])
        full, compact, scores, crop = render(row, summary, own, peer, fused, mask)
        full.save(args.output / f"{scenario}_triptych.png")
        compact.save(args.output / f"{scenario}_focus.png")
        compacts.append((scenario, compact))
        record = dict(row, scores=dict(zip(("ego", "observer_ego_aligned", "fused"), scores)),
            crop_grid=list(crop), historical_proof_audit=audit,
            selection="Closest target in saved verified-occlusion frames; otherwise closest assessable target in saved frames",
            available_frames=sorted({item["frame_id"] for item in candidates}),
            selected_observer_is_assigned_to_anchor=True, interpolation=False,
            source_fused_peer_ids=json.loads((folder / "peers.json").read_text())["ego"]["verified_peer_ids"])
        records.append(record)
        print(f"{scenario} F{row['frame_id']} A{row['anchor_index']} actor={row['actor_id']} "
              f"distance={row['distance_m']:.2f}m occlusion={row['occlusion_verified']}", flush=True)
    compacts.sort(key=lambda item: int(item[0][1:]))
    for offset in range(0, len(compacts), 3):
        group = compacts[offset:offset + 3]
        canvas = Image.new("RGB", (WIDTH, 44 + len(group) * 758), BACKGROUND)
        label(canvas, (MARGIN, 8), "ORANGE: obstacle  |  GRAY: ground  |  DARK: unobserved  |  WHITE: target GT outline only", 20, MUTED)
        for index, (_, tile) in enumerate(group):
            canvas.paste(tile, (0, 44 + index * 758))
        canvas.save(args.output / f"{group[0][0]}_{group[-1][0]}_triptych.png")
    unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    if not unchanged:
        raise RuntimeError("Source evidence changed during rendering.")
    result = {"rows": records, "checkpoint_sha256": checkpoint_hash, "source_files_unchanged": unchanged,
        "source_sha256": hashes, "metric2_pass": None,
        "scope": "Saved same-frame live inference and actual received observer BEV, not a new simulation",
        "limitations": ["Closest among saved BEV snapshots, not the continuous driving trace",
            "Only rows explicitly marked verified prove complete camera/LiDAR occlusion of the focused actor",
            "Ground-truth returns select and outline the target only; never fill prediction cells",
            "Middle column is the assigned observer in ego axes; fused output can include additional verified nearby peers",
            "Historical proof reverification is not a new live network or freshness test"]}
    (args.output / "summary.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"scenarios": len(records), "verified_occlusion_scenarios": [r["scenario"] for r in records if r["occlusion_verified"]],
                      "source_files_unchanged": unchanged, "output": str(args.output)}), flush=True)


if __name__ == "__main__":
    main()
