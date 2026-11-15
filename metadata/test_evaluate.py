import csv
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("metadata_evaluate", Path(__file__).with_name("evaluate.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class EvaluationNumbers(unittest.TestCase):
    def timing_input(self, root):
        rows = [{"session": str(s), "scenario": f"S{c}", "repeat": str(r), "started_ns": "1000000000",
                 "ended_ns": str(2000000000 + r), "elapsed_seconds": (1000000000+r)/1e9}
                for s in range(1, 4) for c in range(1, 10) for r in range(1, 6)]
        folder = root / "analysis"
        folder.mkdir()
        app.write_csv(folder / "measurements.csv", rows)
        return rows

    def test_135_numbers_do_not_reset_at_scenario_or_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.timing_input(root)
            rows = app.timing_records(root)
            self.assertEqual([r["evaluation_number"] for r in rows], list(range(1, 136)))
            self.assertEqual((rows[5]["evaluation_number"], rows[5]["scenario"]), (6, "S2"))
            self.assertEqual((rows[45]["evaluation_number"], rows[45]["session"]), (46, "2"))
            self.assertLess(rows[0]["elapsed_seconds"], rows[4]["elapsed_seconds"])

    def test_duplicate_measurement_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = self.timing_input(root)
            rows[-1] = rows[0]
            app.write_csv(root / "analysis/measurements.csv", rows)
            with self.assertRaises(ValueError):
                app.timing_records(root)

    def test_numbered_csv_matches_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.timing_input(root)
            rows = app.timing_records(root)
            app.write_csv(root / "records.csv", rows)
            result = app.read_csv(root / "records.csv")
            self.assertEqual(list(result[0])[0], "evaluation_number")
            self.assertEqual([int(r["evaluation_number"]) for r in result], list(range(1, 136)))

    def test_timing_disagreement_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = self.timing_input(root)
            rows[0]["elapsed_seconds"] = 9
            app.write_csv(root / "analysis/measurements.csv", rows)
            with self.assertRaises(ValueError):
                app.timing_records(root)

    def anchor_input(self, root):
        for number in range(1, 101):
            folder = root / "results" / f"M3-{number:03d}-S1"
            folder.mkdir(parents=True)
            times = [1000, 3000] if number == 1 else [1000]
            (folder / "result.json").write_text(json.dumps({"case_id": folder.name, "scenario": "S1", "samples": len(times)}))
            for i, ms in enumerate(times, 1):
                part = folder / "recording" / f"round_{i:03d}"
                part.mkdir(parents=True)
                (part / "peers.json").write_text(json.dumps({"ego": {"elapsed_ms": ms}}))

    def test_100_anchor_numbers_and_explicit_sample_weighted_mean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.anchor_input(root)
            rows, samples = app.anchor_records(root)
            self.assertEqual([r["evaluation_number"] for r in rows], list(range(1, 101)))
            self.assertEqual(rows[0]["mean_processing_seconds"], 2)
            overall = math.fsum(s["ego_processing_seconds"] for s in samples)/len(samples)
            self.assertAlmostEqual(overall, 103/101)
            self.assertNotEqual(overall, math.fsum(r["mean_processing_seconds"] for r in rows)/100)

    def test_anchor_missing_processing_point_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.anchor_input(root)
            next((root / "results").glob("*/recording/round_001/peers.json")).unlink()
            with self.assertRaises(ValueError):
                app.anchor_records(root)

    def test_source_copies_and_hashes_follow_actual_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / "metric2"
            common.mkdir()
            (common / "start_metric2.sh").write_text("#!/bin/bash\ntrue\n")
            pkg = root / "metadata/inputs/metric2"
            (pkg / "tools").mkdir(parents=True)
            verifier = pkg / "tools/verify_submission.py"
            verifier.write_text("print('test')\n")
            out = root / "output"
            out.mkdir()
            saved = app.ROOT
            try:
                app.ROOT = root / "metadata"
                execution = {"source_files": app.capture_sources(2, pkg, out)}
                app.check_sources(execution)
                for row in execution["source_files"]:
                    self.assertEqual((out / row["saved_copy"]).read_bytes(), Path(row["file"]).read_bytes())
                verifier.write_text("print('changed')\n")
                with self.assertRaises(ValueError):
                    app.check_sources(execution)
            finally:
                app.ROOT = saved

    def test_first_page_has_no_completed_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            app.start_page({"metric": 2, "run_id": "test", "ready_at": "2026-11-11T12:00:00+09:00", "input_folder": "/input", "source_files": []}, out)
            text = (out / "start.html").read_text()
            self.assertIn("재계산 전", text)
            self.assertNotIn("기준 충족", text)


if __name__ == "__main__":
    unittest.main()
