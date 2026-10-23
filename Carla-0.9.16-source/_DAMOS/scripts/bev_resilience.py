"""Opt-in CARLA service experiment; never used by the formal metric replay."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import queue
import time

import numpy as np

from bev_fusion import fuse_bevs
from bev_fault_quality import (QUALITY_FAULTS, inject_quality_input, inspect_quality,
                               nominal_calibration_task, save_raw_task)
from bev_protocol import protect_bev
from m2x.transport import send_image

INPUT_FAULTS = ("none", "front_blackout", "lidar_missing", "sensor_delay",
                "output_freeze", "rgb_lidar_failure", *QUALITY_FAULTS)
WIRE_FAULTS = ("none", "drop", "tamper", "replay", "bad_proof")


def empty_reference(pose):
    # This is an explicitly unknown alignment reference, not a model prediction.
    return {"probabilities": np.full((17, 200, 200), 1 / 17, np.float16),
            "valid": np.zeros((200, 200), np.uint8),
            "pose": np.asarray(pose, np.float32).copy()}


def inspect_input(sample, expected_frame, reference=None):
    from bev_sensor_rig import CAMERAS, HEIGHT, WIDTH
    label = sample["label"]
    issues, cameras = [], {}
    for name in CAMERAS:
        packets = sample["packets"][label, name]
        packet = packets[-1]
        raw = packet["raw"]
        black_fraction = None
        if len(raw) != WIDTH * HEIGHT * 4:
            issues.append("MALFORMED_CAMERA:" + name)
        else:
            rgb = np.frombuffer(raw, np.uint8).reshape(HEIGHT, WIDTH, 4)[:, :, :3]
            black_fraction = float(np.mean(np.all(rgb == 0, axis=2)))
            if black_fraction >= .9999:
                issues.append("BLACK_CAMERA:" + name)
        if packet["frame"] != expected_frame:
            issues.append("STALE_CAMERA:" + name)
        cameras[name] = {"frame_id": packet["frame"], "black_fraction": black_fraction,
                         "raw_sha256": hashlib.sha256(raw).hexdigest()}
    lidar = sample["packets"][label, "lidar"][-1]
    points = np.frombuffer(lidar["raw"], np.float32) if len(lidar["raw"]) % 16 == 0 else np.array([])
    if not points.size or not np.isfinite(points).all():
        issues.append("MISSING_OR_INVALID_LIDAR")
    if lidar["frame"] != expected_frame:
        issues.append("STALE_LIDAR")
    stamps = [sample["packets"][label, name][-1]["timestamp"] for name in CAMERAS]
    if any(abs(stamp - lidar["timestamp"]) > 1e-6 for stamp in stamps):
        issues.append("UNSYNCHRONIZED_INPUT")
    quality = inspect_quality(sample, reference) if reference is not None else None
    if quality is not None:
        issues.extend(quality["issues"])
    return {"usable": not issues, "issues": issues, "camera_evidence": cameras,
            "lidar_frame_id": lidar["frame"], "lidar_points": int(points.size // 4),
            "capture_sim_ns": round(lidar["timestamp"] * 1e9),
            "lidar_raw_sha256": hashlib.sha256(lidar["raw"]).hexdigest(), "quality": quality}


def inject_input(sample, mode, held):
    if mode not in INPUT_FAULTS:
        raise ValueError("Unsupported input fault")
    if mode in QUALITY_FAULTS:
        return inject_quality_input(sample, mode)
    if mode == "sensor_delay":
        if held is None:
            raise ValueError("Delay injection requires a retained real input")
        return held
    if mode in ("none", "output_freeze"):
        return sample
    changed = dict(sample, packets={key: [dict(p) for p in packets]
                                  for key, packets in sample["packets"].items()})
    label = sample["label"]
    for (owner, name), packets in changed["packets"].items():
        if owner != label:
            continue
        blank = (mode == "front_blackout" and name == "cam_front") or (
            mode == "rgb_lidar_failure" and name.startswith("cam_"))
        if blank or (name == "lidar" and mode in ("lidar_missing", "rgb_lidar_failure")):
            for packet in packets:
                packet["raw"] = bytes(len(packet["raw"])) if blank else b""
    return changed


class ServicePolicy:
    """Conservative cooperative-monitoring states, with three fresh recovery rounds."""

    def __init__(self, recovery_rounds=3):
        if type(recovery_rounds) is not int or recovery_rounds < 1:
            raise ValueError("Recovery requires a positive consecutive round count")
        self.recovery_rounds = recovery_rounds
        self.state, self.clean_streak = "SAFE_STOP", 0

    def update(self, *, local_usable, trusted_peer_count, output_cells, fresh=True):
        previous = self.state
        if not fresh or trusted_peer_count < 1 or output_cells < 3:
            self.clean_streak, self.state = 0, "SAFE_STOP"
        elif not local_usable:
            self.clean_streak, self.state = 0, "DEGRADED_COOPERATIVE"
        elif previous != "NORMAL":
            self.clean_streak += 1
            self.state = "NORMAL" if self.clean_streak >= self.recovery_rounds else "RECOVERING"
        else:
            self.clean_streak = self.recovery_rounds
        return {"previous_state": previous, "state": self.state,
                "clean_streak": self.clean_streak, "recovery_rounds": self.recovery_rounds,
                "cooperative_service_available": self.state in ("NORMAL", "DEGRADED_COOPERATIVE"),
                "stop_required": self.state in ("SAFE_STOP", "RECOVERING"),
                "scope": "monitoring availability, not all-obstacle correctness or certified driving safety"}


def run_service_round(node, task, settings, predictor, backend, secret, commitment,
                      verifier, inbox, cache, prepare):
    from bev_live import task_pose
    from bev_occupancy import render_presence
    started = task["started_ns"]
    deadline = started + round(task["service_timeout_s"] * 1e9)
    current = task["input"]
    fault = task["fault"]
    frame = current["window"][-1]
    reference = empty_reference(task_pose(current))
    sample = inject_input(current, fault.get("input", "none"), cache.get("input"))
    quality_reference = cache.get("quality_reference")
    health = inspect_input(sample, frame, quality_reference)
    row = {"event": "service_round", "node_id": node, "round_id": task["round_id"],
           "frame_id": frame, "fault_injection": fault, "health": health,
           "verified_peer_ids": [], "usable_peer_ids": [], "rejected_packets": [],
           "status": "service_error", "metric2_pass": None, "errors": []}
    own, body, packet, arrays, inference = reference, None, None, None, None
    candidate = None
    capture_ns = health["capture_sim_ns"]
    output_frame = sample["window"][-1]
    if fault.get("input") == "output_freeze":
        if "prediction" not in cache:
            raise ValueError("Output freeze requires a retained real prediction")
        own = cache["prediction"]
        output_frame, capture_ns = cache["prediction_frame"], cache["prediction_capture_ns"]
        health["usable"] = False
        health["issues"].append("STALE_MODEL_OUTPUT")
        row["inference_skipped"] = "retained actual earlier prediction, not OS process termination"
    elif health["usable"] or fault.get("input") in QUALITY_FAULTS:
        try:
            model_sample = nominal_calibration_task(sample) if fault.get("input") == "camera_misalignment" else sample
            arrays, row["input"] = prepare(model_sample)
            candidate, inference = predictor.infer(arrays)
            own = candidate if health["usable"] else reference
            if fault.get("input") in QUALITY_FAULTS:
                row["quality_candidate"] = {"actual_inference_executed": True,
                    "rejected_before_publish_or_fusion": not health["usable"],
                    "uses_stale_nominal_camera_calibration": fault.get("input") == "camera_misalignment",
                    "observed_cells": int(candidate["valid"].sum())}
        except Exception as exc:
            health["usable"] = False
            health["issues"].append("INFERENCE_FAILED")
            row["errors"].append(type(exc).__name__ + ": " + str(exc))
    else:
        row["inference_skipped"] = "invalid raw input; explicitly unknown reference only"
    row["inference"] = inference
    if inference is not None and health["usable"]:
        cache.update(prediction=own, prediction_frame=output_frame, prediction_capture_ns=capture_ns)
    graph = task["graph"]
    expected = graph[node]
    shared, received_records = {}, {}
    try:
        packet, body, row["proof"] = protect_bev(backend, secret, commitment, own, {
            "session_id": settings["session_id"], "round_id": task["round_id"],
            "sender_id": node, "actor_id": current["actor_id"], "frame_id": output_frame,
            "capture_sim_ns": capture_ns, "checkpoint_sha256": predictor.checkpoint_hash,
            "checkpoint_step": predictor.step}, expected)
        sent_packet, sent_body = packet, body
        wire = fault.get("wire", "none")
        if wire not in WIRE_FAULTS:
            raise ValueError("Unsupported wire fault")
        if wire == "tamper":
            sent_body = body[:-1] + bytes([body[-1] ^ 1])
        elif wire == "bad_proof":
            sent_packet = copy.deepcopy(packet)
            sent_packet["proof"]["pi_a"][0] = "0"
        elif wire == "replay":
            sent_packet, sent_body = cache["packet"], cache["body"]
        for peer in expected:
            if wire != "drop":
                remaining = (deadline - time.perf_counter_ns()) / 1e9
                if remaining <= 0:
                    raise TimeoutError("SEND_DEADLINE")
                send_image(settings["addresses"][peer], sent_packet, sent_body, min(2., remaining))
        row["wire_action"] = wire
        packets_seen = 0
        while len(received_records) < len(expected) and packets_seen < 64:
            remaining = (deadline - time.perf_counter_ns()) / 1e9
            if remaining <= 0:
                break
            try:
                incoming, content = inbox.items.get(timeout=remaining)
            except queue.Empty:
                break
            packets_seen += 1
            manifest = incoming.get("manifest", {})
            sender = manifest.get("sender_id")
            try:
                decoded, timing = verifier.verify(incoming, content, round_id=task["round_id"],
                    frame_id=frame, expected_senders=expected,
                    expected_recipients=graph.get(sender, []))
                if (manifest["capture_sim_ns"] != round(current["packets"][current["label"], "lidar"][-1]["timestamp"] * 1e9)
                        or manifest["checkpoint_sha256"] != predictor.checkpoint_hash
                        or manifest["checkpoint_step"] != predictor.step):
                    raise ValueError("INCOMPATIBLE_CAPTURE_OR_MODEL")
                received_records[sender] = {"sha256": manifest["sha256"], "verification": timing,
                    "observed_cells": int(decoded["valid"].sum())}
                row["verified_peer_ids"].append(sender)
                if decoded["valid"].sum() >= 3:
                    shared[sender] = decoded
                    row["usable_peer_ids"].append(sender)
            except Exception as exc:
                row["rejected_packets"].append({"sender_id": sender,
                    "round_id": manifest.get("round_id"), "frame_id": manifest.get("frame_id"),
                    "reason": type(exc).__name__ + ": " + str(exc),
                    "body_sha256": hashlib.sha256(content).hexdigest()})
                # An authenticated-looking bad packet never terminates the receive loop.
                # Await valid data only until the original bounded deadline.
    except Exception as exc:
        row["errors"].append(type(exc).__name__ + ": " + str(exc))
    local = own if health["usable"] else reference
    fused = fuse_bevs(local, [shared[n] for n in sorted(shared)], mode=predictor.fusion_mode)
    ended = time.perf_counter_ns()
    row.update(status="service_complete", elapsed_ms=(ended - started) / 1e6,
        fusion_mode=predictor.fusion_mode,
        local_usable=health["usable"] and int(local["valid"].sum()) >= 3,
        local_observed_cells=int(local["valid"].sum()), output_cells=int(fused["valid"].sum()),
        peer_added_cells=int(fused["peer_added_cells"].sum()), received_bevs=received_records,
        missing_peer_ids=sorted(set(expected) - set(received_records)),
        deadline_expired=ended >= deadline, invalid_data_fused=False)
    folder = Path(task["save_dir"])
    folder.mkdir(parents=True, exist_ok=False)
    front = sample["packets"][sample["label"], "cam_front"][-1]
    if len(front["raw"]) == 450 * 800 * 4:
        import cv2
        rgb = np.frombuffer(front["raw"], np.uint8).reshape(450, 800, 4)[:, :, :3]
        cv2.imwrite(str(folder / f"{node}_input_front.png"), rgb)
    if arrays is not None:
        np.savez_compressed(folder / f"{node}_model_inputs.npz", **arrays)
    if fault.get("input") in QUALITY_FAULTS:
        save_raw_task(folder / f"{node}_raw_before.npz", current)
        save_raw_task(folder / f"{node}_raw_applied.npz", sample)
        if quality_reference is not None:
            save_raw_task(folder / f"{node}_quality_reference.npz", quality_reference)
        if candidate is not None:
            np.savez_compressed(folder / f"{node}_rejected_candidate.npz", **candidate)
    np.savez_compressed(folder / f"{node}_own.npz", **own)
    for sender, decoded in shared.items():
        np.savez_compressed(folder / f"{node}_received_{sender}.npz", **decoded)
    if node == "ego":
        import cv2
        np.savez_compressed(folder / "ego_fused.npz", **fused)
        cv2.imwrite(str(folder / "ego_presence.png"), render_presence(fused))
    if packet is not None:
        (folder / f"{node}_packet.json").write_text(json.dumps(packet, indent=2))
        (folder / f"{node}_body.bin").write_bytes(body)
        if fault.get("wire", "none") != "none":
            (folder / f"{node}_sent_packet.json").write_text(json.dumps(sent_packet, indent=2))
            (folder / f"{node}_sent_body.bin").write_bytes(sent_body)
        cache.update(packet=packet, body=body)
    cache["input"] = current
    if health["usable"] and fault.get("input", "none") == "none":
        cache["quality_reference"] = current
    (folder / f"{node}_service.json").write_text(json.dumps(row, indent=2))
    return row
