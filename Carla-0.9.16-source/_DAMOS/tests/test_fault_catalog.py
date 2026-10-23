"""Synthetic catalog/report checks; no CARLA scene or new fault trial execution."""
from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fault_catalog import FAULTS, PIPELINE_CASE_NAMES, REPORT_FAULTS, catalog_counts, pipeline_cases, resolve_fault


class CatalogTests(unittest.TestCase):
    def test_counts_and_unique_ids(self):
        self.assertEqual(catalog_counts(), {"total": 15, "single": 13, "compound": 2,
                         "pipeline_cases": 15, "capture_only": 0, "report_faults": 8})
        self.assertEqual([f.fault_id for f in FAULTS], [f"F{i:02d}" for i in range(1, 16)])
        self.assertEqual(len({f.key for f in FAULTS}), 15)

    def test_aliases_are_unambiguous(self):
        owners = {}
        for fault in FAULTS:
            for name in (fault.fault_id, fault.key, fault.report_key, *fault.aliases):
                if name is None:
                    continue
                self.assertEqual(owners.setdefault(name, fault.fault_id), fault.fault_id)
                self.assertEqual(resolve_fault(name), fault)

    def test_old_output_names_are_one_fault(self):
        for name in ("module_stop", "stale_perception_output", "output_freeze"):
            self.assertEqual(resolve_fault(name).fault_id, "F09")

    def test_partial_and_complete_lidar_loss_remain_distinct(self):
        self.assertEqual(resolve_fault("lidar_dropout").fault_id, "F05")
        self.assertEqual(resolve_fault("lidar_missing").fault_id, "F07")
        self.assertIsNotNone(resolve_fault("lidar_dropout").expected_state)
        self.assertIsNotNone(resolve_fault("lidar_missing").expected_state)

    def test_unknown_and_empty_names_not_silently_resolved(self):
        for name in (None, "", "missing", "F99"):
            with self.assertRaises(KeyError):
                resolve_fault(name)

    def test_historical_pipeline_conditions_unchanged(self):
        expected = {
            "front_blackout": ({"ego": {"input": "front_blackout"}}, "DEGRADED_COOPERATIVE"),
            "lidar_missing": ({"ego": {"input": "lidar_missing"}}, "DEGRADED_COOPERATIVE"),
            "sensor_delay": ({"ego": {"input": "sensor_delay"}}, "DEGRADED_COOPERATIVE"),
            "output_freeze": ({"ego": {"input": "output_freeze"}}, "DEGRADED_COOPERATIVE"),
            "link_drop": ({"observer": {"wire": "drop"}}, "SAFE_STOP"),
            "packet_tamper": ({"observer": {"wire": "tamper"}}, "SAFE_STOP"),
            "packet_replay": ({"observer": {"wire": "replay"}}, "SAFE_STOP"),
            "invalid_proof": ({"observer": {"wire": "bad_proof"}}, "SAFE_STOP"),
            "rgb_lidar_failure": ({"ego": {"input": "rgb_lidar_failure"}}, "DEGRADED_COOPERATIVE"),
            "compound_link_sensor": ({"ego": {"input": "rgb_lidar_failure"},
                                       "observer": {"wire": "drop"}}, "SAFE_STOP"),
        }
        from carla_resilience_trial import CASES
        from run_carla_resilience import CASE_NAMES
        self.assertEqual({key: pipeline_cases()[key] for key in expected}, expected)
        self.assertEqual(CASES, pipeline_cases())
        self.assertEqual(CASE_NAMES, list(CASES))
        self.assertEqual(PIPELINE_CASE_NAMES, tuple(CASES))
        self.assertEqual(len(CASES), 15)

    def test_returned_case_mutation_does_not_change_catalog(self):
        first = pipeline_cases()
        first["front_blackout"][0]["ego"]["input"] = "bad"
        self.assertEqual(pipeline_cases()["front_blackout"][0]["ego"]["input"], "front_blackout")

    def test_all_report_types_have_a_pipeline_case(self):
        report_ids = {f.fault_id for f in REPORT_FAULTS}
        pipeline_ids = {f.fault_id for f in FAULTS if f.expected_state is not None}
        self.assertEqual(report_ids & pipeline_ids, report_ids)
        self.assertEqual(len(report_ids | pipeline_ids), 15)


