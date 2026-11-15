"""Six-camera / current LiDAR input acquisition for the handed-over BEV model."""

from __future__ import annotations

import ast
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import time

import carla
import cv2
import numpy as np
from PIL import Image

from custom_walker_runtime import (
    SENSOR_CONFIG_LINE_PATTERN, load_camera_specs_from_sensor_config,
    DEFAULT_OBSERVER_CAMERA_SPECS, load_vehicle_camera_specs, safe_sensor_role_name, serialize_transform,
    DELIVERYBOT_ID, HUMANOID_ID,
)

CAMERAS = (
    "cam_front_left", "cam_front", "cam_front_right",
    "cam_back_left", "cam_back", "cam_back_right",
)
WIDTH, HEIGHT, FOV = 800, 450, 90.0
REFERENCE_FOVS = {name: (110.0 if name == "cam_back" else 70.0) for name in CAMERAS}
REFERENCE_PREFIX = "reference_"
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
EGO_BASIS = np.diag([1.0, -1.0, 1.0])
WORLD_BASIS = np.diag([1.0, -1.0, 1.0, 1.0])
# Optical x=right,y=down,z=forward -> CARLA x=forward,y=right,z=up.
OPTICAL_TO_CARLA = np.array([[0., 0., 1.], [1., 0., 0.], [0., -1., 0.]])


def camera_calibration(relative_matrix, fov=FOV):
    m = np.asarray(relative_matrix, np.float64)
    if not 0 < fov < 180:
        raise ValueError("Invalid camera FOV.")
    focal = WIDTH / (2 * math.tan(math.radians(fov / 2)))
    intr = np.array([[focal, 0, WIDTH / 2], [0, focal, HEIGHT / 2], [0, 0, 1]], np.float32)
    return ((EGO_BASIS @ m[:3, :3] @ OPTICAL_TO_CARLA).astype(np.float32),
            (EGO_BASIS @ m[:3, 3]).astype(np.float32), intr)


def preprocess_rgb(rgb):
    # Match the author's validation resize/crop and ImageNet normalization.
    image = Image.fromarray(rgb).resize((400, 225), Image.Resampling.BILINEAR)
    image = np.asarray(image.crop((40, 97, 360, 225)), dtype=np.float32) / 255
    post_rot = np.diag([0.5, 0.5, 1.0]).astype(np.float32)
    post_trans = np.array([-40, -97, 0], np.float32)
    return ((image - MEAN) / STD).transpose(2, 0, 1), post_rot, post_trans


def rasterize_lidar(raw_points, relative_matrix):
    points = np.asarray(raw_points, np.float32)
    if points.ndim != 2 or points.shape[1] != 4 or not len(points):
        raise ValueError("Expected nonempty CARLA ray_cast LiDAR (M,4), not nuScenes (M,5).")
    if not np.isfinite(points).all():
        raise ValueError("Non-finite LiDAR input.")
    m = np.asarray(relative_matrix, np.float64)
    ego = (points[:, :3] @ m[:3, :3].T + m[:3, 3]) @ EGO_BASIS
    ix = np.floor((ego[:, 0] + 40) / 0.4).astype(np.int64)
    iy = np.floor((ego[:, 1] + 40) / 0.4).astype(np.int64)
    ok = (ix >= 0) & (ix < 200) & (iy >= 0) & (iy < 200)
    flat, z, intensity = ix[ok] * 200 + iy[ok], ego[ok, 2], points[ok, 3]
    count = np.bincount(flat, minlength=40000).astype(np.float32)
    hi, lo, refl = np.full(40000, -1e4, np.float32), np.full(40000, 1e4, np.float32), np.zeros(40000, np.float32)
    np.maximum.at(hi, flat, z)
    np.minimum.at(lo, flat, z)
    np.maximum.at(refl, flat, intensity)
    occupied = count > 0
    hi[~occupied] = lo[~occupied] = 0
    return np.stack([occupied, np.log1p(count), hi, lo, hi - lo, refl]).reshape(6, 200, 200).astype(np.float32)


def latest_common_window(buffers, names, minimum_frame=0, history_frames=4):
    if not isinstance(history_frames,int) or not 1 <= history_frames <= 12:
        raise ValueError("History must contain 1..12 frames.")
    frames = set.intersection(*(set(buffers.get(name, {})) for name in names))
    for end in sorted(frames, reverse=True):
        window = list(range(end - history_frames + 1, end + 1))
        if window[0] >= minimum_frame and all(f in frames for f in window):
            return window
    return None


