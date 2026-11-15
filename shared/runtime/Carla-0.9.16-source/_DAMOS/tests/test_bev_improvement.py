import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from bev_lidar_calibration import FEATURES,VERSION,apply_calibration,geometry_features,validate_calibration
from bev_fusion import fuse_bevs


class LiveAuditTests(unittest.TestCase):
    def fixture(self,root):
        from bev_protocol import protect_bev
        from test_bev_integration import StubBackend
        backend = StubBackend()
        prediction = {"probabilities":np.full((17,200,200),.01,np.float16),
                      "valid":np.zeros((200,200),np.uint8),"pose":np.eye(4,dtype=np.float32)}
        prediction["probabilities"][11] = .84
        prediction["valid"][100:103,100:103] = 1
        names = ("ego","observer_1")
        actors = {name:{"actor_id":index+1} for index,name in enumerate(names)}
        setup = {name:{"commitment":str(index+2)} for index,name in enumerate(names)}
        packets,peers = {},{}
        for name in names:
            target = [n for n in names if n!=name]
            metadata = {"session_id":"session","round_id":1,"sender_id":name,
                "actor_id":actors[name]["actor_id"],"frame_id":10,"capture_sim_ns":100,
                "checkpoint_sha256":"a"*64,"checkpoint_step":100000}
            packet,_,_ = protect_bev(backend,"1",setup[name]["commitment"],prediction,metadata,target)
            packets[name] = packet
            (root/f"{name}_packet.json").write_text(json.dumps(packet))
            np.savez_compressed(root/f"{name}_own.npz",**prediction)
            peers[name] = {"status":"fusion_complete","peer_ids":target,"verified_peer_ids":target,
                "inference":{"checkpoint_sha256":"a"*64,"occupancy_calibration_enabled":True},
                "fusion_mode":"ego_observed","round_id":1,"frame_id":10,
                "own_bev_sha256":packet["manifest"]["sha256"],"elapsed_ms":100.}
        for name in names:
            peers[name]["received_bevs"] = {target:{"sha256":packets[target]["manifest"]["sha256"]}
                                             for target in peers[name]["peer_ids"]}
        (root/"peers.json").write_text(json.dumps(peers))
        np.savez_compressed(root/"ego_received_observer_1.npz",**prediction)
        np.savez_compressed(root/"ego_fused.npz",**fuse_bevs(prediction,[prediction],mode="ego_observed"))
        return {"actors":actors,"setup":setup},backend,prediction

    def test_historical_audit_matches_fusion_body_and_model_without_inference(self):
        from audit_bev_candidate_live import audit_round
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); summary,backend,_ = self.fixture(root)
            result = audit_round(root,summary,"a"*64,backend,fusion_mode="ego_observed",calibrated=True)
            self.assertEqual(result["proofs_reverified"],2)
            self.assertEqual(result["verified_directions"],2)

    def test_historical_audit_rejects_substituted_received_body(self):
        from audit_bev_candidate_live import audit_round
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); summary,backend,prediction = self.fixture(root)
            prediction["probabilities"][11,100,100] = .8
            prediction["probabilities"][8,100,100] = .05
            np.savez_compressed(root/"ego_received_observer_1.npz",**prediction)
            with self.assertRaises(AssertionError):
                audit_round(root,summary,"a"*64,backend,fusion_mode="ego_observed",calibrated=True)

    def test_historical_audit_rejects_other_candidate_checkpoint(self):
        from audit_bev_candidate_live import audit_round
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); summary,backend,_ = self.fixture(root)
            with self.assertRaises(ValueError):
                audit_round(root,summary,"b"*64,backend,fusion_mode="ego_observed",calibrated=True)


