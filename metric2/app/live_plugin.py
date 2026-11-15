"""Live-only measurement hook for the existing Scenic/custom-observer integration."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import threading

import numpy as np

from live_checks import raw_live_inputs, round_checks
from live_scene import ScenePreview
from report_data import atomic_json


def clock_anchor():
    before = time.perf_counter_ns()
    wall = time.time_ns()
    after = time.perf_counter_ns()
    return {"monotonic_ns": (before + after) // 2, "utc_ns": wall, "pairing_span_ns": after - before}


def install(context):
    import bev_live
    import bev_sensor_rig
    original_rig = bev_sensor_rig.BEVSensorRig

    class NearbyRig(original_rig):
        def __init__(self, world, ego, observers, config_path, **kwargs):
            import carla
            if not observers:
                raise ValueError("No actual custom observer")
            nearest = min(observers, key=lambda o: o.walker.get_location().distance(ego.get_location()))
            waypoint = world.get_map().get_waypoint(nearest.walker.get_location(), project_to_road=True,
                                                   lane_type=carla.LaneType.Driving)
            candidates = waypoint.previous(35) if waypoint is not None else []
            if not candidates:
                raise ValueError("No road approach for actual observer")
            start = min(candidates, key=lambda w: (w.is_junction, abs(w.lane_id - waypoint.lane_id)))
            transform = start.transform
            transform.location.z += .6
            ego.set_autopilot(False, context["tm_port"])
            ego.set_transform(transform)
            ego.set_target_velocity(carla.Vector3D())
            ego.set_target_angular_velocity(carla.Vector3D())
            ego.apply_control(carla.VehicleControl(brake=1, hand_brake=True))
            super().__init__(world, ego, [nearest], config_path, **kwargs)
            self.entry = {"source": "actual observer projected to Driving lane; 35m upstream before sensor warmup",
                          "ego_actor_id": ego.id, "observer_actor_id": nearest.walker.id,
                          "observer_type_id": nearest.walker.type_id,
                          "transform": transform.get_matrix(), "road_id": start.road_id, "lane_id": start.lane_id}
    bev_sensor_rig.BEVSensorRig = NearbyRig

    def run(rig, report_dir, *, rounds, radius_m, timeout_s, scenic_proc=None,
            scenario_labels=(), on_tick=None, logger=print, **unused):
        import carla
        from audit_bev_candidate_live import audit_round
        from bev_live import select_nearby
        from bev_mesh import BEVMesh
        from m2x.security import real_backend

        job, output = context["job"], context["output"]
        scene, session, scenario = context["scene"], context["session"], context["scenario"]
        folder = output / f"session_{session:02d}" / scenario
        folder.mkdir(parents=True, exist_ok=True)
        atomic_json(folder / "entry.json", rig.entry)
        labels = {("ego" if label == "ego" else f"observer_{actor.id}"): label for label, actor in rig.actors.items()}
        actor_ids = {node: rig.actors[label].id for node, label in labels.items()}
        info = {"session": session, "scenario": scenario,
                "actors": {n: {"actor_id": rig.actors[l].id, "type_id": rig.actors[l].type_id} for n, l in labels.items()}}
        mesh, preview, manager = None, None, None

        def phase(name, repeat=None):
            atomic_json(job / "live.json", {"phase": name, "current": {"session": session, "scenario": scenario, "repeat": repeat},
                                           "source": "live_carla"})

        def acquire(minimum):
            if scenic_proc is None or scenic_proc.poll() is not None:
                raise RuntimeError("Scenic ended before live sensor acceptance")
            if on_tick:
                on_tick()
            window, packets = rig.freeze_inputs(minimum_frame=minimum, timeout_s=40)
            tasks = {node: packets[label] for node, label in labels.items()}
            for task in tasks.values():
                task.update(input_source="live_raw_memory", input_id=f"{context['run']}:{scene}:F{window[-1]}")
            if len(select_nearby(tasks, radius_m)) != 1:
                raise ValueError("Actual observer is outside the 60m communication radius")
            return window, tasks

        try:
            if on_tick is not None:
                stop_tracking = threading.Event()
                def track():
                    while not stop_tracking.wait(.1):
                        try:
                            on_tick()
                        except RuntimeError:
                            pass
                tracking = threading.Thread(target=track, daemon=True)
                context["observer_tracker"] = (stop_tracking, tracking)
                tracking.start()
            preview = ScenePreview(rig, job, scenario)
            phase("warmup")
            mesh = BEVMesh(actor_ids, timeout_s)
            initial, warm = acquire(rig.start_frame + 1)
            mesh.warmup(warm)
            info.update(setup=mesh.setup, worker_pids={n: p.pid for n, p in mesh.processes.items()})
            atomic_json(folder / "setup.json", info)
            manager = context["client"].get_trafficmanager(context["tm_port"])
            context["traffic_manager"] = manager
            manager.set_synchronous_mode(True)
            manager.set_desired_speed(rig.actors["ego"], 10.)
            manager.auto_lane_change(rig.actors["ego"], False)
            rig.actors["ego"].apply_control(carla.VehicleControl(brake=0, hand_brake=False))
            rig.actors["ego"].set_autopilot(True, manager.get_port())
            # A warmup packet is never used as a timed round input.
            previous = rig.world.get_snapshot().frame
            backend = real_backend(60)
            for repeat in range(1, rounds + 1):
                phase("acquiring", repeat)
                window, tasks = acquire(previous + 1)
                evidence = raw_live_inputs(tasks, previous, actor_ids)
                previous = window[-1]
                exact_scene = preview.exact(window[-1])
                target = folder / f"round_{repeat:03d}"
                phase("processing", repeat)
                anchor = clock_anchor()
                peers = mesh.exchange(tasks, repeat, target)
                ego = peers["ego"]
                clock = {"source": "wall-clock anchor paired before actual live exchange",
                         "anchor": {k: str(v) for k, v in anchor.items()},
                         "peers": {node: {"started_ns": str(r["started_ns"]), "ended_ns": str(r["ended_ns"]),
                                   "started_utc_ns": str(anchor["utc_ns"] + r["started_ns"] - anchor["monotonic_ns"]),
                                   "ended_utc_ns": str(anchor["utc_ns"] + r["ended_ns"] - anchor["monotonic_ns"])}
                                   for node, r in peers.items()}}
                atomic_json(target / "ui_clock.json", clock)
                atomic_json(target / "peers.json", peers)
                atomic_json(target / "live_input.json", evidence)
                preview.save_exact(target, exact_scene, window[-1], evidence["capture_sim_ns"])
                result = round_checks(peers, actor_ids, window[-1], context["checkpoint_hash"])
                row = {"session": session, "scenario": scenario, "repeat": repeat,
                       "frame_id": window[-1], "input_id": tasks["ego"]["input_id"],
                       "capture_sim_ns": evidence["capture_sim_ns"], "input_source": "live_carla",
                       "started_ns": ego["started_ns"], "ended_ns": ego["ended_ns"],
                       "elapsed_ms": ego["elapsed_ms"], "stages_ms": ego["stages_ms"],
                       "output": str(target), "status": ego["status"], **result}
                context["rows"].append(row)
                with (output / "rounds.jsonl").open("a") as stream:
                    stream.write(json.dumps(row) + "\n")
                phase("audit", repeat)
                if result["functional_pass"]:
                    audit = audit_round(target, info, context["checkpoint_hash"], backend,
                                        fusion_mode=ego["fusion_mode"], calibrated=True)
                    atomic_json(target / "audit.json", audit)
                    row["audit"] = audit
                logger(f"[LIVE M2] {context.get('completed_before', 0) + len(context['rows'])}/{context['expected']} {scenario} F{window[-1]} "
                       f"ego={ego['elapsed_ms']/1000:.3f}s verified={len(ego['verified_peer_ids'])}/1 "
                       f"within18={result['speed_pass']}")
                if not result["functional_pass"]:
                    raise RuntimeError("; ".join(result["problems"]))
            return {"source": "live_carla", "rounds_completed": rounds}
        finally:
            if mesh:
                mesh.close()
            if preview:
                preview.close()
            if manager is not None:
                try:
                    rig.actors["ego"].set_autopilot(False, manager.get_port())
                    manager.set_synchronous_mode(False)
                except RuntimeError:
                    pass
    bev_live.run_live_bev = run