def config_actor_token(config_path, actor):
    if f"({actor.type_id})" in config_path.read_text(encoding="utf-8"):
        return actor.type_id
    return {HUMANOID_ID: "walker.pedestrian.0080",
            DELIVERYBOT_ID: "walker.pedestrian.0090"}.get(actor.type_id, actor.type_id)


def lidar_mount(config_path, actor):
    height = 1.83 if actor.type_id.startswith("vehicle.") else 1.5
    fallback = carla.Transform(carla.Location(z=height))
    if not config_path.exists():
        raise FileNotFoundError(config_path)
    token = config_actor_token(config_path, actor)
    selected = False
    for line in config_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Initializing Mobility"):
            selected = (("vehicle." in line) if actor.type_id.startswith("vehicle.")
                        else token in line)
        elif selected:
            match = SENSOR_CONFIG_LINE_PATTERN.match(line.strip())
            if match and match.group("name").strip() == "lidar":
                config = ast.literal_eval(match.group("transform"))
                return carla.Transform(carla.Location(*config["location"]),
                                       carla.Rotation(*config["rotation"]))
    return fallback


class BEVSensorRig:
    def __init__(self, world, ego, observers, config_path, *, compare_inputs=False):
        self.world = world
        self.sensors = []
        self.records = []
        self.buffers = defaultdict(dict)
        self.lock = threading.Lock()
        self.errors = []
        self.start_frame = world.get_snapshot().frame
        self.step = float(world.get_settings().fixed_delta_seconds or 0.1)
        self.actors = {"ego": ego, **{o.track_label: o.walker for o in observers}}
        self.observers = {o.track_label: o for o in observers}
        self.sensor_matrices = {}
        self.camera_fovs = {name: FOV for name in CAMERAS}
        self.camera_names = list(CAMERAS)
        if compare_inputs:
            self.camera_names += [REFERENCE_PREFIX + name for name in CAMERAS]
            self.camera_fovs.update({REFERENCE_PREFIX + name: value for name,value in REFERENCE_FOVS.items()})
        try:
            for label, actor in self.actors.items():
                if actor.type_id.startswith("vehicle."):
                    specs = load_vehicle_camera_specs(config_path)
                else:
                    specs = load_camera_specs_from_sensor_config(
                        config_path, actor_type_token=config_actor_token(config_path, actor),
                        fallback_specs=DEFAULT_OBSERVER_CAMERA_SPECS)
                by_name = {s.name: s for s in specs}
                for name in CAMERAS:
                    self._attach(actor, label, name, "sensor.camera.rgb", by_name[name].transform)
                if compare_inputs:
                    for name in CAMERAS:
                        self._attach(actor, label, REFERENCE_PREFIX + name, "sensor.camera.rgb", by_name[name].transform)
                self._attach(actor, label, "lidar", "sensor.lidar.ray_cast", lidar_mount(config_path, actor))
        except Exception:
            self.close()
            raise

    def _attach(self, actor, label, name, blueprint_id, transform):
        bp = self.world.get_blueprint_library().find(blueprint_id)
        attrs = {"sensor_tick": "0.0", "role_name": safe_sensor_role_name(label, "bev_" + name)}
        if name == "lidar":
            attrs.update(channels="64", range="80", points_per_second="1300000",
                         rotation_frequency=str(1 / self.step), upper_fov="7.0", lower_fov="-16.0")
        else:
            attrs.update(image_size_x=str(WIDTH), image_size_y=str(HEIGHT), fov=str(self.camera_fovs[name]))
        for key, value in attrs.items():
            if bp.has_attribute(key):
                bp.set_attribute(key, value)
        sensor = self.world.spawn_actor(bp, transform, attach_to=actor,
                                       attachment_type=carla.AttachmentType.Rigid)
        self.sensors.append(sensor)
        if sensor.parent is None or sensor.parent.id != actor.id:
            raise RuntimeError(f"Incorrect sensor parent: {label}/{name}.")
        if name != "lidar" and label in self.observers:
            self.observers[label].sensors.append(sensor)
        self.sensor_matrices[label, name] = np.asarray(transform.get_matrix(), np.float64)
        self.records.append({"track_label": label, "actor_id": actor.id,
                             "actor_type_id": actor.type_id, "sensor_name": name,
                             "sensor_actor_id": sensor.id, "blueprint_id": blueprint_id,
                             "parent_actor_id": sensor.parent.id,
                             "relative_transform": serialize_transform(transform),
                             "relative_matrix_carla": transform.get_matrix(),
                             "attributes": dict(sensor.attributes)})
        sensor.listen(lambda data, key=(label, name): self._receive(key, data))

    def _receive(self, key, data):
        try:
            packet = {"frame": data.frame, "timestamp": float(data.timestamp),
                      "raw": bytes(data.raw_data),
                      "transform": np.asarray(data.transform.get_matrix(), np.float64)}
            with self.lock:
                buffer = self.buffers[key]
                buffer[data.frame] = packet
                for frame in sorted(buffer)[:-12]:
                    del buffer[frame]
        except Exception as exc:
            with self.lock:
                self.errors.append(f"{key}: {exc}")

    def freeze_inputs(self, labels=None, minimum_frame=0, timeout_s=30, history_frames=4, *, end_frame=None):
        if not isinstance(history_frames, int) or not 1 <= history_frames <= 12:
            raise ValueError("History must contain 1..12 frames.")
        if end_frame is not None and (not isinstance(end_frame, int) or end_frame < history_frames - 1):
            raise ValueError("Invalid exact input end frame.")
        labels = list(self.actors if labels is None else labels)
        cameras = getattr(self, "camera_names", CAMERAS)
        names = [(label, name) for label in labels for name in (*cameras, "lidar")]
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self.lock:
                if self.errors:
                    raise RuntimeError("; ".join(self.errors))
                if end_frame is None:
                    window = latest_common_window(self.buffers, names, minimum_frame, history_frames)
                else:
                    window = list(range(end_frame - history_frames + 1, end_frame + 1))
                    if window[0] < minimum_frame or not all(
                            f in self.buffers.get(name, {}) for name in names for f in window):
                        window = None
                if window:
                    tasks = {}
                    for label in labels:
                        actor = self.actors[label]
                        tasks[label] = {
                            "label": label, "actor_id": actor.id, "actor_type_id": actor.type_id,
                            "window": window,
                            "packets": {(label, name): [self.buffers[label, name][f] for f in window]
                                        for name in (*cameras, "lidar")},
                            "camera_fovs": dict(self.camera_fovs),
                            "sensor_matrices": {key: matrix for key, matrix in self.sensor_matrices.items()
                                                if key[0] == label},
                        }
                    return window, tasks
            time.sleep(0.05)
        raise TimeoutError("No four consecutive common BEV sensor frames.")

    def capture(self, report_root, scenario_labels, timeout_s=30):
        names = [(label, name) for label in self.actors for name in (*CAMERAS, "lidar")]
        # No world.tick(): Scenic remains the sole synchronous clock owner.
        deadline = time.monotonic() + timeout_s
        minimum_frame = self.world.get_snapshot().frame + 3
        window = None
        while time.monotonic() < deadline:
            with self.lock:
                if self.errors:
                    raise RuntimeError("; ".join(self.errors))
                window = latest_common_window(self.buffers, names, minimum_frame)
                if window:
                    packets = {key: [self.buffers[key][f] for f in window] for key in names}
                    break
            time.sleep(0.05)
        if not window:
            coverage = {f"{label}/{name}": sorted(self.buffers.get((label, name), {})) for label, name in names}
            raise TimeoutError(f"No four common camera/LiDAR frames: {coverage}")
        report = Path(report_root) / ("bev_sensor_inputs_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"))
        report.mkdir(parents=True)
        manifest = {"schema": "damos-bev-input-v1", "scenarios": list(scenario_labels),
                    "frames": window, "history_frames": 4, "camera_order": list(CAMERAS),
                    "fixed_delta_seconds": self.step, "sensor_config": "sensor_config.txt mounts",
                    "coordinate_convention": "model ego/world: x forward, y left, z up; cameras optical x right,y down,z forward",
                    "inference_executed": False, "zkp_bev_sharing_executed": False,
                    "sensors": self.records, "actors": []}
        sheets = []
        for label, actor in self.actors.items():
            arrays, tiles, actor_record = self._save_actor(report, label, actor, packets, window)
            np.savez_compressed(report / f"{label}_model_inputs.npz", **arrays)
            sheet = np.vstack([np.hstack(tiles[:3]), np.hstack(tiles[3:])])
            cv2.imwrite(str(report / f"{label}_six_cameras.png"), sheet)
            sheets.append(sheet)
            manifest["actors"].append(actor_record)
        cv2.imwrite(str(report / "all_mobility_cameras.png"), np.vstack(sheets))
        (report / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return report, manifest

    def _save_actor(self, report, label, actor, packets, window):
        histories, rots, trans, intrs, prs, pts, poses = [], [], [], [], [], [], []
        timestamps = []
        latest_tiles = []
        for t, frame in enumerate(window):
            imgs, rr, tt, kk, pr, pt = [], [], [], [], [], []
            lidar_packet = packets[label, "lidar"][t]
            sensor_world = lidar_packet["transform"]
            actor_world = sensor_world @ np.linalg.inv(self.sensor_matrices[label, "lidar"])
            poses.append((WORLD_BASIS @ actor_world @ WORLD_BASIS).astype(np.float32))
            timestamps.append(lidar_packet["timestamp"])
            for name in CAMERAS:
                packet = packets[label, name][t]
                if abs(packet["timestamp"] - timestamps[-1]) > 1e-6:
                    raise ValueError(f"Timestamp mismatch at {label}/{name}/frame {frame}.")
                bgra = np.frombuffer(packet["raw"], np.uint8).reshape(HEIGHT, WIDTH, 4)
                rgb = np.ascontiguousarray(bgra[:, :, :3][:, :, ::-1])
                if report is not None:
                    cv2.imwrite(str(report / f"{label}_{name}_f{frame}.png"), rgb[:, :, ::-1])
                image, post_rot, post_trans = preprocess_rgb(rgb)
                # Measured transform includes any parent movement in this exact sensor frame.
                relative = np.linalg.inv(actor_world) @ packet["transform"]
                rot, tran, intr = camera_calibration(relative, getattr(self,"camera_fovs",{}).get(name,FOV))
                if not np.allclose(relative, self.sensor_matrices[label, name], atol=2e-3):
                    raise ValueError(f"Non-rigid mount or pose mismatch: {label}/{name}.")
                imgs.append(image); rr.append(rot); tt.append(tran); kk.append(intr); pr.append(post_rot); pt.append(post_trans)
                if t == 3 and report is not None:
                    tile = cv2.resize(rgb[:, :, ::-1], (480, 270))
                    cv2.rectangle(tile, (0, 0), (480, 26), (15, 15, 15), -1)
                    display = "ego" if label == "ego" else label.split("damos_")[-1].replace(":anchor", " A")
                    cv2.putText(tile, f"{display} {name} | actor {actor.id} F{frame}", (8, 18),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
                    latest_tiles.append(tile)
            histories.append(imgs); rots.append(rr); trans.append(tt); intrs.append(kk); prs.append(pr); pts.append(pt)
        raw = np.frombuffer(packets[label, "lidar"][-1]["raw"], np.float32)
        if raw.size % 4:
            raise ValueError("Malformed CARLA LiDAR byte buffer.")
        points = raw.reshape(-1, 4).copy()
        if report is not None:
            np.save(report / f"{label}_lidar_f{window[-1]}.npy", points)
        lidar = rasterize_lidar(points, self.sensor_matrices[label, "lidar"])
        arrays = {"imgs": np.asarray(histories, np.float32)[None],
                  "rots": np.asarray(rots, np.float32)[None], "trans": np.asarray(trans, np.float32)[None],
                  "intrins": np.asarray(intrs, np.float32)[None], "post_rots": np.asarray(prs, np.float32)[None],
                  "post_trans": np.asarray(pts, np.float32)[None], "ego2globals": np.asarray(poses, np.float32)[None],
                  "lidar_bev": lidar[None], "frame_ids": np.array(window, np.int64),
                  "timestamps": np.array(timestamps, np.float64)}
        if not all(np.isfinite(value).all() for value in arrays.values()):
            raise ValueError(f"Non-finite model input: {label}.")
        entry = {"track_label": label, "actor_id": actor.id, "actor_type_id": actor.type_id,
                 "frames": window, "timestamps": timestamps, "camera_count": 6, "lidar_count": 1,
                 "lidar_points": len(points), "occupied_lidar_cells": int(lidar[0].sum()),
                 "input_shapes": {key: list(value.shape) for key, value in arrays.items()},
                 "input_path": f"{label}_model_inputs.npz"}
        return arrays, latest_tiles, entry

    def close(self):
        owned_ids = {sensor.id for sensor in self.sensors}
        for observer in self.observers.values():
            observer.sensors[:] = [s for s in observer.sensors if s.id not in owned_ids]
        for sensor in reversed(self.sensors):
            try:
                if sensor.is_listening:
                    sensor.stop()
                sensor.destroy()
            except RuntimeError:
                pass
        self.sensors.clear()


def prepare_raw_task(task):
    """Same calibration/preprocessing as saved inputs, without timed disk writes."""
    from types import SimpleNamespace
    rig = SimpleNamespace(sensor_matrices=task["sensor_matrices"], camera_fovs=task.get("camera_fovs",{}))
    actor = SimpleNamespace(id=task["actor_id"], type_id=task["actor_type_id"])
    arrays, _, record = BEVSensorRig._save_actor(
        rig, None, task["label"], actor, task["packets"], task["window"])
    record.pop("input_path", None)
    record["input_source"] = "live_raw_memory"
    return arrays, record
