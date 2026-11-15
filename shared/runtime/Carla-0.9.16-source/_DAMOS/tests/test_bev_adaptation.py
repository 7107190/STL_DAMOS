from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from bev_adaptation_data import dataset_scenes, semantic_labels
from train_bev_adaptation import adaptation_loss, check_decoder_gradients, eligible, frozen_state_unchanged, selection_ok


class AdaptationDataTests(unittest.TestCase):
    def test_pedestrian_priority_and_no_densification(self):
        xyz = np.array([[10,0,0],[10,0,1],[11,0,0],[12,0,1]],np.float32)
        labels = semantic_labels((xyz,np.array([1,2,3,99]),np.array([2,12,1,12])),np.eye(4),exclude_actor_id=99)
        self.assertEqual(labels[125,100],7)
        self.assertEqual(labels[127,100],11)
        self.assertEqual(labels[130,100],255)
        self.assertEqual(int((labels!=255).sum()),2)

    def manifests(self,root):
        for index,split in enumerate(("train","val","test")):
            folder = Path(root)/split/f"scene{index}"; folder.mkdir(parents=True)
            (folder/"manifest.json").write_text(json.dumps({"scenario":"S1","split":split,"seed":index,
                "capture_complete":True,"samples":[{"content_sha256":str(index)}]}))

    def test_scene_split_leakage_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            self.manifests(root)
            self.assertEqual(len(dataset_scenes(root)),3)
            path = Path(root)/"test/scene2/manifest.json"
            item = json.loads(path.read_text()); item["seed"] = 0
            path.write_text(json.dumps(item))
            with self.assertRaisesRegex(ValueError,"seed"):
                dataset_scenes(root)

    def test_content_leakage_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            self.manifests(root)
            path = Path(root)/"test/scene2/manifest.json"
            item = json.loads(path.read_text()); item["samples"][0]["content_sha256"] = "0"
            path.write_text(json.dumps(item))
            with self.assertRaisesRegex(ValueError,"Identical"):
                dataset_scenes(root)

    def test_directory_split_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            self.manifests(root)
            path = Path(root)/"test/scene2/manifest.json"
            item = json.loads(path.read_text()); item["split"] = "train"
            path.write_text(json.dumps(item))
            with self.assertRaisesRegex(ValueError,"directory"):
                dataset_scenes(root)

    def test_capture_cli_isolated_and_seed_bound(self):
        import argparse
        from scenic_custom_walker_injector import add_integration_args,config_from_args,build_scenic_command,validate_config
        parser = argparse.ArgumentParser(); add_integration_args(parser)
        args = parser.parse_args(["--collect-bev-dataset","--bev-dataset-root","/tmp/unused",
                                  "--scene-seed","4101","--realtime-factor","1"])
        config = config_from_args(args)
        self.assertTrue(config.attach_bev_sensors)
        command = build_scenic_command(config)
        self.assertEqual(command[command.index("--seed")+1],"4101")
        self.assertEqual(command[command.index("timestep")+1],"0.1")
        validate_config(config)
        for changes in ({"scene_seed":None},{"live_bev_fusion":True},{"compare_bev_inputs":True},{"bev_rounds":0}):
            with self.assertRaises(ValueError):
                validate_config(replace(config,**changes))


