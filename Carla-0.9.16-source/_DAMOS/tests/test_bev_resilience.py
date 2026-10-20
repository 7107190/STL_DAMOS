"""Synthetic unit fixtures only; not CARLA trials or formal accuracy samples."""
from pathlib import Path
import hashlib
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bev_resilience import ServicePolicy, empty_reference, inject_input, inspect_input

NAMES = ("cam_front_left", "cam_front", "cam_front_right", "cam_back_left", "cam_back", "cam_back_right")


def sample(frame=12):
    packets = {}
    for name in (*NAMES, "lidar"):
        raw = np.ones((2, 2, 4), np.uint8).tobytes() if name != "lidar" else np.ones((3, 4), np.float32).tobytes()
        packets["ego", name] = [{"frame": frame - 3 + i, "timestamp": (frame - 3 + i) / 10,
                                  "raw": raw, "transform": np.eye(4)} for i in range(4)]
    return {"label": "ego", "actor_id": 1, "window": list(range(frame - 3, frame + 1)), "packets": packets}


class PolicyTests(unittest.TestCase):
    def update(self, policy, local=True, peers=1, cells=100, fresh=True):
        return policy.update(local_usable=local, trusted_peer_count=peers, output_cells=cells, fresh=fresh)

    def test_requires_three_clean_rounds(self):
        p = ServicePolicy()
        self.assertEqual([self.update(p)["state"] for _ in range(3)], ["RECOVERING", "RECOVERING", "NORMAL"])

    def test_valid_peer_can_supply_degraded_monitoring(self):
        p = ServicePolicy()
        row = self.update(p, local=False)
        self.assertEqual(row["state"], "DEGRADED_COOPERATIVE")
        self.assertTrue(row["cooperative_service_available"])

    def test_local_only_does_not_claim_cooperative_coverage(self):
        row = self.update(ServicePolicy(), peers=0)
        self.assertEqual(row["state"], "SAFE_STOP")
        self.assertTrue(row["stop_required"])

    def test_old_result_and_empty_output_stop(self):
        self.assertEqual(self.update(ServicePolicy(), fresh=False)["state"], "SAFE_STOP")
        self.assertEqual(self.update(ServicePolicy(), cells=2)["state"], "SAFE_STOP")

    def test_fault_resets_recovery_streak(self):
        p = ServicePolicy()
        self.update(p); self.update(p)
        self.update(p, peers=0)
        self.assertEqual(self.update(p)["clean_streak"], 1)

    def test_degraded_resets_recovery_streak(self):
        p = ServicePolicy()
        self.update(p); self.update(p, local=False)
        self.assertEqual(self.update(p)["state"], "RECOVERING")

    def test_normal_stays_normal(self):
        p = ServicePolicy()
        for _ in range(3): self.update(p)
        self.assertEqual(self.update(p)["state"], "NORMAL")

    def test_invalid_recovery_threshold(self):
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError): ServicePolicy(value)


class InputTests(unittest.TestCase):
    def health(self, task, frame=12):
        with mock.patch.dict(sys.modules, {"bev_sensor_rig": SimpleNamespace(CAMERAS=NAMES, HEIGHT=2, WIDTH=2)}):
            return inspect_input(task, frame)

    def test_normal_fixture(self):
        self.assertTrue(self.health(sample())["usable"])

    def test_black_front_rejected_without_changing_original(self):
        original = sample()
        changed = inject_input(original, "front_blackout", None)
        self.assertEqual(self.health(changed)["issues"], ["BLACK_CAMERA:cam_front"])
        self.assertTrue(self.health(original)["usable"])

    def test_missing_lidar_rejected(self):
        self.assertIn("MISSING_OR_INVALID_LIDAR", self.health(inject_input(sample(), "lidar_missing", None))["issues"])

    def test_retained_real_input_keeps_its_old_frame(self):
        held = sample(8)
        changed = inject_input(sample(), "sensor_delay", held)
        self.assertIs(changed, held)
        self.assertIn("STALE_LIDAR", self.health(changed)["issues"])

    def test_compound_failure_rejects_all_sensors(self):
        issues = self.health(inject_input(sample(), "rgb_lidar_failure", None))["issues"]
        self.assertEqual(len([i for i in issues if i.startswith("BLACK_CAMERA")]), 6)
        self.assertIn("MISSING_OR_INVALID_LIDAR", issues)

    def test_nonfinite_lidar_rejected(self):
        task = sample()
        task["packets"]["ego", "lidar"][-1]["raw"] = np.full((3, 4), np.nan, np.float32).tobytes()
        self.assertFalse(self.health(task)["usable"])

    def test_malformed_camera_rejected(self):
        task = sample()
        task["packets"]["ego", "cam_front"][-1]["raw"] = b"bad"
        self.assertIn("MALFORMED_CAMERA:cam_front", self.health(task)["issues"])

    def test_unsynchronized_timestamp_rejected(self):
        task = sample()
        task["packets"]["ego", "cam_front"][-1]["timestamp"] += .1
        self.assertIn("UNSYNCHRONIZED_INPUT", self.health(task)["issues"])

    def test_delay_requires_prior_input(self):
        with self.assertRaises(ValueError): inject_input(sample(), "sensor_delay", None)

    def test_unknown_fault_rejected(self):
        with self.assertRaises(ValueError): inject_input(sample(), "unsupported", None)

    def test_empty_reference_never_counts_as_observed(self):
        result = empty_reference(np.eye(4))
        self.assertEqual(int(result["valid"].sum()), 0)
        self.assertTrue(np.isfinite(result["probabilities"]).all())


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.summary = {"planned": 30, "completed": 30, "successful": 30,
                        "executions": [{"returncode": 0, "summary": {"fatal_error": None}}]}

    def test_all_conditions_required(self):
        from run_carla_resilience import completed_successfully
        self.assertTrue(completed_successfully(self.summary))
        self.summary["successful"] = 29
        self.assertFalse(completed_successfully(self.summary))

    def test_incomplete_is_not_success(self):
        from run_carla_resilience import completed_successfully
        self.summary.update(completed=20, successful=20)
        self.assertFalse(completed_successfully(self.summary))

    def test_runtime_error_is_not_success(self):
        from run_carla_resilience import completed_successfully
        self.summary["executions"][0]["returncode"] = 1
        self.assertFalse(completed_successfully(self.summary))


class SourceSnapshotTests(unittest.TestCase):
    def test_saved_source_and_hash_use_same_bytes(self):
        from run_carla_resilience import freeze_source
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "example.py"
            original = b"print('original')\n"
            source.write_bytes(original)
            digests = freeze_source([source], root)
            source.write_bytes(b"print('changed')\n")
            saved = root / "frozen_source" / source.name
            self.assertEqual(saved.read_bytes(), original)
            self.assertEqual(digests, {str(source.resolve()): hashlib.sha256(original).hexdigest()})

    def test_existing_snapshot_not_replaced(self):
        from run_carla_resilience import freeze_source
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "example.py"
            source.write_bytes(b"original")
            freeze_source([source], root)
            source.write_bytes(b"changed")
            with self.assertRaises(FileExistsError):
                freeze_source([source], root)
            self.assertEqual((root / "frozen_source" / source.name).read_bytes(), b"original")

    def test_duplicate_filenames_rejected_before_snapshot(self):
        from run_carla_resilience import freeze_source
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "example.py"
            second = root / "other" / "example.py"
            second.parent.mkdir()
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            with self.assertRaises(ValueError):
                freeze_source([first, second], root)
            self.assertFalse((root / "frozen_source").exists())


if __name__ == "__main__":
    unittest.main()
