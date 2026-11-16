import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from evaluation_report import aggregate, render_report, summary_csv


class EvaluationReport(unittest.TestCase):
    def state(self, count=3):
        rows = [{"evaluation_number": i, "scenario": "S1", "session": 1, "repeat": i,
                 "start_kst": "2026-11-11 18:00:00.000", "end_kst": "2026-11-11 18:00:01.000",
                 "elapsed_ms": 1000, "status": "passed", "audit_pass": True} for i in range(1, count+1)]
        return {"id": "test_run", "status": "complete", "expected": count, "processed": count,
                "normal": count, "mean_seconds": 1, "max_seconds": 1, "rows": rows,
                "protocol": {}, "full_scope": count == 135, "audited": count}

    def test_all_135_rows_no_truncation(self):
        with tempfile.TemporaryDirectory() as folder:
            page = render_report(2, self.state(135), Path(folder), "records")
        self.assertEqual(page.count("<tr data-evaluation="), 135)
        self.assertIn("data-evaluation='135'", page)

    def test_summary_csv_matches_same_run(self):
        s = self.state()
        row = list(csv.DictReader(io.StringIO(summary_csv(2, s).decode("utf-8-sig"))))[0]
        self.assertEqual(row["run_id"], s["id"])
        self.assertEqual(row["planned"], "3")
        self.assertEqual(float(row["mean_processing_seconds"]), 1)

    def test_escapes_record_text(self):
        s = self.state()
        s["rows"][0]["scenario"] = "<script>bad</script>"
        with tempfile.TemporaryDirectory() as folder:
            page = render_report(2, s, Path(folder), "records")
        self.assertNotIn("<script>bad", page)
        self.assertIn("&lt;script&gt;", page)

    def test_slow_record_counted(self):
        s = self.state()
        s["rows"][1]["elapsed_ms"] = 18001
        self.assertEqual(aggregate(2, s)["over_18_seconds"], 1)

    def test_unknown_view_rejected(self):
        with self.assertRaises(ValueError):
            render_report(2, self.state(), Path("/tmp"), "unknown")

    def test_start_time_uses_actual_job_record(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder) / ".metric2_ui_jobs/test_run"
            job.mkdir(parents=True)
            (job / "state.json").write_text(json.dumps({"started_utc": "2026-11-11T09:50:00+00:00"}))
            page = render_report(2, self.state(), Path(folder), "summary")
        self.assertIn("2026-11-11T18:50:00.000+09:00", page)


if __name__ == "__main__":
    unittest.main()
