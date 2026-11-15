from dataclasses import replace
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from bev_fusion import fuse_bevs
from bev_input_comparison import variant_task
from bev_sensor_rig import CAMERAS, REFERENCE_PREFIX, camera_calibration, latest_common_window


class InputComparisonTests(unittest.TestCase):
    def task(self):
        names = [*CAMERAS,*[REFERENCE_PREFIX+n for n in CAMERAS],"lidar"]
        return {"label":"ego","window":list(range(10,17)),
                "packets":{("ego",name):[dict(frame=f,name=name) for f in range(10,17)] for name in names},
                "sensor_matrices":{("ego",name):np.eye(4) for name in names}}

    def test_paired_end_frame_and_lidar_identity(self):
        task = self.task()
        slow = variant_task(task,"current",2)
        fast = variant_task(task,"reference",1)
        self.assertEqual(slow["window"],[10,12,14,16])
        self.assertEqual(fast["window"],[13,14,15,16])
        self.assertIs(slow["packets"]["ego","lidar"][-1],fast["packets"]["ego","lidar"][-1])
        self.assertEqual(fast["packets"]["ego","cam_front"][-1]["name"],"reference_cam_front")
        self.assertEqual(fast["camera_fovs"]["cam_back"],110)
        self.assertEqual(fast["camera_fovs"]["cam_front"],70)
        self.assertEqual(task["window"],list(range(10,17)))

    def test_invalid_condition_rejected(self):
        for profile,stride in [("missing",1),("current",3)]:
            with self.assertRaises(ValueError):
                variant_task(self.task(),profile,stride)

    def test_intrinsics_match_real_fov(self):
        for fov in (70,90,110):
            _,_,k = camera_calibration(np.eye(4),fov)
            self.assertAlmostEqual(float(k[0,0]),400/np.tan(np.deg2rad(fov/2)),places=4)

    def test_seven_common_frames_not_partial_window(self):
        buffers = {"a":{f:None for f in range(9,17)},"b":{f:None for f in range(8,16)}}
        self.assertEqual(latest_common_window(buffers,["a","b"],history_frames=7),list(range(9,16)))
        self.assertIsNone(latest_common_window(buffers,["a","b"],10,history_frames=7))
        for count in (0,13,1.5):
            with self.assertRaises(ValueError):
                latest_common_window(buffers,["a","b"],history_frames=count)

    def test_cli_and_simulation_step_are_isolated(self):
        import argparse
        from scenic_custom_walker_injector import add_integration_args, config_from_args, build_scenic_command, validate_config
        parser = argparse.ArgumentParser(); add_integration_args(parser)
        config = config_from_args(parser.parse_args(["--compare-bev-inputs","--realtime-factor","1"]))
        self.assertTrue(config.attach_bev_sensors)
        self.assertFalse(config.live_bev_fusion)
        command = build_scenic_command(config)
        self.assertEqual(command[command.index("timestep")+1],"0.05")
        normal = build_scenic_command(replace(config,compare_bev_inputs=False))
        self.assertEqual(normal[normal.index("timestep")+1],"0.1")
        for changes in ({"live_bev_fusion":True},{"evaluate_bev":True},{"bev_rounds":0},{"bev_rounds":11}):
            with self.assertRaises(ValueError):
                validate_config(replace(config,**changes))


class ExperimentalFusionTests(unittest.TestCase):
    def bev(self,cls,confidence=1):
        p = np.full((17,200,200),(1-confidence)/16,np.float16)
        p[cls] = confidence
        return {"probabilities":p,"valid":np.zeros((200,200),np.uint8),"pose":np.eye(4)}

    def test_no_erasure_and_peer_blind_fill(self):
        ego,peer = self.bev(7,.4),self.bev(13,.9)
        ego["valid"][100,100] = 1
        peer["valid"][100,100:102] = 1
        old = fuse_bevs(ego,[peer])
        new = fuse_bevs(ego,[peer],mode="ego_observed")
        self.assertEqual(old["probabilities"][:,100,100].argmax(),13)
        np.testing.assert_array_equal(new["probabilities"][:,100,100],ego["probabilities"][:,100,100])
        self.assertEqual(new["probabilities"][:,100,101].argmax(),13)
        self.assertEqual(new["selected_source"][100,100],0)
        self.assertEqual(new["strongest_source"][100,100],1)
        self.assertFalse(new["valid"][0,0])

    def test_preserves_false_positives_too_not_magic_filter(self):
        ego,peer = self.bev(8),self.bev(11)
        ego["valid"][10,10] = peer["valid"][10,10] = 1
        new = fuse_bevs(ego,[peer],mode="ego_observed")
        self.assertEqual(new["probabilities"][:,10,10].argmax(),8)

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            fuse_bevs(self.bev(8),[],mode="unknown")

    def test_ground_miss_cannot_be_corrected_by_conservative_mode(self):
        ego,peer = self.bev(11),self.bev(7)
        ego["valid"][100,100] = peer["valid"][100,100] = 1
        new = fuse_bevs(ego,[peer],mode="ego_observed")
        self.assertEqual(new["probabilities"][:,100,100].argmax(),11)


if __name__=="__main__":
    unittest.main()
