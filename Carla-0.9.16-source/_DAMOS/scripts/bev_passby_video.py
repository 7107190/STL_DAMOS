"""Capture a real pass-by first, then run synchronized BEV/Groth16 replay."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np

from bev_evaluation import SemanticEvaluationRig, local_points
from bev_live import task_pose
from bev_scene_camera import OverheadCamera
from bev_sensor_rig import CAMERAS

ROOT = Path(__file__).resolve().parents[2]
FPS = 5


def video_waypoint(road_map, x, y, scenario):
    import carla
    waypoint = road_map.get_waypoint(carla.Location(x=x, y=y), True, carla.LaneType.Driving)
    if scenario in ("S4", "S6"):
        alternatives, seen = [waypoint], {waypoint.id}
        for side in ("get_left_lane", "get_right_lane"):
            current = waypoint
            for _ in range(5):
                current = getattr(current, side)()
                if current is None or current.id in seen or current.lane_type != carla.LaneType.Driving:
                    break
                alternatives.append(current); seen.add(current.id)
        # Junction connector lanes may have no left/right link despite parallel roads.
        yaw = np.deg2rad(waypoint.transform.rotation.yaw)
        tangent = np.array([np.cos(yaw), np.sin(yaw)])
        normal = np.array([-np.sin(yaw), np.cos(yaw)])
        origin = waypoint.transform.location
        for offset in (-10.5, -7., -3.5, 3.5, 7., 10.5):
            point = carla.Location(x=origin.x + normal[0] * offset, y=origin.y + normal[1] * offset)
            other = road_map.get_waypoint(point, True, carla.LaneType.Driving)
            delta = np.array([other.transform.location.x - origin.x, other.transform.location.y - origin.y])
            parallel = abs((other.transform.rotation.yaw - waypoint.transform.rotation.yaw + 90) % 180 - 90) < 25
            if (other.id not in seen and parallel and abs(delta @ tangent) < 5
                    and 2.8 < abs(delta @ normal) < 12):
                alternatives.append(other); seen.add(other.id)
        waypoint = max(alternatives, key=lambda p: p.transform.location.distance(carla.Location(x=x, y=y)))
        if scenario == "S4" and waypoint.transform.location.distance(carla.Location(x=x, y=y)) < 3:
            raise ValueError("No separate Driving lane beside the S4 obstacle.")
    return waypoint


class NativeTrafficManagerRoute:
    """Start CARLA's existing pedestrian-aware controller after sensor readiness."""

    def __init__(self, ego, traffic_manager, port, route, speed_kmh):
        self.ego, self.manager, self.port = ego, traffic_manager, port
        self.path = [point.transform.location for point, _ in route]
        self.speed_kmh, self.started = speed_kmh, False

    def run_step(self):
        if not self.started:
            import carla
            self.ego.apply_control(carla.VehicleControl())
            self.ego.set_autopilot(True, self.port)
            self.manager.auto_lane_change(self.ego, False)
            self.manager.ignore_lights_percentage(self.ego, 100.)
            self.manager.ignore_signs_percentage(self.ego, 100.)
            self.manager.ignore_walkers_percentage(self.ego, 0.)
            self.manager.ignore_vehicles_percentage(self.ego, 0.)
            self.manager.set_desired_speed(self.ego, self.speed_kmh)
            self.manager.set_path(self.ego, self.path)
            self.started = True
        return None


