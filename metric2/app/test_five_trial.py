import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import server
from report_data import expected_count, is_full


class CumulativeFive(unittest.TestCase):
    def launch(self, mode):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "reports").mkdir()
        console = server.Console(root, offscreen=True)
        process = Mock(pid=123456)
        with patch.object(server.subprocess, "Popen", return_value=process) as start, \
                patch.object(server.threading.Thread, "start"):
            created = console.start(mode, "S1")
        start.assert_called_once()
        proto = json.loads((console.data.job(created["run"]) / "launch.json").read_text())["protocol"]
        return created, proto

    def test_five_uses_one_job_and_one_scene(self):
        created, proto = self.launch("five")
        self.assertEqual(proto["sessions"], 1)
        self.assertEqual(proto["repeats_per_input_per_session"], 5)
        self.assertEqual(proto["scenarios"], ["S1"])
        self.assertEqual(expected_count(proto), 5)
        self.assertFalse(is_full(proto))
        self.assertTrue(created["run"].startswith("live_metric2_"))

    def test_full_135_unchanged(self):
        _, proto = self.launch("full")
        self.assertEqual(expected_count(proto), 135)
        self.assertTrue(is_full(proto))

    def test_three_uses_one_job_and_one_scene(self):
        _, proto = self.launch("three")
        self.assertEqual(proto["sessions"], 1)
        self.assertEqual(proto["scenarios"], ["S1"])
        self.assertEqual(expected_count(proto), 3)
        self.assertFalse(is_full(proto))

    def test_single_unchanged(self):
        _, proto = self.launch("single")
        self.assertEqual(expected_count(proto), 1)

    def test_unknown_mode_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reports").mkdir()
            with self.assertRaises(ValueError):
                server.Console(root).start("unknown", "S1")


if __name__ == "__main__":
    unittest.main()
