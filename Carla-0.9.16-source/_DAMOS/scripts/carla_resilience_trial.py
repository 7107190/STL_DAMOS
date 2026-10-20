"""CARLA/Scenic-owned clock, live sensors, real BEV/Groth16 and bounded fault recovery."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from bev_mesh import BEVMesh
from bev_resilience import ServicePolicy

CASES = {
    "front_blackout": ({"ego": {"input": "front_blackout"}}, "DEGRADED_COOPERATIVE"),
    "lidar_missing": ({"ego": {"input": "lidar_missing"}}, "DEGRADED_COOPERATIVE"),
    "sensor_delay": ({"ego": {"input": "sensor_delay"}}, "DEGRADED_COOPERATIVE"),
    "output_freeze": ({"ego": {"input": "output_freeze"}}, "DEGRADED_COOPERATIVE"),
    "link_drop": ({"observer": {"wire": "drop"}}, "SAFE_STOP"),
    "packet_tamper": ({"observer": {"wire": "tamper"}}, "SAFE_STOP"),
    "packet_replay": ({"observer": {"wire": "replay"}}, "SAFE_STOP"),
    "invalid_proof": ({"observer": {"wire": "bad_proof"}}, "SAFE_STOP"),
    "rgb_lidar_failure": ({"ego": {"input": "rgb_lidar_failure"}}, "DEGRADED_COOPERATIVE"),
    "compound_link_sensor": ({"ego": {"input": "rgb_lidar_failure"},
                              "observer": {"wire": "drop"}}, "SAFE_STOP"),
}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def stop_control(ego):
    import carla
    ego.apply_control(carla.VehicleControl(throttle=0., brake=1., hand_brake=False))


def speed(ego):
    v = ego.get_velocity()
    return float(np.linalg.norm([v.x, v.y, v.z]))


def brake_check(world, ego, origin):
    import carla
    ego.set_transform(origin)
    ego.apply_control(carla.VehicleControl())
    direction = origin.get_forward_vector()
    ego.set_target_velocity(carla.Vector3D(x=2 * direction.x, y=2 * direction.y, z=0.))
    before = world.wait_for_tick(5.)
    if before is None:
        raise TimeoutError("No CARLA tick after controlled brake-test velocity initialization")
    initial_speed = speed(ego)
    stop_control(ego)
    rows = []
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        snap = world.wait_for_tick(5.)
        if snap is None:
            continue
        current = speed(ego)
        location = ego.get_location()
        rows.append({"frame_id": snap.frame, "sim_seconds": snap.timestamp.elapsed_seconds,
                     "speed_mps": current, "location": [location.x, location.y, location.z]})
        if current <= .2:
            break
    stopped = bool(rows) and rows[-1]["speed_mps"] <= .2
    span = rows[-1]["sim_seconds"] - before.timestamp.elapsed_seconds if rows else None
    control = ego.get_control()
    return {"scope": "separate controlled 2m/s actuator check after the monitoring decision; not natural-driving detection distance",
        "initial_speed_mps": initial_speed, "control": {"throttle": control.throttle,
            "brake": control.brake, "hand_brake": control.hand_brake},
        "trace": rows, "stopped": stopped, "stop_sim_seconds": span,
        "criterion_pass": initial_speed >= 1 and stopped and span <= 5 and control.brake >= .99}


def run_trial(rig, report_root, plan_path, *, scenic_proc, assignments, on_tick=None, logger=print):
    import carla
    import cv2
    plan = json.loads(plan_path.read_text())
    if (plan.get("schema") != "DAMOS_RESILIENCE_TRIAL_V1"
            or not plan["cases"] or not set(plan["cases"]) <= set(CASES)
            or len(plan["cases"]) != len(set(plan["cases"]))):
        raise ValueError("Invalid frozen resilience plan")
    folder = Path(report_root) / "resilience"
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "plan.json", plan)
    if not assignments:
        raise ValueError("Resilience trial requires a real Scenic anchor and custom observer")
    ego, world = rig.actors["ego"], rig.world
    ego.set_autopilot(False, int(plan["port"]) + 6000)
    stop_control(ego)
    anchor = min(assignments, key=lambda a: ego.get_location().distance(
        rig.actors[a["track_label"]].get_location()))
    label = anchor["track_label"]
    observer = f"observer_{rig.actors[label].id}"
    labels = {"ego": "ego", observer: label}
    actors = {node: rig.actors[track].id for node, track in labels.items()}
    origin = world.get_map().get_waypoint(ego.get_location(), True, carla.LaneType.Driving).transform
    origin.location.z += .4
    ego.set_transform(origin)
    ego.set_target_velocity(carla.Vector3D())
    stop_control(ego)
    contacts = []
    collision = world.spawn_actor(world.get_blueprint_library().find("sensor.other.collision"),
                                  carla.Transform(), attach_to=ego)
    collision.listen(lambda d: contacts.append({"frame_id": d.frame, "other_actor_id": d.other_actor.id}))
    mesh, completed, round_id = None, [], 0
    minimum = world.get_snapshot().frame + 4

    def acquire():
        nonlocal minimum
        if scenic_proc is not None and scenic_proc.poll() is not None:
            raise RuntimeError("Scenic ended during resilience trial")
        if on_tick:
            on_tick()
        window, tasks = rig.freeze_inputs(labels=list(labels.values()), minimum_frame=minimum, timeout_s=30)
        minimum = window[-1] + 1
        distance = ego.get_location().distance(rig.actors[label].get_location())
        if distance > 60:
            raise RuntimeError("Pinned observer is outside the 60m service radius")
        return window, {node: tasks[track] for node, track in labels.items()}

    try:
        _, warmup = acquire()
        mesh = BEVMesh(actors, timeout_s=60)
        mesh.warmup(warmup)
        write_json(folder / "enrollment.json", {"session_id": mesh.session_id,
                   "actor_ids": actors, "peers": mesh.setup})
        for name in plan["cases"]:
            faults, expected = CASES[name]
            faults = {observer if node == "observer" else node: value for node, value in faults.items()}
            target = folder / name
            target.mkdir()
            policy = ServicePolicy(3)
            rounds = []
            case_contacts = len(contacts)
            for phase, count in (("baseline", 3), ("fault", 1), ("recovery", 3)):
                for index in range(count):
                    stop_control(ego)
                    window, tasks = acquire()
                    round_id += 1
                    output = target / f"round_{round_id:03d}"
                    output.mkdir()
                    for node, sample in tasks.items():
                        front = sample["packets"][sample["label"], "cam_front"][-1]
                        rgb = np.frombuffer(front["raw"], np.uint8).reshape(450, 800, 4)[:, :, :3]
                        cv2.imwrite(str(output / f"{node}_current_front.png"), rgb)
                    wall_start = time.perf_counter_ns()
                    rows = mesh.service_exchange(tasks, round_id, output,
                        faults=faults if phase == "fault" else {}, timeout_s=plan["receive_timeout_s"])
                    wall_elapsed = (time.perf_counter_ns() - wall_start) / 1e9
                    row = rows["ego"]
                    decision = policy.update(local_usable=row["local_usable"],
                        trusted_peer_count=len(row["usable_peer_ids"]), output_cells=row["output_cells"],
                        fresh=wall_elapsed <= plan["maximum_round_age_s"] and row["status"] == "service_complete")
                    snap = world.get_snapshot()
                    location = ego.get_location()
                    driving = world.get_map().get_waypoint(location, False, carla.LaneType.Driving) is not None
                    record = {"phase": phase, "phase_index": index + 1, "round_id": round_id,
                        "frame_id": window[-1], "capture_sim_ns": row["health"]["capture_sim_ns"],
                        "decision": decision, "ego": row, "peers": rows,
                        "parent_round_seconds_including_artifact_wait": wall_elapsed,
                        "on_driving_lane": driving, "snapshot_frame_after_result": snap.frame,
                        "path": str(output.relative_to(folder))}
                    rounds.append(record)
                    write_json(output / "decision.json", record)
                    logger(f"[RESILIENCE] {plan['weather']} {name} {phase}:{index+1} "
                           f"state={decision['state']} verified={len(row['verified_peer_ids'])}/1 "
                           f"elapsed={wall_elapsed:.3f}s")
                    if phase == "fault" and decision["stop_required"]:
                        record["brake_check"] = brake_check(world, ego, origin)
                        stop_control(ego)
                        minimum = world.get_snapshot().frame + 4
                        write_json(output / "decision.json", record)
            fault_row = next(r for r in rounds if r["phase"] == "fault")
            baseline = [r for r in rounds if r["phase"] == "baseline"]
            recovery = [r for r in rounds if r["phase"] == "recovery"]
            # Success means correct rejection/fallback/recovery, not target detection accuracy.
            checks = {"baseline_normal": baseline[-1]["decision"]["state"] == "NORMAL",
                "expected_fault_state": fault_row["decision"]["state"] == expected,
                "recovery_hysteresis": [r["decision"]["state"] for r in recovery] == ["RECOVERING", "RECOVERING", "NORMAL"],
                "all_on_driving_lane": all(r["on_driving_lane"] for r in rounds),
                "no_collision": len(contacts) == case_contacts,
                "no_unverified_peer_fused": all(set(r["ego"]["usable_peer_ids"]) <= set(r["ego"]["verified_peer_ids"]) for r in rounds),
                "brake_actuator_check": (fault_row.get("brake_check", {}).get("criterion_pass", False)
                                          if expected == "SAFE_STOP" else True)}
            if name in ("packet_tamper", "packet_replay", "invalid_proof"):
                checks["attack_rejected"] = bool(fault_row["ego"]["rejected_packets"])
            result = {"case": name, "weather": plan["weather"], "expected_fault_state": expected,
                "checks": checks, "criterion_pass": all(checks.values()), "rounds": rounds,
                "formal_metric2_or_3_case": False}
            completed.append(result)
            write_json(target / "result.json", result)
            logger(f"[RESILIENCE CASE] {name} pass={result['criterion_pass']} checks={checks}")
            write_json(folder / "summary.json", {"cases": completed, "planned": len(plan["cases"]),
                "completed": len(completed), "successful": sum(r["criterion_pass"] for r in completed),
                "fatal_error": None, "formal_metric2_or_3_case": False})
    except BaseException as exc:
        write_json(folder / "summary.json", {"cases": completed, "planned": len(plan["cases"]),
            "completed": len(completed), "successful": sum(r["criterion_pass"] for r in completed),
            "fatal_error": type(exc).__name__ + ": " + str(exc), "formal_metric2_or_3_case": False})
        raise
    finally:
        stop_control(ego)
        if mesh is not None:
            mesh.close()
        collision.stop()
        collision.destroy()
        write_json(folder / "collision_events.json", contacts)
        write_json(folder / "scene.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
            "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(), "anchor": anchor,
            "actors": {node: {"actor_id": actor.id, "type_id": actor.type_id}
                       for node, track in labels.items() for actor in [rig.actors[track]]},
            "ego_pose_carla": origin.get_matrix(),
            "stationary_monitoring_trial": True, "automatic_driving_resume": False,
            "clock_owner": "Scenic; this experiment never calls world.tick",
            "backend": "REAL_GROTH16", "deployment": "single-host independent processes, TCP loopback"})
    return completed
