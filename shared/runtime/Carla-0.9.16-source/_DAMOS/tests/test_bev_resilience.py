"""Synthetic unit fixtures only; not CARLA trials or formal accuracy samples."""
from pathlib import Path
import hashlib
import queue
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bev_resilience import (ServicePolicy, empty_reference, inject_input, inspect_input,
                            inspect_model_output, model_output, run_service_round)

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


class OutputFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.expected = dict(round_id=2, frame_id=12, capture_sim_ns=1200000000,
                             started_ns=100, deadline_ns=200, checked_ns=160)
        self.result = model_output(empty_reference(np.eye(4)), round_id=2, frame_id=12,
            capture_sim_ns=1200000000, started_ns=110, completed_ns=150)

    def inspect(self, metadata=None, **changes):
        return inspect_model_output(self.result["metadata"] if metadata is None else metadata,
                                    **dict(self.expected, **changes))

    def test_current_actual_completion_accepted(self):
        self.assertTrue(self.inspect()["usable"])

    def test_old_source_rejected_without_any_fault_label(self):
        for key in ("round_id", "frame_id", "capture_sim_ns", "started_ns"):
            with self.subTest(field=key):
                changed = dict(self.result["metadata"], **{key: self.expected[key] - 1})
                self.assertEqual(self.inspect(changed)["issues"], ["STALE_MODEL_OUTPUT"])

    def test_missing_or_malformed_provenance_rejected(self):
        for metadata in ({}, [], None, {"schema": "unknown"}):
            with self.subTest(metadata=metadata):
                row = inspect_model_output(metadata, **self.expected)
                self.assertEqual(row["issues"], ["INVALID_MODEL_OUTPUT_METADATA"])
        for value in (True, "12", 12., -1):
            changed = dict(self.result["metadata"], frame_id=value)
            self.assertFalse(self.inspect(changed)["usable"])

    def test_completion_cannot_precede_start_or_be_in_future(self):
        for completed in (109, 161):
            self.assertIn("INVALID_MODEL_OUTPUT_TIME", self.inspect(
                dict(self.result["metadata"], completed_ns=completed))["issues"])

    def test_deadline_enforced_without_sleep(self):
        self.assertEqual(self.inspect(checked_ns=201)["issues"], ["MODEL_OUTPUT_DEADLINE"])
        self.assertTrue(self.inspect(checked_ns=200)["usable"])

    def test_same_bev_content_with_new_completion_is_not_a_freeze(self):
        other = model_output(self.result["prediction"], round_id=3, frame_id=13,
            capture_sim_ns=1300000000, started_ns=210, completed_ns=250)
        self.assertIs(other["prediction"], self.result["prediction"])
        self.assertTrue(inspect_model_output(other["metadata"], round_id=3, frame_id=13,
            capture_sim_ns=1300000000, started_ns=200, deadline_ns=300, checked_ns=260)["usable"])


class OutputServiceTests(unittest.TestCase):
    def setUp(self):
        self.prediction = empty_reference(np.eye(4))
        self.prediction["valid"][:3, :3] = 1
        self.predictor = SimpleNamespace(checkpoint_hash="fixture", step=1, fusion_mode="weighted",
            infer=mock.Mock(return_value=(self.prediction, {"synthetic_fixture": True})))

    def run_round(self, directory, frame=12, cache=None, fault=None, override=None):
        task = {"started_ns": time.perf_counter_ns(), "service_timeout_s": 8,
                "input": sample(frame), "fault": fault or {}, "round_id": frame - 10,
                "graph": {"ego": []}, "save_dir": str(Path(directory) / str(frame))}
        modules = {"bev_live": SimpleNamespace(task_pose=lambda _: np.eye(4)),
            "bev_sensor_rig": SimpleNamespace(CAMERAS=NAMES, HEIGHT=2, WIDTH=2),
            "bev_occupancy": SimpleNamespace(render_presence=lambda _: np.zeros((2, 2, 3), np.uint8))}
        prepare = lambda s: ({"timestamps": np.array([s["packets"]["ego", "lidar"][-1]["timestamp"]])}, {})
        with mock.patch.dict(sys.modules, modules), \
                mock.patch("bev_resilience.inspect_quality", return_value={"issues": []}), \
                mock.patch("bev_resilience.protect_bev", return_value=({}, b"fixture", {})) as protect, \
                mock.patch("bev_resilience.model_output", side_effect=override or model_output):
            row = run_service_round("ego", task, {"session_id": "fixture", "addresses": {}},
                self.predictor, None, None, None, None, SimpleNamespace(items=queue.Queue()),
                {} if cache is None else cache, prepare)
        return row, protect.call_args.args[3]

    def test_unlabelled_old_output_not_signed_or_fused(self):
        stale = model_output(self.prediction, round_id=1, frame_id=11,
            capture_sim_ns=1100000000, started_ns=1, completed_ns=2)
        with tempfile.TemporaryDirectory() as directory:
            row, published = self.run_round(directory, override=lambda *a, **k: stale)
            self.assertEqual(row["fault_injection"], {})
            self.assertEqual(row["health"]["issues"], ["STALE_MODEL_OUTPUT"])
            self.assertFalse(row["local_usable"])
            self.assertEqual(row["local_observed_cells"], 0)
            self.assertFalse(published["valid"].any())
            saved = Path(directory) / "12" / "ego_rejected_output.npz"
            with np.load(saved, allow_pickle=False) as data:
                np.testing.assert_array_equal(data["valid"], self.prediction["valid"])

    def test_injected_freeze_uses_same_guard_and_preserves_provenance(self):
        cache = {}
        with tempfile.TemporaryDirectory() as directory:
            normal, _ = self.run_round(directory, cache=cache)
            frozen, published = self.run_round(directory, frame=13, cache=cache,
                                               fault={"input": "output_freeze"})
            self.assertEqual(normal["model_output"], frozen["model_output"])
            self.assertEqual(frozen["health"]["issues"], ["STALE_MODEL_OUTPUT"])
            self.assertFalse(published["valid"].any())
            self.assertEqual(self.predictor.infer.call_count, 1)
            recovered, _ = self.run_round(directory, frame=14, cache=cache)
            self.assertTrue(recovered["model_output_health"]["usable"])
            self.assertTrue(recovered["local_usable"])

    def test_identical_fresh_predictions_remain_usable(self):
        cache = {}
        with tempfile.TemporaryDirectory() as directory:
            for frame in (12, 13, 14):
                row, published = self.run_round(directory, frame=frame, cache=cache)
                self.assertTrue(row["model_output_health"]["usable"])
                self.assertTrue(row["local_usable"])
                np.testing.assert_array_equal(published["valid"], self.prediction["valid"])


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
