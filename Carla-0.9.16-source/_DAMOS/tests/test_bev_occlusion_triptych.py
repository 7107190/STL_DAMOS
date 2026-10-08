from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from report_bev_occlusion_triptych import raw_images, select_candidate, target_crop


def candidate(frame, distance, hidden=False, hit=False):
    return {"frame_id": frame, "distance_m": distance, "occlusion_verified": hidden,
            "anchor_index": 1, "actor_id": 25, "detected": hit}


class OcclusionTriptychTests(unittest.TestCase):
    def test_occlusion_filter_precedes_distance(self):
        rows = [candidate(1, 2), candidate(2, 10, True), candidate(3, 8, True)]
        self.assertEqual(select_candidate(rows)["frame_id"], 3)

    def test_distance_not_success_selects_frame(self):
        rows = [candidate(1, 5, hit=True), candidate(2, 2, hit=False)]
        result = select_candidate(rows)
        self.assertEqual(result["frame_id"], 2)
        self.assertFalse(result["occlusion_verified"])
        self.assertFalse(result["detected"])

    def test_ties_choose_earlier_frame(self):
        self.assertEqual(select_candidate([candidate(3, 5), candidate(2, 5)])["frame_id"], 2)

    def test_empty_or_invalid_distances_rejected(self):
        for rows in ([], [candidate(1, float("nan"))], [candidate(1, -1)]):
            with self.assertRaises(ValueError):
                select_candidate(rows)

    def test_crop_keeps_edge_targets_and_metric_square(self):
        for point in ((0, 0), (199, 199), (100, 100)):
            mask = np.zeros((200, 200), bool)
            mask[point] = True
            x, y, width = target_crop(mask)
            self.assertEqual(width, 50)
            self.assertTrue(mask[x:x + width, y:y + width].any())
            self.assertLessEqual(max(x, y) + width, 200)

    def test_no_target_returns_cannot_make_a_crop(self):
        with self.assertRaises(ValueError):
            target_crop(np.zeros((200, 200), bool))

    def test_render_does_not_change_prediction_or_gt(self):
        probs = np.zeros((17, 200, 200), np.float16)
        probs[8] = 1
        valid = np.ones((200, 200), np.uint8)
        mask = np.zeros((200, 200), bool)
        mask[100:103, 100:103] = True
        prediction = {"probabilities": probs, "valid": valid}
        before = (probs.copy(), valid.copy(), mask.copy())
        full, zoom = raw_images(prediction, mask, target_crop(mask))
        self.assertEqual(full.size, zoom.size)
        for actual, expected in zip((probs, valid, mask), before):
            np.testing.assert_array_equal(actual, expected)


if __name__ == "__main__":
    unittest.main()
