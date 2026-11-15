"""Live sensor acceptance -> per-peer inference -> verified ego BEV fusion."""
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time

import numpy as np

from bev_mesh import BEVMesh
from bev_sensor_rig import WORLD_BASIS


def task_pose(task):
    label = task["label"]
    world = task["packets"][label, "lidar"][-1]["transform"]
    actor = world @ np.linalg.inv(task["sensor_matrices"][label, "lidar"])
    return WORLD_BASIS @ actor @ WORLD_BASIS


def select_nearby(tasks, radius_m):
    origin = task_pose(tasks["ego"])[:3,3]
    return [n for n in sorted(tasks) if n != "ego" and
            (radius_m == 0 or np.linalg.norm(task_pose(tasks[n])[:3,3]-origin) <= radius_m)]


def save_comparison(folder, nodes, record):
    import cv2
    from bev_model_runtime import render_bev
    tiles = []
    for node in nodes:
        front = cv2.imread(str(folder / f"{node}_front.png"))
        if front is None:
            continue
        front = cv2.resize(front, (600,338))
        if node == "ego":
            bev = cv2.imread(str(folder / f"{node}_own.png"))
        else:
            with np.load(folder / f"ego_received_{node}.npz", allow_pickle=False) as data:
                bev = render_bev(data["probabilities"], data["valid"])
        panel = np.vstack([front, bev])
        cv2.rectangle(panel, (0,0), (600,32), (20,20,20), -1)
        label = f"{node} | F{record['frame_id']}" + (" | ego verified BEV" if node != "ego" else "")
        cv2.putText(panel, label, (10,22),
                    cv2.FONT_HERSHEY_SIMPLEX, .55, (255,255,255), 1)
        tiles.append(panel)
    with np.load(folder / "ego_fused.npz", allow_pickle=False) as data:
        fused = {k:data[k] for k in data.files}
    top = np.full((338,600,3), 25, np.uint8)
    evaluation = record.get("evaluation")
    recovery = (f"Situation regions: {evaluation.get('anchor_fused_detected','not evaluated')}/{evaluation.get('anchor_assessable','?')}"
                if evaluation else "Object recovery: not evaluated")
    lines = ["EGO FUSED BEV", f"Elapsed: {record['elapsed_ms']/1000:.3f} s",
             f"Verified peers: {len(record['verified_peer_ids'])}",
             f"Peer-added cells: {record['peer_added_cells']}",
             "Dark = no current LiDAR return", recovery]
    for i, line in enumerate(lines):
        cv2.putText(top, line, (18,40+40*i), cv2.FONT_HERSHEY_SIMPLEX, .62, (240,240,240), 1)
    tiles.append(np.vstack([top, render_bev(fused["probabilities"], fused["valid"])]))
    cv2.imwrite(str(folder / "comparison.png"), np.hstack(tiles))


