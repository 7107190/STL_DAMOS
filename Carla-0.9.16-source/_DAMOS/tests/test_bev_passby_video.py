from pathlib import Path
from dataclasses import replace
import sys
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bev_passby_video import NativeTrafficManagerRoute, configure_route, select_passage, video_waypoint
from bev_sensor_rig import BEVSensorRig, CAMERAS
from bev_video_render import stable_camera
from run_bev_passby_sweep import require_tmpfs_headroom


class PassbyVideoTests(unittest.TestCase):
    def test_route_setup_does_not_release_brake_before_sensor_readiness(self):
        ego = MagicMock()
        point = SimpleNamespace(transform=SimpleNamespace(
            location=SimpleNamespace(x=0., y=0.), rotation=SimpleNamespace(yaw=0.)))
        goal = SimpleNamespace(transform=point.transform, next=lambda _: [point])
        planner = MagicMock()
        planner.trace_route.return_value = [(point, None), (point, None)]
        route_agent, client = MagicMock(), MagicMock()
        rig = SimpleNamespace(actors={"ego": ego}, world=MagicMock())
        modules = {
            "agents.navigation.local_planner": SimpleNamespace(LocalPlanner=MagicMock(return_value=route_agent)),
            "agents.navigation.global_route_planner": SimpleNamespace(GlobalRoutePlanner=MagicMock(return_value=planner)),
        }
        for scenario in ("S1", "S3", "S5"):
            with self.subTest(scenario=scenario), patch.dict(sys.modules, modules), patch(
                    "bev_passby_video.video_waypoint", return_value=goal):
                agent, metadata = configure_route(rig, client, {"anchor_location": {"x": 0., "y": 0.}}, scenario, 8670)
                if scenario == "S3":
                    self.assertIsInstance(agent, NativeTrafficManagerRoute)
                    self.assertFalse(agent.started)
                    self.assertEqual(agent.speed_kmh, 8.)
                    client.get_trafficmanager.return_value.set_path.assert_not_called()
                else:
                    self.assertIs(agent, route_agent)
                self.assertEqual(metadata["pedestrian_avoidance"], scenario == "S3")
                self.assertTrue(metadata["sensor_readiness_brake_retained"])
                ego.apply_control.assert_not_called()
                route_agent.run_step.assert_not_called()

    def test_native_route_starts_only_once_without_overriding_tm_each_frame(self):
        ego, manager = MagicMock(), MagicMock()
        point = SimpleNamespace(transform=SimpleNamespace(location=object()))
        driver = NativeTrafficManagerRoute(ego, manager, 8670, [(point, None)], 8.)
        carla = SimpleNamespace(VehicleControl=MagicMock(return_value=object()))
        ego.apply_control.assert_not_called()
        with patch.dict(sys.modules, carla=carla):
            self.assertIsNone(driver.run_step())
            self.assertIsNone(driver.run_step())
        ego.set_autopilot.assert_called_once_with(True, 8670)
        ego.apply_control.assert_called_once()
        manager.set_path.assert_called_once_with(ego, [point.transform.location])
        manager.ignore_walkers_percentage.assert_called_once_with(ego, 0.)
        manager.auto_lane_change.assert_called_once_with(ego, False)

    def timeline(self):
        return [{"frame_id": 100 + 2*i, "capture_sim_ns": 10**9 + i*200000000,
                 "anchor_forward_m": 30 - i, "distance_m": abs(30-i) + 4,
                 "on_driving_lane": True} for i in range(60)]

    def test_window_contains_real_approach_and_departure(self):
        rows = self.timeline()
        start, end = select_passage(rows, [], {42})
        self.assertGreater(rows[start]["anchor_forward_m"], 0)
        self.assertLess(rows[end-1]["anchor_forward_m"], 0)
        self.assertGreaterEqual(end-start, 25)

    def test_no_pass_is_not_silently_reported_as_success(self):
        rows = self.timeline()[:20]
        with self.assertRaisesRegex(ValueError, "did not pass"):
            select_passage(rows, [], {42})

    def test_missing_input_frame_is_not_interpolated(self):
        rows = self.timeline()
        del rows[18]
        with self.assertRaisesRegex(ValueError, "missing/duplicate"):
            select_passage(rows, [], {42})

    def test_target_collision_cannot_be_hidden_by_selecting_a_later_clip(self):
        with self.assertRaisesRegex(ValueError, "Collision"):
            select_passage(self.timeline(), [{"frame_id": 100, "other_actor_id": 42, "impulse": 1}], {42})

    def test_offroad_capture_fails(self):
        rows = self.timeline(); rows[35]["on_driving_lane"] = False
        with self.assertRaisesRegex(ValueError, "Off-road"):
            select_passage(rows, [], {42})

    def test_direction_hysteresis_does_not_switch_on_minor_count_changes(self):
        counts = dict.fromkeys(CAMERAS, 0); counts.update(cam_front=8, cam_front_left=10)
        self.assertEqual(stable_camera(counts, "cam_front"), "cam_front")
        counts["cam_front"] = 1
        self.assertEqual(stable_camera(counts, "cam_front"), "cam_front_left")

    def fake_rig(self):
        rig = BEVSensorRig.__new__(BEVSensorRig)
        rig.lock, rig.errors = threading.Lock(), []
        rig.actors = {"ego": SimpleNamespace(id=1, type_id="vehicle.ford.mustang")}
        rig.camera_fovs, rig.sensor_matrices = {}, {}
        rig.buffers = {("ego", name): {frame: {"frame": frame} for frame in range(10, 20)}
                       for name in (*CAMERAS, "lidar")}
        return rig

    def test_exact_historical_window_does_not_substitute_latest(self):
        frames, tasks = self.fake_rig().freeze_inputs(end_frame=15, timeout_s=.01)
        self.assertEqual(frames, [12, 13, 14, 15])
        self.assertEqual(tasks["ego"]["packets"]["ego", "cam_front"][-1]["frame"], 15)

    def test_missing_exact_frame_fails_even_if_newer_frame_exists(self):
        rig = self.fake_rig(); del rig.buffers["ego", "lidar"][14]
        with self.assertRaises(TimeoutError):
            rig.freeze_inputs(end_frame=15, timeout_s=.01)

    def test_video_requires_moving_selected_scene_and_same_frame_evidence(self):
        from scenic_custom_walker_injector import ScenicCustomWalkerConfig, validate_config
        config = ScenicCustomWalkerConfig(bev_video_seconds=30., selected_scenario="S1",
            live_bev_fusion=True, evaluate_bev=True, bev_scene_cameras=True,
            attach_bev_sensors=True, realtime_factor=1.)
        validate_config(config)
        for changes in ({"static_ego": True}, {"selected_scenario": None},
                        {"evaluate_bev": False}, {"bev_scene_cameras": False},
                        {"bev_video_seconds": float("nan")}, {"bev_video_anchor": 0},
                        {"bev_video_target": (float("inf"), 0.)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_config(replace(config, **changes))

    def test_anchor_reference_without_video_is_rejected(self):
        from scenic_custom_walker_injector import ScenicCustomWalkerConfig, validate_config
        with self.assertRaises(ValueError):
            validate_config(ScenicCustomWalkerConfig(bev_video_target=(1., 2.)))

    def fake_road(self, alternate):
        class Location:
            def __init__(self, x=0., y=0., z=0.):
                self.x, self.y, self.z = x, y, z
            def distance(self, other):
                return np.linalg.norm([self.x-other.x, self.y-other.y, self.z-other.z])
        def waypoint(x, identity):
            return SimpleNamespace(id=identity, lane_type=1,
                transform=SimpleNamespace(location=Location(x=x), rotation=SimpleNamespace(yaw=90.)),
                get_left_lane=lambda: None, get_right_lane=lambda: None)
        own, side = waypoint(0., 1), waypoint(7., 2)
        road = SimpleNamespace(get_waypoint=lambda p, *_: side if alternate and p.x > 3.5 else own)
        return road, own, side, SimpleNamespace(Location=Location, LaneType=SimpleNamespace(Driving=1))

    def test_parallel_driving_lane_found_without_junction_neighbour_links(self):
        road, _, side, carla = self.fake_road(True)
        with patch.dict(sys.modules, carla=carla):
            self.assertIs(video_waypoint(road, 0., 0., "S4"), side)

    def test_blocked_lane_is_not_silently_used_if_no_parallel_lane_exists(self):
        road, _, _, carla = self.fake_road(False)
        with patch.dict(sys.modules, carla=carla), self.assertRaisesRegex(ValueError, "separate Driving"):
            video_waypoint(road, 0., 0., "S4")

    def test_ram_report_path_rejects_insufficient_available_memory(self):
        with patch.object(Path, "is_file", return_value=True), patch.object(
                Path, "read_text", return_value="MemAvailable: 12000000 kB\n"):
            with self.assertRaisesRegex(RuntimeError, "24GiB"):
                require_tmpfs_headroom(Path("/dev/shm/test-video"))

    def test_ram_report_path_accepts_conservative_memory_headroom(self):
        with patch.object(Path, "is_file", return_value=True), patch.object(
                Path, "read_text", return_value="MemAvailable: 28000000 kB\n"):
            require_tmpfs_headroom(Path("/dev/shm/test-video"))

    def test_disk_report_path_does_not_apply_ram_storage_limit(self):
        with patch.object(Path, "read_text", side_effect=AssertionError("No RAM report")):
            require_tmpfs_headroom(Path("/tmp/disk-video"))


if __name__ == "__main__":
    unittest.main()
