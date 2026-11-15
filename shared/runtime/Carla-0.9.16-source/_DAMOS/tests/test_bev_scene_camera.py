import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from bev_scene_camera import OverheadCamera, save_overhead


class SceneCameraTests(unittest.TestCase):
    def packet(self):
        return {"frame_id":10,"capture_sim_ns":1000000000,"width":4,"height":4,
                "raw":np.full((4,4,4),128,np.uint8).tobytes(),"fov":90.,"transform":np.eye(4)}

    def test_capture_binds_frame_transform_intrinsic_and_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            result=save_overhead(folder,self.packet(),10,1000000000)
            self.assertFalse(result["fed_to_model_or_fusion"])
            self.assertEqual(result["sha256"],hashlib.sha256((folder/"overhead_rgb.png").read_bytes()).hexdigest())
            self.assertAlmostEqual(result["intrinsic"][0][0],2)
            self.assertEqual(json.loads((folder/"overhead_capture.json").read_text())["frame_id"],10)

    def test_never_substitute_later_frame_or_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            for frame,stamp in ((11,1000000000),(10,1000010000)):
                with self.assertRaises(ValueError):
                    save_overhead(Path(temporary),self.packet(),frame,stamp)
            self.assertEqual(list(Path(temporary).iterdir()),[])

    def test_capture_cannot_overwrite_existing_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            save_overhead(folder,self.packet(),10,1000000000)
            with self.assertRaises(FileExistsError):
                save_overhead(folder,self.packet(),10,1000000000)

    def test_freeze_requires_exact_frame_not_latest(self):
        camera=OverheadCamera.__new__(OverheadCamera)
        camera.lock=threading.Lock()
        camera.frames={10:self.packet()}
        self.assertEqual(camera.freeze(10)["frame_id"],10)
        with self.assertRaises(TimeoutError):
            camera.freeze(9,timeout_s=.01)

    def test_cli_flag_requires_live_evaluation(self):
        from scenic_custom_walker_injector import ScenicCustomWalkerConfig, validate_config
        with self.assertRaises(ValueError):
            validate_config(ScenicCustomWalkerConfig(bev_scene_cameras=True))
        validate_config(ScenicCustomWalkerConfig(bev_scene_cameras=True,evaluate_bev=True,
                        live_bev_fusion=True,attach_bev_sensors=True,realtime_factor=1.))


if __name__=="__main__":
    unittest.main()