def run_live_bev(rig, report_dir, *, rounds=3, radius_m=60., timeout_s=60.,
                 scenic_proc=None, scenario_labels=(), on_tick=None, logger=print,
                 evaluate=False, anchor_assignments=(), scene_cameras=False):
    import os
    experiment = os.environ.get("DAMOS_RESILIENCE_PLAN")
    if experiment:
        from carla_resilience_trial import run_trial
        return run_trial(rig, report_dir, Path(experiment), scenic_proc=scenic_proc,
                         assignments=anchor_assignments, on_tick=on_tick, logger=logger)
    if scene_cameras and not evaluate:
        raise ValueError("Scene cameras require same-frame BEV evaluation.")
    folder = Path(report_dir) / ("live_bev_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"))
    folder.mkdir(parents=True, exist_ok=False)
    node_labels = {("ego" if label == "ego" else f"observer_{actor.id}"): label
                   for label, actor in rig.actors.items()}
    actor_ids = {node: rig.actors[label].id for node,label in node_labels.items()}
    records, mesh, failure = [], None, None
    evaluator, overhead = None, None
    setup_start = time.monotonic()

    def acquire(minimum):
        if scenic_proc is not None and scenic_proc.poll() is not None:
            raise RuntimeError("SCENIC_ENDED_DURING_BEV")
        if on_tick is not None:
            on_tick()
        window, raw = rig.freeze_inputs(minimum_frame=minimum, timeout_s=30)
        return window, {node: raw[label] for node,label in node_labels.items()}

    logger(f"[BEV] Enrolling {len(actor_ids)} peers; GPU models limited to nearby nodes; report={folder}")
    try:
        if evaluate:
            from bev_evaluation import SemanticEvaluationRig
            evaluator = SemanticEvaluationRig(rig,anchor_assignments)
        if scene_cameras:
            from bev_scene_camera import OverheadCamera
            overhead = OverheadCamera(rig.world, rig.actors["ego"])
        mesh = BEVMesh(actor_ids, timeout_s)
        window, tasks = acquire(0)
        setup_seconds = time.monotonic()-setup_start
        logger(f"[BEV] Process/key enrollment: {setup_seconds:.1f}s (excluded)")
        deadline = time.monotonic()+max(90, rounds*(timeout_s+30))
        minimum_frame, previous = window[-1]+1, None
        while len(records) < rounds and time.monotonic() < deadline:
            window, tasks = acquire(minimum_frame)
            minimum_frame = window[-1]+1
            nearby = select_nearby(tasks, radius_m)
            if nearby != previous:
                logger(f"[BEV] Nearby within {radius_m:g}m: {', '.join(nearby) or 'none; waiting'}")
                previous = nearby
            if not nearby:
                time.sleep(.2)
                continue
            selected = {n: tasks[n] for n in ["ego", *nearby]}
            if set(selected) != mesh.loaded:
                logger(f"[BEV] Preparing {len(selected)} nearby models; far models released (excluded)")
                mesh.warmup(selected)
                # Model setup is outside the metric; never reuse the warmup frame.
                continue
            round_folder = folder / f"round_{len(records)+1:03d}"
            if evaluator is not None:
                truth,gt_packets = evaluator.freeze(window[-1],[node_labels[n] for n in selected])
            overhead_packet = overhead.freeze(window[-1]) if overhead is not None else None
            rows = mesh.exchange(selected, len(records)+1, round_folder,save_inputs=evaluate)
            ego = rows["ego"]
            ego["frames"] = window
            ego["peer_distances_m"] = {n: float(np.linalg.norm(
                task_pose(tasks[n])[:3,3]-task_pose(tasks["ego"])[:3,3])) for n in nearby}
            records.append(ego)
            if ego["status"] == "fusion_complete":
                if evaluator is not None:
                    from bev_evaluation import evaluate_round
                    from bev_sensor_rig import CAMERAS
                    rgb = {node:{name:{"frame_id":selected[node]["packets"][node_labels[node],name][-1]["frame"],
                                      "raw":selected[node]["packets"][node_labels[node],name][-1]["raw"]}
                                 for name in CAMERAS} for node in selected}
                    evaluation = evaluate_round(round_folder,truth,gt_packets,
                                                {n:node_labels[n] for n in selected},window[-1],rgb_evidence=rgb)
                    if overhead_packet is not None:
                        from bev_scene_camera import save_overhead
                        save_overhead(round_folder, overhead_packet, window[-1], truth["capture_sim_ns"])
                    ego["object_recovery"] = "diagnostic_only"
                    ego["evaluation"] = {k:evaluation[k] for k in
                        ("in_roi_objects","fused_diagnostic_matches","peer_only_candidates","recovered_candidates",
                         "ego_presence_matches","fused_presence_matches","presence_added_objects","occluded_objects","occluded_recovered",
                         "anchor_total","anchor_assessable","anchor_ego_detected","anchor_fused_detected",
                         "anchor_new_detections","anchor_hidden_parts_recovered","ego_road_state")}
                    logger(f"[BEV GT] Situation regions ego/fused={evaluation['anchor_ego_detected']}/"
                           f"{evaluation['anchor_fused_detected']} of {evaluation['anchor_assessable']} assessable "
                           f"({evaluation['anchor_total']} total); hidden-part recovery="
                           f"{evaluation['anchor_hidden_parts_recovered']}; road="
                           f"{evaluation['ego_road_state']['on_driving_lane']} (diagnostic only)")
                save_comparison(round_folder, ["ego", *nearby], ego)
            (round_folder / "peers.json").write_text(json.dumps(rows, indent=2))
            logger(f"[BEV] {len(records)}/{rounds} F{window[-1]} "
                   f"verified={len(ego['verified_peer_ids'])}/{len(nearby)} "
                   f"ego fusion={ego['elapsed_ms']/1000:.3f}s "
                   f"status={ego['status']} time<=18s={ego['pipeline_budget_pass']} "
                   f"object recovery={ego['object_recovery']}")
            if ego.get("error"):
                logger(f"[BEV] {ego['error']}")
    except Exception as exc:
        failure = type(exc).__name__+": "+str(exc)
        raise
    finally:
        if mesh is not None:
            mesh.close()
        if overhead is not None:
            overhead.close()
        if evaluator is not None:
            evaluator.close()
        trace = evaluator.ego_trace if evaluator is not None else []
        distance = sum(float(np.linalg.norm(np.array(b["location_carla"][:2])-a["location_carla"][:2]))
                       for a,b in zip(trace,trace[1:]))
        if evaluator is not None:
            (folder/"ego_driving_trace.json").write_text(json.dumps(trace,indent=2))
        times = [r["elapsed_ms"] for r in records if r["status"] == "fusion_complete"]
        summary = {
            "source": "live_carla", "evaluation_target": "ego", "target_ms": 18000,
            "measurement": "common raw sensor history accepted -> preprocessing/inference/proof/TCP/verify/ego fusion and obstacle presence complete",
            "deployment": "single_host_independent_gpu_processes_direct_tcp_loopback",
            "topology": "ego_star_no_observer_to_observer", "backend": "REAL_GROTH16",
            "coverage": "current-lidar-return-cells", "alignment": "planar ground z=0, nearest neighbour",
            "recognition_goal":"situation-region obstacle presence and location; category/individual-object counts are secondary",
            "binary_output":"ego_fused.npz occupancy: 0 unknown, 1 ground, 2 obstacle; derived before timer stops",
            "metric2_pass": None, "object_recovery": "diagnostic_only" if evaluate else "not_evaluated",
            "gt_evaluation_enabled":evaluate, "evaluation_feeds_model_or_fusion":False,
            "ego_driving":{"recorded_frames":len(trace),"distance_m":distance,
                "all_recorded_centres_on_driving_lane":all(r["on_driving_lane"] for r in trace) if trace else None,
                "offroad_frames":sum(not r["on_driving_lane"] for r in trace),
                "max_speed_mps":max((r["speed_mps"] for r in trace),default=None),
                "scope":"Snapshot centres; road checks are evaluation only, not vehicle control"},
            "evaluation_sensor_overhead":"Additional semantic LiDAR and six instance cameras per mobility when enabled; render load remains",
            "overhead_scene_camera_enabled":scene_cameras,
            "overhead_scene_camera_scope":"Evaluation-only RGB, same input frame; no model/fusion input. Acquisition wait and save excluded; sensor rendering still adds load.",
            "excluded": ["sensor/history acquisition wait", "model/key setup and warmup", "report/image writes", "path planning",
                         "evaluation-only GT sensor wait and scoring"],
            "scenario_labels": list(scenario_labels), "radius_m": radius_m,
            "fixed_delta_seconds": rig.step, "sensor_count": len(rig.records),
            "actors": {n: {"actor_id": actor_ids[n], "track_label": label,
                            "type_id": rig.actors[label].type_id} for n,label in node_labels.items()},
            "rounds_requested": rounds, "rounds": records,
            "fusion_successes": len(times), "mean_ms": statistics.mean(times) if times else None,
            "max_ms": max(times) if times else None,
            "setup": mesh.setup if mesh is not None else None, "fatal_error": failure,
        }
        (folder / "summary.json").write_text(json.dumps(summary, indent=2))
        logger(f"[BEV] Summary: {folder / 'summary.json'}")
    if len(records) != rounds or any(r["status"] != "fusion_complete" or not r["bidirectional_complete"] for r in records):
        raise RuntimeError(f"BEV fusion incomplete; see {folder}")
    return summary
