import sys
from pathlib import Path
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bev_ground_guard import VERSION, apply_ground_guard, validate_ground_guard


class GroundGuardTests(unittest.TestCase):
    def fixture(self):
        raster = np.zeros((6, 200, 200), np.float32)
        raster[0] = 1
        raster[1] = 2
        raster[2:4] = -.8
        p = np.full((17, 200, 200), .001, np.float32)
        p[8] = .55
        p[11] = .435
        prediction = {"probabilities": p.astype(np.float16),
                      "valid": raster[0].astype(np.uint8), "pose": np.eye(4)}
        config = {"version": VERSION, "height_m": .1, "span_m": .1}
        return raster, prediction, config

    def test_ground_is_corrected_but_elevated_object_and_unknown_are_preserved(self):
        raster, prediction, config = self.fixture()
        raster[2:4, 120:123, 100:103] = .2
        prediction["valid"][0, 0] = 0
        old = prediction["probabilities"].copy()
        result, changed = apply_ground_guard(prediction, raster, config)
        self.assertTrue(changed[110, 110])
        self.assertEqual(result["probabilities"][:, 110, 110].argmax(), 11)
        self.assertFalse(changed[121, 101])
        np.testing.assert_array_equal(result["probabilities"][:, 121, 101], old[:, 121, 101])
        np.testing.assert_array_equal(result["probabilities"][:, 0, 0], old[:, 0, 0])
        np.testing.assert_array_equal(prediction["probabilities"], old)
        np.testing.assert_array_equal(result["valid"], prediction["valid"])

    def test_height_offset_and_sloped_plane(self):
        raster, prediction, config = self.fixture()
        axis = -40 + (np.arange(200) + .5) * .4
        floor = -1.2 + .02 * axis[:, None] + .01 * axis[None, :]
        raster[2:4] = floor
        result, changed = apply_ground_guard(prediction, raster, config)
        self.assertGreater(changed.sum(), 39000)

    def test_vertical_span_preserves_object_touching_ground(self):
        raster, prediction, config = self.fixture()
        raster[2, 100, 100] = .6
        raster[4, 100, 100] = 1.4
        _, changed = apply_ground_guard(prediction, raster, config)
        self.assertFalse(changed[100, 100])

    def test_invalid_config_and_nonfinite_input_fail_closed(self):
        raster, prediction, config = self.fixture()
        for value in (0, float("nan"), .9):
            with self.assertRaises(ValueError):
                validate_ground_guard(dict(config, height_m=value))
        raster[2, 0, 0] = float("nan")
        with self.assertRaises(ValueError):
            apply_ground_guard(prediction, raster, config)

    def test_small_isolated_flat_patch_is_not_enough_ground_evidence(self):
        raster, prediction, config = self.fixture()
        raster[0] = 0
        raster[0, 90:100, 90:100] = 1
        _, changed = apply_ground_guard(prediction, raster, config)
        self.assertFalse(changed.any())

    def test_structure_neighbours_preserve_feet_and_registration_edges(self):
        raster, prediction, config = self.fixture()
        raster[2, 120, 100] = .3
        raster[4, 120, 100] = 1.1
        config["protect_radius_cells"] = 1
        _, changed = apply_ground_guard(prediction, raster, config)
        self.assertFalse(changed[121, 101])
        self.assertTrue(changed[122, 102])

    def test_asymmetric_lower_band_keeps_elevated_tops(self):
        raster, prediction, config = self.fixture()
        config["below_plane_m"] = .25
        raster[2:4, 120, 100] = -.95
        raster[2:4, 130, 100] = -.6
        _, changed = apply_ground_guard(prediction, raster, config)
        self.assertTrue(changed[120, 100])
        self.assertFalse(changed[130, 100])

    def test_context_colour_keeps_buildings_and_vegetation_as_obstacles(self):
        from bev_occupancy import obstacle_presence, render_presence, render_presence_context
        _, prediction, _ = self.fixture()
        prediction["probabilities"][:, 120, 120] = 0
        prediction["probabilities"][15, 120, 120] = 1
        before = prediction["probabilities"].copy()
        raw, context = render_presence(prediction, 200), render_presence_context(prediction, 200)
        np.testing.assert_array_equal(raw, context)
        np.testing.assert_array_equal(context[79, 79], [0, 180, 255])
        self.assertEqual(obstacle_presence(before, prediction["valid"])["occupancy"][120, 120], 2)
        np.testing.assert_array_equal(prediction["probabilities"], before)

    def test_foreground_protection_requires_model_evidence(self):
        from bev_ground_guard import ground_candidates
        raster, prediction, config = self.fixture()
        config.update(protect_radius_cells=1, protect_mode="foreground")
        with self.assertRaises(ValueError):
            ground_candidates(raster, config)

    def test_focus_zoom_is_display_only_and_clamps_at_grid_edge(self):
        from bev_occupancy import render_presence_focus
        _, prediction, _ = self.fixture()
        old = prediction["probabilities"].copy()
        mask = np.zeros((200, 200), bool); mask[0:2, 0:2] = True
        image = render_presence_focus(prediction, mask, (0, 0, 100))
        self.assertEqual(image.shape, (640, 640, 3))
        np.testing.assert_array_equal(prediction["probabilities"], old)
        empty = render_presence_focus(prediction, np.zeros_like(mask), (0, 0, 100))
        self.assertEqual(empty.shape, image.shape)


if __name__ == "__main__":
    unittest.main()
