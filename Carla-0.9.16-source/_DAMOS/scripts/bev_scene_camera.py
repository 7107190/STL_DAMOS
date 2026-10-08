"""Optional evaluation-only overhead RGB camera with exact-frame freezing."""
import hashlib
import json
from pathlib import Path
import threading
import time

import cv2
import numpy as np


def save_overhead(folder, packet, frame_id, capture_sim_ns):
    if packet["frame_id"] != frame_id or abs(packet["capture_sim_ns"] - capture_sim_ns) > 1000:
        raise ValueError("Overhead camera is not the BEV input frame.")
    width, height = packet["width"], packet["height"]
    pixels = np.frombuffer(packet["raw"], np.uint8).reshape(height, width, 4)[:, :, :3]
    folder = Path(folder)
    image = folder / "overhead_rgb.png"
    if image.exists():
        raise FileExistsError("Refusing to replace an existing scene capture.")
    if not cv2.imwrite(str(image), pixels):
        raise OSError("Cannot save overhead RGB.")
    focal = width / (2 * np.tan(np.deg2rad(packet["fov"]) / 2))
    metadata = {"frame_id": frame_id, "capture_sim_ns": capture_sim_ns,
        "image": image.name, "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "width": width, "height": height, "fov": packet["fov"],
        "camera_to_world_carla": np.asarray(packet["transform"]).tolist(),
        "intrinsic": [[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]],
        "evaluation_only": True, "fed_to_model_or_fusion": False,
        "source": "Actual CARLA RGB sensor; frozen before the timed BEV exchange"}
    (folder / "overhead_capture.json").write_text(json.dumps(metadata, indent=2))
    return metadata


class OverheadCamera:
    def __init__(self, world, ego):
        import carla
        self.lock, self.frames = threading.Lock(), {}
        self.sensor = None
        self.fov = 90.
        try:
            blueprint = world.get_blueprint_library().find("sensor.camera.rgb")
            for key, value in {"image_size_x": "1600", "image_size_y": "1600", "fov": "90",
                               "sensor_tick": "0.0", "role_name": "damos_eval_only_overhead"}.items():
                blueprint.set_attribute(key, value)
            self.sensor = world.spawn_actor(blueprint,
                carla.Transform(carla.Location(z=60), carla.Rotation(pitch=-90)),
                attach_to=ego, attachment_type=carla.AttachmentType.Rigid)
            self.sensor.listen(self.receive)
        except BaseException:
            self.close()
            raise

    def receive(self, data):
        packet = {"frame_id": data.frame, "capture_sim_ns": round(data.timestamp * 1e9),
            "width": data.width, "height": data.height, "raw": bytes(data.raw_data),
            "transform": np.asarray(data.transform.get_matrix()), "fov": self.fov}
        with self.lock:
            self.frames[data.frame] = packet
            for old in sorted(self.frames)[:-12]:
                del self.frames[old]

    def freeze(self, frame_id, timeout_s=10):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self.lock:
                if frame_id in self.frames:
                    return dict(self.frames[frame_id])
            time.sleep(.01)
        raise TimeoutError(f"No overhead RGB at BEV input frame {frame_id}.")

    def close(self):
        if self.sensor is not None:
            try:
                self.sensor.stop()
                self.sensor.destroy()
            except RuntimeError:
                pass
            finally:
                self.sensor = None
