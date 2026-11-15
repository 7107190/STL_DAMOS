import json
from pathlib import Path
import tempfile
import unittest

from bev_accuracy_batch import choose_assignment, longest_span
from run_metric3_100 import plan_cases
from verify_metric3_100 import cells, independent_duration, largest_overlap, verify_case, EXPECTED_CHECKPOINT


def sample(index, detected=True, eligible=True, *, gap=0):
    return {"round_index": index, "frame_id": index * 2 + gap,
        "capture_sim_ns": str(index * 200000000 + gap * 100000000),
        "fused_detected": detected, "eligible": eligible}


class TestMetric3(unittest.TestCase):
    def test_plan_unique(self):
        rows = plan_cases()
        self.assertEqual(len(rows), 100)
        self.assertEqual(len({r["scene_seed"] for r in rows}), 100)
        self.assertTrue(all(0 <= r["scene_seed"] < 2**32 for r in rows))
        self.assertEqual(len({r["case_id"] for r in rows}), 100)
        for s in range(1, 10):
            self.assertEqual(sum(r["scenario"] == f"S{s}" for r in rows), 12 if s == 9 else 11)

    def test_plan_repeatable(self):
        self.assertEqual(plan_cases(), plan_cases())

    def test_three_not_four_samples(self):
        for size, expected in ((3, 400000000), (4, 600000000)):
            rows = [sample(i) for i in range(1, size + 1)]
            self.assertEqual(longest_span(rows, "fused_detected"), expected)
            self.assertEqual(independent_duration(rows, "fused"), expected)

    def test_miss_breaks(self):
        for miss in (False, None):
            rows = [sample(1), sample(2), sample(3, miss), sample(4), sample(5)]
            self.assertEqual(longest_span(rows, "fused_detected"), 200000000)
            self.assertEqual(independent_duration(rows, "fused"), 200000000)

    def test_ineligible_breaks(self):
        rows = [sample(1), sample(2), sample(3, eligible=False), sample(4), sample(5)]
        self.assertEqual(longest_span(rows, "fused_detected"), 200000000)
        self.assertEqual(independent_duration(rows, "fused"), 200000000)

    def test_gap_breaks(self):
        rows = [sample(1), sample(2), sample(3, gap=2), sample(4, gap=2)]
        self.assertEqual(longest_span(rows, "fused_detected"), 200000000)
        self.assertEqual(independent_duration(rows, "fused"), 200000000)

    def test_clock_reversal(self):
        for fn in (lambda rows: longest_span(rows, "fused_detected"),
                   lambda rows: independent_duration(rows, "fused")):
            with self.assertRaises(ValueError):
                fn([sample(2), sample(1)])

    def test_diagonal_connected(self):
        self.assertEqual(largest_overlap({0, 201, 402}, {0, 201, 402}), 3)

    def test_disconnected_not_sum(self):
        self.assertEqual(largest_overlap({0, 1000, 2000}, {0, 1000, 2000}), 1)

    def test_no_row_wrap(self):
        self.assertEqual(largest_overlap({199, 200, 201}, {199, 200, 201}), 2)

    def test_truth_overlap_only(self):
        self.assertEqual(largest_overlap({0, 1, 2}, {1000, 1001, 1002}), 0)

    def test_sequential_witness_matches_full_binary_verdict(self):
        for pattern in range(256):
            rows = [sample(i + 1, bool(pattern & (1 << i))) for i in range(8)]
            prefix = []
            for row in rows:
                prefix.append(row)
                if longest_span(prefix, "fused_detected") >= 500000000:
                    break
            self.assertEqual(independent_duration(prefix, "fused") >= 500000000,
                             independent_duration(rows, "fused") >= 500000000)

    def test_invalid_cells(self):
        for data in ([True], [-1], [40000], [0, 0], [2, 1]):
            with self.assertRaises(ValueError):
                cells(data)

    def test_anchor_geometry_not_prediction(self):
        anchors = [{"anchor_index": 1, "anchor_location": {"x": 20, "y": -30}},
                   {"anchor_index": 2, "anchor_location": {"x": 1, "y": -2}}]
        self.assertEqual(choose_assignment(anchors, {"target_scenic_xy": [0, 0]})["anchor_index"], 2)


