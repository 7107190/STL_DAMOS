import csv
import io
from pathlib import Path
import tempfile
import unittest

from evaluation_report import render_report, summary_csv


class AccuracyReport(unittest.TestCase):
    def state(self, count):
        cases = [{"evaluation_number": i, "scenario": "S1", "case_id": f"case-{i}",
                  "weather": "ClearNoon", "samples": 4, "status": "success",
                  "fused_longest_span_seconds": 0.6} for i in range(1, count+1)]
        return {"id": "test_run", "status": "complete", "planned": count, "evaluated": count,
                "successful": count, "detection_failures": 0, "execution_errors": 0,
                "success_rate_percent": 100, "mean_continuous_detection_seconds": 0.6,
                "all_cases_complete": True, "protocol": {}, "cases": cases}

    def test_full_100_rows_present(self):
        with tempfile.TemporaryDirectory() as folder:
            page = render_report(3, self.state(100), Path(folder), "records")
        self.assertEqual(page.count("<tr data-evaluation="), 100)
        self.assertIn("data-evaluation='100'", page)

    def test_three_case_aggregate_csv(self):
        data = list(csv.DictReader(io.StringIO(summary_csv(3, self.state(3)).decode("utf-8-sig"))))[0]
        self.assertEqual(data["evaluated"], "3")
        self.assertEqual(float(data["success_rate_percent"]), 100)
        self.assertEqual(data["run_id"], "test_run")


if __name__ == "__main__":
    unittest.main()
