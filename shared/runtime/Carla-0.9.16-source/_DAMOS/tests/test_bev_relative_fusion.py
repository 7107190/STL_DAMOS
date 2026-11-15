"""Relative-confidence policy invariants, with valid probability distributions."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bev_relative_fusion import relative_correction


def prediction(label=11, score=.8):
    p = np.full((17, 200, 200), (1 - score) / 16, np.float16)
    p[label] = score
    return {"probabilities": p, "valid": np.ones((200, 200), np.uint8),
            "pose": np.eye(4, dtype=np.float32)}


def peer_body(score=.55):
    p = prediction()
    for y in (100, 101):
        p["probabilities"][:, 100, y] = (1 - score) / 16
        p["probabilities"][7, 100, y] = score
    return p


class RelativeTest(unittest.TestCase):
    def run_policy(self, ego, peer):
        return relative_correction(ego, ego, [peer], np.zeros((1, 6, 200, 200), np.float32),
                                   np.zeros((5, 200, 200), np.float32))

    def test_weak_peer_defeats_uncertain_ego_ground(self):
        result, flags = self.run_policy(prediction(score=.45), peer_body())
        self.assertEqual(int(flags["relative_promoted"].sum()), 2)
        self.assertEqual(result["occupancy"][100, 100], 2)

    def test_confident_ego_ground_blocks_weak_peer(self):
        result, flags = self.run_policy(prediction(score=.8), peer_body())
        self.assertFalse(flags["relative_promoted"].any())
        self.assertEqual(result["occupancy"][100, 100], 1)

    def test_tied_source_scores_do_not_override(self):
        _, flags = self.run_policy(prediction(score=.55), peer_body())
        self.assertFalse(flags["relative_promoted"].any())

    def test_submajority_peer_is_blocked(self):
        _, flags = self.run_policy(prediction(score=.40), peer_body(.49))
        self.assertFalse(flags["relative_promoted"].any())

    def test_unobserved_peer_is_blocked(self):
        peer = peer_body()
        peer["valid"][:] = 0
        _, flags = self.run_policy(prediction(score=.45), peer)
        self.assertFalse(flags["relative_promoted"].any())

    def test_unobserved_ego_is_not_used_for_comparison(self):
        ego = prediction(score=.45)
        ego["valid"][:] = 0
        _, flags = self.run_policy(ego, peer_body())
        self.assertFalse(flags["relative_promoted"].any())

    def test_existing_obstacle_is_preserved(self):
        ego = prediction(label=4)
        before = ego["probabilities"].copy()
        result, _ = self.run_policy(ego, peer_body())
        np.testing.assert_array_equal(result["probabilities"], before)
        np.testing.assert_array_equal(ego["probabilities"], before)

    def test_strong_peer_policy_is_preserved(self):
        result, flags = self.run_policy(prediction(score=.9), peer_body(.8))
        self.assertEqual(result["occupancy"][100, 100], 2)
        self.assertFalse(flags["relative_promoted"].any())

    def test_isolated_weak_peer_pixel_is_blocked(self):
        peer = peer_body()
        peer["probabilities"][:, 100, 101] = prediction()["probabilities"][:, 100, 101]
        _, flags = self.run_policy(prediction(score=.45), peer)
        self.assertFalse(flags["relative_promoted"].any())

    def test_wrong_source_class_is_blocked(self):
        peer = peer_body()
        peer["probabilities"][[4, 7]] = peer["probabilities"][[7, 4]]
        _, flags = self.run_policy(prediction(score=.45), peer)
        self.assertFalse(flags["relative_promoted"].any())

    def test_misaligned_ego_pose_is_rejected(self):
        ego, baseline = prediction(), prediction()
        baseline["pose"][0, 3] = .4
        with self.assertRaises(ValueError):
            relative_correction(baseline, ego, [peer_body()], np.zeros((1, 6, 200, 200)),
                                np.zeros((5, 200, 200)))


if __name__ == "__main__":
    unittest.main()
