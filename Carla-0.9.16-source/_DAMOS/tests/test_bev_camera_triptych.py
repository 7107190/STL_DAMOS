from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from report_bev_camera_triptych import choose_camera, context_bounds, project_overhead


class CameraTriptychTests(unittest.TestCase):
    def test_overhead_camera_uses_carla_basis_not_model_y(self):
        transform=np.array([[0,0,1,0],[0,1,0,0],[-1,0,0,60],[0,0,0,1]],float)
        metadata={"camera_to_world_carla":transform,"intrinsic":[[800,0,800],[0,800,800],[0,0,1]],
                  "width":1600,"height":1600}
        uv,valid=project_overhead([[0,0,0],[10,5,0]],metadata)
        np.testing.assert_allclose(uv,[[800,800],[800-800*5/60,800-800*10/60]])
        self.assertTrue(valid.all())

    def test_overhead_does_not_project_points_behind_camera_as_visible(self):
        metadata={"camera_to_world_carla":np.eye(4),"intrinsic":np.eye(3),"width":100,"height":100}
        _,valid=project_overhead([[-10,0,0]],metadata)
        self.assertFalse(valid[0])

    def test_context_crop_keeps_near_edge_points_in_bounds(self):
        for points in ([[3,3],[60,60]],[[1500,1500],[1598,1598]]):
            x0,y0,x1,y1=context_bounds(points,1600,1600)
            self.assertTrue(0<=x0<x1<=1600 and 0<=y0<y1<=1600)
            self.assertTrue(all(x0<=x<=x1 and y0<=y<=y1 for x,y in points))

    def test_camera_choice_uses_geometry_even_when_target_is_hidden(self):
        name,basis=choose_camera({"cam_back":8,"cam_front":0},{"cam_front":10})
        self.assertEqual(name,"cam_back")
        self.assertIn("geometry",basis)

    def test_missing_fov_is_explicit_not_claimed_visible(self):
        name,basis=choose_camera({}, {})
        self.assertEqual(name,"cam_front")
        self.assertIn("fallback",basis)


if __name__=="__main__":
    unittest.main()
