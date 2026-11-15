import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from report_bev_driving_sweep import region_statistics, representative, resolve_attempts
from run_bev_driving_sweep import aggregate


def evaluation(frame, hit, assessable=True):
    anchor = {"scenario":"S4", "anchor_index":1, "member_actor_ids":[25],
        "assessable":assessable, "ego":{"detected":False if assessable else None},
        "fused":{"detected":hit if assessable else None}, "hidden_part_recovered":False}
    return {"frame_id":frame, "anchors":[anchor], "anchor_total":1,
        "anchor_assessable":int(assessable), "anchor_ego_detected":0,
        "anchor_fused_detected":int(hit and assessable),
        "ego_road_state":{"on_driving_lane":True}}


class DrivingReportTests(unittest.TestCase):
    def test_infrastructure_retry_keeps_first_failure(self):
        first = {"scenario":"S8", "returncode":1, "rounds":0, "fatal_error":"timeout"}
        retry = {"scenario":"S8", "returncode":0, "rounds":5}
        result = resolve_attempts([first], [retry])[0]
        self.assertEqual(result["initial_failed_attempt"],first)
        self.assertEqual(result["rounds"],5)
        failed_retry = dict(first, fatal_error="missing target")
        chain = resolve_attempts([first], [failed_retry,retry])[0]
        self.assertEqual(chain["initial_failed_attempt"]["fatal_error"],"missing target")
        self.assertEqual(chain["initial_failed_attempt"]["initial_failed_attempt"],first)

    def test_retry_cannot_replace_detection_failure(self):
        first = {"scenario":"S1", "returncode":1, "rounds":5}
        with self.assertRaises(ValueError):
            resolve_attempts([first], [{"scenario":"S1", "returncode":0, "rounds":5}])
        with self.assertRaises(ValueError):
            resolve_attempts([first,first], [])

    def test_first_assessable_not_best_frame(self):
        items = [(Path("a"), evaluation(1,False,False)),
                 (Path("b"), evaluation(2,False)), (Path("c"), evaluation(3,True))]
        folder, selected, _ = representative(items)
        self.assertEqual(folder, Path("b"))
        self.assertEqual(selected["frame_id"],2)
        self.assertFalse(selected["anchors"][0]["fused"]["detected"])

    def test_unassessable_frames_are_not_passes_or_misses(self):
        items = [(None,evaluation(1,False,False)),(None,evaluation(2,False)),(None,evaluation(3,True))]
        stats = region_statistics(items)[0]
        self.assertEqual(stats["assessable_frames"],2)
        self.assertEqual(stats["fused_detected_frames"],1)

    def test_actor_replacement_rejected(self):
        a,b = evaluation(1,True),evaluation(2,True)
        b["anchors"][0]["member_actor_ids"] = [99]
        with self.assertRaises(ValueError):
            region_statistics([(None,a),(None,b)])

    def test_ever_detected_and_frame_stability_are_separate(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            (root / "summary.json").write_text(json.dumps({"mean_ms":10,"max_ms":20,
                "ego_driving":{"distance_m":10},"fatal_error":None}))
            for frame,hit in enumerate((False,True,False)):
                folder = root / f"round_{frame:03d}"; folder.mkdir()
                (folder / "evaluation.json").write_text(json.dumps(evaluation(frame,hit)))
            result = aggregate(root)
            self.assertEqual(result["anchors_fused_detected"],1)
            self.assertEqual(result["region_frames_fused_detected"],1)
            self.assertEqual(result["region_frames_assessable"],3)
            self.assertTrue(result["captures_on_driving_lane"])


if __name__=="__main__":
    unittest.main()