class CalibrationTests(unittest.TestCase):
    def test_confirmation_image_cannot_select_best_or_hide_unassessable_scenario(self):
        from report_bev_improvement import first_confirmation_rows
        def row(scenario,frame,assessable,hit):
            return {"scenario":scenario,"frame_id":frame,"scores":{"weighted":{
                "anchors":[{"assessable":assessable,"detected":hit}]}}}
        rows = [row("S1",1,False,None),row("S1",2,True,False),row("S1",3,True,True),
                row("S2",1,False,None),row("S2",2,False,None)]
        result = first_confirmation_rows(rows)
        self.assertEqual([(r["scenario"],r["frame_id"]) for r in result],[("S1",2),("S2",1)])

    def config(self):
        return {"version":VERSION,"features":list(FEATURES),"mean":[0.]*len(FEATURES),
                "scale":[1.]*len(FEATURES),"coef":[0.,0.,0.,0.,4.,0.,0.,0.,0.],"intercept":-1.,"strength":1.}

    def test_lidar_plane_uses_geometry_not_gt_and_tracks_height_offset(self):
        raster = np.zeros((6,200,200),np.float32)
        raster[0] = 1; raster[1] = 2; raster[2:4] = -.9
        raster[2,100,110] = .5; raster[4,100,110] = 1.4
        first = geometry_features(raster)
        raster[2:4] += .6
        second = geometry_features(raster)
        np.testing.assert_allclose(first,second,atol=2e-6)
        self.assertAlmostEqual(float(first[0,100,110]),1.4,places=5)

    def test_observed_obstacle_can_change_but_unknown_coverage_cannot_grow(self):
        prediction = {"probabilities":np.full((17,200,200),.01,np.float16),
                      "valid":np.zeros((200,200),np.uint8),"pose":np.eye(4)}
        prediction["probabilities"][11] = .84
        prediction["valid"][100,110] = 1
        geometry = np.zeros((5,200,200),np.float32); geometry[0,100,110] = 1.4
        result = apply_calibration(prediction,self.config(),geometry=geometry)
        self.assertNotEqual(int(result["probabilities"][:,100,110].argmax()),11)
        np.testing.assert_array_equal(result["valid"],prediction["valid"])
        np.testing.assert_array_equal(result["probabilities"][:,0,0],prediction["probabilities"][:,0,0])
        np.testing.assert_allclose(result["probabilities"][:,100,110].sum(dtype=np.float32),1,atol=.003)

    def test_malformed_calibration_rejected(self):
        config = self.config(); config["scale"][0] = 0
        with self.assertRaises(ValueError):
            validate_calibration(config)

    def test_plan_rejects_cross_split_seed_reuse(self):
        from run_bev_adaptation_collection import load_plan
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/"plan.json"
            path.write_text(json.dumps([["train","S1",1,10,"ClearNoon"],
                ["val","S1",2,20,"ClearNoon"],["test","S1",1,30,"ClearNoon"]]))
            with self.assertRaisesRegex(ValueError,"Repeated"):
                load_plan(path)

    def test_binary_fusion_is_bounded_and_preserves_coverage(self):
        prediction = {"probabilities":np.full((17,200,200),1/17,np.float16),
                      "valid":np.ones((200,200),np.uint8),"pose":np.eye(4)}
        result = fuse_bevs(prediction,[prediction],mode="binary_weighted")
        self.assertTrue(np.isfinite(result["probabilities"]).all())
        np.testing.assert_array_equal(result["valid"],prediction["valid"])
        np.testing.assert_allclose(result["probabilities"].sum(0,dtype=np.float32),1,atol=.003)

    def test_presence_loss_gradients_do_not_use_unknown_cells(self):
        import torch
        from train_bev_presence_candidates import presence_loss
        logits = torch.zeros((17,200,200),requires_grad=True)
        labels = torch.full((200,200),255,dtype=torch.long); labels[100,100]=7; labels[100,101]=11
        mask = labels!=255
        loss = presence_loss(logits,labels,mask,mask,torch.full_like(logits,1/17))
        loss.backward()
        self.assertGreater(float(logits.grad[:,100,100].abs().sum()),0)
        self.assertEqual(float(logits.grad[:,0,0].abs().sum()),0)

    def gate_result(self):
        metrics = {"regions_assessable":1,"regions_detected":1,"ground_fp_rate":.3,
                   "unknown_fraction":.1,"obstacle_f1":.5}
        own = {"scenario":"S6","anchor_index":1,"assessable":True,"detected":False}
        fused = dict(own,detected=True)
        return {"aggregate":dict(metrics),"per_scenario":{"S6":dict(metrics)},
                "per_group_scenario":{"known/S6":dict(metrics)},
                "rounds":[{"group":"known","scene_id":"scene","scenario":"S6","frame_id":1,
                           "hidden_scores":{"ego":[own],"weighted":[fused]}}]}

    def test_search_cannot_hide_old_split_or_hidden_regressions(self):
        from run_bev_improvement_experiment import candidate_gate
        baseline = self.gate_result(); candidate = self.gate_result()
        candidate["aggregate"]["obstacle_f1"] = .6
        self.assertTrue(candidate_gate(candidate,baseline)[0])
        candidate["per_group_scenario"]["known/S6"]["regions_detected"] = 0
        self.assertFalse(candidate_gate(candidate,baseline)[0])
        candidate["per_group_scenario"]["known/S6"]["regions_detected"] = 1
        candidate["rounds"][0]["hidden_scores"]["weighted"][0]["detected"] = False
        self.assertFalse(candidate_gate(candidate,baseline)[0])

    def test_search_rejects_frame_identity_mixing(self):
        from run_bev_improvement_experiment import candidate_gate
        baseline = self.gate_result(); candidate = self.gate_result()
        candidate["aggregate"]["obstacle_f1"] = .6
        candidate["rounds"][0]["frame_id"] = 2
        with self.assertRaisesRegex(ValueError,"identity"):
            candidate_gate(candidate,baseline)

    def test_wire_pose_matches_protocol_rounding_not_float64_reinterpretation(self):
        from run_bev_improvement_experiment import wire_pose
        pose = np.eye(4,dtype=np.float32); pose[0,3] = -112.68853; pose[1,3] = 93.5918
        expected = (np.rint(pose.reshape(-1)*1e6).astype(np.int64).reshape(4,4)/1e6).astype(np.float32)
        np.testing.assert_array_equal(wire_pose(pose),expected)


if __name__=="__main__":
    unittest.main()