class AdaptationSelectionTests(unittest.TestCase):
    def metrics(self):
        return {"regions_assessable":10,"regions_detected":6,"ground_fp_rate":.3,
                "unknown_fraction":.2,"obstacle_f1":.5}

    def test_no_accuracy_only_or_unknown_cheating(self):
        baseline = self.metrics()
        improved = dict(baseline,obstacle_f1=.6)
        self.assertTrue(eligible(improved,baseline))
        for change in ({"regions_detected":5},{"ground_fp_rate":.31},{"unknown_fraction":.21}):
            self.assertFalse(eligible(dict(improved,**change),baseline))

    def test_global_gain_cannot_hide_scenario_regression(self):
        baseline = {"aggregate":self.metrics(),"per_scenario":{"S1":self.metrics()}}
        candidate = {"aggregate":dict(self.metrics(),obstacle_f1=.6),
                     "per_scenario":{"S1":dict(self.metrics(),regions_detected=5)}}
        self.assertFalse(selection_ok(candidate,baseline))

    def test_sparse_loss_has_grad_only_on_observed_cells(self):
        import torch
        logits = torch.zeros((17,200,200),requires_grad=True)
        labels = torch.full((200,200),255,dtype=torch.long)
        labels[100,100],labels[100,101] = 7,11
        observed = torch.zeros((200,200),dtype=torch.bool); observed[100,100:102] = True
        target = torch.zeros_like(observed); target[100,100] = True
        loss = adaptation_loss(logits,labels,observed,target,torch.ones(17))
        loss.backward()
        self.assertGreater(float(logits.grad[:,100,100].abs().sum()),0)
        self.assertEqual(float(logits.grad[:,0,0].abs().sum()),0)

    def test_missing_or_nonfinite_decoder_gradient_rejected(self):
        import torch
        decoder = torch.nn.Conv2d(2,17,1)
        with self.assertRaisesRegex(RuntimeError,"missing"):
            check_decoder_gradients(decoder)
        decoder(torch.ones(1,2,2,2)).sum().backward()
        check_decoder_gradients(decoder)
        decoder.weight.grad[0,0,0,0] = float("nan")
        with self.assertRaisesRegex(RuntimeError,"nonfinite"):
            check_decoder_gradients(decoder)

    def test_hidden_recovery_requires_ego_miss_and_fused_hit(self):
        from audit_bev_adaptation import hidden_recoveries
        scores = {"ego":[{"assessable":True,"detected":False},{"assessable":True,"detected":True}],
                  "weighted":[{"detected":True},{"detected":True}]}
        self.assertEqual(hidden_recoveries(scores),1)

    def test_hidden_recovery_cannot_trade_anchor_identity(self):
        from audit_bev_adaptation import previous_hidden_detections_retained
        own = [{"scenario":"S7","anchor_index":i,"assessable":True,"detected":False} for i in (1,2)]
        original = {"ego":own,"weighted":[dict(own[0],detected=True),own[1]]}
        candidate = {"weighted":[own[0],dict(own[1],detected=True)]}
        self.assertFalse(previous_hidden_detections_retained(original,candidate))
        self.assertTrue(previous_hidden_detections_retained(original,original))

    def test_only_forward_step_sync_is_allowed_for_frozen_state(self):
        import torch
        parent = {"z_collapse._step":torch.tensor(99999),"backbone.weight":torch.ones(2)}
        candidate = dict(parent,**{"z_collapse._step":torch.tensor(100000)})
        self.assertTrue(frozen_state_unchanged(candidate,parent,100000))
        self.assertFalse(frozen_state_unchanged({"z_collapse._step":torch.tensor(100000)},parent,100000))
        self.assertFalse(frozen_state_unchanged(parent,parent,100000))
        candidate["backbone.weight"] = torch.zeros(2)
        self.assertFalse(frozen_state_unchanged(candidate,parent,100000))

    def test_heldout_started_directory_blocks_repeated_evaluation(self):
        from types import SimpleNamespace
        from train_bev_adaptation import complete_run
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/"heldout_original").mkdir()
            with self.assertRaisesRegex(RuntimeError,"already started"):
                complete_run(SimpleNamespace(output=Path(root)),{}, {}, [],None,None)

    def test_replay_keeps_original_transport_pose_and_sensor_coverage(self):
        from audit_bev_adaptation import replay_pose_and_coverage
        original = {"pose":np.eye(4,dtype=np.float32),"valid":np.ones((200,200),np.uint8)}
        candidate = {key:value.copy() for key,value in original.items()}
        candidate["pose"][0,3] = 1e-6
        replay_pose_and_coverage(candidate,original)
        np.testing.assert_array_equal(candidate["pose"],original["pose"])
        candidate["valid"][0,0] = 0
        with self.assertRaisesRegex(ValueError,"coverage"):
            replay_pose_and_coverage(candidate,original)


if __name__=="__main__":
    unittest.main()
