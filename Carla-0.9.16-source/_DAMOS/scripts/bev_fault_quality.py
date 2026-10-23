"""Opt-in stationary raw-sensor quality checks and reproducible fault evidence."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

QUALITY_FAULTS = ("camera_degradation", "camera_misalignment", "lidar_noise",
                  "lidar_dropout", "lidar_outlier")
QUALITY_LIMITS = {"minimum_sharpness_ratio": .10, "minimum_reference_sharpness": .01,
                  "minimum_lidar_count_ratio": .25, "lidar_lower_deg": -17.,
                  "lidar_upper_deg": 8., "lidar_max_range_m": 81.,
                  "maximum_scan_violation_fraction": .05,
                  "maximum_mount_rotation_deg": 3., "maximum_mount_translation_m": .05}
INJECTION_SETTINGS = {"seed_origin": 20261024, "blur_kernel": 101, "blur_sigma": 25.,
                      "lidar_noise_std_m": 4., "lidar_retained_fraction": .10,
                      "lidar_outlier_added_fraction": .5,
                      "misalignment_yaw_delta_deg": 25.}
INFERENCE_REPLAY_LIMITS = {"maximum_probability_error": .01, "mean_probability_error": .0001}


def inject_quality_input(sample, mode):
    if mode not in QUALITY_FAULTS:
        raise ValueError("Unsupported quality fault")
    if mode == "camera_misalignment":
        return sample  # The live trial physically replaces the front sensor at a changed mount.
    changed = dict(sample, packets={key: [dict(p) for p in values]
                                  for key, values in sample["packets"].items()})
    label = sample["label"]
    for (owner, name), packets in changed["packets"].items():
        if owner != label:
            continue
        for packet in packets:
            rng = np.random.default_rng(INJECTION_SETTINGS["seed_origin"] + packet["frame"])
            if mode == "camera_degradation" and name == "cam_front":
                from bev_sensor_rig import HEIGHT, WIDTH
                image = np.frombuffer(packet["raw"], np.uint8).reshape(HEIGHT, WIDTH, 4).copy()
                image[:, :, :3] = cv2.GaussianBlur(image[:, :, :3], (101, 101), 25.)
                packet["raw"] = image.tobytes()
            elif name == "lidar" and mode.startswith("lidar_"):
                points = np.frombuffer(packet["raw"], np.float32).reshape(-1, 4).copy()
                if not len(points):
                    raise ValueError("Quality injection requires a nonempty actual LiDAR scan")
                if mode == "lidar_noise":
                    points[:, :3] += rng.normal(0., 4., points[:, :3].shape).astype(np.float32)
                elif mode == "lidar_dropout":
                    keep = max(1, int(len(points) * .10))
                    points = points[np.sort(rng.choice(len(points), keep, replace=False))]
                else:
                    count = max(1, len(points) // 2)
                    ghosts = np.column_stack([rng.uniform(8., 20., count), rng.uniform(-8., 8., count),
                                              rng.uniform(8., 14., count), np.full(count, .5)]).astype(np.float32)
                    points = np.concatenate([points, ghosts])
                packet["raw"] = points.tobytes()
    return changed


def sharpness(packet):
    from bev_sensor_rig import HEIGHT, WIDTH
    if len(packet["raw"]) != HEIGHT * WIDTH * 4:
        return None
    image = np.frombuffer(packet["raw"], np.uint8).reshape(HEIGHT, WIDTH, 4)
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (min(WIDTH, 400), min(HEIGHT, 225)))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def inspect_quality(sample, reference):
    """Decide from measured input, never from the requested injection name or scenario GT."""
    label, issues, evidence = sample["label"], [], {}
    current = sample["packets"][label, "cam_front"][-1]
    previous = reference["packets"][reference["label"], "cam_front"][-1]
    score, baseline = sharpness(current), sharpness(previous)
    ratio = score / baseline if baseline is not None and baseline > .01 and score is not None else None
    evidence["front_sharpness"] = {"current": score, "reference": baseline, "ratio": ratio}
    if ratio is not None and ratio < QUALITY_LIMITS["minimum_sharpness_ratio"]:
        issues.append("CAMERA_DETAIL_LOSS")
    lidar = sample["packets"][label, "lidar"][-1]
    old_lidar = reference["packets"][reference["label"], "lidar"][-1]
    points = np.frombuffer(lidar["raw"], np.float32).reshape(-1, 4) if len(lidar["raw"]) % 16 == 0 else np.empty((0, 4))
    count_ratio = len(points) / max(1, len(old_lidar["raw"]) // 16)
    evidence["lidar_count_ratio"] = count_ratio
    if count_ratio < QUALITY_LIMITS["minimum_lidar_count_ratio"]:
        issues.append("LIDAR_DENSITY_LOSS")
    if len(points) and np.isfinite(points).all():
        elevation = np.degrees(np.arctan2(points[:, 2], np.linalg.norm(points[:, :2], axis=1)))
        distance = np.linalg.norm(points[:, :3], axis=1)
        invalid = ((elevation < QUALITY_LIMITS["lidar_lower_deg"]) |
                   (elevation > QUALITY_LIMITS["lidar_upper_deg"]) |
                   (distance > QUALITY_LIMITS["lidar_max_range_m"]))
        fraction = float(invalid.mean())
        evidence["lidar_scan_violation_fraction"] = fraction
        if fraction > QUALITY_LIMITS["maximum_scan_violation_fraction"]:
            issues.append("LIDAR_SCAN_ENVELOPE_VIOLATION")
    mounts = sample.get("sensor_matrices", {})
    mount_evidence = {}
    if (label, "lidar") in mounts:
        actor_world = lidar["transform"] @ np.linalg.inv(mounts[label, "lidar"])
        from bev_sensor_rig import CAMERAS
        for name in CAMERAS:
            measured = np.linalg.inv(actor_world) @ sample["packets"][label, name][-1]["transform"]
            expected = mounts[label, name]
            delta = expected[:3, :3].T @ measured[:3, :3]
            angle = float(np.degrees(np.arccos(np.clip((np.trace(delta) - 1) / 2, -1, 1))))
            translation = float(np.linalg.norm(expected[:3, 3] - measured[:3, 3]))
            mount_evidence[name] = {"rotation_error_deg": angle, "translation_error_m": translation,
                                   "measured_relative": measured.tolist(), "declared_relative": expected.tolist()}
            if angle > 3. or translation > .05:
                issues.append("CAMERA_MOUNT_MISMATCH:" + name)
    evidence["mounts"] = mount_evidence
    return {"issues": issues, "evidence": evidence, "limits": dict(QUALITY_LIMITS),
            "reference_frame_id": reference["window"][-1],
            "scope": "stationary baseline, severe blur/density loss or known scan/mount violation; not universal sensor fault detection"}


def nominal_calibration_task(sample):
    """Diagnostic model pass with stale nominal calibration; keep measured raw evidence separately."""
    changed = dict(sample, packets={key: [dict(p) for p in packets]
                                  for key, packets in sample["packets"].items()})
    label = sample["label"]
    from bev_sensor_rig import CAMERAS
    for index, lidar in enumerate(sample["packets"][label, "lidar"]):
        actor_world = lidar["transform"] @ np.linalg.inv(sample["sensor_matrices"][label, "lidar"])
        for name in CAMERAS:
            changed["packets"][label, name][index]["transform"] = actor_world @ sample["sensor_matrices"][label, name]
    return changed


def save_raw_task(path, task):
    names = [name for owner, name in task["packets"] if owner == task["label"]]
    metadata = {key: task[key] for key in ("label", "actor_id", "actor_type_id", "window", "camera_fovs")}
    metadata["names"] = names
    arrays = {"metadata": np.array(json.dumps(metadata))}
    for i, name in enumerate(names):
        arrays[f"mount_{i}"] = task["sensor_matrices"][task["label"], name]
        for t, packet in enumerate(task["packets"][task["label"], name]):
            arrays[f"raw_{i}_{t}"] = np.frombuffer(packet["raw"], np.uint8)
            arrays[f"transform_{i}_{t}"] = packet["transform"]
            arrays[f"stamp_{i}_{t}"] = np.array([packet["frame"], packet["timestamp"]], np.float64)
    np.savez_compressed(path, **arrays)


def load_raw_task(path):
    with np.load(Path(path), allow_pickle=False) as archive:
        task = json.loads(archive["metadata"].item())
        names = task.pop("names")
        label = task["label"]
        task.update(packets={}, sensor_matrices={})
        for i, name in enumerate(names):
            task["sensor_matrices"][label, name] = archive[f"mount_{i}"].copy()
            task["packets"][label, name] = []
            for t in range(len(task["window"])):
                frame, stamp = archive[f"stamp_{i}_{t}"]
                task["packets"][label, name].append({"raw": archive[f"raw_{i}_{t}"].tobytes(),
                    "transform": archive[f"transform_{i}_{t}"].copy(), "frame": int(frame), "timestamp": float(stamp)})
    return task
