from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from bev_anchor_evaluation import evaluate_anchors,region_presence


def prediction():
    probabilities = np.zeros((17,200,200),np.float16); probabilities[8] = 1
    return {"probabilities":probabilities,"valid":np.zeros((200,200),np.uint8)}


class AnchorTests(unittest.TestCase):
    def test_six_cars_are_one_situation_region(self):
        own,fused = prediction(),prediction()
        objects,masks = [],{}
        for actor in range(6):
            mask = np.zeros((200,200),bool); mask[100+actor*4,100:104] = True
            masks[actor] = mask
            objects.append({"actor_id":actor,"scenario":"S5","anchor_index":1,
                            "in_ego_roi":True,"occlusion_verified":actor!=0})
        own["valid"][masks[0]] = 1
        fused["valid"][masks[0]|masks[1]] = 1
        result = evaluate_anchors(objects,masks,own,fused)
        self.assertEqual(result["anchor_total"],1)
        self.assertEqual(result["anchor_ego_detected"],1)
        self.assertEqual(result["anchor_fused_detected"],1)
        self.assertEqual(result["anchor_new_detections"],0)
        self.assertEqual(result["anchor_hidden_parts_recovered"],1)

    def test_distant_anchors_are_not_one_scenario_success(self):
        own,fused = prediction(),prediction()
        objects,masks = [],{}
        for actor in range(2):
            mask = np.zeros((200,200),bool)
            if actor==0:
                mask[100,100:104] = True
            masks[actor] = mask
            objects.append({"actor_id":actor,"scenario":"S4","anchor_index":actor,
                            "in_ego_roi":actor==0,"occlusion_verified":False})
        fused["valid"][masks[0]] = 1
        result = evaluate_anchors(objects,masks,own,fused)
        self.assertEqual(result["anchor_total"],2)
        self.assertEqual(result["anchor_assessable"],1)
        self.assertEqual(result["anchor_fused_detected"],1)
        self.assertIsNone(result["anchors"][1]["fused"]["detected"])

    def test_three_scattered_false_points_are_not_a_cluster(self):
        p = prediction(); mask = np.zeros((200,200),bool)
        mask[100,100] = mask[110,110] = mask[120,120] = True
        p["valid"][mask] = 1
        self.assertFalse(region_presence(p,mask)["detected"])

    def test_invalid_cells_and_wrong_locations_never_count(self):
        p = prediction(); mask = np.zeros((200,200),bool); mask[100,100:104] = True
        p["valid"][110,100:104] = 1
        self.assertFalse(region_presence(p,mask)["detected"])

    def test_ground_does_not_count_as_region_obstacle(self):
        p = prediction(); p["probabilities"][:] = 0; p["probabilities"][11] = 1
        mask = np.zeros((200,200),bool); mask[100,100:104] = True
        p["valid"][mask] = 1
        self.assertFalse(region_presence(p,mask)["detected"])


if __name__=="__main__":
    unittest.main()
