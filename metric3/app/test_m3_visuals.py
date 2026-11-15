import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from m3_data import Reports
from m3_visuals import OVERLAY_IMAGE, OVERLAY_RECORD, TARGET_BGR, draw_target_zoom, paint_target_matches, render_target, target_match_cells


class TargetOverlay(unittest.TestCase):
    def test_only_target_and_predicted_intersection_is_colored(self):
        row = {"gt_target_cell_indices": [0, 1, 201], "fused_obstacle_cell_indices": [1, 201, 202]}
        base = np.full((600, 600, 3), 90, np.uint8)
        original = base.copy()
        colored, cells = paint_target_matches(base, row)
        np.testing.assert_array_equal(cells, [1, 201])
        self.assertTrue(np.all(colored[597:600, 594:597] == TARGET_BGR))
        self.assertTrue(np.all(colored[594:597, 594:597] == TARGET_BGR))
        self.assertTrue(np.all(colored[597:600, 597:600] == 90))
        self.assertTrue(np.all(colored[594:597, 591:594] == 90))
        np.testing.assert_array_equal(base, original)

    def test_missed_target_is_not_filled(self):
        row = {"gt_target_cell_indices": [200, 201, 202], "fused_obstacle_cell_indices": [5, 6]}
        base = np.zeros((600, 600, 3), np.uint8)
        colored, cells = paint_target_matches(base, row)
        self.assertEqual(len(cells), 0)
        np.testing.assert_array_equal(colored, base)

    def test_duplicate_indices_do_not_expand_target(self):
        row = {"gt_target_cell_indices": [1, 1, 2], "fused_obstacle_cell_indices": [1, 1]}
        np.testing.assert_array_equal(target_match_cells(row), [1])

    def test_projection_matches_existing_bev_renderer_at_grid_edges(self):
        cells = [0, 199, 200, 39_800, 39_999]
        row = {"gt_target_cell_indices": cells, "fused_obstacle_cell_indices": cells}
        colored, _ = paint_target_matches(np.zeros((600, 600, 3), np.uint8), row)
        mask = np.zeros((200, 200), np.uint8)
        mask.flat[cells] = 1
        expected = cv2.resize(mask[::-1, ::-1], (600, 600), interpolation=cv2.INTER_NEAREST)
        np.testing.assert_array_equal(np.all(colored == TARGET_BGR, axis=2), expected.astype(bool))

    def test_invalid_indices_rejected(self):
        for cells in ([-1], [40_000], [[2]]):
            with self.assertRaises(ValueError):
                target_match_cells({"gt_target_cell_indices": cells, "fused_obstacle_cell_indices": [2]})

    def test_zoom_magnifies_existing_matches_without_modifying_source(self):
        row = {"gt_target_cell_indices": [28_093, 28_293, 28_493], "fused_obstacle_cell_indices": [28_093, 28_293, 28_493]}
        source, _ = paint_target_matches(np.zeros((600, 600, 3), np.uint8), row)
        before = source.copy()
        image, zoom = draw_target_zoom(source, source, row, [[310, 164], [328, 188]], ((298, 298), (353, 137)))
        self.assertEqual(zoom["width_m"], 4)
        self.assertGreater(np.sum(np.all(image == TARGET_BGR, axis=2)), np.sum(np.all(source == TARGET_BGR, axis=2)))
        np.testing.assert_array_equal(source, before)

    def test_zoom_at_grid_edges_stays_inside_image(self):
        source = np.zeros((600, 600, 3), np.uint8)
        for cell in (0, 199, 39_800, 39_999):
            _, zoom = draw_target_zoom(source, source, {"gt_target_cell_indices": [cell]}, None)
            self.assertIsNotNone(zoom)
            a, b = zoom["source_pixel_rectangle"]
            self.assertTrue(0 <= a[0] < b[0] <= 600 and 0 <= a[1] < b[1] <= 600)

    def test_zoom_without_target_has_no_inset(self):
        source = np.zeros((600, 600, 3), np.uint8)
        image, zoom = draw_target_zoom(source, source, {"gt_target_cell_indices": []}, None)
        self.assertIsNone(zoom)
        np.testing.assert_array_equal(image, source)

    def test_versioned_overlay_preserves_original_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cv2.imwrite(str(folder / "ego_obstacle_presence.png"), np.zeros((600, 600, 3), np.uint8))
            cv2.imwrite(str(folder / "evaluation_bev.png"), np.full((600, 600, 3), 5, np.uint8))
            np.savez(folder / "ego_own.npz", pose=np.eye(4))
            np.savez(folder / "ego_received_observer_1.npz", pose=np.eye(4))
            before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
            row = {"frame_id": 100, "gt_target_cell_indices": [1, 2, 3], "fused_obstacle_cell_indices": [2, 3, 7]}
            render_target(folder, row)
            record = json.loads((folder / OVERLAY_RECORD).read_text())
            self.assertEqual(record["target_match_cell_indices"], [2, 3])
            self.assertFalse(record["prediction_arrays_modified"])
            self.assertFalse(record["missed_target_cells_filled"])
            self.assertTrue((folder / OVERLAY_IMAGE).is_file())
            self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_viewer_prefers_versioned_image_with_original_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, case = "live_metric3_20261106T102959_306619cf", "M3-001-S1"
            report = root / "reports" / run
            sample = report / "cases" / case / "attempt_001" / "round_001"
            sample.mkdir(parents=True)
            (report / "protocol.json").write_text(json.dumps({"source": "live_carla_sensor_stream", "cases": [{"case_id": case}]}))
            (sample.parent / "samples.jsonl").write_text('{}\n')
            original = sample / "evaluation_bev.png"
            original.touch()
            reports = Reports(root)
            self.assertEqual(reports.asset(run, case, 0, "bev"), original)
            updated = sample / OVERLAY_IMAGE
            updated.touch()
            self.assertEqual(reports.asset(run, case, 0, "bev"), updated)


if __name__ == "__main__":
    unittest.main()