class TestMetadataVerification(unittest.TestCase):
    """Synthetic temporary fixtures only; never packaged as CARLA evidence."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.case = {"case_id": "UNIT-TEST-S5", "scenario": "S5", "entry_radius_m": 30}
        self.protocol = {"case_sha256": {"UNIT-TEST-S5": "unit-test-hash"}}
        self.recording = self.folder / "recording"
        self.recording.mkdir()
        self.put("invocation.json", {"case_sha256": "unit-test-hash"})
        actors = {"ego": {"actor_id": 1}, "observer_2": {"actor_id": 2}}
        audit = {"frames_checked": 30, "nodes": {n: {"all_six_identical_for_entire_clip": False} for n in actors}}
        ticks = []
        for index in range(1, 31):
            frame, stamp = index * 2, index * 200000000
            ticks.append({"frame_id": frame, "capture_sim_ns": stamp, "distance_m": 20})
            name = f"recording/round_{index:03d}"
            (self.folder / name).mkdir()
            self.put(name + "/ground_truth.json", {"frame_id": frame, "capture_sim_ns": stamp,
                "targets": [{"actor_id": 3, "anchor_index": 1, "scenario": "S5#1"}],
                "ego_road_state": {"frame_id": frame, "capture_sim_ns": stamp,
                    "actor_id": 1, "on_driving_lane": True, "location_carla": [0, 0, 0]}})
            self.put(name + "/evidence.json", {"case_id": self.case["case_id"], "round_index": index,
                "frame_id": frame, "capture_sim_ns": str(stamp), "member_actor_ids": [3],
                "gt_target_cell_indices": [0, 1, 2], "gt_return_cells": 3,
                "ego_obstacle_cell_indices": [0, 1, 2], "fused_obstacle_cell_indices": [0, 1, 2],
                "ego_connected_overlap_cells": 3, "fused_connected_overlap_cells": 3,
                "ego_detected": True, "fused_detected": True, "on_driving_lane": True,
                "in_ego_roi": True, "eligible": True, "ground_truth_fed_to_model_or_fusion": False,
                "ego_pose_model_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]})
            peers = {}
            for node, actor in actors.items():
                other = next(n for n in actors if n != node)
                peers[node] = {"status": "fusion_complete", "actor_id": actor["actor_id"],
                    "frame_id": frame, "inference": {"checkpoint_sha256": EXPECTED_CHECKPOINT},
                    "fusion_mode": "ego_observed", "verified_peer_ids": [other], "missing_peer_ids": []}
                self.put(name + "/" + node + "_packet.json", {"manifest": {"frame_id": frame,
                    "capture_sim_ns": stamp, "actor_id": actor["actor_id"], "checkpoint_sha256": EXPECTED_CHECKPOINT}})
            self.put(name + "/peers.json", peers)
        self.put("recording/capture.json", {"capture_complete": True, "error": None,
            "case": self.case, "case_sha256": "unit-test-hash", "fps": 5, "timeline": ticks,
            "actors": actors, "target_ids": [3], "anchor_index": 1, "camera_content_audit": audit})
        self.put("result.json", {"case_id": self.case["case_id"], "scenario": "S5",
            "status": "success", "successful": True, "returncode": 0, "error": None,
            "member_actor_ids": [3], "anchor_index": 1, "samples": 30, "eligible_samples": 30,
            "fused_longest_span_ns": "5800000000", "ego_longest_span_ns": "5800000000"})

    def put(self, name, value):
        (self.folder / name).write_text(json.dumps(value))

    def change(self, name, mutate):
        data = json.loads((self.folder / name).read_text())
        mutate(data)
        self.put(name, data)

    def test_valid_synthetic_fixture(self):
        self.assertTrue(verify_case(self.folder, self.case, self.protocol)["successful"])

    def test_forged_duration(self):
        self.change("result.json", lambda d: d.update(fused_longest_span_ns="9999999999"))
        with self.assertRaises(ValueError):
            verify_case(self.folder, self.case, self.protocol)

    def test_forged_overlap(self):
        self.change("recording/round_001/evidence.json", lambda d: d.update(fused_obstacle_cell_indices=[100, 101, 102]))
        with self.assertRaises(ValueError):
            verify_case(self.folder, self.case, self.protocol)

    def test_unverified_peer(self):
        self.change("recording/round_001/peers.json", lambda d: d["ego"].update(verified_peer_ids=[]))
        with self.assertRaises(ValueError):
            verify_case(self.folder, self.case, self.protocol)

    def test_wrong_model(self):
        self.change("recording/round_001/peers.json", lambda d: d["ego"]["inference"].update(checkpoint_sha256="wrong"))
        with self.assertRaises(ValueError):
            verify_case(self.folder, self.case, self.protocol)

    def test_target_replacement(self):
        self.change("recording/round_001/ground_truth.json", lambda d: d["targets"][0].update(actor_id=4))
        with self.assertRaises(ValueError):
            verify_case(self.folder, self.case, self.protocol)

    def test_coordinate_flip(self):
        self.change("recording/round_001/ground_truth.json", lambda d: d["ego_road_state"].update(location_carla=[0, 2, 0]))
        with self.assertRaises(ValueError):
            verify_case(self.folder, self.case, self.protocol)

    def test_missing_round(self):
        self.change("result.json", lambda d: d.update(samples=29))
        with self.assertRaises(ValueError):
            verify_case(self.folder, self.case, self.protocol)

    def test_failed_camera_gate(self):
        self.change("recording/capture.json", lambda d: d["camera_content_audit"]["nodes"]["ego"].update(all_six_identical_for_entire_clip=True))
        with self.assertRaises(ValueError):
            verify_case(self.folder, self.case, self.protocol)

    def test_failed_case_not_success(self):
        self.change("result.json", lambda d: d.update(status="data_error", successful=False, returncode=1))
        self.assertFalse(verify_case(self.folder, self.case, self.protocol)["successful"])

    def test_failed_case_cannot_be_promoted(self):
        self.change("result.json", lambda d: d.update(status="data_error", successful=True))
        with self.assertRaises(ValueError):
            verify_case(self.folder, self.case, self.protocol)

    def test_early_success_prefix(self):
        self.case["early_success_stop"] = True
        self.change("recording/capture.json", lambda d: d.update(case=self.case))
        self.change("result.json", lambda d: d.update(samples=4, eligible_samples=4,
            fused_longest_span_ns="600000000", ego_longest_span_ns="600000000",
            early_success_stop=True, captured_samples=30, unprocessed_samples=26))
        import shutil
        for index in range(5, 31):
            shutil.rmtree(self.recording / f"round_{index:03d}")
        self.assertTrue(verify_case(self.folder, self.case, self.protocol)["successful"])

    def test_early_failure_rejected(self):
        self.case["early_success_stop"] = True
        self.change("recording/capture.json", lambda d: d.update(case=self.case))
        self.change("result.json", lambda d: d.update(samples=3, eligible_samples=3,
            fused_longest_span_ns="400000000", ego_longest_span_ns="400000000",
            early_success_stop=True, status="failure", successful=False))
        import shutil
        for index in range(4, 31):
            shutil.rmtree(self.recording / f"round_{index:03d}")
        with self.assertRaises(ValueError):
            verify_case(self.folder, self.case, self.protocol)


if __name__ == "__main__":
    unittest.main()
