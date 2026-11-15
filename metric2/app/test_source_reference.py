import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from source_reference import catalog, recorded_digest, reference, snippet


class SourceReference(unittest.TestCase):
    def test_function_extraction_uses_real_line_numbers(self):
        text = "# comment\nclass Mesh:\n    def exchange(self):\n        start = 1\n        return start\n"
        self.assertEqual(snippet(text, {"function": "Mesh.exchange"}), (3, 5, "    def exchange(self):\n        start = 1\n        return start"))

    def test_nested_function_extraction(self):
        text = "def install():\n    def run():\n        span = 10\n        return span\n"
        self.assertEqual(snippet(text, {"function": "install.run", "start": "span =", "end": "return span"})[:2], (3, 4))

    def test_duplicate_markers_rejected(self):
        with self.assertRaises(ValueError):
            snippet("def f():\n    start = 1\n    start = 2\n", {"function": "f", "start": "start ="})

    def test_missing_function_rejected(self):
        with self.assertRaises(ValueError):
            snippet("def a():\n    pass\n", {"function": "b"})

    def test_arbitrary_file_selection_rejected(self):
        with self.assertRaises(ValueError):
            reference(Path("/tmp"), 2, section="../../etc/passwd")

    def test_unknown_metric_rejected(self):
        with self.assertRaises(ValueError):
            catalog(Path("/tmp"), 1)

    def test_trial_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            recorded_digest(Path("/tmp"), 2, "../../secret", Path("/tmp/source.py"))

    def test_current_file_compared_to_selected_trial_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / "app/report_data.py"
            file.parent.mkdir()
            file.write_text("def is_full(protocol):\n    return True\n")
            run = "live_metric2_20261106T082516_7483d2a9"
            report = root / "reports" / run
            report.mkdir(parents=True)
            frozen = hashlib.sha256(file.read_bytes()).hexdigest()
            (report / "environment.json").write_text(json.dumps({"measured_code_sha256": {str(file): frozen}}))
            result = reference(root, 2, run, "scope")
            self.assertTrue(result["matches_selected_run"])
            self.assertTrue(result["read_only"])
            file.write_text("def is_full(protocol):\n    return False\n")
            self.assertFalse(reference(root, 2, run, "scope")["matches_selected_run"])

    def test_missing_record_is_not_reported_as_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(recorded_digest(Path(tmp), 3, "live_metric3_20261106T102959_306619cf", Path(tmp) / "source.py"))

    def test_source_text_preserves_markup_without_executing_it(self):
        text = "def f():\n    return '<script>not executable</script>'\n"
        self.assertIn("<script>", snippet(text, {"function": "f"})[2])
