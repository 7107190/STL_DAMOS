import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import m3_launch


class DesktopLaunch(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()

    def ready(self):
        (self.root/"runtime_ready.json").write_text('{"verified":true}')

    def test_missing_ready_marker_blocks_execution(self):
        with patch("sys.argv",["launcher","--root",str(self.root),"--full"]),patch.object(m3_launch.subprocess,"Popen") as start:
            with self.assertRaises(SystemExit):
                m3_launch.main()
            start.assert_not_called()

    def test_full_new_trial_precedes_opening_exact_result(self):
        self.ready()
        calls=[]
        def get(request,timeout):
            if isinstance(request,str):
                calls.append("config")
                return io.BytesIO(b'{"token":"csrf"}')
            calls.append("start")
            self.assertEqual(json.loads(request.data)["mode"],"full")
            self.assertEqual(request.full_url,m3_launch.URL+"/api/start")
            return io.BytesIO(b'{"run":"new-current-cases"}')
        def browser(command,check):
            calls.append("browser")
            self.assertEqual(command,["xdg-open",m3_launch.URL+"/?run=new-current-cases"])
        with patch("sys.argv",["launcher","--root",str(self.root),"--full"]),patch.object(m3_launch,"healthy",return_value=True),patch.object(m3_launch,"urlopen",side_effect=get),patch.object(m3_launch.subprocess,"run",side_effect=browser):
            self.assertEqual(m3_launch.main(),0)
        self.assertEqual(calls,["config","start","browser"])

    def test_camera_browser_failure_stops_only_owned_server(self):
        self.ready()
        process=Mock()
        process.poll.return_value=None
        with patch("sys.argv",["launcher","--root",str(self.root)]),patch.object(m3_launch,"healthy",side_effect=[False,True]),patch.object(m3_launch.subprocess,"Popen",return_value=process),patch.object(m3_launch.subprocess,"run",side_effect=OSError("No browser")):
            with self.assertRaises(OSError):
                m3_launch.main()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=150)

    def test_control_c_cleans_owned_server(self):
        self.ready()
        process=Mock()
        process.poll.return_value=None
        process.wait.side_effect=[KeyboardInterrupt(),0]
        with patch("sys.argv",["launcher","--root",str(self.root)]),patch.object(m3_launch,"healthy",side_effect=[False,True]),patch.object(m3_launch.subprocess,"Popen",return_value=process),patch.object(m3_launch.subprocess,"run"):
            self.assertEqual(m3_launch.main(),130)
        process.terminate.assert_called_once()
        self.assertEqual(process.wait.call_args.kwargs,{"timeout":150})


if __name__=="__main__":
    unittest.main()
