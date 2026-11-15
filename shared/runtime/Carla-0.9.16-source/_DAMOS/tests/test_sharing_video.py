"""Receiver panes must match the actual verified actor/frame/round bytes."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from m2x.video import SharingVideo
from m2x.video_views import ReceivedFrames, SharingDashboard
from render_received_sharing_video import frame_timeline


class ReceivedVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.nodes = {"ego": {"actor_id": 1}, "observer_2": {"actor_id": 2},
                      "observer_3": {"actor_id": 3}}
        self.result = {"round_id": 1, "frame_id": 10, "peer_ids": ["observer_2", "observer_3"],
                       "verified_peer_ids": ["observer_2", "observer_3"],
                       "reciprocal_verified_peer_ids": ["observer_2", "observer_3"],
                       "elapsed_ms": 850, "peer_images": {}}
        self.images = {}
        (self.folder / "round_001").mkdir()
        for node, color in (("observer_2", (20, 40, 220)), ("observer_3", (210, 30, 20))):
            image = np.full((60, 80, 3), color, np.uint8)
            data = cv2.imencode(".png", image)[1].tobytes()
            (self.folder / "round_001" / f"{node}.png").write_bytes(data)
            self.images[node] = image
            self.result["peer_images"][node] = {"actor_id": self.nodes[node]["actor_id"],
                                               "frame_id": 10, "sha256": hashlib.sha256(data).hexdigest()}
        self.store = ReceivedFrames(self.folder, self.nodes)
        self.dashboard = SharingDashboard(self.folder, self.nodes, 60, ["S9"])
        self.telemetry = {"video_wall_seconds": 1, "front_frame": 15, "pose_frame": 15}

    def compose(self, selected, result=None):
        return self.dashboard.compose(np.zeros((60, 80, 3), np.uint8),
                                      np.zeros((600, 600, 3), np.uint8),
                                      {"selected": selected, "phase": "exchanging", "last_result": result},
                                      self.telemetry)

    def test_exact_incoming_pixels_and_two_distinct_senders(self):
        image, views = self.compose(["observer_2", "observer_3"], self.result)
        self.assertEqual(image.shape, (1080, 1920, 3))
        self.assertEqual(set(views), {"observer_2", "observer_3"})
        np.testing.assert_array_equal(image[350, 950], self.images["observer_2"][0, 0])
        np.testing.assert_array_equal(image[350, 1580], self.images["observer_3"][0, 0])
        json.dumps(views)

    def test_unverified_and_new_peer_never_show_existing_file(self):
        self.result["verified_peer_ids"] = ["observer_2"]
        view = self.store.get("observer_3", self.result)
        self.assertEqual(view["status"], "waiting")
        self.assertIsNone(view["image"])
        self.assertEqual(self.store.get("observer_2", None)["status"], "waiting")

    def test_actor_frame_hash_and_missing_artifact_fail_closed(self):
        for field, value, error in (("actor_id", 99, "identity_mismatch"),
                                    ("frame_id", 11, "identity_mismatch"),
                                    ("sha256", "0" * 64, "image_hash_mismatch")):
            result = copy.deepcopy(self.result)
            result["peer_images"]["observer_2"][field] = value
            with self.subTest(field=field):
                view = self.store.get("observer_2", result)
                self.assertEqual(view["status"], error)
                self.assertIsNone(view["image"])
        (self.folder / "round_001" / "observer_2.png").unlink()
        self.assertEqual(self.store.get("observer_2", self.result)["status"], "artifact_missing")

    def test_departed_peer_removed_without_substitution(self):
        _, views = self.compose(["observer_3"], self.result)
        self.assertEqual(set(views), {"observer_3"})
        self.assertEqual(views["observer_3"]["actor_id"], 3)
        _, views = self.compose([], self.result)
        self.assertEqual(views, {})

    def test_no_fallback_to_an_old_round_when_new_file_missing(self):
        self.assertEqual(self.store.get("observer_2", self.result)["status"], "verified")
        result = copy.deepcopy(self.result)
        result["round_id"] = 2
        self.assertEqual(self.store.get("observer_2", result)["status"], "artifact_missing")

    def test_more_than_two_peers_all_displayed(self):
        for actor in range(4, 9):
            self.nodes[f"observer_{actor}"] = {"actor_id": actor}
        self.dashboard = SharingDashboard(self.folder, self.nodes, 60, ["S9"])
        selected = list(self.nodes)[1:]
        image, views = self.compose(selected, self.result)
        self.assertEqual(set(views), set(selected))
        self.assertEqual(image.shape, (1080, 1920, 3))

    def test_replay_preserves_original_catchup_frame_association(self):
        rows = [{"video_wall_seconds": t, "last_completed_round": i}
                for i, t in enumerate([0.003, 0.202, 0.61, 0.801])]
        mapped = list(frame_timeline(rows, 5, 5))
        self.assertEqual([r["last_completed_round"] for _, r in mapped], [0, 1, 2, 2, 3])
        with self.assertRaisesRegex(ValueError, "TELEMETRY_FRAME_COUNT_MISMATCH"):
            list(frame_timeline(rows, 5, 6))

    def test_live_composer_uses_same_receiver_renderer_and_audit(self):
        video = object.__new__(SharingVideo)
        video.world, video.nodes, video.buffer, video.state = Mock(), self.nodes, Mock(), Mock()
        video.buffer.latest.return_value = {"raw": np.zeros((60, 80, 4), np.uint8).tobytes(),
                                            "height": 60, "width": 80, "frame_id": 15}
        video.state.snapshot.return_value = {"selected": ["observer_2"], "phase": "verified",
                                             "last_result": self.result, "round_id": 1}
        video.dashboard, video.names = self.dashboard, self.dashboard.names
        video.last_image, video.trail, video.frames = None, [], 5
        video.map, video.bounds, video.radius = np.zeros((600, 600, 3), np.uint8), (-100, 100, -100, 100), 60
        positions = {n: {"position": [i * 20, 0, 0], "yaw": 0} for i, n in enumerate(self.nodes)}
        with patch("m2x.video.camera_positions", return_value=(positions, 15)):
            image, audit = video._compose(1.1)
        self.assertEqual(image.shape, (1080, 1920, 3))
        self.assertEqual(audit["received_views"]["observer_2"]["frame_id"], 10)
        self.assertEqual(audit["front_frame"], 15)
        self.assertEqual((audit["video_frame_start"], audit["video_frame_end"]), (5, 5))
        json.dumps(audit)


if __name__ == "__main__":
    unittest.main()