def configure_route(rig, client, assignment, scenario, tm_port):
    sys.path.insert(0, str(ROOT / "PythonAPI/carla"))
    from agents.navigation.local_planner import LocalPlanner
    from agents.navigation.global_route_planner import GlobalRoutePlanner
    anchor = assignment["anchor_location"]
    waypoint = video_waypoint(rig.world.get_map(), anchor["x"], anchor["y"], scenario)
    planner = GlobalRoutePlanner(rig.world.get_map(), 2.)
    ego = rig.actors["ego"]
    after = min(waypoint.next(30), key=lambda p: abs(
        (p.transform.rotation.yaw - waypoint.transform.rotation.yaw + 180) % 360 - 180))
    route = planner.trace_route(ego.get_location(), waypoint.transform.location)
    route += planner.trace_route(waypoint.transform.location, after.transform.location)
    if len(route) < 3:
        raise RuntimeError("No continuous Driving-lane video route.")
    speed_kmh = 8. if scenario in ("S1", "S2", "S3") else 15.
    ego.set_autopilot(False, tm_port)
    dt = 1. / FPS
    options = {"dt": dt, "target_speed": speed_kmh, "sampling_radius": 2.,
        "lateral_control_dict": {"K_P": 1.95, "K_I": .05, "K_D": .2, "dt": dt},
        "longitudinal_control_dict": {"K_P": 1., "K_I": .05, "K_D": 0., "dt": dt}}
    if scenario == "S3":
        agent = NativeTrafficManagerRoute(ego, client.get_trafficmanager(tm_port),
                                         tm_port, route, speed_kmh)
    else:
        agent = LocalPlanner(ego, map_inst=rig.world.get_map(), opt_dict=options)
        agent.set_global_plan(route)
    # Retain the brake until evaluation cameras and the common input window are ready.
    return agent, {"desired_speed_kmh": speed_kmh,
            ("traffic_manager_port" if scenario == "S3" else "traffic_manager_port_disabled"): tm_port,
            "controller": ("CARLA native TrafficManager; video-only actor-state safety braking"
                           if scenario == "S3" else "CARLA LocalPlanner/PID; video-only low-speed fixed-route tracking"),
            "control_period_seconds": dt,
            "planner": "CARLA GlobalRoutePlanner; video-only predefined road route",
            "teleport": False, "pedestrian_avoidance": scenario == "S3", "collision_rejection": True,
            "sensor_readiness_brake_retained": True,
            "traffic_controls": ("S3 uses native TrafficManager actor-state braking, not BEV; traffic signals ignored; visualization only, not avoidance evaluation"
                                 if scenario == "S3" else "No traffic/hazard decision layer; controlled visualization, not avoidance evaluation"),
            "path_carla": [[p.transform.location.x, p.transform.location.y] for p, _ in route]}


def select_passage(timeline, collisions, target_ids, *, pre_seconds=10):
    if len(timeline) < 10:
        raise ValueError("Too few real synchronized video frames.")
    stamps = np.array([r["capture_sim_ns"] for r in timeline], np.int64)
    if not np.all(np.abs(np.diff(stamps) - round(1e9 / FPS)) <= 1000):
        raise ValueError("Video has missing/duplicate input frames; refusing a fake constant-rate timeline.")
    crossings = [i for i, row in enumerate(timeline)
                 if row["anchor_forward_m"] < -5 and row["distance_m"] < 25
                 and any(p["anchor_forward_m"] > 8 for p in timeline[:i])]
    if not crossings:
        raise ValueError("Ego did not pass the selected anchor during capture.")
    passed = crossings[0]
    start = max(0, passed - pre_seconds * FPS)
    end = min(len(timeline), passed + 3 * FPS + 1)
    rows = timeline[start:end]
    if rows[-1]["capture_sim_ns"] - timeline[passed]["capture_sim_ns"] < 2e9:
        raise ValueError("No post-pass video context.")
    if not all(r["on_driving_lane"] for r in rows):
        raise ValueError("Off-road ego in the requested video window.")
    contacts = [c for c in collisions if c["other_actor_id"] in target_ids or
                (rows[0]["frame_id"] <= c["frame_id"] <= rows[-1]["frame_id"] and c["impulse"] > 100)]
    if contacts:
        raise ValueError("Collision in pass-by video; evidence retained, not presented as a normal passage.")
    if rows[0]["anchor_forward_m"] <= 0 or rows[-1]["anchor_forward_m"] >= 0:
        raise ValueError("Selected video does not contain both approach and departure.")
    return start, end


