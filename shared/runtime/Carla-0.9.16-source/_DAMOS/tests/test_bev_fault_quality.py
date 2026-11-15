"""Synthetic data-quality fixtures; real CUDA/CARLA results live in separate trial folders."""
from pathlib import Path
import io
import json
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bev_fault_quality import (QUALITY_FAULTS, inject_quality_input, inspect_quality,
                               load_raw_task, nominal_calibration_task, save_raw_task)
from bev_sensor_rig import CAMERAS, HEIGHT, WIDTH


def sample(frame=12):
    rng = np.random.default_rng(15)
    image = rng.integers(20, 230, (HEIGHT, WIDTH, 4), np.uint8)
    image[:, :, 3] = 255
    angle = rng.uniform(-np.pi, np.pi, 1000)
    distance = rng.uniform(5, 35, 1000)
    points = np.column_stack((distance * np.cos(angle), distance * np.sin(angle),
                              np.zeros(1000), np.full(1000, .5))).astype(np.float32)
    names = (*CAMERAS, "lidar")
    packets = {}
    for name in names:
        raw = points.tobytes() if name == "lidar" else image.tobytes()
        packets["ego", name] = [{"raw": raw, "frame": frame - 3 + i,
            "timestamp": (frame - 3 + i) / 10., "transform": np.eye(4)} for i in range(4)]
    return {"label": "ego", "actor_id": 42, "actor_type_id": "vehicle.test",
            "window": list(range(frame - 3, frame + 1)), "packets": packets,
            "sensor_matrices": {("ego", n): np.eye(4) for n in names}, "camera_fovs": {n: 90. for n in CAMERAS}}


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.original = sample()

    def test_normal_input_has_no_quality_issue(self):
        self.assertEqual(inspect_quality(self.original, self.original)["issues"], [])

    def test_blur_measured_from_pixels_not_a_mode_flag(self):
        changed = inject_quality_input(self.original, "camera_degradation")
        self.assertIn("CAMERA_DETAIL_LOSS", inspect_quality(changed, self.original)["issues"])
        self.assertNotEqual(changed["packets"]["ego", "cam_front"][-1]["raw"], self.original["packets"]["ego", "cam_front"][-1]["raw"])
        self.assertEqual(inspect_quality(self.original, self.original)["issues"], [])

    def test_gaussian_noise_violates_known_scan_envelope(self):
        changed = inject_quality_input(self.original, "lidar_noise")
        self.assertIn("LIDAR_SCAN_ENVELOPE_VIOLATION", inspect_quality(changed, self.original)["issues"])

    def test_dropout_is_nonempty_and_not_complete_loss(self):
        changed = inject_quality_input(self.original, "lidar_dropout")
        points = np.frombuffer(changed["packets"]["ego", "lidar"][-1]["raw"], np.float32).reshape(-1, 4)
        self.assertEqual(len(points), 100)
        self.assertIn("LIDAR_DENSITY_LOSS", inspect_quality(changed, self.original)["issues"])

    def test_phantom_points_are_in_roi_but_outside_scan_envelope(self):
        changed = inject_quality_input(self.original, "lidar_outlier")
        points = np.frombuffer(changed["packets"]["ego", "lidar"][-1]["raw"], np.float32).reshape(-1, 4)
        self.assertEqual(len(points), 1500)
        self.assertTrue((np.abs(points[1000:, :2]) < 40).all())
        self.assertIn("LIDAR_SCAN_ENVELOPE_VIOLATION", inspect_quality(changed, self.original)["issues"])

    def test_injection_is_replayable_and_original_not_mutated(self):
        for name in QUALITY_FAULTS:
            first = inject_quality_input(self.original, name)
            second = inject_quality_input(self.original, name)
            for key in self.original["packets"]:
                for a, b in zip(first["packets"][key], second["packets"][key]):
                    self.assertEqual(a["raw"], b["raw"])
            self.assertEqual(inspect_quality(self.original, self.original)["issues"], [])

    def test_physical_mount_mismatch_not_an_image_rotation(self):
        theta = np.radians(25)
        rotation = np.eye(4)
        rotation[:2, :2] = [[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]]
        self.original["packets"]["ego", "cam_front"][-1]["transform"] = rotation
        health = inspect_quality(self.original, sample())
        self.assertIn("CAMERA_MOUNT_MISMATCH:cam_front", health["issues"])
        self.assertAlmostEqual(health["evidence"]["mounts"]["cam_front"]["rotation_error_deg"], 25)

    def test_diagnostic_model_uses_nominal_calibration_without_overwriting_measured_pose(self):
        altered = np.eye(4)
        altered[0, 3] = 2.
        self.original["packets"]["ego", "cam_front"][-1]["transform"] = altered
        diagnostic = nominal_calibration_task(self.original)
        np.testing.assert_array_equal(diagnostic["packets"]["ego", "cam_front"][-1]["transform"], np.eye(4))
        np.testing.assert_array_equal(self.original["packets"]["ego", "cam_front"][-1]["transform"], altered)

    def test_raw_evidence_roundtrip_retains_frame_pose_and_every_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.npz"
            save_raw_task(path, self.original)
            restored = load_raw_task(path)
            self.assertEqual(restored["window"], self.original["window"])
            self.assertEqual(restored["actor_id"], self.original["actor_id"])
            for key in self.original["packets"]:
                for a, b in zip(self.original["packets"][key], restored["packets"][key]):
                    self.assertEqual(a["raw"], b["raw"])
                    self.assertEqual(a["frame"], b["frame"])
                    self.assertEqual(a["timestamp"], b["timestamp"])
                    np.testing.assert_array_equal(a["transform"], b["transform"])

    def test_other_cameras_and_intensities_not_silently_replaced(self):
        blurred = inject_quality_input(self.original, "camera_degradation")
        self.assertEqual(blurred["packets"]["ego", "cam_back"][-1]["raw"], self.original["packets"]["ego", "cam_back"][-1]["raw"])
        noisy = inject_quality_input(self.original, "lidar_noise")
        points = np.frombuffer(noisy["packets"]["ego", "lidar"][-1]["raw"], np.float32).reshape(-1, 4)
        self.assertTrue((points[:, 3] == .5).all())


class AuditStatusTests(unittest.TestCase):
    def run_status(self, numeric_status):
        import audit_carla_resilience as auditor
        payload = {"counts": {"rounds": 1}, "all_saved_arrays_and_states_consistent": True,
                   "inference_replay_within_limits": numeric_status}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "audit.json"
            args = ["audit", directory, "--output", str(output)]
            if numeric_status is not None:
                args.append("--replay-inference")
            code = 0
            with mock.patch.object(sys, "argv", args), mock.patch.object(auditor, "audit", return_value=payload), \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                try:
                    auditor.main()
                except SystemExit as exc:
                    code = exc.code
            saved = json.loads(output.read_text())
        return code, saved

    def test_numeric_failure_is_saved_and_returns_nonzero(self):
        code, saved = self.run_status(False)
        self.assertEqual(code, 2)
        self.assertFalse(saved["inference_replay_within_limits"])
        self.assertTrue(saved["all_saved_arrays_and_states_consistent"])

    def test_numeric_success_returns_zero(self):
        code, saved = self.run_status(True)
        self.assertEqual(code, 0)
        self.assertTrue(saved["inference_replay_within_limits"])

    def test_unrequested_numeric_replay_is_not_claimed(self):
        code, saved = self.run_status(None)
        self.assertEqual(code, 0)
        self.assertIsNone(saved["inference_replay_within_limits"])


if __name__ == "__main__":
    unittest.main()
