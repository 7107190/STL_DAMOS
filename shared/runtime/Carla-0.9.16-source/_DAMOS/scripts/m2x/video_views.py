"""Render only ego's recorded, verified incoming bytes, never sender previews."""
import hashlib
import math
from pathlib import Path

import cv2
import numpy as np


class ReceivedFrames:
    def __init__(self, folder, nodes):
        self.folder, self.nodes = Path(folder), nodes
        self.cache_round = None
        self.cache = {}

    def get(self, node, result):
        if not result or node not in result.get("verified_peer_ids", []):
            return {"status": "waiting", "image": None}
        round_id = result["round_id"]
        meta = result.get("peer_images", {}).get(node, {})
        if (node not in self.nodes or node not in result.get("peer_ids", [])
                or meta.get("actor_id") != self.nodes[node]["actor_id"]
                or meta.get("frame_id") != result["frame_id"]):
            return {"status": "identity_mismatch", "image": None}
        if round_id != self.cache_round:
            self.cache_round, self.cache = round_id, {}
        key = (node, meta.get("sha256"), meta["actor_id"], meta["frame_id"])
        if key not in self.cache:
            path = self.folder / f"round_{round_id:03d}" / f"{node}.png"
            try:
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != meta.get("sha256"):
                    raise ValueError("image_hash_mismatch")
                decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if decoded is None:
                    raise ValueError("image_decode_failed")
                view = {"status": "verified", "image": decoded, "round_id": round_id,
                        "frame_id": meta["frame_id"], "actor_id": meta["actor_id"],
                        "sha256": meta["sha256"]}
            except OSError:
                view = {"status": "artifact_missing", "image": None}
            except ValueError as exc:
                view = {"status": str(exc), "image": None}
            self.cache[key] = view
        return self.cache[key]


def text(image, message, x, y, size=0.7, color=(220, 225, 230), thickness=1,
         max_width=None):
    if max_width:
        width = cv2.getTextSize(message, cv2.FONT_HERSHEY_SIMPLEX, size, thickness)[0][0]
        size *= min(1, max_width / max(1, width))
    cv2.putText(image, message, (x, y), cv2.FONT_HERSHEY_SIMPLEX, size, color,
                thickness, cv2.LINE_AA)


def letterbox(canvas, image, rect):
    x, y, width, height = rect
    canvas[y:y+height, x:x+width] = (12, 15, 17)
    if image is None:
        return
    scale = min(width / image.shape[1], height / image.shape[0])
    w, h = max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))
    left, top = x + (width - w) // 2, y + (height - h) // 2
    canvas[top:top+h, left:left+w] = cv2.resize(image, (w, h))


class SharingDashboard:
    WIDTH, HEIGHT = 1920, 1080

    def __init__(self, folder, nodes, radius, labels):
        self.nodes, self.radius = nodes, radius
        self.labels = ", ".join(labels)
        self.names = {node: f"O{i}" for i, node in enumerate((n for n in nodes if n != "ego"), 1)}
        self.received = ReceivedFrames(folder, nodes)

    def compose(self, camera, context, state, telemetry):
        image = np.full((self.HEIGHT, self.WIDTH, 3), (25, 28, 30), np.uint8)
        text(image, "DAMOS | EGO + IMAGES ACTUALLY RECEIVED BY EGO", 16, 35, 0.95, thickness=2)
        text(image, f"{self.labels} | wall {telemetry['video_wall_seconds']:.1f}s | "
             f"radius {self.radius:g}m | {state['phase'].upper()}", 16, 69, 0.7)
        text(image, "EGO FRONT / LIVE", 16, 104, 0.74, thickness=2)
        text(image, f"actor {self.nodes['ego']['actor_id']} | frame {telemetry.get('front_frame', '-')}",
             16, 129, 0.6)
        letterbox(image, camera, (16, 138, 624, 468))
        if camera is None:
            text(image, "Waiting for ego camera", 100, 370, 0.75)

        selected = list(state["selected"])
        last = state.get("last_result")
        # Keep two full-size receiving slots; show every selected peer if there are more.
        slots = max(2, len(selected))
        rows = math.ceil(slots / 2)
        cell_height = 522 // rows
        displayed = {}
        for i in range(slots):
            x, y = 648 + (i % 2) * 632, 84 + (i // 2) * cell_height
            rect = (x, y + 54, 624, cell_height - 54)
            node = selected[i] if i < len(selected) else None
            view = self.received.get(node, last) if node else {"status": "no_sender", "image": None}
            letterbox(image, view["image"], rect)
            if node:
                role = self.nodes[node].get("actor_role", "observer").upper()
                title = f"{self.names[node]} / {role} / actor {self.nodes[node]['actor_id']}"
                color = (95, 225, 130) if view["status"] == "verified" else (70, 195, 245)
                subtitle = (f"RECEIVED + ZKP VERIFIED | R{view['round_id']} / F{view['frame_id']}"
                            if view["image"] is not None else view["status"].replace("_", " ").upper())
                displayed[node] = {key: value for key, value in view.items() if key != "image"}
            else:
                title, subtitle, color = "RECEIVED VIEW", "NO NEARBY SENDER", (150, 160, 170)
            text(image, title, x, y + 21, 0.73, thickness=2, max_width=620)
            text(image, subtitle, x, y + 46, 0.59, color, max_width=620)
            if view["image"] is None:
                message = "Waiting for verified image" if node else "No image exchange"
                text(image, message, x + 24, y + 54 + (cell_height - 54) // 2,
                     0.75, color, max_width=576)

        text(image, "FULL MAP / EGO PATH + NEARBY PEERS", 16, 630, 0.56)
        letterbox(image, context, (16, 640, 424, 424))
        x = 472
        text(image, "RECEIVER STATUS", x, 650, 0.83, thickness=2)
        participants = ", ".join(self.names[n] for n in selected) or "none (ego keeps driving)"
        text(image, f"Selected peers: {participants}", x, 690, 0.76, max_width=1420)
        visible = sum(v["status"] == "verified" for v in displayed.values())
        text(image, f"Verified received views shown: {visible}/{len(selected)}", x, 729, 0.76)
        if last:
            text(image, f"Last completed round {last['round_id']} | source frame {last['frame_id']} | "
                 f"ego sharing {last['elapsed_ms']/1000:.3f}s", x, 773, 0.77)
            text(image, f"Ego verified {len(last['verified_peer_ids'])}/{len(last['peer_ids'])} | "
                 f"Observers verified ego {len(last.get('reciprocal_verified_peer_ids', []))}/{len(last['peer_ids'])}",
                 x, 812, 0.73)
        else:
            text(image, "No completed exchange yet", x, 773, 0.77)
        text(image, "Received panes hold the last verified ONE-FRAME image until the next exchange.",
             x, 857, 0.64)
        text(image, "These are ego's incoming PNG bytes, not a direct preview of the sender camera.",
             x, 889, 0.64)
        text(image, "Blue: ego / travelled path | Green: last reciprocal verification | Yellow: selected",
             x, 934, 0.62)
        text(image, "Observer <-> observer: OFF | Detector: NOT CONNECTED (18s full metric not evaluated)",
             x, 979, 0.62, (160, 180, 190))
        text(image, "Real Groth16 / direct TCP / single host | Recorded wall-time playback 1x",
             x, 1014, 0.62, (160, 180, 190))
        text(image, f"Pose frame {telemetry['pose_frame']} | LIVE ego and RECEIVED images have separate frame IDs",
             x, 1049, 0.6, (160, 180, 190))
        return image, displayed
