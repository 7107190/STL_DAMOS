import json
from pathlib import Path
import tempfile
import unittest

from m3_data import Reports
from m3_rules import duration, rollup, safe_run


def samples(count, *, stride=2, period=200_000_000):
    return [{"round_index":i+1,"frame_id":100+i*stride,"capture_sim_ns":str(1_000_000_000+i*period),
             "eligible":True,"fused_detected":True,"ego_detected":False} for i in range(count)]


class ContinuousDetection(unittest.TestCase):
    def test_three_samples_only_cover_point_four_seconds(self):
        self.assertEqual(duration(samples(3)),400_000_000)

    def test_four_samples_pass_half_second_threshold(self):
        self.assertEqual(duration(samples(4)),600_000_000)

    def test_processing_wall_time_does_not_define_detection_span(self):
        rows=samples(3)
        for i,r in enumerate(rows):
            r["dispatch_wall_ns"]=i*20_000_000_000
        self.assertEqual(duration(rows),400_000_000)

    def test_missing_sensor_frame_breaks_continuity(self):
        self.assertEqual(duration(samples(5,stride=4)),0)

    def test_missing_sensor_timestamp_breaks_continuity(self):
        self.assertEqual(duration(samples(5,period=400_000_000)),0)

    def test_one_missed_detection_resets_run(self):
        rows=samples(6)
        rows[3]["fused_detected"]=False
        self.assertEqual(duration(rows),400_000_000)

    def test_unassessable_frame_resets_run(self):
        rows=samples(6)
        rows[3]["eligible"]=False
        self.assertEqual(duration(rows),400_000_000)

    def test_reversed_clock_is_rejected(self):
        rows=samples(4)
        rows[2]["capture_sim_ns"]=rows[1]["capture_sim_ns"]
        with self.assertRaises(ValueError):
            duration(rows)

    def test_ego_and_fusion_are_distinct(self):
        rows=samples(4)
        self.assertEqual(duration(rows,"ego"),0)
        self.assertEqual(duration(rows),600_000_000)


class Aggregate(unittest.TestCase):
    def cases(self):
        return [{"case_id":str(i)} for i in range(100)]

    def test_partial_seventy_successes_are_not_final_pass(self):
        results={str(i):{"status":"success","successful":True} for i in range(70)}
        summary=rollup(self.cases(),results)
        self.assertFalse(summary["criterion_pass"])
        self.assertEqual(summary["success_rate_percent"],70)

    def test_seventy_of_one_hundred_meets_criterion(self):
        results={str(i):{"status":"success" if i<70 else "failure","successful":i<70} for i in range(100)}
        self.assertTrue(rollup(self.cases(),results)["criterion_pass"])

    def test_execution_error_is_not_detection_failure(self):
        summary=rollup(self.cases(),{"0":{"status":"execution_error","successful":False}})
        self.assertEqual(summary["execution_errors"],1)
        self.assertEqual(summary["detection_failures"],0)
        self.assertEqual(summary["evaluated"],0)
        self.assertEqual(summary["planned"],100)

    def test_single_success_cannot_claim_full_metric(self):
        summary=rollup([{"case_id":"one"}],{"one":{"status":"success","successful":True}})
        self.assertTrue(summary["all_cases_complete"])
        self.assertFalse(summary["criterion_pass"])

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            safe_run("../../old_metadata")

    def test_valid_run_id(self):
        self.assertEqual(safe_run("live_metric3_20261106T092719_ef6875ea"),"live_metric3_20261106T092719_ef6875ea")

    def test_offline_protocol_rejected_by_live_viewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            run="live_metric3_20261106T092719_ef6875ea"
            folder=root/"reports"/run
            folder.mkdir(parents=True)
            (folder/"protocol.json").write_text(json.dumps({"source":"offline_replay"}))
            with self.assertRaises(ValueError):
                Reports(root).snapshot(run)


if __name__=="__main__":
    unittest.main()
