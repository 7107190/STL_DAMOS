"""Current CARLA callbacks -> bounded live queue -> model/proofs/fusion -> scoring."""
import json
import queue
import threading
import time
from types import SimpleNamespace

import numpy as np

from live_checks import raw_live_inputs, round_checks
from live_plugin import clock_anchor
from live_scene import ScenePreview
from m3_rules import MIN_SPAN_NS, duration
from report_data import atomic_json
from damos_paths import runtime_root


def install(context):
    import bev_live
    import bev_sensor_rig
    original = bev_sensor_rig.BEVSensorRig
    case = context["case"]

    class SelectedRig(original):
        def __init__(self, world, ego, observers, config_path, **kwargs):
            x, y = case["target_scenic_xy"]
            choice = min(observers, key=lambda o: (
                (o.anchor.location.x - x) ** 2 + (o.anchor.location.y + y) ** 2,
                o.anchor.anchor_index))
            self.selected_label = choice.anchor.track_label
            super().__init__(world, ego, [choice], config_path, **kwargs)

    bev_sensor_rig.BEVSensorRig = SelectedRig

    def run(rig, report_dir, *, timeout_s, anchor_assignments, on_tick=None, **unused):
        import carla
        from audit_bev_candidate_live import audit_round
        from bev_accuracy_batch import CHECKPOINT_SHA, score_round
        from bev_camera_freshness import CameraContentAudit
        from bev_evaluation import SemanticEvaluationRig, local_points
        from bev_live import task_pose
        from bev_mesh import BEVMesh
        from bev_passby_video import configure_route
        from m2x.security import real_backend
        from m3_visuals import render_target
        from m3_driving import maintain

        folder, job = context["target"], context["job"]
        assignment = next(a for a in anchor_assignments if a["track_label"] == rig.selected_label)
        label = assignment["track_label"]
        observer = "observer_" + str(rig.actors[label].id)
        labels = {"ego": "ego", observer: label}
        actor_ids = {n: rig.actors[l].id for n, l in labels.items()}
        manifest = {"case": case, "scenario": case["scenario"], "anchor_index": assignment["anchor_index"],
                    "actors": {n: {"actor_id": rig.actors[l].id, "type_id": rig.actors[l].type_id} for n,l in labels.items()},
                    "target_ids": sorted(m["actor_id"] for m in assignment["member_actors"]),
                    "assignment": assignment,
                    "candidate_anchors": [{k:a[k] for k in ("anchor_index", "track_label", "anchor_location")} for a in anchor_assignments],
                    "selection_wall_ns": time.time_ns(),
                    "source": "live_carla_sensor_stream", "gt_used_for_inference": False,
                    "selection": "closest actual anchor to frozen map reference, selected before inference"}
        atomic_json(folder / "manifest.json", manifest)
        context["manifest"] = manifest
        stream, stop = queue.Queue(maxsize=32), threading.Event()
        worker = mesh = evaluator = preview = None
        post_driver = None
        post_drive_rows, post_drive_errors = [], []
        capture = {"case_id": case["case_id"], "source": "live_carla_sensor_stream", "timeline": [],
                   "capture_complete": False, "error": None, "producer_started_wall_ns": None}
        rows = context["rows"]
        content = CameraContentAudit()

        def state(phase, **extra):
            atomic_json(job / "live.json", {"case_id": case["case_id"], "scenario": case["scenario"],
                        "phase": phase, "samples_processed": len(rows), **extra})

        def acquire(frame=None):
            window, raw = rig.freeze_inputs(labels=list(labels.values()), end_frame=frame, timeout_s=20)
            tasks = {n: dict(raw[l]) for n,l in labels.items()}
            for task in tasks.values():
                task.update(input_source="live_raw_memory", input_id=f"{context['run']}:{case['case_id']}:F{window[-1]}")
            return window, tasks

        def produce(agent):
            nonlocal post_driver
            try:
                capture["producer_started_wall_ns"] = time.time_ns()
                frame = rig.world.get_snapshot().frame + 4
                total = int(case["window_seconds"] * 5)
                for index in range(int(case["approach_timeout_seconds"] * 5) + total):
                    if stop.is_set():
                        break
                    control = agent.run_step()
                    if control is not None:
                        rig.actors["ego"].apply_control(control)
                    if on_tick:
                        on_tick()
                    window, tasks = acquire(frame)
                    truth, semantic = evaluator.freeze(frame, list(labels.values()), timeout_s=20)
                    pose = task_pose(tasks["ego"])
                    centres = np.asarray([t["location_model_world"] for t in truth["targets"]])
                    relative = local_points(centres.mean(axis=0)[None], pose)[0]
                    distance = float(np.linalg.norm(relative[:2]))
                    atomic_json(job / "capture_progress.json", {"case_id": case["case_id"], "distance_m": distance,
                                "captured": len(capture["timeline"]), "target": total, "frame_id": frame})
                    if capture["timeline"] or distance <= case["entry_radius_m"]:
                        from bev_sensor_rig import CAMERAS
                        content.observe(frame, truth["capture_sim_ns"], {node:{name:tasks[node]["packets"][label,name][-1]["raw"] for name in CAMERAS} for node,label in labels.items()})
                        tick = {"frame_id": frame, "capture_sim_ns": truth["capture_sim_ns"], "distance_m": distance,
                                "captured_wall_ns": time.time_ns(), "on_driving_lane": truth["ego_road_state"]["on_driving_lane"]}
                        capture["timeline"].append(tick)
                        # Temporal RGB histories overlap by two frames; evaluated current frames never repeat.
                        stream.put((window, tasks, truth, semantic, preview.exact(frame), tick), timeout=20)
                        if len(capture["timeline"]) == total:
                            capture["capture_complete"] = True
                            break
                    elif index >= int(case["approach_timeout_seconds"] * 5):
                        raise TimeoutError("Ego did not reach the preselected anchor within 240 simulated seconds")
                    frame += 2
                capture["camera_content_audit"] = content.require_passby_content()
                capture["producer_ended_wall_ns"] = time.time_ns()
                atomic_json(folder / "capture.json", capture)
                atomic_json(job / "capture_progress.json", {"case_id": case["case_id"], "distance_m": distance,
                            "captured": len(capture["timeline"]), "target": total, "frame_id": frame})
                stream.put(None, timeout=20)
                post_driver=threading.Thread(target=maintain,args=(rig.world,rig.actors["ego"],agent,stop,post_drive_rows,post_drive_errors),daemon=True)
                post_driver.start()
            except BaseException as exc:
                capture["error"] = type(exc).__name__ + ": " + str(exc)
                atomic_json(folder / "capture.json", capture)
                stream.put(exc)

        try:
            if on_tick:
                orient_stop = threading.Event()
                def orient():
                    while not orient_stop.wait(.1):
                        try:
                            on_tick()
                        except RuntimeError:
                            pass
                orient_thread = threading.Thread(target=orient, daemon=True)
                orient_thread.start()
                context["observer_tracker"] = (orient_stop, orient_thread)
            preview = ScenePreview(rig, job, case["scenario"])
            state("warmup")
            mesh = BEVMesh(actor_ids, timeout_s)
            _, warm = acquire()
            mesh.warmup(warm)
            info = {"actors": manifest["actors"], "setup": mesh.setup}
            atomic_json(folder / "setup.json", info)
            subset = SimpleNamespace(world=rig.world, step=rig.step, records=rig.records,
                                     actors={l: rig.actors[l] for l in labels.values()})
            evaluator = SemanticEvaluationRig(subset, [assignment], instance_cameras=False)
            if case["case_id"] in ("M3-004-S4", "M3-047-S2"):
                import metric3_retest_navigation
                metric3_retest_navigation.ROOT = runtime_root(context["root"]) / "Carla-0.9.16-source"
                configure_route = metric3_retest_navigation.configure_route
            agent, drive = configure_route(rig, context["client"], assignment, case["scenario"], context["port"] + 6000)
            if case["scenario"] == "S3":
                context["client"].get_trafficmanager(context["port"] + 6000).set_synchronous_mode(True)
            atomic_json(folder / "drive.json", drive)
            backend = real_backend(60)
            state("approach")
            worker = threading.Thread(target=produce, args=(agent,), daemon=True)
            worker.start()
            while True:
                item = stream.get(timeout=case["approach_timeout_seconds"] + 120)
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                window, tasks, truth, semantic, scene, tick = item
                index = len(rows) + 1
                target = folder / f"round_{index:03d}"
                state("processing", frame_id=window[-1])
                evidence = raw_live_inputs(tasks, window[0] - 1, actor_ids)
                anchor = clock_anchor()
                peers = mesh.exchange(tasks, index, target, save_inputs=False)
                atomic_json(target / "peers.json", peers)
                atomic_json(target / "live_input.json", evidence)
                atomic_json(target / "stream_clock.json", {"captured_wall_ns": tick["captured_wall_ns"],
                            "dispatch_wall_ns": anchor["utc_ns"], "dispatch_perf_ns": anchor["monotonic_ns"],
                            "queue_age_ms": (anchor["utc_ns"]-tick["captured_wall_ns"])/1e6})
                preview.save_exact(target, scene, window[-1], truth["capture_sim_ns"])
                checks = round_checks(peers, actor_ids, window[-1], CHECKPOINT_SHA)
                if not checks["functional_pass"]:
                    raise RuntimeError(str(checks["problems"]))
                row = score_round(target, truth, semantic, labels, manifest, index)
                row.update(source="live_carla_sensor_stream", queued_before_inference=True,
                           dispatch_wall_ns=anchor["utc_ns"], ego_processing_ms=peers["ego"]["elapsed_ms"])
                atomic_json(target / "evidence.json", row)
                render_target(target, row)
                audit = audit_round(target, info, CHECKPOINT_SHA, backend, fusion_mode=peers["ego"]["fusion_mode"], calibrated=True)
                atomic_json(target / "audit.json", audit)
                if audit["proofs_reverified"] != 2:
                    raise ValueError("Real Groth16 proof audit incomplete")
                rows.append(row)
                with (folder / "samples.jsonl").open("a") as output:
                    output.write(json.dumps(row) + "\n")
                state("scoring", frame_id=window[-1], longest_seconds=duration(rows)/1e9,
                      detected=row["fused_detected"], connected_cells=row["fused_connected_overlap_cells"],
                      eligible=row["eligible"], proofs_reverified=len(rows)*2)
                print(f"[LIVE M3] {case['case_id']} sample={index}/30 span={duration(rows)/1e9:.3f}s cells={row['fused_connected_overlap_cells']}", flush=True)
                if duration(rows) >= MIN_SPAN_NS:
                    break
            worker.join(timeout=10)
            if worker.is_alive() or not capture["capture_complete"]:
                raise RuntimeError("Incomplete fixed six-second sensor acquisition")
            if capture["error"]:
                raise RuntimeError(capture["error"])
            if post_drive_errors:
                raise RuntimeError("Post-window navigation: "+str(post_drive_errors))
            span = duration(rows)
            result = {"case_id": case["case_id"], "scenario": case["scenario"], "source": "live_carla_sensor_stream",
                      "status": "success" if span >= MIN_SPAN_NS else "failure" if any(r["eligible"] for r in rows) else "not_evaluable",
                      "successful": span >= MIN_SPAN_NS, "samples": len(rows), "anchor_index": assignment["anchor_index"],
                      "member_actor_ids": manifest["target_ids"], "fused_longest_span_ns": str(span),
                      "fused_longest_span_seconds": span/1e9, "ego_longest_span_seconds": duration(rows,"ego")/1e9,
                      "proofs_reverified": len(rows)*2, "gt_used_for_inference": False,
                      "first_inference_dispatch_wall_ns": rows[0]["dispatch_wall_ns"] if rows else None,
                      "producer_ended_wall_ns": capture.get("producer_ended_wall_ns"), "error": None}
            if result["status"] == "failure" and len(rows) != 30:
                raise ValueError("A detection failure requires the complete 30-sample window")
            context["result"] = result
            atomic_json(folder / "result.json", result)
            return result
        finally:
            stop.set()
            if worker:
                worker.join(timeout=25)
                if worker.is_alive():
                    raise RuntimeError("Sensor stream producer did not stop")
            if post_driver:
                post_driver.join(timeout=5)
                if post_driver.is_alive():
                    raise RuntimeError("Post-window driver did not stop")
            atomic_json(folder/"post_window_drive.json",{"records":post_drive_rows,"errors":post_drive_errors,
                        "scope":"same existing agent, after the fixed 30-sample acquisition only; not a model input or detection result"})
            try:
                rig.actors["ego"].set_autopilot(False,context["port"]+6000)
                rig.actors["ego"].apply_control(carla.VehicleControl(brake=1,hand_brake=True))
            except RuntimeError:
                pass
            atomic_json(folder / "capture.json", capture)
            if mesh:
                mesh.close()
            if evaluator:
                evaluator.close()
            if preview:
                preview.close()
            if post_drive_errors:
                raise RuntimeError("Post-window navigation: "+str(post_drive_errors))

    bev_live.run_live_bev = run
