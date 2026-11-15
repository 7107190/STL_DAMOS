"""Preplanned CARLA anchor acquisition and actual model/Groth16 replay."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np

CHECKPOINT_SHA = "3600d8e29f602f77ca142637c0336d267e0d041a2d5486739fca0070e9e7646e"
PERIOD_NS = 200_000_000
MINIMUM_NS = 500_000_000


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def longest_span(rows, field):
    first = previous = None
    best = 0
    for row in rows:
        stamp = int(row["capture_sim_ns"])
        connected = previous is not None and (
            row["round_index"] == previous["round_index"] + 1
            and row["frame_id"] == previous["frame_id"] + 2
            and abs(stamp - int(previous["capture_sim_ns"]) - PERIOD_NS) <= 1000)
        if previous is not None and (stamp <= int(previous["capture_sim_ns"])
                                     or row["frame_id"] <= previous["frame_id"]):
            raise ValueError("Reversed or duplicate clock")
        if not row["eligible"] or row[field] is not True:
            first = None
        else:
            if first is None or not connected:
                first = stamp
            best = max(best, stamp - first)
        previous = row
    return best


def choose_assignment(assignments, case):
    x, y = case["target_scenic_xy"]
    if not assignments:
        raise ValueError("No actual anchor")
    # Geometry-only selection precedes all model outputs.
    return min(assignments, key=lambda a: (
        (a["anchor_location"]["x"] - x) ** 2 + (a["anchor_location"]["y"] + y) ** 2,
        a["anchor_index"]))


def collect_accuracy(rig, client, report_root, assignments, *, scenario, tm_port,
                     on_tick=None, logger=print):
    from bev_camera_freshness import CameraContentAudit
    from bev_evaluation import SemanticEvaluationRig, local_points
    from bev_live import task_pose
    from bev_passby_video import configure_route, FPS
    from bev_sensor_rig import CAMERAS

    case_path = Path(os.environ["DAMOS_BEV_ACCURACY_CASE"])
    case = json.loads(case_path.read_text())
    if case["scenario"] != scenario or not np.isclose(rig.step, .1):
        raise ValueError("Case identity or sensor period mismatch")
    assignment = choose_assignment(assignments, case)
    label = assignment["track_label"]
    observer = f"observer_{rig.actors[label].id}"
    node_labels = {"ego": "ego", observer: label}
    selected_rig = SimpleNamespace(world=rig.world, step=rig.step, records=rig.records,
                                  actors={n: rig.actors[n] for n in node_labels.values()})
    folder = Path(report_root) / "recording"
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"case": case, "case_sha256": sha256(case_path), "scenario": scenario,
        "anchor_index": assignment["anchor_index"], "fps": FPS,
        "source": "New CARLA synchronized sensors; actual offline BEV/Groth16 replay",
        "actors": {n: {"actor_id": rig.actors[l].id, "type_id": rig.actors[l].type_id,
                       "track_label": l} for n, l in node_labels.items()},
        "target_ids": sorted(m["actor_id"] for m in assignment["member_actors"]),
        "anchor_model_world": [assignment["anchor_location"]["x"],
                               -assignment["anchor_location"]["y"],
                               assignment["anchor_location"]["z"]],
        "selection": "closest generated semantic anchor to preplanned map point; before inference",
        "timeline": [], "approach_timeline": [], "collisions": [],
        "capture_complete": False, "error": None,
        "gt_used_for_inference": False, "metric2_pass": None}
    captures, events = [], []
    evaluator = collision_sensor = None
    logger(f"[ACCURACY] Capture {case['case_id']} anchor={assignment['anchor_index']} report={folder}")
    try:
        import carla
        collision_sensor = rig.world.spawn_actor(
            rig.world.get_blueprint_library().find("sensor.other.collision"),
            carla.Transform(), attach_to=rig.actors["ego"])
        collision_sensor.listen(lambda data: events.append({
            "frame_id": data.frame, "other_actor_id": data.other_actor.id,
            "impulse": float(np.linalg.norm([data.normal_impulse.x,
                data.normal_impulse.y, data.normal_impulse.z]))}))
        agent, manifest["drive"] = configure_route(rig, client, assignment, scenario, tm_port)
        evaluator = SemanticEvaluationRig(selected_rig, [assignment], instance_cameras=False)
        window, _ = rig.freeze_inputs(labels=list(node_labels.values()),
            minimum_frame=rig.world.get_snapshot().frame, timeout_s=30)
        next_frame = window[-1]
        content = CameraContentAudit()
        wanted = round(case["window_seconds"] * FPS)
        for index in range(round(case["approach_timeout_seconds"] * FPS) + wanted):
            control = agent.run_step()
            if control is not None:
                rig.actors["ego"].apply_control(control)
            if on_tick:
                on_tick()
            window, raw_tasks = rig.freeze_inputs(labels=list(node_labels.values()),
                                                  end_frame=next_frame, timeout_s=10)
            truth, packets = evaluator.freeze(next_frame, list(node_labels.values()))
            pose = task_pose(raw_tasks["ego"])
            centres = np.array([t["location_model_world"] for t in truth["targets"]])
            centre = centres.mean(axis=0)
            relative = local_points(centre[None], pose)[0]
            distance = float(np.linalg.norm(relative[:2]))
            row = {"frame_id": next_frame, "capture_sim_ns": truth["capture_sim_ns"],
                "distance_m": distance, "anchor_forward_m": float(relative[0]),
                "ego_pose_model": pose.tolist(), **truth["ego_road_state"]}
            if captures or distance <= case["entry_radius_m"]:
                tasks = {n: raw_tasks[l] for n, l in node_labels.items()}
                images = {n: {name: task["packets"][node_labels[n], name][-1]["raw"]
                              for name in CAMERAS} for n, task in tasks.items()}
                content.observe(next_frame, truth["capture_sim_ns"], images)
                manifest["timeline"].append(row)
                captures.append((window, tasks, truth, packets))
                if len(captures) == wanted:
                    break
            else:
                manifest["approach_timeline"].append(row)
                if index >= round(case["approach_timeout_seconds"] * FPS):
                    raise TimeoutError("Ego did not reach the preselected anchor within the approach limit")
            if index % (5 * FPS) == 0:
                logger(f"[ACCURACY] Approach t={index/FPS:.1f}s distance={distance:.1f}m samples={len(captures)}/{wanted}")
            next_frame += 2
        if len(captures) != wanted:
            raise ValueError("Incomplete fixed acquisition window")
        manifest["camera_content_audit"] = content.require_passby_content()
        manifest.update(capture_complete=True, selected_frames=len(captures),
                        capture_frames=len(captures), selected_indices=[0, len(captures)])
        logger(f"[ACCURACY] Acquired {len(captures)} exact-frame samples; inference starts after acquisition")
    except BaseException as exc:
        manifest["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        manifest["collisions"] = events
        if collision_sensor is not None:
            try:
                collision_sensor.stop()
                collision_sensor.destroy()
            except RuntimeError:
                pass
        if evaluator is not None:
            evaluator.close()
        write_json(folder / "capture.json", manifest)
    return {"accuracy_case": True, "folder": folder, "manifest": manifest,
            "captures": captures, "node_labels": node_labels}


def load_npz(path):
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def reconstruct_recorded_fusion(folder, own, peers, evidence):
    from bev_fusion import fuse_bevs
    refusion = fuse_bevs(own, peers, mode="ego_observed")
    policy = evidence.get("policy", "off")
    if policy == "relative-v3":
        from bev_lidar_calibration import geometry_features
        from bev_relative_fusion import PARAMETERS, relative_correction
        if evidence.get("parameters") != PARAMETERS:
            raise ValueError("Recorded evidence fusion parameters differ")
        with np.load(folder / "ego_model_inputs.npz", allow_pickle=False) as arrays:
            lidar = arrays["lidar_bev"]
        refusion, _ = relative_correction(refusion, own, peers, lidar, geometry_features(lidar))
    elif policy != "off":
        raise ValueError("Unknown recorded evidence fusion policy")
    return refusion


def score_round(folder, truth, packets, node_labels, manifest, index):
    from bev_anchor_evaluation import region_presence
    from bev_evaluation import decode_semantic, points_mask
    from bev_fusion import fuse_bevs
    from bev_occupancy import obstacle_presence, OBSTACLE

    observer = next(n for n in node_labels if n != "ego")
    own = load_npz(folder / "ego_own.npz")
    peer = load_npz(folder / f"ego_received_{observer}.npz")
    fused = load_npz(folder / "ego_fused.npz")
    peers = json.loads((folder / "peers.json").read_text())
    frame, stamp = truth["frame_id"], truth["capture_sim_ns"]
    for node, result in peers.items():
        if (result["status"] != "fusion_complete" or result.get("artifact_error")
                or result["frame_id"] != frame
                or result["actor_id"] != manifest["actors"][node]["actor_id"]
                or result["inference"]["checkpoint_sha256"] != CHECKPOINT_SHA
                or result["fusion_mode"] != "ego_observed"
                or set(result["peer_ids"]) != set(result["verified_peer_ids"])
                or result["missing_peer_ids"]):
            raise ValueError("Actual model/ZKP peer evidence failed")
        packet = json.loads((folder / f"{node}_packet.json").read_text())["manifest"]
        if (packet["frame_id"] != frame or abs(packet["capture_sim_ns"] - stamp) > 1000
                or packet["actor_id"] != result["actor_id"]):
            raise ValueError("Model packet clock or actor mismatch")
    refusion = reconstruct_recorded_fusion(folder, own, [peer], peers["ego"].get("evidence_fusion", {}))
    np.testing.assert_array_equal(refusion["probabilities"], fused["probabilities"])
    np.testing.assert_array_equal(refusion["valid"], fused["valid"])
    road = truth["ego_road_state"]
    np.testing.assert_allclose(own["pose"][:3, 3],
        np.asarray(road["location_carla"]) * [1, -1, 1], atol=1e-4, rtol=0)
    if sorted(t["actor_id"] for t in truth["targets"]) != manifest["target_ids"]:
        raise ValueError("Pinned abnormal actors changed")
    target_points = []
    for node, label in node_labels.items():
        packet = packets[label]
        if packet["frame_id"] != frame or abs(packet["capture_sim_ns"] - stamp) > 1000:
            raise ValueError("Semantic clock mismatch")
        xyz, ids, tags = decode_semantic(packet)
        np.savez_compressed(folder / f"{node}_eval_only_semantic.npz", world_xyz=xyz,
                            actor_ids=ids, tags=tags)
        target_points.append(xyz[np.isin(ids, manifest["target_ids"])])
    target = points_mask(np.concatenate(target_points), own["pose"])
    centres = np.asarray([t["location_model_world"] for t in truth["targets"]])
    local = np.linalg.solve(own["pose"], np.c_[centres, np.ones(len(centres))].T).T
    roi = bool(target.any() or ((local[:, :2] >= -40) & (local[:, :2] < 40)).all(1).any())
    scores = {"ego": region_presence(own, target), "fused": region_presence(fused, target)}
    row = {"case_id": manifest["case"]["case_id"], "scenario": manifest["scenario"],
        "anchor_index": manifest["anchor_index"], "round_index": index,
        "frame_id": frame, "capture_sim_ns": str(stamp),
        "member_actor_ids": manifest["target_ids"], "on_driving_lane": road["on_driving_lane"],
        "in_ego_roi": roi, "eligible": roi and road["on_driving_lane"] is True and scores["fused"]["assessable"],
        "gt_return_cells": int(target.sum()), "ego_pose_model_world": own["pose"].tolist(),
        "gt_target_cell_indices": np.flatnonzero(target).tolist(),
        "ground_truth_fed_to_model_or_fusion": False}
    for name, prediction in (("ego", own), ("fused", fused)):
        occupancy = obstacle_presence(prediction["probabilities"], prediction["valid"])["occupancy"]
        row[name + "_detected"] = scores[name]["detected"]
        row[name + "_connected_overlap_cells"] = scores[name]["largest_connected_overlap_cells"]
        row[name + "_obstacle_cell_indices"] = np.flatnonzero(occupancy == OBSTACLE).tolist()
    write_json(folder / "ground_truth.json", truth)
    write_json(folder / "evidence.json", row)
    return row


def process_accuracy(sequence, *, timeout_s=60, logger=print):
    from bev_mesh import BEVMesh

    folder, manifest = sequence["folder"], sequence["manifest"]
    captures = sequence["captures"]
    mesh = None
    rows = []
    summary = {"case_id": manifest["case"]["case_id"], "scenario": manifest["scenario"],
        "anchor_index": manifest["anchor_index"], "member_actor_ids": manifest["target_ids"],
        "status": "data_error", "successful": False, "samples": 0,
        "error": None, "backend": "REAL_GROTH16", "metric2_pass": None}
    try:
        mesh = BEVMesh({n: a["actor_id"] for n, a in manifest["actors"].items()}, timeout_s)
        mesh.warmup(captures[0][1])
        if any(r["warmup"]["checkpoint_sha256"] != CHECKPOINT_SHA for r in mesh.setup.values()):
            raise ValueError("Frozen checkpoint mismatch")
        for index, (window, tasks, truth, packets) in enumerate(captures, 1):
            target = folder / f"round_{index:03d}"
            results = mesh.exchange(tasks, index, target, save_inputs=True)
            write_json(target / "peers.json", results)
            rows.append(score_round(target, truth, packets, sequence["node_labels"], manifest, index))
            captures[index - 1] = None
            if index % 5 == 0:
                logger(f"[ACCURACY] Replay {index}/{len(captures)} F{window[-1]} actual ZKP verified")
            if manifest["case"].get("early_success_stop", False) and longest_span(rows, "fused_detected") >= MINIMUM_NS:
                logger(f"[ACCURACY] Sufficient continuous detection witness after {index} processed samples")
                break
        duration = longest_span(rows, "fused_detected")
        success = duration >= MINIMUM_NS
        eligible = sum(r["eligible"] for r in rows)
        summary.update(status="success" if success else "failure" if eligible else "not_evaluable", successful=success,
            samples=len(rows), eligible_samples=eligible,
            fused_longest_span_ns=str(duration), fused_longest_span_seconds=duration / 1e9,
            ego_longest_span_ns=str(longest_span(rows, "ego_detected")), setup=mesh.setup,
            checkpoint_sha256=CHECKPOINT_SHA, captured_samples=len(captures),
            unprocessed_samples=len(captures) - len(rows),
            early_success_stop=manifest["case"].get("early_success_stop", False),
            duration_scope="longest confirmed span in the processed prefix, not a full-window maximum")
    except BaseException as exc:
        summary["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        if mesh is not None:
            mesh.close()
        summary["samples"] = len(rows)
        write_json(folder / "summary.json", summary)
    logger(f"[ACCURACY RESULT] {summary['case_id']} {summary['status']} continuous={summary['fused_longest_span_seconds']:.3f}s")
    return summary