class ReportTests(unittest.TestCase):
    def test_temporal_report_no_longer_generates_duplicate_stale_output(self):
        import scenic_custom_walker_injector as injector
        frames = [(i, np.full((96, 128, 3), i + 1, np.uint8)) for i in range(9)]
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(injector, "find_observer_camera_sensor", return_value=object()), \
                mock.patch.object(injector, "capture_rgb_sensor_sequence", return_value=frames), \
                mock.patch.object(injector, "save_rgb_array"), \
                mock.patch.object(injector, "save_rgb_panel") as panels:
            records = injector.save_ego_camera_temporal_fault_report(
                object(), object(), [], Path(directory), injector.ScenicCustomWalkerConfig())
            types = [record["fault_type"] for record in records]
            self.assertEqual(types.count("output_freeze"), 1)
            self.assertNotIn("module_stop", types)
            self.assertNotIn("stale_perception_output", types)
            self.assertEqual(len(types), 6)  # Normal reference plus five RGB/temporal faults.
            self.assertEqual(panels.call_count, 2)
            self.assertFalse((Path(directory) / "fault_09_stale_perception_output.png").exists())

    def test_grid_has_eight_faults_and_a_normal_reference_even_for_old_records(self):
        import scenic_custom_walker_injector as injector
        records = [{"fault_type": f.report_key, "status": "saved"} for f in REPORT_FAULTS
                   if f.fault_id != "F09"]
        records += [{"fault_type": "module_stop", "status": "saved", "marker": "first"},
                    {"fault_type": "stale_perception_output", "status": "saved"},
                    {"fault_type": "ego_front_camera_current", "status": "saved"}]
        with mock.patch.object(injector, "make_fault_grid_tile", return_value=np.zeros((2, 2, 3), np.uint8)) as tile, \
                mock.patch.object(injector, "save_rgb_array") as save:
            injector.save_ego_fault_3x3_grid(Path("unused.png"), records)
            self.assertEqual(tile.call_count, 9)
            self.assertIn("not a fault", tile.call_args_list[0].args[1])
            frozen = [call for call in tile.call_args_list if call.args[1].startswith("F09.")]
            self.assertEqual(len(frozen), 1)
            self.assertEqual(frozen[0].args[0]["marker"], "first")
            self.assertEqual(save.call_args.args[1].shape, (6, 6, 3))

    def test_manifest_counts_unique_captured_faults_not_normal_or_grid(self):
        import scenic_custom_walker_injector as injector
        with tempfile.TemporaryDirectory() as directory:
            records = [{"fault_type": f.report_key, "status": "saved"} for f in REPORT_FAULTS]
            records.append({"fault_type": "ego_front_camera_current", "status": "saved"})
            config = injector.ScenicCustomWalkerConfig(report_dir=Path(directory))
            with mock.patch.object(injector, "safe_map_name", return_value="Town10HD_Opt"), \
                    mock.patch.object(injector, "wait_for_capture_settle"), \
                    mock.patch.object(injector, "save_ego_lidar_noise_report", return_value=records), \
                    mock.patch.object(injector, "save_ego_camera_temporal_fault_report", return_value=[]), \
                    mock.patch.object(injector, "save_ego_fault_3x3_grid", return_value=True), \
                    mock.patch.object(injector, "save_contact_sheet", return_value=False):
                injector.save_ego_fault_report(object(), SimpleNamespace(id=7, type_id="vehicle.test"), [], config)
            manifest = json.loads(next(Path(directory).glob("*/ego_fault_report.json")).read_text())
            self.assertEqual(manifest["captured_unique_faults"], 8)
            self.assertEqual(manifest["fault_catalog_counts"]["total"], 15)
            self.assertFalse(manifest["normal_reference_is_fault"])
            actual = [r for r in manifest["records"] if "canonical_fault_id" in r]
            self.assertEqual(len({r["canonical_fault_id"] for r in actual}), 8)
            self.assertTrue(all(r["implementation"] == "capture_illustration" for r in actual))


if __name__ == "__main__":
    unittest.main()
