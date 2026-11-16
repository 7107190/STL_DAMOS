from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from live_checks import raw_live_inputs, round_checks, post_distance_diagnostic_only
from report_data import Reports


NAMES = ("cam_front_left", "cam_front", "cam_front_right", "cam_back_left", "cam_back", "cam_back_right", "lidar")


def tasks():
    return {node: {"actor_id": actor, "actor_type_id": "test", "label": node,
                   "input_source": "live_raw_memory", "window": [10, 11, 12, 13],
                   "packets": {(node, name): [{"frame": f, "timestamp": f / 10, "raw": b"actual sensor bytes"}
                                             for f in [10, 11, 12, 13]] for name in NAMES}}
            for node, actor in {"ego": 24, "observer_60": 60}.items()}


class LiveInputs(unittest.TestCase):
    def setUp(self):
        self.tasks = tasks()
        self.actors = {"ego": 24, "observer_60": 60}

    def test_actual_raw_windows_record_each_sensor_hash(self):
        evidence = raw_live_inputs(self.tasks, 9, self.actors)
        self.assertEqual(evidence["capture_sim_ns"], 1_300_000_000)
        self.assertEqual(len(evidence["nodes"]["ego"]["sensors"]), 7)

    def reject(self, change):
        change(self.tasks)
        with self.assertRaises(ValueError):
            raw_live_inputs(self.tasks, 9, self.actors)

    def test_recorded_tensor_rejected(self):
        self.reject(lambda t: t["ego"].update(arrays={}))

    def test_recorded_input_source_rejected(self):
        self.reject(lambda t: t["ego"].update(input_source="recorded_original_rgb_lidar_memory"))

    def test_actor_replacement_rejected(self):
        self.reject(lambda t: t["ego"].update(actor_id=99))

    def test_unequal_peer_frames_rejected(self):
        self.reject(lambda t: t["observer_60"].update(window=[11, 12, 13, 14]))

    def test_repeated_previous_frame_rejected(self):
        with self.assertRaises(ValueError):
            raw_live_inputs(self.tasks, 10, self.actors)

    def test_missing_camera_rejected(self):
        self.reject(lambda t: t["ego"]["packets"].pop(("ego", "cam_front")))

    def test_inconsistent_packet_frame_rejected(self):
        self.reject(lambda t: t["ego"]["packets"]["ego", "cam_front"][-1].update(frame=12))

    def test_inconsistent_sensor_time_rejected(self):
        self.reject(lambda t: t["ego"]["packets"]["ego", "cam_front"][-1].update(timestamp=5))

    def test_inconsistent_peer_time_rejected(self):
        for packets in self.tasks["observer_60"]["packets"].values():
            for packet in packets:
                packet["timestamp"] += 1
        with self.assertRaises(ValueError):
            raw_live_inputs(self.tasks, 9, self.actors)

    def test_missing_peer_rejected(self):
        self.reject(lambda t: t.pop("observer_60"))


class Results(unittest.TestCase):
    def setUp(self):
        self.actors = {"ego": 24, "observer_60": 60}
        self.rows = {node: {"actor_id": actor, "frame_id": 13, "input": {"input_source": "live_raw_memory"},
                            "status": "fusion_complete", "peer_ids": [peer], "verified_peer_ids": [peer],
                            "missing_peer_ids": [], "proof": {"result_class": "REAL_GROTH16"},
                            "inference": {"checkpoint_sha256": "model"},
                            "started_ns": 1_000_000, "ended_ns": 1_501_000_000, "elapsed_ms": 1500}
                     for node, actor, peer in (("ego", 24, "observer_60"), ("observer_60", 60, "ego"))}

    def check(self):
        return round_checks(self.rows, self.actors, 13, "model")

    def test_real_round_passes(self):
        self.assertTrue(self.check()["speed_pass"])

    def test_wrong_capture_frame_rejected(self):
        self.rows["ego"]["frame_id"] = 12
        self.assertFalse(self.check()["functional_pass"])

    def test_dummy_proof_rejected(self):
        self.rows["ego"]["proof"]["result_class"] = "MOCK"
        self.assertFalse(self.check()["speed_pass"])

    def test_unequal_clock_origins_rejected(self):
        self.rows["ego"]["started_ns"] = 0
        self.assertFalse(self.check()["functional_pass"])

    def test_model_mismatch_rejected(self):
        self.rows["ego"]["inference"]["checkpoint_sha256"] = "wrong"
        self.assertFalse(self.check()["functional_pass"])

    def test_over18_not_speed_pass(self):
        self.rows["ego"].update(ended_ns=19_001_000_000, elapsed_ms=19000)
        result = self.check()
        self.assertTrue(result["functional_pass"])
        self.assertFalse(result["speed_pass"])

    def test_offline_report_cannot_enter_live_ui(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "reports/offline"
            folder.mkdir(parents=True)
            (folder / "protocol.json").write_text(json.dumps({"ego_only": True, "budget_seconds": 18.0,
                "sessions": 3, "repeats_per_input_per_session": 5, "scenarios": [f"S{i}" for i in range(1, 10)]}))
            reports = Reports(root)
            self.assertEqual(reports.history(), [])
            with self.assertRaises(ValueError):
                reports.folder("offline")


class PostSceneScope(unittest.TestCase):
    def setUp(self):
        self.error = RuntimeError('Custom observers did not satisfy Scenic anchor coverage requirements. Observed: moving target')
        self.metrics = [{'status': 'ok', 'on_sidewalk': True}]
        self.failures = [('observer', 'observer_to_anchor_distance=28.22m')]
        self.rows = [{'functional_pass': True, 'audit': {'proofs_reverified': 2}} for _ in range(5)]

    def check(self):
        return post_distance_diagnostic_only(self.error, self.metrics, self.failures, self.rows, 5)

    def test_only_finished_pipeline_can_separate_post_run_distance(self):
        self.assertTrue(self.check())

    def test_missing_timed_round_cannot_be_accepted(self):
        self.rows.pop()
        self.assertFalse(self.check())

    def test_failed_pipeline_cannot_be_accepted(self):
        self.rows[0]['functional_pass'] = False
        self.assertFalse(self.check())

    def test_facing_or_sidewalk_issue_is_not_distance_only(self):
        self.failures.append(('observer', 'facing_error=40deg'))
        self.assertFalse(self.check())
        self.failures.pop()
        self.metrics[0]['on_sidewalk'] = False
        self.assertFalse(self.check())

    def test_arbitrary_runtime_error_cannot_be_accepted(self):
        self.error = RuntimeError('CUDA out of memory')
        self.assertFalse(self.check())


if __name__ == "__main__":
    unittest.main()
