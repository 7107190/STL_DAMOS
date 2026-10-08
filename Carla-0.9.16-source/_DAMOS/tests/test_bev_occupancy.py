from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from bev_evaluation import decode_instance_cameras,observed_presence_ground_truth
from bev_fusion import fuse_bevs
from bev_occupancy import (GROUND,OBSTACLE,UNKNOWN,draw_ego_pose,ego_visible_crop,
                           project_ego_pose,render_presence_context,render_presence_focus,
                           interpolate_presence_for_display,
                           obstacle_presence,render_presence,render_presence_comparison,
                           score_presence,score_scene_presence)


def prediction(cls):
    probabilities = np.zeros((17,200,200),np.float16)
    probabilities[cls] = 1
    return {"probabilities":probabilities,"valid":np.ones((200,200),np.uint8),"pose":np.eye(4)}


class PresenceTests(unittest.TestCase):
    def test_category_does_not_matter_but_location_does(self):
        p = prediction(11)
        p["probabilities"][11,100,100:104] = 0
        p["probabilities"][7,100,100:104] = 1
        mask = np.zeros((200,200),bool); mask[100,100:104] = True
        self.assertTrue(score_presence(p,mask)["diagnostic_match"])
        self.assertFalse(score_presence(p,np.roll(mask,10,axis=0))["diagnostic_match"])

    def test_ground_is_not_an_obstacle(self):
        for cls in (11,12,13,14):
            p = prediction(cls)
            out = obstacle_presence(p["probabilities"],p["valid"])["occupancy"]
            self.assertTrue((out==GROUND).all())

    def test_unknown_and_unobserved_are_not_free(self):
        p = prediction(8); p["valid"][:] = 0
        self.assertFalse(obstacle_presence(p["probabilities"],p["valid"])["occupancy"].any())
        p = prediction(0)
        self.assertTrue((obstacle_presence(p["probabilities"],p["valid"])["occupancy"]==UNKNOWN).all())

    def test_uniform_probabilities_are_not_obstacles(self):
        p = prediction(8); p["probabilities"][:] = 1/17
        self.assertFalse(obstacle_presence(p["probabilities"],p["valid"])["occupancy"].any())

    def test_shape_validation(self):
        with self.assertRaises(ValueError):
            obstacle_presence(np.ones((16,200,200)),np.ones((200,200)))

    def test_fusion_produces_presence_for_verified_peer_cells(self):
        ego,peer = prediction(11),prediction(7)
        ego["valid"][:] = 0; peer["valid"][:] = 0
        peer["valid"][100,100:104] = 1
        fused = fuse_bevs(ego,[peer])
        self.assertEqual(int((fused["occupancy"]==OBSTACLE).sum()),4)
        self.assertEqual(fused["occupancy"][0,0],UNKNOWN)

    def test_false_positives_and_unknowns_are_reported(self):
        p = prediction(8)
        p["probabilities"][8,0,2] = 0; p["probabilities"][11,0,2] = 1
        p["valid"][0,3] = 0
        gt = np.zeros((200,200),np.uint8); gt[0,:4] = [OBSTACLE,GROUND,GROUND,GROUND]
        score = score_scene_presence(p,gt,gt!=UNKNOWN)
        self.assertEqual(score["true_positive_cells"],1)
        self.assertEqual(score["ground_false_positive_cells"],1)
        self.assertEqual(score["true_ground_cells"],1)
        self.assertEqual(score["unknown_on_ground_cells"],1)
        self.assertEqual(score["ground_false_positive_rate_on_decided"],.5)
        self.assertEqual(score["obstacle_precision"],.5)

    def test_semantic_ground_truth_obstacle_wins_vertical_overlap(self):
        xyz = np.array([[.2,.2,0],[.2,.2,1],[.6,.2,0]])
        evidence = {"ego":(xyz,np.array([0,42,0]),np.array([1,14,2]))}
        gt,valid = observed_presence_ground_truth(evidence,np.eye(4))
        self.assertEqual(gt[100,100],OBSTACLE)
        self.assertEqual(gt[101,100],GROUND)
        self.assertEqual(int(valid.sum()),2)

    def test_instance_camera_actor_id_and_frame_are_exact(self):
        raw = np.array([[[1,44,14,255]]],np.uint8)
        packet = {"raw":raw.tobytes(),"height":1,"width":1,"frame_id":100}
        decoded = decode_instance_cameras({"front":packet},100)["front"]
        self.assertEqual(int(decoded["ids"][0,0]),300)
        self.assertEqual(int(decoded["tags"][0,0]),14)
        with self.assertRaises(ValueError):
            decode_instance_cameras({"front":packet},101)

    def test_display_closes_small_holes_without_changing_predictions_or_scores(self):
        p = prediction(11); p["valid"][99:101,99:101] = 0
        before = {k:v.copy() for k,v in p.items()}
        mask = np.zeros((200,200),bool); mask[99:101,99:101] = True
        score = score_presence(p,mask)
        display = interpolate_presence_for_display(p)
        self.assertTrue((display["occupancy"][mask]==GROUND).all())
        self.assertEqual(int(display["interpolated"].sum()),4)
        self.assertFalse(display["metadata"]["used_for_metrics_or_sharing"])
        self.assertEqual(score_presence(p,mask),score)
        for key in p:
            np.testing.assert_array_equal(p[key],before[key])

    def test_display_leaves_large_unobserved_area_unknown(self):
        p = prediction(11); p["valid"][95:105,95:105] = 0
        display = interpolate_presence_for_display(p)
        self.assertEqual(display["occupancy"][100,100],UNKNOWN)
        self.assertFalse(display["interpolated"][100,100])

    def test_display_preserves_observed_unknown_and_other_classes(self):
        p = prediction(11)
        p["probabilities"][11,100,100:102] = 0
        p["probabilities"][0,100,100] = 1
        p["probabilities"][8,100,101] = 1
        display = interpolate_presence_for_display(p)
        self.assertEqual(display["occupancy"][100,100],UNKNOWN)
        self.assertEqual(display["occupancy"][100,101],OBSTACLE)
        self.assertFalse(display["interpolated"].any())

    def test_display_does_not_grow_isolated_points(self):
        p = prediction(8); p["valid"][:] = 0; p["valid"][100,100] = 1
        display = interpolate_presence_for_display(p)
        self.assertFalse(display["interpolated"].any())
        self.assertEqual(int((display["occupancy"]==OBSTACLE).sum()),1)

    def test_display_conflicting_class_proposals_remain_unknown(self):
        p = prediction(11); p["valid"][:] = 0
        p["valid"][100,[99,101]] = 1
        p["valid"][[99,101],100] = 1
        p["probabilities"][11,[99,101],100] = 0
        p["probabilities"][8,[99,101],100] = 1
        display = interpolate_presence_for_display(p)
        self.assertEqual(display["occupancy"][100,100],UNKNOWN)
        self.assertGreaterEqual(display["metadata"]["ambiguous_cells_left_unknown"],1)

    def test_display_empty_input_does_not_create_obstacles(self):
        p = prediction(8); p["valid"][:] = 0
        display = interpolate_presence_for_display(p)
        self.assertFalse(display["interpolated"].any())
        self.assertFalse(display["occupancy"].any())

    def test_display_comparison_keeps_raw_panel_and_observed_pixels_exact(self):
        p = prediction(11); p["valid"][99:101,99:101] = 0
        image,metadata = render_presence_comparison(p,frame_id=123)
        raw,filled = image[84:684,:600],image[84:684,600:1200]
        np.testing.assert_array_equal(raw,render_presence(p))
        valid = np.repeat(np.repeat(p["valid"][::-1,::-1],3,axis=0),3,axis=1).astype(bool)
        np.testing.assert_array_equal(raw[valid],filled[valid])
        self.assertEqual(metadata["filled_ground_cells"],4)
        with self.assertRaises(ValueError):
            render_presence_comparison(p,crop=(180,180,40))

    def test_all_obstacle_categories_use_the_same_orange(self):
        for cls in (1,2,3,4,5,6,7,8,9,10,15,16):
            p = prediction(cls)
            before = p["probabilities"].copy()
            image = render_presence_context(p, 200)
            self.assertTrue((image == [0,180,255]).all())
            np.testing.assert_array_equal(p["probabilities"], before)

    def test_ego_origin_is_centre_and_forward_is_up(self):
        marker = project_ego_pose(np.eye(4), np.eye(4), (50,50,100))
        np.testing.assert_allclose(marker["pixel_xy"], [319.5,319.5])
        np.testing.assert_allclose(marker["forward_pixel_unit"], [0,-1])
        self.assertTrue(marker["in_frame"])

    def test_ego_projection_respects_translation_rotation_and_display_flips(self):
        ego = np.array([[0,-1,0,4],[1,0,0,8],[0,0,1,0],[0,0,0,1]], float)
        marker = project_ego_pose(ego, np.eye(4), (50,50,100))
        np.testing.assert_allclose(marker["pixel_xy"], [191.5,255.5])
        np.testing.assert_allclose(marker["forward_pixel_unit"], [-1,0])
        same_grid = project_ego_pose(ego, ego, (50,50,100))
        np.testing.assert_allclose(same_grid["pixel_xy"], [319.5,319.5])

    def test_crop_keeps_original_region_and_adds_ego(self):
        for x in (0,20,73,100):
            for y in (0,33,75,100):
                crop = ego_visible_crop((x,y,100), np.eye(4), np.eye(4))
                a,b,width = crop
                self.assertTrue(0 <= a <= x and a+width >= x+100 and a+width <= 200)
                self.assertTrue(0 <= b <= y and b+width >= y+100 and b+width <= 200)
                marker = project_ego_pose(np.eye(4), np.eye(4), crop)
                self.assertTrue(marker["in_frame"])
                self.assertTrue(all(35 < value < 605 for value in marker["pixel_xy"]))

    def test_out_of_crop_pose_is_not_faked_at_an_edge(self):
        image = np.full((640,640,3), 28, np.uint8)
        drawn, marker = draw_ego_pose(image, np.eye(4), np.eye(4), (0,0,50))
        self.assertFalse(marker["in_frame"])
        np.testing.assert_array_equal(drawn, image)
        ego = np.eye(4); ego[0,3] = 50
        with self.assertRaises(ValueError):
            ego_visible_crop((0,0,100), ego, np.eye(4))

    def test_ego_overlay_does_not_mutate_input_and_marks_the_exact_pose(self):
        image = np.full((640,640,3), 28, np.uint8)
        drawn, marker = draw_ego_pose(image, np.eye(4), np.eye(4), (50,50,100))
        self.assertTrue((image == 28).all())
        np.testing.assert_array_equal(drawn[320,320], [35,55,240])
        self.assertEqual(marker["local_xy_m"], [0,0])

    def test_focus_marks_ego_even_without_a_target_and_preserves_predictions(self):
        p = prediction(15)
        before = {key:value.copy() for key,value in p.items()}
        for populated in (False, True):
            mask = np.zeros((200,200), bool)
            mask[110,110] = populated
            image = render_presence_focus(p, mask, (50,50,100), ego_pose=p["pose"])
            np.testing.assert_array_equal(image[320,320], [35,55,240])
        for key in p:
            np.testing.assert_array_equal(p[key], before[key])

    def test_marker_rejects_invalid_pose_and_crop(self):
        for pose in (np.eye(3), np.full((4,4), np.nan)):
            with self.assertRaises(ValueError):
                project_ego_pose(pose, np.eye(4), (0,0,200))
        with self.assertRaises(ValueError):
            project_ego_pose(np.eye(4), np.eye(4), (180,180,100))


if __name__=="__main__":
    unittest.main()
