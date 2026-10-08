import tempfile
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bev_sensor_rig import (
    BEVSensorRig, CAMERAS, EGO_BASIS, HEIGHT, WIDTH,
    camera_calibration, latest_common_window, lidar_mount, preprocess_rgb, rasterize_lidar,
)


class BEVSensorTests(unittest.TestCase):
    def test_optical_projection_and_rotation(self):
        m = np.eye(4)
        r, t, k = camera_calibration(m)
        np.testing.assert_allclose(r.T @ r, np.eye(3), atol=1e-6)
        self.assertAlmostEqual(np.linalg.det(r), 1.0)
        # Straight ahead and CARLA-right points must project at centre and right.
        for carla_point, expected_u in [([10, 0, 0], 400), ([10, 2, 0], 480)]:
            p = r.T @ (EGO_BASIS @ carla_point - t)
            uv = k @ p
            self.assertAlmostEqual(uv[0] / uv[2], expected_u)
            self.assertAlmostEqual(uv[1] / uv[2], 225)

    def test_rotated_and_translated_camera(self):
        yaw = np.deg2rad(55)
        m = np.eye(4)
        m[:3, :3] = [[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]]
        m[:3, 3] = [1.5, .54, 1.75]
        r, t, k = camera_calibration(m)
        carla_point = m[:3, 3] + m[:3, :3] @ [10, 0, 0]
        optical = r.T @ (EGO_BASIS @ carla_point - t)
        uv = k @ optical
        np.testing.assert_allclose(uv[:2] / uv[2], [400, 225], atol=1e-4)

    def test_author_preprocessing(self):
        rgb = np.full((HEIGHT, WIDTH, 3), 255, np.uint8)
        image, pr, pt = preprocess_rgb(rgb)
        self.assertEqual(image.shape, (3, 128, 320))
        np.testing.assert_allclose(image[:, 0, 0], (1 - np.array([.485, .456, .406])) / [.229, .224, .225], rtol=1e-6)
        np.testing.assert_allclose(pr, np.diag([.5, .5, 1]))
        np.testing.assert_allclose(pt, [-40, -97, 0])

    def test_lidar_geometry_and_six_channels(self):
        m = np.eye(4); m[2, 3] = 1.83
        pts = np.array([[10, 2, 0, .3], [10, 2, 1, .8], [40, 0, 0, 1]], np.float32)
        bev = rasterize_lidar(pts, m)
        self.assertEqual(bev.shape, (6, 200, 200))
        self.assertEqual(bev[0].sum(), 1)
        np.testing.assert_allclose(bev[:, 125, 95], [1, np.log(3), 2.83, 1.83, 1, .8], rtol=1e-6)
        self.assertEqual(bev[0, 125, 105], 0)

    def test_bad_lidar_is_not_silent_zero(self):
        for points in [np.empty((0, 4)), np.ones((4, 5)), np.full((2, 4), np.nan)]:
            with self.assertRaises(ValueError):
                rasterize_lidar(points, np.eye(4))

    def test_exact_common_history(self):
        buffers = {"front": {f: None for f in range(10, 17)}, "lidar": {f: None for f in range(9, 16)}}
        self.assertEqual(latest_common_window(buffers, ["front", "lidar"]), [12, 13, 14, 15])
        self.assertIsNone(latest_common_window(buffers, ["front", "lidar"], 13))
        del buffers["lidar"][13]
        self.assertIsNone(latest_common_window(buffers, ["front", "lidar"]))

    def test_missing_sensor_never_uses_another_actor(self):
        self.assertIsNone(latest_common_window({"a/front": {f: 1 for f in range(4)}}, ["a/front", "b/front"]))

    def test_cleanup_preserves_unrelated_sensor(self):
        destroyed = []
        owned = SimpleNamespace(id=7, is_listening=True, stop=lambda: None,
                                destroy=lambda: destroyed.append(7))
        foreign = SimpleNamespace(id=8)
        observer = SimpleNamespace(sensors=[owned, foreign])
        rig = BEVSensorRig.__new__(BEVSensorRig)
        rig.sensors = [owned]
        rig.observers = {"observer": observer}
        rig.close()
        self.assertEqual(destroyed, [7])
        self.assertEqual(observer.sensors, [foreign])
        rig.close()
        self.assertEqual(destroyed, [7])

    def test_custom_actor_uses_its_original_config_block(self):
        config = (
            "Initializing Mobility for actor 1 (walker.pedestrian.0080) with 1 sensors\n"
            "sensor : lidar ,bp : ActorBlueprint(id=sensor.lidar.ray_cast,tags=[]),  transform : {'location': [0, 0, 1.7], 'rotation': [0, 0, 0]}\n"
            "Initializing Mobility for actor 2 (walker.pedestrian.0090) with 1 sensors\n"
            "sensor : lidar ,bp : ActorBlueprint(id=sensor.lidar.ray_cast,tags=[]),  transform : {'location': [0, 0, 1.2], 'rotation': [0, 0, 0]}\n")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sensor_config.txt"
            path.write_text(config)
            human = lidar_mount(path, SimpleNamespace(type_id="walker.pedestrian.damos_humanoid"))
            robot = lidar_mount(path, SimpleNamespace(type_id="walker.pedestrian.damos_deliverybot"))
        self.assertAlmostEqual(human.location.z, 1.7, places=5)
        self.assertAlmostEqual(robot.location.z, 1.2, places=5)

    def test_full_input_shapes_and_poses(self):
        rig = BEVSensorRig.__new__(BEVSensorRig)
        actor = SimpleNamespace(id=42, type_id="vehicle.ford.mustang")
        relative = np.eye(4); relative[2, 3] = 1.83
        rig.sensor_matrices = {("ego", "lidar"): relative}
        packets = {}
        bgra = np.full((HEIGHT, WIDTH, 4), 100, np.uint8).tobytes()
        for name in (*CAMERAS, "lidar"):
            rig.sensor_matrices["ego", name] = relative
            frames = []
            for i in range(4):
                world = relative.copy(); world[0, 3] = i
                raw = np.array([[10, 2, 0, .5]], np.float32).tobytes() if name == "lidar" else bgra
                frames.append({"frame": i + 20, "timestamp": i * .1, "raw": raw, "transform": world})
            packets["ego", name] = frames
        with tempfile.TemporaryDirectory() as folder:
            arrays, tiles, record = rig._save_actor(Path(folder), "ego", actor, packets, list(range(20, 24)))
        self.assertEqual(arrays["imgs"].shape, (1, 4, 6, 3, 128, 320))
        self.assertEqual(arrays["lidar_bev"].shape, (1, 6, 200, 200))
        self.assertEqual(arrays["rots"].shape, (1, 4, 6, 3, 3))
        np.testing.assert_allclose(arrays["ego2globals"][0, :, 0, 3], [0, 1, 2, 3])
        self.assertEqual(record["actor_id"], 42)
        self.assertEqual(len(tiles), 6)


if __name__ == "__main__":
    unittest.main()
