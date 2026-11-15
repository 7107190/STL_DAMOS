import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import launch_local


class LocalLaunch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def ready(self):
        (self.root / "runtime_ready.json").write_text('{"verified": true}')

    def test_unverified_runtime_cannot_start(self):
        with patch("sys.argv", ["launcher", "--root", str(self.root), "--full"]), \
                patch.object(launch_local.subprocess, "Popen") as start:
            with self.assertRaises(SystemExit):
                launch_local.main()
            start.assert_not_called()

    def test_full_starts_new_trial_before_opening_exact_run(self):
        self.ready()
        calls = []

        def get(request, timeout):
            if isinstance(request, str):
                calls.append("config")
                return io.BytesIO(b'{"token":"csrf"}')
            calls.append("start")
            self.assertEqual(json.loads(request.data)["mode"], "full")
            return io.BytesIO(b'{"run":"new-live-trial"}')

        def browser(command, check):
            calls.append("browser")
            self.assertEqual(command, ["xdg-open", "http://127.0.0.1:8762/?run=new-live-trial"])

        with patch("sys.argv", ["launcher", "--root", str(self.root), "--full"]), \
                patch.object(launch_local, "healthy", return_value=True), \
                patch.object(launch_local, "urlopen", side_effect=get), \
                patch.object(launch_local.subprocess, "run", side_effect=browser), \
                patch.object(launch_local.subprocess, "Popen") as start:
            self.assertEqual(launch_local.main(), 0)
            start.assert_not_called()
        self.assertEqual(calls, ["config", "start", "browser"])

    def test_browser_failure_cleans_only_server_started_here(self):
        self.ready()
        process = Mock()
        process.poll.return_value = None
        with patch("sys.argv", ["launcher", "--root", str(self.root)]), \
                patch.object(launch_local, "healthy", side_effect=[False, True]), \
                patch.object(launch_local.subprocess, "Popen", return_value=process), \
                patch.object(launch_local.subprocess, "run", side_effect=OSError("browser unavailable")):
            with self.assertRaises(OSError):
                launch_local.main()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=90)

    def test_control_c_cleans_owned_server(self):
        self.ready()
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [KeyboardInterrupt(), 0]
        with patch("sys.argv", ["launcher", "--root", str(self.root)]), \
                patch.object(launch_local, "healthy", side_effect=[False, True]), \
                patch.object(launch_local.subprocess, "Popen", return_value=process), \
                patch.object(launch_local.subprocess, "run"):
            self.assertEqual(launch_local.main(), 130)
        process.terminate.assert_called_once()
        self.assertEqual(process.wait.call_args.kwargs, {"timeout": 90})


if __name__ == "__main__":
    unittest.main()
