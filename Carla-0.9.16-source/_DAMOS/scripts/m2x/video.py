"""Evidence video from live CARLA images, poses and completed protocol results."""
import json
import math
from pathlib import Path
import shutil
import subprocess
import threading
import time

import cv2
import numpy as np

from .live import camera_positions
from .video_views import SharingDashboard


class SharingVideo:
    WIDTH, HEIGHT, FPS = SharingDashboard.WIDTH, SharingDashboard.HEIGHT, 5

    def __init__(self, world, nodes, buffer, state, folder, radius_m, scenario_labels):
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg is required for --save-sharing-video")
        self.world, self.nodes, self.buffer, self.state = world, nodes, buffer, state
        self.folder, self.radius = Path(folder), radius_m
        self.labels = ", ".join(scenario_labels)
        self.names = {node: f"O{i}" for i, node in enumerate((n for n in nodes if n != "ego"), 1)}
        self.output = self.folder / "ego_neighbour_sharing.mp4"
        self.stop = threading.Event()
        self.frames, self.error, self.elapsed = 0, None, 0.0
        self.process = None
        self.last_image = None
        self.trail = []
        self.dashboard = SharingDashboard(self.folder, nodes, radius_m, scenario_labels)
        self._make_map()

    def _make_map(self):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from scenic_custom_walker_injector import draw_carla_map_context
        points = [w.transform.location for w in self.world.get_map().generate_waypoints(3.0)]
        if not points:
            raise RuntimeError("CARLA_MAP_HAS_NO_WAYPOINTS")
        min_x, max_x = min(p.x for p in points), max(p.x for p in points)
        min_y, max_y = min(p.y for p in points), max(p.y for p in points)
        side = max(max_x - min_x, max_y - min_y) + 24
        self.bounds = ((min_x + max_x - side) / 2, (max_x + min_x + side) / 2,
                       (min_y + max_y - side) / 2, (max_y + min_y + side) / 2)
        figure = Figure(figsize=(6, 6), dpi=100)
        canvas = FigureCanvasAgg(figure)
        ax = figure.add_axes([0, 0, 1, 1])
        draw_carla_map_context(ax, self.world, bounds=self.bounds)
        ax.set_xlim(self.bounds[:2])
        ax.set_ylim(self.bounds[2:])
        ax.set_axis_off()
        canvas.draw()
        self.map = cv2.cvtColor(np.asarray(canvas.buffer_rgba()), cv2.COLOR_RGBA2BGR)
        cv2.imwrite(str(self.folder / "map_background.png"), self.map)

    def _point(self, position):
        x0, x1, y0, y1 = self.bounds
        return (round((position[0] - x0) / (x1 - x0) * 599),
                round((y1 - position[1]) / (y1 - y0) * 599))

    @staticmethod
    def _text(image, text, x, y, size=0.7, color=(220, 225, 230), thickness=1):
        cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, size, color, thickness, cv2.LINE_AA)

    def _compose(self, elapsed):
        positions, frame = camera_positions(self.world, self.nodes)
        state = self.state.snapshot()
        camera = None
        sample = self.buffer.latest("ego")
        if sample is not None:
            self.last_image = sample
        if self.last_image is not None:
            sample = self.last_image
            bgra = np.frombuffer(sample["raw"], np.uint8).reshape(sample["height"], sample["width"], 4)
            camera = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)

        context = self.map.copy()
        ego = positions["ego"]["position"]
        origin = self._point(ego)
        self.trail.append(origin)
        if len(self.trail) > 1:
            cv2.polylines(context, [np.asarray(self.trail, np.int32)], False, (210, 100, 20), 2)
        if self.radius > 0:
            radius_pixels = round(self.radius / (self.bounds[1] - self.bounds[0]) * 599)
            cv2.circle(context, origin, radius_pixels, (210, 135, 50), 1, cv2.LINE_AA)
        selected = set(state["selected"])
        last = state["last_result"]
        verified = set(last["verified_peer_ids"]) & set(last.get("reciprocal_verified_peer_ids", [])) if last else set()
        for node, info in positions.items():
            if node == "ego":
                continue
            point = self._point(info["position"])
            color = (120, 128, 130)
            if node in selected:
                color = (65, 155, 25) if node in verified else (0, 175, 235)
                cv2.line(context, origin, point, color, 2, cv2.LINE_AA)
            cv2.circle(context, point, 7, color, -1, cv2.LINE_AA)
            self._text(context, self.names[node], max(1, min(point[0] + 8, 560)),
                       max(20, min(point[1] - 7, 585)), 0.55, (25, 35, 40), 2)
        angle = math.radians(positions["ego"]["yaw"])
        tip = (origin[0] + round(15 * math.cos(angle)), origin[1] - round(15 * math.sin(angle)))
        cv2.circle(context, origin, 8, (230, 95, 10), -1, cv2.LINE_AA)
        cv2.arrowedLine(context, origin, tip, (230, 95, 10), 3, cv2.LINE_AA, tipLength=0.5)
        telemetry = {"video_wall_seconds": elapsed, "pose_frame": frame,
                     "front_frame": sample["frame_id"] if sample else None,
                     "positions": positions, "phase": state["phase"], "selected": state["selected"],
                     "round_id": state["round_id"], "last_completed_round": last["round_id"] if last else None}
        image, telemetry["received_views"] = self.dashboard.compose(camera, context, state, telemetry)
        # Index exactly which telemetry sample supplied each encoded video frame.
        telemetry["video_frame_start"] = self.frames
        telemetry["video_frame_end"] = int(elapsed * self.FPS)
        return image, telemetry

    def start(self):
        self.thread = threading.Thread(target=self._run, name="sharing-evidence-video", daemon=True)
        self.thread.start()

    def _run(self):
        start = time.monotonic()
        try:
            with (self.folder / "ffmpeg.log").open("w") as log, \
                 (self.folder / "video_telemetry.jsonl").open("w") as telemetry_file:
                self.process = subprocess.Popen([
                    "ffmpeg", "-y", "-loglevel", "warning", "-f", "rawvideo",
                    "-pixel_format", "bgr24", "-video_size", f"{self.WIDTH}x{self.HEIGHT}",
                    "-framerate", str(self.FPS), "-i", "pipe:0", "-an", "-c:v", "libx264",
                    "-threads", "2", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(self.output)], stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=log)
                while not self.stop.is_set():
                    self.elapsed = time.monotonic() - start
                    image, telemetry = self._compose(self.elapsed)
                    telemetry_file.write(json.dumps(telemetry) + "\n")
                    target = int(self.elapsed * self.FPS)
                    # Preserve wall-time playback even if a capture/encode iteration is late.
                    while self.frames <= target:
                        self.process.stdin.write(image.tobytes())
                        self.frames += 1
                    self.stop.wait(max(0, self.frames / self.FPS - (time.monotonic() - start)))
                self.process.stdin.close()
                if self.process.wait(timeout=30) != 0:
                    raise RuntimeError("ffmpeg failed; see ffmpeg.log")
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=35)
        if self.thread.is_alive():
            self.error = "Video encoder shutdown timeout"
            if self.process and self.process.poll() is None:
                self.process.kill()
            self.thread.join(timeout=5)
        return {"path": str(self.output), "fps": self.FPS, "frames": self.frames,
                "duration_seconds": self.frames / self.FPS, "error": self.error,
                "playback": "wall_time_1x", "extra_capture_load_in_benchmark": True}
