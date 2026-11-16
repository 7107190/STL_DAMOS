"""Actual CARLA RGB preview; frame counters never stand in for rendered pixels."""
import json
import hashlib
from pathlib import Path
import threading
import time

import cv2
import numpy as np

from report_data import atomic_json


def atomic_image(path, pixels, suffix=".jpg"):
    ok, encoded = cv2.imencode(suffix, pixels)
    if not ok:
        raise OSError("Cannot encode actual CARLA preview")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded.tobytes())
    temporary.replace(path)


class ScenePreview:
    def __init__(self, rig, job, scenario):
        import carla
        self.rig, self.job, self.scenario = rig, Path(job), scenario
        self.frames, self.lock = {}, threading.Lock()
        self.stop = threading.Event()
        self.sensor = None
        self.thread = None
        try:
            bp = rig.world.get_blueprint_library().find("sensor.camera.rgb")
            for key, value in {"image_size_x": "960", "image_size_y": "540", "fov": "95",
                               "sensor_tick": "0.1", "role_name": "damos_metric2_actual_scene"}.items():
                bp.set_attribute(key, value)
            self.sensor = rig.world.spawn_actor(bp,
                carla.Transform(carla.Location(x=-9, z=5), carla.Rotation(pitch=-20)),
                attach_to=rig.actors["ego"], attachment_type=carla.AttachmentType.Rigid)
            self.sensor.listen(self.receive)
            self.thread = threading.Thread(target=self.publish, daemon=True)
            self.thread.start()
        except BaseException:
            self.close()
            raise

    def receive(self, data):
        packet = {"frame_id": data.frame, "capture_sim_ns": round(data.timestamp * 1e9),
                  "width": data.width, "height": data.height, "raw": bytes(data.raw_data),
                  "camera_to_world_carla": data.transform.get_matrix()}
        with self.lock:
            self.frames[data.frame] = packet
            for old in sorted(self.frames)[:-100]:
                del self.frames[old]

    def exact(self, frame, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if frame in self.frames:
                    return dict(self.frames[frame])
            time.sleep(.01)
        raise TimeoutError("No actual CARLA scene RGB at input frame " + str(frame))

    @staticmethod
    def pixels(packet):
        return np.frombuffer(packet["raw"], np.uint8).reshape(packet["height"], packet["width"], 4)[:, :, :3]

    def publish(self):
        last = None
        while not self.stop.wait(.2):
            with self.lock:
                packet = dict(self.frames[max(self.frames)]) if self.frames else None
            if not packet or packet["frame_id"] == last:
                continue
            try:
                pixels = self.pixels(packet)
                image_file = f"scene_live_F{packet['frame_id']:010d}.jpg"
                atomic_image(self.job / image_file, pixels)
                metadata = {key: value for key, value in packet.items() if key != "raw"}
                metadata.update(source="live_carla_rgb_sensor", scenario=self.scenario,
                                ego_actor_id=self.rig.actors["ego"].id, updated_wall_ns=time.time_ns(), image_file=image_file)
                atomic_json(self.job / "scene_live.json", metadata)
                for old in sorted(self.job.glob("scene_live_F*.jpg"))[:-24]:
                    old.unlink(missing_ok=True)
                # The native CARLA spectator is the same physical chase camera.
                self.rig.world.get_spectator().set_transform(self.sensor.get_transform())
                last = packet["frame_id"]
            except Exception as exc:
                atomic_json(self.job / "scene_preview_error.json", {"error": type(exc).__name__ + ": " + str(exc)})

    def save_exact(self, folder, packet, expected_frame, capture_sim_ns):
        if packet["frame_id"] != expected_frame or abs(packet["capture_sim_ns"] - capture_sim_ns) > 1000:
            raise ValueError("Scene RGB does not match measured sensor input")
        pixels = self.pixels(packet)
        if float(pixels.std()) < 1:
            raise ValueError("Actual CARLA scene RGB is blank")
        atomic_image(folder / "scene_rgb.png", pixels, ".png")
        metadata = {key: value for key, value in packet.items() if key != "raw"}
        metadata.update(source="current_carla_rgb_sensor", fed_to_model=False,
                        raw_sha256=hashlib.sha256(packet["raw"]).hexdigest(),
                        rgb_standard_deviation=float(pixels.std()),
                        image_sha256=hashlib.sha256((folder / "scene_rgb.png").read_bytes()).hexdigest())
        atomic_json(folder / "scene_capture.json", metadata)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.sensor is not None:
            try:
                self.sensor.stop()
                self.sensor.destroy()
            except RuntimeError:
                pass
            self.sensor = None