def collect_passby(rig, client, report_root, assignments, *, anchor_index, seconds,
                   scenario, tm_port, target_xy=None, on_tick=None, logger=print):
    if target_xy is not None:
        x, y = target_xy
        choice = min(assignments, key=lambda a: np.hypot(a["anchor_location"]["x"] - x,
                                                       a["anchor_location"]["y"] + y))
        if np.hypot(choice["anchor_location"]["x"] - x, choice["anchor_location"]["y"] + y) > 12:
            raise ValueError("The requested video target is not near any actual anchor.")
        anchor_index = choice["anchor_index"]
    choices = [a for a in assignments if a["anchor_index"] == anchor_index]
    if len(choices) != 1 or not np.isclose(rig.step, .1):
        raise ValueError("Video needs one pinned anchor and production 0.1s sensor ticks.")
    assignment = choices[0]
    label = assignment["track_label"]
    observer = f"observer_{rig.actors[label].id}"
    node_labels = {"ego": "ego", observer: label}
    selected_rig = SimpleNamespace(world=rig.world, step=rig.step, records=rig.records,
                                  actors={n: rig.actors[n] for n in node_labels.values()})
    folder = Path(report_root) / ("bev_video_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"))
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"scenario": scenario, "anchor_index": anchor_index, "fps": FPS,
        "source": "Actual CARLA common-frame sensor recording, followed by offline BEV/Groth16 replay",
        "metric2_pass": None, "live_latency_benchmark": False,
        "actors": {n: {"actor_id": rig.actors[l].id, "type_id": rig.actors[l].type_id,
                       "track_label": l} for n, l in node_labels.items()},
        "target_ids": [m["actor_id"] for m in assignment["member_actors"]],
        "timeline": [], "collisions": [], "capture_complete": False, "error": None}
    anchor = assignment["anchor_location"]
    anchor_model = np.array([anchor["x"], -anchor["y"], anchor["z"]])
    manifest["anchor_model_world"] = anchor_model.tolist()
    logger(f"[BEV VIDEO] Recording {scenario} anchor {anchor_index}; report={folder}")
    evaluator = overhead = collision_sensor = None
    captures, events, lock = [], [], threading.Lock()

    def collision(data):
        v = data.normal_impulse
        with lock:
            events.append({"frame_id": data.frame, "other_actor_id": data.other_actor.id,
                "other_type_id": data.other_actor.type_id,
                "impulse": float(np.linalg.norm([v.x, v.y, v.z]))})

    try:
        import carla
        collision_sensor = rig.world.spawn_actor(
            rig.world.get_blueprint_library().find("sensor.other.collision"),
            carla.Transform(), attach_to=rig.actors["ego"])
        collision_sensor.listen(collision)
        agent, manifest["drive"] = configure_route(rig, client, assignment, scenario, tm_port)
        evaluator = SemanticEvaluationRig(selected_rig, [assignment])
        overhead = OverheadCamera(rig.world, rig.actors["ego"])
        window, tasks = rig.freeze_inputs(labels=list(node_labels.values()),
                                         minimum_frame=rig.world.get_snapshot().frame, timeout_s=30)
        next_frame, passed_stamp = window[-1], None
        for index in range(int(seconds * FPS)):
            control = agent.run_step()
            if control is not None:
                rig.actors["ego"].apply_control(control)
            if on_tick:
                on_tick()
            window, tasks = rig.freeze_inputs(labels=list(node_labels.values()), end_frame=next_frame, timeout_s=10)
            truth, packets = evaluator.freeze(next_frame, list(node_labels.values()))
            image = overhead.freeze(next_frame)
            pose = task_pose(tasks["ego"])
            relative = local_points(anchor_model[None], pose)[0]
            row = {"frame_id": next_frame, "capture_sim_ns": truth["capture_sim_ns"],
                "anchor_forward_m": float(relative[0]), "distance_m": float(np.linalg.norm(relative[:2])),
                "ego_pose_model": pose.tolist(), **truth["ego_road_state"]}
            manifest["timeline"].append(row)
            captures.append((window, {n: tasks[l] for n, l in node_labels.items()}, truth, packets, image))
            if relative[0] < -5 and np.linalg.norm(relative[:2]) < 25 and any(
                    p["anchor_forward_m"] > 8 for p in manifest["timeline"][:-1]):
                passed_stamp = passed_stamp or truth["capture_sim_ns"]
            if index % FPS == 0:
                logger(f"[BEV VIDEO] Capture F{next_frame} t={index/FPS:.1f}s distance={row['distance_m']:.1f}m")
            if passed_stamp is not None and truth["capture_sim_ns"] - passed_stamp >= 3e9:
                break
            next_frame += round(1 / FPS / rig.step)
        manifest["collisions"] = list(events)
        start, end = select_passage(manifest["timeline"], events, set(manifest["target_ids"]),
                                    pre_seconds=14 if scenario == "S2" else 10)
        manifest.update(selected_indices=[start, end], capture_frames=len(captures), selected_frames=end - start)
        captures = captures[start:end]
        from bev_camera_freshness import CameraContentAudit
        content = CameraContentAudit()
        for _, tasks, truth, _, _ in captures:
            images = {node: {name: task["packets"][node_labels[node], name][-1]["raw"]
                             for name in CAMERAS} for node, task in tasks.items()}
            content.observe(truth["frame_id"], truth["capture_sim_ns"], images)
        manifest["camera_content_audit"] = content.result()
        content.require_passby_content()
        manifest["capture_complete"] = True
        logger(f"[BEV VIDEO] Pinned {len(captures)} same-frame samples; replay starts after Scenic stops.")
    except BaseException as exc:
        manifest["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        manifest["collisions"] = list(events)
        if collision_sensor is not None:
            try:
                collision_sensor.stop(); collision_sensor.destroy()
            except RuntimeError:
                pass
        if evaluator is not None:
            evaluator.close()
        if overhead is not None:
            overhead.close()
        (folder / "capture.json").write_text(json.dumps(manifest, indent=2))
    return {"folder": folder, "manifest": manifest, "captures": captures, "node_labels": node_labels}


def process_passby(sequence, *, timeout_s=60, logger=print):
    from bev_mesh import BEVMesh
    from bev_video_render import VideoRenderer, save_video_evidence
    folder, manifest = sequence["folder"], sequence["manifest"]
    captures, node_labels = sequence["captures"], sequence["node_labels"]
    mesh = renderer = None
    summary = {"source": manifest["source"], "metric2_pass": None, "live_latency_benchmark": False,
        "backend": "REAL_GROTH16", "actors": manifest["actors"], "frames": [], "error": None,
        "topology": "ego <-> assigned observer only; video demonstration, not all-neighbour evaluation"}
    try:
        mesh = BEVMesh({n: a["actor_id"] for n, a in manifest["actors"].items()}, timeout_s)
        mesh.warmup(captures[0][1])
        renderer = VideoRenderer(folder, manifest)
        for index, (window, tasks, truth, packets, overhead) in enumerate(captures):
            target_folder = folder / f"round_{index+1:03d}"
            rows = mesh.exchange(tasks, index+1, target_folder, save_inputs=True)
            if any(r["status"] != "fusion_complete" or r.get("artifact_error") for r in rows.values()):
                raise RuntimeError("Actual BEV/Groth16 replay failed; no video placeholder substituted.")
            (target_folder / "peers.json").write_text(json.dumps(rows, indent=2))
            evidence = save_video_evidence(target_folder, tasks, truth, packets, overhead, node_labels, manifest)
            record = renderer.append(target_folder, truth, evidence, index)
            summary["frames"].append(dict(record, verified_peers=rows["ego"]["verified_peer_ids"]))
            captures[index] = None
            if index % FPS == 0:
                logger(f"[BEV VIDEO] Replay {index+1}/{len(captures)} F{window[-1]} verified=1/1")
        renderer.close()
        summary.update(renderer.metadata, setup=mesh.setup)
        logger(f"[BEV VIDEO] Saved {folder / 'passby_2x3.mp4'}")
    except BaseException as exc:
        summary["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        close_error = None
        try:
            if renderer is not None:
                renderer.close()
        except Exception as exc:
            close_error = exc
            summary["encoder_cleanup_error"] = str(exc)
        finally:
            if mesh is not None:
                mesh.close()
            (folder / "summary.json").write_text(json.dumps(summary, indent=2))
        if close_error is not None and summary["error"] is None:
            raise close_error
    return summary
