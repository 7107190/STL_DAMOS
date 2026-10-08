from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from bev_evaluation import (SEMANTIC_DTYPE,decode_semantic,evaluate_round,footprint_mask,
                            points_mask,render_camera_projection,score_prediction,target_class,target_roi)


class EvaluationTests(unittest.TestCase):
    def test_snapshot_roi_without_bbox(self):
        empty = np.zeros((200,200),bool)
        inside,box,basis = target_roi({"location_model_world":[39.9,0,0]},np.eye(4),empty)
        self.assertTrue(inside)
        self.assertIsNone(box)
        self.assertIn("snapshot centre",basis)
        self.assertFalse(target_roi({"location_model_world":[40.,0,0]},np.eye(4),empty)[0])
        empty[199,100] = True
        self.assertTrue(target_roi({"location_model_world":[40.,0,0]},np.eye(4),empty)[0])

    def test_location_only_truth_scores_exact_returns(self):
        probabilities = np.zeros((17,200,200),np.float16); probabilities[8] = 1
        prediction = {"probabilities":probabilities,"valid":np.ones((200,200),np.uint8),"pose":np.eye(4)}
        evidence = {"ego":(np.array([[.2,.2,0],[.6,.2,0],[1.,.2,0]]),np.full(3,42),np.full(3,21))}
        truth = {"frame_id":100,"capture_sim_ns":10000000000,"targets":[
            {"actor_id":42,"type_id":"static.prop.container","expected_class":8,
             "expected_name":"object","scenario":"S4","anchor_index":1,
             "location_model_world":[.6,.2,0]}]}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            np.savez_compressed(folder/"ego_own.npz",**prediction)
            np.savez_compressed(folder/"ego_fused.npz",**prediction)
            report = evaluate_round(folder,truth,{}, {"ego":"ego"},100,decoded_evidence=evidence)
            self.assertEqual(report["anchor_fused_detected"],1)
            self.assertEqual(report["objects"][0]["fused"]["gt_return_cells"],3)
            self.assertIsNone(report["objects"][0]["bbox_cells"])

    def test_semantic_actor_identity_and_basis(self):
        points = np.zeros(2,SEMANTIC_DTYPE)
        points["x"] = [10,12]; points["y"] = [2,-3]
        points["actor_id"] = [42,99]; points["tag"] = [20,12]
        matrix = np.eye(4); matrix[0,3] = 4
        xyz,ids,tags = decode_semantic({"raw":points.tobytes(),"transform":matrix})
        np.testing.assert_allclose(xyz,[[14,-2,0],[16,3,0]])
        np.testing.assert_array_equal(ids,[42,99])
        np.testing.assert_array_equal(tags,[20,12])

    def test_malformed_semantic(self):
        with self.assertRaises(ValueError):
            decode_semantic({"raw":b"short","transform":np.eye(4)})

    def test_points_mask_no_range_clipping(self):
        p = np.array([[10,-2,0],[40,0,0],[0,0,6.]])
        mask = points_mask(p,np.eye(4))
        self.assertEqual(mask.sum(),1)
        self.assertTrue(mask[125,95])

    def test_pose_alignment(self):
        pose = np.eye(4); pose[0,3] = 4
        self.assertTrue(points_mask(np.array([[14,-2,0]]),pose)[125,95])

    def test_oriented_box_centres(self):
        vertices = np.array([[x,y,z] for x in [1.6,2.4] for y in [-.4,.4] for z in [0,1]])
        mask = footprint_mask(vertices,np.eye(4))
        self.assertEqual(mask.sum(),4)
        self.assertTrue(mask[104,99]); self.assertTrue(mask[105,100])
        pose = np.eye(4); pose[0,3] = 200
        self.assertFalse(footprint_mask(vertices,pose).any())

    def test_no_evidence_is_not_detection(self):
        p = np.zeros((17,200,200),np.float16); p[8] = 1
        prediction = {"probabilities":p,"valid":np.zeros((200,200),np.uint8)}
        mask = np.zeros((200,200),bool); mask[100,100:105] = True
        score = score_prediction(prediction,mask,8)
        self.assertFalse(score["diagnostic_match"])
        self.assertEqual(score["dominant_prediction"],"unknown")
        score = score_prediction(prediction,np.zeros_like(mask),8)
        self.assertIsNone(score["correct_fraction"])

    def test_expected_class_and_location(self):
        p = np.zeros((17,200,200),np.float16); p[11] = 1
        p[11,100,100:104] = 0; p[8,100,100:104] = 1
        prediction = {"probabilities":p,"valid":np.ones((200,200),np.uint8)}
        mask = np.zeros((200,200),bool); mask[100,100:104] = True
        self.assertTrue(score_prediction(prediction,mask,8)["diagnostic_match"])
        self.assertFalse(score_prediction(prediction,mask,7)["diagnostic_match"])
        other = np.roll(mask,10,axis=0)
        self.assertFalse(score_prediction(prediction,other,8)["diagnostic_match"])

    def test_one_cell_tolerance_does_not_change_strict_match(self):
        p = np.zeros((17,200,200),np.float16); p[11] = 1
        p[11,101,100:104] = 0; p[8,101,100:104] = 1
        prediction = {"probabilities":p,"valid":np.ones((200,200),np.uint8)}
        mask = np.zeros((200,200),bool); mask[100,100:104] = True
        score = score_prediction(prediction,mask,8)
        self.assertEqual(score["correct_cells"],0)
        self.assertEqual(score["correct_cells_with_0_4m_tolerance"],4)
        self.assertFalse(score["diagnostic_match"])
        prediction["valid"][:] = 0
        self.assertEqual(score_prediction(prediction,mask,8)["correct_cells_with_0_4m_tolerance"],0)

    def test_never_mix_ground_truth_frames(self):
        with self.assertRaises(ValueError):
            evaluate_round(Path("/nonexistent"),{"frame_id":10},{},{},11)

    def test_saved_round_scores_exact_actor_id_only(self):
        p = np.zeros((17,200,200),np.float16); p[8] = 1
        prediction = {"probabilities":p,"valid":np.ones((200,200),np.uint8),"pose":np.eye(4)}
        xyz = np.array([[.2,.2,0],[.6,.2,0],[1.,.2,0],[1.4,.2,0],[8.,8.,0]])
        evidence = {"ego":(xyz,np.array([42,42,42,42,999]),np.full(5,21))}
        vertices = [[x,y,z] for x in [0,1.6] for y in [0,.4] for z in [0,1]]
        truth = {"frame_id":100,"capture_sim_ns":10000000000,"targets":[
            {"actor_id":42,"type_id":"static.prop.container","expected_class":8,
             "expected_name":"object","scenario":"S4","anchor_index":1,
             "vertices_model_world":vertices}]}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            np.savez_compressed(folder/"ego_own.npz",**prediction)
            np.savez_compressed(folder/"ego_fused.npz",**prediction)
            report = evaluate_round(folder,truth,{}, {"ego":"ego"},100,decoded_evidence=evidence)
            target = report["objects"][0]
            self.assertEqual(target["ego_lidar_points"],4)
            self.assertEqual(target["fused"]["gt_return_cells"],4)
            self.assertEqual(report["fused_diagnostic_matches"],1)
            self.assertEqual(report["peer_only_candidates"],0)
            self.assertFalse(report["gt_fed_to_model_or_fusion"])
            self.assertIsNone(report["metric2_pass"])
            self.assertTrue((folder/"evaluation_crops.png").exists())

    def test_projection_rejects_other_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            np.savez_compressed(folder/"ego_model_inputs.npz",frame_ids=np.array([99]))
            with self.assertRaises(ValueError):
                render_camera_projection(folder,{"objects":[],"frame_id":100},{"ego":{}},{})

    def test_occlusion_requires_rgb_invisibility_peer_visibility_and_ego_fov(self):
        from bev_sensor_rig import CAMERAS
        p = np.zeros((17,200,200),np.float16); p[7] = 1
        prediction = {"probabilities":p,"valid":np.ones((200,200),np.uint8),"pose":np.eye(4)}
        empty = dict(prediction,valid=np.zeros((200,200),np.uint8))
        xyz = np.array([[.2,.2,0],[.6,.2,0],[1.,.2,0],[1.4,.2,0]])
        evidence = {"ego":(np.empty((0,3)),np.array([],int),np.array([],int)),
                    "peer":(xyz,np.full(4,42),np.full(4,21))}
        vertices = [[x,y,z] for x in [0,1.6] for y in [0,.4] for z in [0,1]]
        truth = {"frame_id":100,"capture_sim_ns":10000000000,"targets":[
            {"actor_id":42,"type_id":"static.prop.container","expected_class":8,
             "expected_name":"object","scenario":"S4","anchor_index":1,
             "vertices_model_world":vertices}]}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            np.savez_compressed(folder/"ego_own.npz",**empty)
            np.savez_compressed(folder/"ego_received_peer.npz",**prediction)
            np.savez_compressed(folder/"ego_fused.npz",**prediction)
            for ego_pixels,peer_pixels,projection,expected in ((0,4,None,False),(4,4,4,False),
                                                               (0,0,4,False),(0,4,0,False),(0,4,4,True)):
                visibility = {node:{name:{"ids":np.full((2,2),42 if pixels else 0,np.uint16),
                                          "tags":np.full((2,2),21,np.uint8)} for name in CAMERAS}
                              for node,pixels in (("ego",ego_pixels),("peer",peer_pixels))}
                with patch("bev_evaluation.camera_projection_counts",return_value=
                           None if projection is None else {n:projection for n in CAMERAS}), \
                     patch("bev_evaluation.render_evaluation"),patch("bev_evaluation.render_presence_evaluation"), \
                     patch("bev_evaluation.render_visibility_evaluation"):
                    report = evaluate_round(folder,truth,{}, {"ego":"ego","peer":"peer"},100,
                                            decoded_evidence=evidence,visibility_evidence=visibility)
                self.assertEqual(report["occluded_objects"],int(expected))
                self.assertEqual(report["occluded_recovered"],int(expected))
                self.assertEqual(report["fused_presence_matches"],1)
                self.assertEqual(report["fused_diagnostic_matches"],0)

    def test_project_categories(self):
        self.assertEqual(target_class("static.prop.container"),8)
        self.assertEqual(target_class("static.prop.streetbarrier"),1)
        self.assertEqual(target_class("vehicle.diamondback.century"),2)
        self.assertEqual(target_class("walker.pedestrian.0001"),7)
        self.assertEqual(target_class("vehicle.ford.mustang"),4)
        with self.assertRaises(ValueError):
            target_class("unknown.category")

    def test_evaluation_requires_live_mode(self):
        from scenic_custom_walker_injector import ScenicCustomWalkerConfig,validate_config
        with self.assertRaises(ValueError):
            validate_config(ScenicCustomWalkerConfig(evaluate_bev=True))


if __name__=="__main__":
    unittest.main()
