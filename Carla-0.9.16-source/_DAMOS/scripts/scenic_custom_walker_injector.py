#!/usr/bin/env python3

from __future__ import annotations

import argparse
import queue
from dataclasses import dataclass, replace
import json
import math
import pathlib
import random
import re
import subprocess
import sys
import threading
import time
from typing import Callable

import carla
import cv2
import numpy as np

from custom_walker_runtime import (
    CustomWalkerAnchor,
    DELIVERYBOT_ID,
    HUMANOID_ID,
    anchor_member_locations,
    attach_actor_cameras,
    attach_observer_cameras,
    connect_to_world,
    distance_between,
    destroy_spawned_walkers,
    find_invalid_anchor_spawned_walkers,
    initialize_custom_walker_movement,
    is_sidewalk_location,
    load_observer_camera_specs,
    load_vehicle_camera_specs,
    measure_walker_movements,
    observer_radius_profile_for_anchor,
    pick_navigation_location_near_anchor,
    probe_anchor_spawned_walkers,
    send_walkers_to_anchor_destinations,
    serialize_transform,
    serialize_observer_camera_specs,
    sidewalk_spawn_locations_near_anchor,
    snapshot_walker_locations,
    spawn_custom_walkers_near_anchors,
    stop_spawned_walker_controllers,
    try_get_actor_location,
    yaw_toward,
)


SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_SCENIC_ROOT = SOURCE_ROOT
DEFAULT_SCENIC_FILE = SOURCE_ROOT / "_DAMOS" / "_scenarios" / "S_.scenic"
DEFAULT_SCENIC_BIN = pathlib.Path("/home/vvu/anaconda3/envs/carla4/bin/scenic")
DEFAULT_REPORT_DIR = SOURCE_ROOT / "_DAMOS" / "reports"
DEFAULT_SENSOR_CONFIG = SOURCE_ROOT.parent / "sensor_config.txt"
SCENIC_SCENARIO_PATTERN = re.compile(r"^(S[_0-9]+(?:#\d+)?):")
BICYCLE_KEYWORDS = (
    "crossbike",
    "omafiets",
    "diamondback",
    "gazelle",
    "century",
    "harley",
    "kawasaki",
    "vespa",
    "yamaha",
)
DAMOS_SCENARIO_ROLE_PREFIX = "damos."
TRASH_PROP_KEYWORDS = ("trash", "garbage", "rubbish", "plasticbag", "bin")
ABNORMAL_SCENARIOS = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9")
ABNORMAL_SCENARIO_DESCRIPTIONS = {
    "S1": "보행자 무단 횡단",
    "S2": "자전거 무단 횡단",
    "S3": "비가시 영역 무단 횡단",
    "S4": "도로위 장애물",
    "S5": "인도 위 장애물",
    "S6": "도로 공사로 인한 차선 감소",
    "S7": "인도 공사로 인한 통행 불가",
    "S8": "인도 내 군중",
    "S9": "인도 쓰레기 더미",
}
ANCHOR_STABLE_OBSERVATIONS = 1
DEBUG_MARKER_LIFETIME_SECONDS = 2.5
SCENIC_TIMESTEP_SECONDS = 0.1
PERSISTENT_MEMBER_ACTOR_REFS = []
EGO_FRONT_CAMERA_FAULT_CHOICES = (
    "none",
    "random",
    "blackout",
    "blur",
    "occlusion",
    "color_failure",
    "misalignment",
    "shaking",
    "freeze_cycle",
)
EGO_FRONT_RANDOM_STILL_FAULTS = (
    "blackout",
    "blur",
    "occlusion",
    "color_failure",
    "misalignment",
    "shaking",
)


@dataclass(frozen=True)
class ScenicCustomWalkerConfig:
    host: str = "127.0.0.1"
    port: int = 2000
    scenic_timeout_seconds: int = 60
    carla_map: str | None = None
    map_xodr: str | None = None
    weather: str | None = None
    wait_for_server_seconds: float = 60.0
    wait_for_ego_seconds: float = 30.0
    wait_for_support_seconds: float = 20.0
    scenic_time: float = 8.0
    n_scenarios: int = 1
    selected_scenario: str | None = None
    static_ego: bool = False
    ego_start: tuple[float, float, float] | None = None
    realtime_factor: float = 0.0
    min_move_meters: float = 0.5
    observer_mode: bool = True
    max_observer_anchor_distance: float = 22.0
    max_observer_facing_error_degrees: float = 35.0
    observer_blueprint: str = "random"
    max_anchor_pairs: int | None = None
    max_deliverybots: int = 2
    max_humanoids: int = 2
    attach_observer_cameras: bool = True
    observer_camera_config: pathlib.Path = DEFAULT_SENSOR_CONFIG
    scenic_bin: pathlib.Path = DEFAULT_SCENIC_BIN
    scenic_file: pathlib.Path = DEFAULT_SCENIC_FILE
    keep_existing_custom_walkers: bool = False
    report_dir: pathlib.Path = DEFAULT_REPORT_DIR
    sample_interval_seconds: float = 0.5
    save_trajectory_report: bool = True
    save_actor_camera_captures: bool = False
    save_observer_scene_captures: bool = False
    save_ego_fault_report: bool = False
    ego_front_camera_fault: str = "none"
    capture_image_width: int = 1280
    capture_image_height: int = 720
    capture_timeout_seconds: float = 6.0
    verify_s1_crossing_autopilot: bool = False
    s1_verify_per_anchor_seconds: float = 15.0
    s1_verify_trigger_distance: float = 15.0
    s1_verify_pass_move_meters: float = 2.0
    s1_verify_ego_upstream_distance: float = 12.0
    verbose: bool = False


@dataclass(frozen=True)
class ScenicCustomWalkerResult:
    scenic_command: tuple[str, ...]
    map_name: str
    ego_actor_id: int
    ego_type_id: str
    scenario_labels: tuple[str, ...]
    anchor_assignments: tuple[dict[str, object], ...]
    walker_movements: dict[str, float]
    observer_metrics: tuple[dict[str, object], ...]
    observer_camera_specs: tuple[dict[str, object], ...]
    observer_camera_attachments: tuple[dict[str, object], ...]
    observer_scene_captures: tuple[dict[str, object], ...]
    scenic_returncode: int | None
    scenic_output_tail: tuple[str, ...]
    trajectory_report_png: str | None
    trajectory_report_focus_png: str | None
    trajectory_report_json: str | None


@dataclass
class EgoTrackingState:
    preferred_id: int
    preferred_type_id: str
    last_resolved_id: int | None = None
    last_valid_location_xyz: tuple[float, float, float] | None = None
    last_valid_timestamp: float | None = None


def add_integration_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument(
        "--scenic-timeout-seconds",
        type=int,
        default=60,
        help="Timeout passed to Scenic's CARLA model for load_world/connect operations.",
    )
    parser.add_argument(
        "--carla-map",
        default=None,
        help="Override Scenic param carla_map without modifying S_.scenic.",
    )
    parser.add_argument(
        "--map-xodr",
        default=None,
        help="Override Scenic param map with an explicit .xodr path.",
    )
    parser.add_argument(
        "--weather",
        default=None,
        help="Override Scenic weather param, e.g. ClearNoon.",
    )
    parser.add_argument("--wait-for-server-seconds", type=float, default=60.0)
    parser.add_argument("--wait-for-ego-seconds", type=float, default=30.0)
    parser.add_argument("--wait-for-support-seconds", type=float, default=20.0)
    parser.add_argument("--scenic-time", type=float, default=8.0)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed anchor, observer, metric, and report-path logs.",
    )
    parser.add_argument("--n-scenarios", type=int, default=1)
    parser.add_argument(
        "--selected-scenario",
        choices=("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"),
        default=None,
        help="Force BaseSetup to compose one specific Scenic abnormal scenario.",
    )
    parser.add_argument(
        "--static-ego",
        action="store_true",
        help="Spawn the Scenic ego vehicle without AutopilotBehavior for manual inspection.",
    )
    parser.add_argument(
        "--ego-start",
        nargs=3,
        type=float,
        metavar=("X", "Y", "HEADING_DEG"),
        default=None,
        help=(
            "Spawn Scenic ego at a fixed Scenic coordinate/heading instead of "
            "random lane placement. Useful for deterministic S1 crossing checks."
        ),
    )
    parser.add_argument(
        "--realtime-factor",
        type=float,
        default=0.0,
        help=(
            "If positive, pace Scenic/CARLA ticks against wall-clock time. "
            "Use 1.0 for approximate real-time playback."
        ),
    )
    parser.add_argument("--min-move-meters", type=float, default=0.5)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--observer-mode",
        dest="observer_mode",
        action="store_true",
        default=True,
        help=(
            "Spawn custom walkers as static observers near Scenic abnormal anchors "
            "(default)."
        ),
    )
    mode_group.add_argument(
        "--walker-mode",
        dest="observer_mode",
        action="store_false",
        help="Route custom walkers and require movement; useful as a movement smoke test.",
    )
    parser.add_argument("--max-observer-anchor-distance", type=float, default=22.0)
    parser.add_argument("--max-observer-facing-error-degrees", type=float, default=35.0)
    parser.add_argument(
        "--observer-blueprint",
        choices=("deliverybot", "humanoid", "random"),
        default="random",
        help=(
            "Observer type to place at each Scenic anchor. Use random to mix "
            "deliverybot and humanoid observers (default: random)."
        ),
    )
    parser.add_argument(
        "--max-anchor-pairs",
        type=int,
        default=None,
        help=(
            "Maximum Scenic anchors to cover. Each selected anchor gets one "
            "observer of the chosen type. When omitted, all semantic anchor "
            "candidates are covered."
        ),
    )
    parser.add_argument(
        "--max-deliverybots",
        type=int,
        default=0,
        help="Legacy compatibility flag; ignored by current observer placement.",
    )
    parser.add_argument(
        "--max-humanoids",
        type=int,
        default=0,
        help="Legacy compatibility flag; ignored by current observer placement.",
    )
    camera_group = parser.add_mutually_exclusive_group()
    camera_group.add_argument(
        "--attach-observer-cameras",
        dest="attach_observer_cameras",
        action="store_true",
        default=True,
        help="Attach six RGB cameras to the ego vehicle and each custom observer (default).",
    )
    camera_group.add_argument(
        "--no-observer-cameras",
        dest="attach_observer_cameras",
        action="store_false",
        help="Spawn actors without attaching ego/custom observer camera sensor actors.",
    )
    parser.add_argument(
        "--observer-camera-config",
        default=str(DEFAULT_SENSOR_CONFIG),
        help=(
            "Path to the sensor_config.txt-style log used to load observer camera "
            "mount transforms."
        ),
    )
    parser.add_argument("--scenic-bin", default=str(DEFAULT_SCENIC_BIN))
    parser.add_argument("--scenic-file", default=str(DEFAULT_SCENIC_FILE))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--sample-interval-seconds", type=float, default=0.5)
    parser.add_argument(
        "--no-trajectory-report",
        action="store_true",
        help="Do not save trajectory PNG/JSON artifacts for Scenic integration runs.",
    )
    parser.add_argument(
        "--save-observer-scene-captures",
        action="store_true",
        help=(
            "Save RGB captures for each observer-anchor pair: an external scene "
            "view and the observer cam_front view."
        ),
    )
    parser.add_argument(
        "--save-actor-camera-captures",
        action="store_true",
        help=(
            "Save one RGB frame from every camera attached to the ego vehicle "
            "and custom observers."
        ),
    )
    parser.add_argument(
        "--ego-front-camera-fault",
        choices=EGO_FRONT_CAMERA_FAULT_CHOICES,
        default="none",
        help=(
            "Apply a camera fault only to ego cam_front captures. Use random to "
            "sample one visible still-image fault for this run."
        ),
    )
    parser.add_argument(
        "--save-ego-fault-report",
        action="store_true",
        help=(
            "Save ego-centric report images for LiDAR noise, RGB sensor delay, "
            "and module stop/freeze demonstrations."
        ),
    )
    parser.add_argument("--capture-image-width", type=int, default=1280)
    parser.add_argument("--capture-image-height", type=int, default=720)
    parser.add_argument("--capture-timeout-seconds", type=float, default=6.0)
    parser.add_argument(
        "--verify-s1-crossing-autopilot",
        action="store_true",
        help=(
            "For selected S1 runs, move the Scenic ego upstream of each S1 "
            "pedestrian, enable CARLA autopilot, and verify that crossing starts."
        ),
    )
    parser.add_argument("--s1-verify-per-anchor-seconds", type=float, default=15.0)
    parser.add_argument("--s1-verify-trigger-distance", type=float, default=13.0)
    parser.add_argument("--s1-verify-pass-move-meters", type=float, default=2.0)
    parser.add_argument("--s1-verify-ego-upstream-distance", type=float, default=16.0)
    parser.add_argument(
        "--keep-existing-custom-walkers",
        action="store_true",
        help="Do not clean up existing custom walkers before spawning new ones.",
    )


def config_from_args(args: argparse.Namespace) -> ScenicCustomWalkerConfig:
    return ScenicCustomWalkerConfig(
        host=args.host,
        port=args.port,
        scenic_timeout_seconds=args.scenic_timeout_seconds,
        carla_map=args.carla_map,
        map_xodr=args.map_xodr,
        weather=args.weather,
        wait_for_server_seconds=args.wait_for_server_seconds,
        wait_for_ego_seconds=args.wait_for_ego_seconds,
        wait_for_support_seconds=args.wait_for_support_seconds,
        scenic_time=args.scenic_time,
        n_scenarios=args.n_scenarios,
        selected_scenario=args.selected_scenario,
        static_ego=args.static_ego,
        ego_start=tuple(args.ego_start) if args.ego_start is not None else None,
        realtime_factor=args.realtime_factor,
        min_move_meters=args.min_move_meters,
        observer_mode=args.observer_mode,
        max_observer_anchor_distance=args.max_observer_anchor_distance,
        max_observer_facing_error_degrees=args.max_observer_facing_error_degrees,
        observer_blueprint=args.observer_blueprint,
        max_anchor_pairs=args.max_anchor_pairs,
        max_deliverybots=args.max_deliverybots,
        max_humanoids=args.max_humanoids,
        attach_observer_cameras=args.attach_observer_cameras,
        observer_camera_config=pathlib.Path(args.observer_camera_config),
        scenic_bin=pathlib.Path(args.scenic_bin),
        scenic_file=pathlib.Path(args.scenic_file),
        keep_existing_custom_walkers=args.keep_existing_custom_walkers,
        report_dir=pathlib.Path(args.report_dir),
        sample_interval_seconds=args.sample_interval_seconds,
        save_trajectory_report=not args.no_trajectory_report,
        save_actor_camera_captures=args.save_actor_camera_captures,
        save_observer_scene_captures=args.save_observer_scene_captures,
        save_ego_fault_report=args.save_ego_fault_report,
        ego_front_camera_fault=args.ego_front_camera_fault,
        capture_image_width=args.capture_image_width,
        capture_image_height=args.capture_image_height,
        capture_timeout_seconds=args.capture_timeout_seconds,
        verify_s1_crossing_autopilot=args.verify_s1_crossing_autopilot,
        s1_verify_per_anchor_seconds=args.s1_verify_per_anchor_seconds,
        s1_verify_trigger_distance=args.s1_verify_trigger_distance,
        s1_verify_pass_move_meters=args.s1_verify_pass_move_meters,
        s1_verify_ego_upstream_distance=args.s1_verify_ego_upstream_distance,
        verbose=args.verbose,
    )


def validate_config(config: ScenicCustomWalkerConfig) -> ScenicCustomWalkerConfig:
    if not config.scenic_bin.exists():
        raise FileNotFoundError(f"Missing Scenic executable: {config.scenic_bin}")
    if not config.scenic_file.exists():
        raise FileNotFoundError(f"Missing Scenic scenario file: {config.scenic_file}")
    if config.max_anchor_pairs is not None and config.max_anchor_pairs < 0:
        raise ValueError("--max-anchor-pairs must be zero or positive.")
    if config.max_deliverybots < 0:
        raise ValueError("--max-deliverybots must be zero or positive.")
    if config.max_humanoids < 0:
        raise ValueError("--max-humanoids must be zero or positive.")
    if config.max_observer_anchor_distance <= 0.0:
        raise ValueError("--max-observer-anchor-distance must be positive.")
    if config.capture_image_width <= 0 or config.capture_image_height <= 0:
        raise ValueError("--capture-image-width/height must be positive.")
    if config.capture_timeout_seconds <= 0.0:
        raise ValueError("--capture-timeout-seconds must be positive.")
    if config.verify_s1_crossing_autopilot and config.selected_scenario != "S1":
        raise ValueError("--verify-s1-crossing-autopilot requires --selected-scenario S1.")
    if config.s1_verify_per_anchor_seconds <= 0.0:
        raise ValueError("--s1-verify-per-anchor-seconds must be positive.")
    if config.s1_verify_trigger_distance <= 0.0:
        raise ValueError("--s1-verify-trigger-distance must be positive.")
    if config.s1_verify_pass_move_meters <= 0.0:
        raise ValueError("--s1-verify-pass-move-meters must be positive.")
    if config.s1_verify_ego_upstream_distance <= 0.0:
        raise ValueError("--s1-verify-ego-upstream-distance must be positive.")
    if not 0.0 <= config.max_observer_facing_error_degrees <= 180.0:
        raise ValueError("--max-observer-facing-error-degrees must be between 0 and 180.")
    if config.observer_blueprint not in {"deliverybot", "humanoid", "random"}:
        raise ValueError(
            "--observer-blueprint must be deliverybot, humanoid, or random."
        )
    return config


def effective_anchor_pair_count(
    config: ScenicCustomWalkerConfig,
    semantic_anchor_count: int,
) -> int:
    if config.max_anchor_pairs is not None:
        return config.max_anchor_pairs
    return semantic_anchor_count


def scenic_root_for(config: ScenicCustomWalkerConfig) -> pathlib.Path:
    return config.scenic_file.resolve().parents[2]


def safe_get_world(client):
    try:
        return client.get_world()
    except RuntimeError:
        return None


def safe_map_name(world) -> str:
    try:
        return world.get_map().name
    except RuntimeError:
        return "<loading>"


def is_all_zero_location(location) -> bool:
    return (
        abs(float(location.x)) < 1e-4
        and abs(float(location.y)) < 1e-4
        and abs(float(location.z)) < 1e-4
    )


def location_to_xyz(location) -> tuple[float, float, float]:
    return (float(location.x), float(location.y), float(location.z))


def distance_to_xyz(location, xyz: tuple[float, float, float] | None) -> float:
    if xyz is None:
        return float("inf")
    dx = float(location.x) - xyz[0]
    dy = float(location.y) - xyz[1]
    dz = float(location.z) - xyz[2]
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def distance_xy_between_locations(first, second) -> float:
    dx = float(first.x) - float(second.x)
    dy = float(first.y) - float(second.y)
    return (dx * dx + dy * dy) ** 0.5


def location_from_report_dict(data: dict[str, object]) -> carla.Location:
    return carla.Location(
        x=float(data["x"]),
        y=float(data["y"]),
        z=float(data.get("z", 0.0)),
    )


def valid_actor_location(actor):
    try:
        location = actor.get_transform().location
    except RuntimeError:
        return None
    if is_all_zero_location(location):
        return None
    return location


def is_s1_pedestrian_actor(actor) -> bool:
    role_name = actor.attributes.get("role_name", "")
    return (
        actor.type_id.startswith("walker.pedestrian.")
        and not actor.type_id.startswith("walker.pedestrian.damos_")
        and role_name.startswith("damos.S1.")
    )


def resolve_s1_pedestrian_actor(world, assignment, *, max_anchor_distance=12.0):
    expected_location = location_from_report_dict(assignment["anchor_location"])
    anchor_actor_id = assignment.get("anchor_actor_id")
    if anchor_actor_id is not None:
        actor = world.get_actor(int(anchor_actor_id))
        if actor is not None and is_s1_pedestrian_actor(actor):
            location = valid_actor_location(actor)
            if (
                location is not None
                and distance_xy_between_locations(location, expected_location)
                <= max_anchor_distance
            ):
                return actor, location

    best_actor = None
    best_location = None
    best_distance = float("inf")
    try:
        actors = world.get_actors()
    except RuntimeError:
        return None, None
    for actor in actors:
        if not is_s1_pedestrian_actor(actor):
            continue
        location = valid_actor_location(actor)
        if location is None:
            continue
        distance = distance_xy_between_locations(location, expected_location)
        if distance < best_distance:
            best_actor = actor
            best_location = location
            best_distance = distance
    if best_actor is not None and best_distance <= max_anchor_distance:
        return best_actor, best_location
    return None, None


def place_ego_upstream_of_location(world, ego_actor, target_location, upstream_distance: float):
    carla_map = world.get_map()
    waypoint = carla_map.get_waypoint(
        target_location,
        project_to_road=True,
        lane_type=carla.LaneType.Driving,
    )
    if waypoint is None:
        raise RuntimeError("No driving waypoint near S1 pedestrian anchor.")

    forward = waypoint.transform.get_forward_vector()
    ego_location = carla.Location(
        x=waypoint.transform.location.x - float(forward.x) * upstream_distance,
        y=waypoint.transform.location.y - float(forward.y) * upstream_distance,
        z=waypoint.transform.location.z + 0.5,
    )
    ego_transform = carla.Transform(ego_location, waypoint.transform.rotation)
    ego_actor.set_autopilot(False)
    ego_actor.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
    ego_actor.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
    ego_actor.set_transform(ego_transform)
    ego_actor.apply_control(
        carla.VehicleControl(throttle=0.0, brake=1.0, hand_brake=True)
    )
    time.sleep(0.2)
    ego_actor.apply_control(
        carla.VehicleControl(throttle=0.0, brake=0.0, hand_brake=False)
    )
    return waypoint


def place_ego_upstream_of_actor(world, ego_actor, target_actor, upstream_distance: float):
    target_location = valid_actor_location(target_actor)
    if target_location is None:
        raise RuntimeError(f"Actor {target_actor.id} has no valid location.")
    return place_ego_upstream_of_location(
        world,
        ego_actor,
        target_location,
        upstream_distance,
    )


def enable_ego_autopilot_for_verification(client, ego_actor) -> int | None:
    try:
        traffic_manager = client.get_trafficmanager()
        traffic_manager.ignore_lights_percentage(ego_actor, 100.0)
        traffic_manager.ignore_signs_percentage(ego_actor, 100.0)
        traffic_manager.distance_to_leading_vehicle(ego_actor, 2.0)
        traffic_manager.vehicle_percentage_speed_difference(ego_actor, -20.0)
        ego_actor.set_autopilot(True, traffic_manager.get_port())
        return traffic_manager.get_port()
    except RuntimeError:
        ego_actor.set_autopilot(True)
        return None


def verify_s1_crossing_autopilot(
    client,
    world,
    ego_actor,
    anchor_assignments,
    config: ScenicCustomWalkerConfig,
    *,
    logger: Callable[[str], None] = print,
) -> tuple[dict[str, object], ...]:
    results = []
    assignments = sorted(
        anchor_assignments,
        key=lambda assignment: int(assignment.get("anchor_index") or 0),
    )
    for assignment in assignments:
        anchor_actor_id = assignment.get("anchor_actor_id")
        anchor_index = assignment.get("anchor_index")
        if anchor_actor_id is None:
            continue

        pedestrian, start_location = resolve_s1_pedestrian_actor(world, assignment)
        if pedestrian is None or start_location is None:
            results.append(
                {
                    "anchor_index": anchor_index,
                    "anchor_actor_id": anchor_actor_id,
                    "passed": False,
                    "reason": "anchor actor missing or invalid location",
                }
            )
            continue

        waypoint = place_ego_upstream_of_location(
            world,
            ego_actor,
            start_location,
            config.s1_verify_ego_upstream_distance,
        )
        traffic_manager_port = enable_ego_autopilot_for_verification(client, ego_actor)

        min_ego_distance = float("inf")
        max_pedestrian_movement = 0.0
        samples = []
        zero_location_samples = 0
        deadline = time.monotonic() + config.s1_verify_per_anchor_seconds
        while time.monotonic() < deadline:
            try:
                world.wait_for_tick(2.0)
            except RuntimeError:
                time.sleep(0.2)

            ego_location = valid_actor_location(ego_actor)
            pedestrian_location = (
                valid_actor_location(pedestrian)
                if pedestrian is not None
                else None
            )
            if pedestrian_location is None:
                pedestrian, pedestrian_location = resolve_s1_pedestrian_actor(
                    world,
                    assignment,
                    max_anchor_distance=35.0,
                )
            if ego_location is None or pedestrian_location is None:
                zero_location_samples += 1
                continue

            ego_distance = distance_xy_between_locations(ego_location, pedestrian_location)
            pedestrian_movement = distance_xy_between_locations(
                start_location,
                pedestrian_location,
            )
            min_ego_distance = min(min_ego_distance, ego_distance)
            max_pedestrian_movement = max(max_pedestrian_movement, pedestrian_movement)
            samples.append(
                {
                    "ego": {
                        "x": float(ego_location.x),
                        "y": float(ego_location.y),
                        "z": float(ego_location.z),
                    },
                    "pedestrian": {
                        "x": float(pedestrian_location.x),
                        "y": float(pedestrian_location.y),
                        "z": float(pedestrian_location.z),
                    },
                    "ego_distance": round(float(ego_distance), 3),
                    "pedestrian_movement": round(float(pedestrian_movement), 3),
                }
            )
            if (
                min_ego_distance <= config.s1_verify_trigger_distance
                and max_pedestrian_movement >= config.s1_verify_pass_move_meters
            ):
                break

        try:
            ego_actor.set_autopilot(False)
        except RuntimeError:
            pass
        passed = (
            min_ego_distance <= config.s1_verify_trigger_distance
            and max_pedestrian_movement >= config.s1_verify_pass_move_meters
        )
        result = {
            "anchor_index": anchor_index,
            "anchor_actor_id": anchor_actor_id,
            "anchor_label": assignment.get("anchor_label"),
            "traffic_manager_port": traffic_manager_port,
            "zero_location_samples": zero_location_samples,
            "nearest_driving_waypoint": {
                "x": float(waypoint.transform.location.x),
                "y": float(waypoint.transform.location.y),
                "z": float(waypoint.transform.location.z),
                "yaw": float(waypoint.transform.rotation.yaw),
                "road_id": waypoint.road_id,
                "lane_id": waypoint.lane_id,
            },
            "min_ego_distance": round(float(min_ego_distance), 3),
            "max_pedestrian_movement": round(float(max_pedestrian_movement), 3),
            "passed": passed,
            "samples": samples,
        }
        status = "PASS" if passed else "FAIL"
        logger(
            f"S1 crossing autopilot verification [{status}] "
            f"anchor={anchor_index} min_ego_distance={result['min_ego_distance']}m "
            f"pedestrian_moved={result['max_pedestrian_movement']}m "
            f"valid_samples={len(samples)} zero_samples={zero_location_samples}"
        )
        results.append(result)
    return tuple(results)


def select_latest_ego_actor(world):
    latest_actor = None
    try:
        actors = world.get_actors()
    except RuntimeError:
        return None

    for actor in actors:
        if (
            actor.attributes.get("role_name") != "ego"
            or not actor.type_id.startswith("vehicle.")
        ):
            continue
        try:
            location = actor.get_transform().location
        except RuntimeError:
            continue
        if is_all_zero_location(location):
            continue
        if latest_actor is None or int(actor.id) > int(latest_actor.id):
            latest_actor = actor
    return latest_actor


def wait_for_ego_actor(client, timeout_seconds: float):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        world = safe_get_world(client)
        if world is None:
            time.sleep(1.0)
            continue
        actor = select_latest_ego_actor(world)
        if actor is not None:
            return world, actor
        time.sleep(0.5)
    raise RuntimeError(
        f"Timed out after {timeout_seconds:.0f}s waiting for Scenic ego actor."
    )


def freeze_static_ego_actor(ego_actor) -> None:
    try:
        ego_actor.set_autopilot(False)
    except RuntimeError:
        pass
    try:
        ego_actor.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
        ego_actor.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
    except RuntimeError:
        pass
    try:
        ego_actor.apply_control(
            carla.VehicleControl(
                throttle=0.0,
                steer=0.0,
                brake=1.0,
                hand_brake=True,
                manual_gear_shift=False,
            )
        )
    except RuntimeError:
        pass
def classify_anchor_category(actor) -> tuple[str, int] | None:
    type_id = actor.type_id
    if type_id.startswith("walker.pedestrian."):
        return "pedestrian", 0
    if type_id.startswith("static.prop."):
        return "prop", 1
    if is_scenic_bicycle_actor(actor):
        return "bicycle", 2
    if type_id.startswith("vehicle."):
        return "vehicle", 3
    return None


def parse_damos_scenario_role(role_name: str | None):
    if not role_name or not role_name.startswith(DAMOS_SCENARIO_ROLE_PREFIX):
        return None
    parts = role_name.split(".")
    if len(parts) < 3:
        return None
    scenario_label = parts[1]
    instance_token = parts[2]
    try:
        scenario_index = int(instance_token)
    except ValueError:
        scenario_index = None
    return {
        "role_name": role_name,
        "scenario_label": scenario_label,
        "scenario_instance": f"{scenario_label}#{instance_token}",
        "scenario_index": scenario_index,
    }


def scenario_sort_key(candidate):
    scenario_index = candidate.get("scenario_index")
    return (
        scenario_index if scenario_index is not None else 10**9,
        str(candidate.get("scenario_label") or ""),
        float(candidate.get("distance_to_ego", float("inf"))),
        int(candidate.get("actor_id", 0)),
    )


def extract_scenario_labels_from_candidates(candidates) -> tuple[str, ...]:
    labels = []
    seen = set()
    for candidate in sorted(candidates, key=scenario_sort_key):
        label = candidate.get("scenario_instance") or candidate.get("scenario_label")
        if not label or label in seen:
            continue
        labels.append(label)
        seen.add(label)
    return tuple(labels)


def scenic_candidate_dedupe_key(candidate):
    location = candidate["location"]
    return (
        str(candidate.get("role_name") or ""),
        str(candidate.get("category") or ""),
        round(float(location.x), 1),
        round(float(location.y), 1),
        round(float(location.z), 1),
    )


def keep_latest_scenic_actor_candidates(candidates):
    latest_by_key = {}
    for candidate in candidates:
        key = scenic_candidate_dedupe_key(candidate)
        previous = latest_by_key.get(key)
        if previous is None or int(candidate["actor_id"]) > int(previous["actor_id"]):
            latest_by_key[key] = candidate
    return sorted(
        latest_by_key.values(),
        key=lambda item: (item["priority"], item["distance_to_ego"], item["actor_id"]),
    )


def destroy_stale_duplicate_scenic_candidates(candidates):
    latest_by_key = {}
    for candidate in candidates:
        key = scenic_candidate_dedupe_key(candidate)
        previous = latest_by_key.get(key)
        if previous is None or int(candidate["actor_id"]) > int(previous["actor_id"]):
            latest_by_key[key] = candidate

    for candidate in candidates:
        if latest_by_key.get(scenic_candidate_dedupe_key(candidate)) is candidate:
            continue
        actor = candidate.get("actor")
        if actor is None:
            continue
        try:
            actor.destroy()
        except RuntimeError:
            pass


def collect_scenic_anchor_candidates(world, ego, spawned_walkers=()):
    custom_ids = {spawned_walker.walker.id for spawned_walker in spawned_walkers}
    ego_location = ego.get_transform().location
    candidates = []
    for actor in world.get_actors():
        if actor.id == ego.id or actor.id in custom_ids:
            continue
        if actor.type_id in {DELIVERYBOT_ID, HUMANOID_ID}:
            continue
        if actor.type_id == "controller.ai.walker":
            continue

        classified = classify_anchor_category(actor)
        if classified is None:
            continue
        scenario_info = parse_damos_scenario_role(actor.attributes.get("role_name"))
        if scenario_info is None:
            continue

        category, priority = classified
        location = actor.get_transform().location
        candidate = {
            "actor": actor,
            "actor_id": actor.id,
            "type_id": actor.type_id,
            "category": category,
            "priority": priority,
            "distance_to_ego": distance_between(location, ego_location),
            "location": location,
        }
        candidate.update(scenario_info)
        candidates.append(candidate)

    destroy_stale_duplicate_scenic_candidates(candidates)
    return keep_latest_scenic_actor_candidates(candidates)


def centroid_location(candidates):
    count = len(candidates)
    return carla.Location(
        x=sum(float(candidate["location"].x) for candidate in candidates) / count,
        y=sum(float(candidate["location"].y) for candidate in candidates) / count,
        z=sum(float(candidate["location"].z) for candidate in candidates) / count,
    )


def nearest_candidate_to_location(candidates, location):
    return min(
        candidates,
        key=lambda candidate: (
            distance_between(candidate["location"], location),
            candidate["actor_id"],
        ),
    )


def serialize_anchor_member_snapshot(candidate):
    return {
        "actor_id": candidate["actor_id"],
        "type_id": candidate["type_id"],
        "category": candidate["category"],
        "role_name": candidate.get("role_name"),
        "scenario_label": candidate.get("scenario_label"),
        "scenario_instance": candidate.get("scenario_instance"),
        "location": serialize_location(candidate["location"]),
    }


def make_semantic_anchor_candidate(candidates, *, label, kind, category=None):
    member_snapshots = tuple(
        serialize_anchor_member_snapshot(candidate) for candidate in candidates
    )
    if len(candidates) == 1 and kind == "actor":
        candidate = dict(candidates[0])
        candidate.setdefault("label", f"scenic.{candidate['category']}:{candidate['actor_id']}")
        candidate.setdefault("anchor_kind", "actor")
        candidate.setdefault("member_actor_ids", (candidate["actor_id"],))
        candidate.setdefault("member_actor_snapshots", member_snapshots)
        candidate.setdefault("member_count", 1)
        candidate.setdefault("dynamic_actor_location", True)
        return candidate

    center = centroid_location(candidates)
    representative = nearest_candidate_to_location(candidates, center)
    semantic_candidate = dict(representative)
    semantic_candidate["location"] = center
    semantic_candidate["category"] = category or representative["category"]
    semantic_candidate["label"] = label
    semantic_candidate["anchor_kind"] = kind
    semantic_candidate["member_actor_ids"] = tuple(
        sorted(candidate["actor_id"] for candidate in candidates)
    )
    semantic_candidate["member_actor_snapshots"] = member_snapshots
    semantic_candidate["member_count"] = len(candidates)
    semantic_candidate["dynamic_actor_location"] = False
    semantic_candidate["distance_to_ego"] = min(
        float(candidate["distance_to_ego"]) for candidate in candidates
    )
    semantic_candidate["role_name"] = representative.get("role_name")
    semantic_candidate["scenario_label"] = representative.get("scenario_label")
    semantic_candidate["scenario_instance"] = representative.get("scenario_instance")
    semantic_candidate["scenario_index"] = representative.get("scenario_index")
    return semantic_candidate


def make_individual_anchor_candidates(candidates, *, label_prefix, kind, category=None):
    return [
        make_semantic_anchor_candidate(
            [candidate],
            label=f"{label_prefix}:{index}",
            kind=kind,
            category=category or candidate["category"],
        )
        for index, candidate in enumerate(
            sorted(
                candidates,
                key=lambda item: (item["distance_to_ego"], item["actor_id"]),
            ),
            start=1,
        )
    ]


def cluster_candidates_by_distance(candidates, *, max_distance):
    clusters = []
    for candidate in sorted(
        candidates,
        key=lambda item: (item["distance_to_ego"], item["actor_id"]),
    ):
        for cluster in clusters:
            center = centroid_location(cluster)
            if distance_between(candidate["location"], center) <= max_distance:
                cluster.append(candidate)
                break
        else:
            clusters.append([candidate])
    return clusters


def semantic_anchor_candidates(candidates, *, selected_scenario: str | None = None):
    if selected_scenario is None and candidates and all(
        candidate.get("scenario_instance") for candidate in candidates
    ):
        grouped = {}
        for candidate in candidates:
            grouped.setdefault(candidate["scenario_instance"], []).append(candidate)
        semantic = []
        for scenario_instance, group in sorted(
            grouped.items(),
            key=lambda item: scenario_sort_key(item[1][0]),
        ):
            scenario_label = group[0].get("scenario_label", "scenario")
            scenario_semantic = semantic_anchor_candidates(
                group,
                selected_scenario=scenario_label,
            )
            for candidate in scenario_semantic:
                candidate = dict(candidate)
                candidate["label"] = (
                    f"scenic.random_scenario:{scenario_instance}."
                    f"{candidate.get('label', candidate.get('anchor_kind', 'anchor'))}"
                )
                semantic.append(candidate)
        semantic.sort(
            key=lambda item: (item["priority"], item["distance_to_ego"], item["actor_id"])
        )
        return semantic

    pedestrians = []
    bicycles = []
    vehicles = []
    construction_props = []
    trash_props = []
    other_props = []

    for candidate in candidates:
        type_id = candidate["type_id"].lower()
        category = candidate["category"]
        if category == "pedestrian":
            pedestrians.append(candidate)
        elif category == "bicycle":
            bicycles.append(candidate)
        elif category == "vehicle":
            vehicles.append(candidate)
        elif category == "prop":
            if any(keyword in type_id for keyword in TRASH_PROP_KEYWORDS):
                trash_props.append(candidate)
            elif "streetbarrier" in type_id or "trafficwarning" in type_id:
                construction_props.append(candidate)
            else:
                other_props.append(candidate)

    semantic = []

    if selected_scenario == "S4":
        road_obstacles = [
            candidate
            for candidate in candidates
            if candidate["category"] in {"prop", "vehicle"}
        ]
        if road_obstacles:
            return make_individual_anchor_candidates(
                road_obstacles,
                label_prefix="scenic.road_obstacle",
                kind="road_obstacle",
            )

    if len(pedestrians) > 3:
        for index, cluster in enumerate(
            cluster_candidates_by_distance(pedestrians, max_distance=10.0),
            start=1,
        ):
            semantic.append(
                make_semantic_anchor_candidate(
                    cluster,
                    label=f"scenic.pedestrian_cluster:{index}",
                    kind="pedestrian_cluster",
                    category="pedestrian",
                )
            )
    else:
        semantic.extend(
            make_semantic_anchor_candidate([candidate], label="", kind="actor")
            for candidate in pedestrians
        )

    semantic.extend(
        make_semantic_anchor_candidate([candidate], label="", kind="actor")
        for candidate in bicycles
    )

    if vehicles:
        vehicle_region_kind = (
            "s5_vehicle_region" if selected_scenario == "S5" else "vehicle_region"
        )
        semantic.append(
            make_semantic_anchor_candidate(
                vehicles,
                label="scenic.vehicle_region:1",
                kind=vehicle_region_kind,
                category="vehicle",
            )
        )

    if construction_props:
        if selected_scenario == "S6":
            construction_region_kind = "s6_construction_region"
        elif selected_scenario == "S7":
            construction_region_kind = "s7_construction_region"
        else:
            construction_region_kind = "construction_region"
        semantic.append(
            make_semantic_anchor_candidate(
                construction_props,
                label="scenic.construction_region:1",
                kind=construction_region_kind,
                category="prop",
            )
        )

    for index, cluster in enumerate(
        cluster_candidates_by_distance(trash_props, max_distance=3.5),
        start=1,
    ):
        semantic.append(
            make_semantic_anchor_candidate(
                cluster,
                label=f"scenic.trash_pile:{index}",
                kind="trash_pile",
                category="prop",
            )
        )

    if other_props:
        semantic.append(
            make_semantic_anchor_candidate(
                other_props,
                label="scenic.obstacle_region:1",
                kind="obstacle_region",
                category="prop",
            )
        )

    semantic.sort(
        key=lambda item: (item["priority"], item["distance_to_ego"], item["actor_id"])
    )
    return semantic


def filter_candidates_for_selected_scenario(candidates, selected_scenario):
    if selected_scenario in {"S1", "S3", "S8"}:
        return [
            candidate for candidate in candidates if candidate["category"] == "pedestrian"
        ]
    if selected_scenario == "S2":
        return [candidate for candidate in candidates if candidate["category"] == "bicycle"]
    if selected_scenario == "S4":
        return [
            candidate
            for candidate in candidates
            if candidate["category"] in {"prop", "vehicle"}
        ]
    if selected_scenario == "S5":
        return [candidate for candidate in candidates if candidate["category"] == "vehicle"]
    if selected_scenario == "S9":
        return [
            candidate
            for candidate in candidates
            if candidate["category"] == "prop"
            and any(
                keyword in candidate["type_id"].lower()
                for keyword in TRASH_PROP_KEYWORDS
            )
        ]
    if selected_scenario in {"S6", "S7"}:
        return [candidate for candidate in candidates if candidate["category"] == "prop"]
    return candidates


def wait_for_anchor_candidates(
    client,
    ego,
    timeout_seconds: float,
    *,
    min_candidates: int = 1,
    candidate_filter: (
        Callable[[list[dict[str, object]]], list[dict[str, object]]] | None
    ) = None,
    accumulate_by: Callable[[dict[str, object]], object] | None = None,
    readiness_check: Callable[[list[dict[str, object]]], bool] | None = None,
    readiness_label: str | None = None,
):
    deadline = time.monotonic() + timeout_seconds
    required_candidates = max(1, min_candidates)
    accumulated_groups = {} if accumulate_by is not None else None
    stable_signature = None
    stable_observations = 0
    while time.monotonic() < deadline:
        world = safe_get_world(client)
        if world is None:
            time.sleep(1.0)
            continue
        try:
            candidates = collect_scenic_anchor_candidates(world, ego)
        except RuntimeError:
            time.sleep(1.0)
            continue
        filtered_candidates = (
            candidate_filter(candidates) if candidate_filter else candidates
        )
        candidates_to_check = filtered_candidates
        if accumulated_groups is not None:
            current_groups = {}
            for candidate in filtered_candidates:
                group_key = accumulate_by(candidate)
                if group_key is None:
                    continue
                current_groups.setdefault(group_key, {})[candidate["actor_id"]] = candidate
            for group_key, group_candidates in current_groups.items():
                accumulated_groups[group_key] = group_candidates
            candidates_to_check = [
                candidate
                for group_candidates in accumulated_groups.values()
                for candidate in group_candidates.values()
            ]

        candidate_count_ready = len(candidates_to_check) >= required_candidates
        readiness_ok = readiness_check(candidates_to_check) if readiness_check else True
        if candidate_count_ready and readiness_ok:
            signature = tuple(
                sorted(
                    (
                        int(candidate["actor_id"]),
                        str(candidate["type_id"]),
                        str(candidate.get("role_name") or ""),
                    )
                    for candidate in candidates_to_check
                )
            )
            if signature == stable_signature:
                stable_observations += 1
            else:
                stable_signature = signature
                stable_observations = 1
            if stable_observations >= ANCHOR_STABLE_OBSERVATIONS:
                return world, candidates_to_check
        else:
            stable_signature = None
            stable_observations = 0
        time.sleep(0.5)
    readiness_suffix = (
        f" and {readiness_label}" if readiness_label else ""
    )
    raise RuntimeError(
        f"Timed out after {timeout_seconds:.0f}s waiting for at least "
        f"{required_candidates} Scenic support actors{readiness_suffix}."
    )


def anchor_is_far_enough(candidate, excluded_locations, min_separation):
    if min_separation <= 0.0:
        return True
    candidate_location = candidate["location"]
    return all(
        distance_between(candidate_location, excluded_location) >= min_separation
        for excluded_location in excluded_locations
    )


def select_anchor_for_blueprint(
    candidates,
    preferred_categories,
    excluded_actor_ids,
    *,
    excluded_locations=(),
    min_separation=0.0,
):
    for category in preferred_categories:
        for candidate in candidates:
            if candidate["actor_id"] in excluded_actor_ids:
                continue
            if not anchor_is_far_enough(candidate, excluded_locations, min_separation):
                continue
            if candidate["category"] == category:
                return candidate

    for candidate in candidates:
        if candidate["actor_id"] in excluded_actor_ids:
            continue
        if not anchor_is_far_enough(candidate, excluded_locations, min_separation):
            continue
        return candidate

    return None


def observer_blueprint_config(observer_blueprint: str) -> tuple[str, str]:
    if observer_blueprint == "humanoid":
        return HUMANOID_ID, "humanoid"
    return DELIVERYBOT_ID, "deliverybot"


def observer_blueprint_configs(observer_blueprint: str, count: int) -> list[tuple[str, str]]:
    if count <= 0:
        return []
    if observer_blueprint != "random":
        return [observer_blueprint_config(observer_blueprint)] * count

    base_configs = [
        (DELIVERYBOT_ID, "deliverybot"),
        (HUMANOID_ID, "humanoid"),
    ]
    configs = [base_configs[index % len(base_configs)] for index in range(count)]
    random.shuffle(configs)
    return configs


def choose_custom_walker_anchors(
    candidates,
    *,
    max_anchor_pairs: int,
    observer_blueprint: str,
    blocked_actor_ids=(),
):
    if not candidates:
        raise RuntimeError("No Scenic support actors were found for anchor-based spawn.")
    if max_anchor_pairs <= 0:
        return []

    selected_anchor_candidates = []
    selected_actor_ids = set(blocked_actor_ids)
    anchors = []
    capture_records = []

    for candidate in candidates:
        if len(selected_anchor_candidates) >= max_anchor_pairs:
            break
        if candidate["actor_id"] in selected_actor_ids:
            continue
        selected_anchor_candidates.append(candidate)
        selected_actor_ids.add(candidate["actor_id"])

    observer_configs = observer_blueprint_configs(
        observer_blueprint,
        len(selected_anchor_candidates),
    )
    for anchor_index, (candidate, observer_config) in enumerate(
        zip(selected_anchor_candidates, observer_configs),
        start=1,
    ):
        blueprint_id, role_label = observer_config
        anchors.append(
            CustomWalkerAnchor(
                blueprint_id=blueprint_id,
                track_label=f"{blueprint_id}:anchor{anchor_index}",
                actor_id=candidate["actor_id"],
                actor_type_id=candidate["type_id"],
                label=candidate.get(
                    "label",
                    f"scenic.{candidate['category']}:{candidate['actor_id']}",
                ),
                location=candidate["location"],
                anchor_index=anchor_index,
                observer_role=role_label,
                anchor_kind=candidate.get("anchor_kind", "actor"),
                member_actor_ids=tuple(candidate.get("member_actor_ids", ())),
                member_actor_snapshots=tuple(
                    candidate.get("member_actor_snapshots", ())
                ),
                dynamic_actor_location=bool(
                    candidate.get("dynamic_actor_location", True)
                ),
            )
        )
    return anchors


def serialize_anchor_assignments(anchors):
    assignments = []
    for anchor in anchors:
        assignments.append(
            {
                "blueprint_id": anchor.blueprint_id,
                "track_label": anchor.track_label,
                "anchor_index": anchor.anchor_index,
                "observer_role": anchor.observer_role,
                "anchor_kind": anchor.anchor_kind,
                "member_actor_ids": list(anchor.member_actor_ids),
                "member_actors": list(anchor.member_actor_snapshots),
                "member_count": len(anchor.member_actor_ids) or 1,
                "anchor_actor_id": anchor.actor_id,
                "anchor_type_id": anchor.actor_type_id,
                "anchor_label": anchor.label,
                "anchor_location": {
                    "x": float(anchor.location.x),
                    "y": float(anchor.location.y),
                    "z": float(anchor.location.z),
                },
            }
        )
    assignments.sort(key=lambda item: item["track_label"])
    return tuple(assignments)


def serialize_location(location) -> dict[str, float]:
    return {
        "x": float(location.x),
        "y": float(location.y),
        "z": float(location.z),
    }


def distance_between_serialized_locations(first, second) -> float:
    dx = float(first["x"]) - float(second["x"])
    dy = float(first["y"]) - float(second["y"])
    dz = float(first["z"]) - float(second["z"])
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def normalize_degrees(angle: float) -> float:
    return (float(angle) + 180.0) % 360.0 - 180.0


def is_live_actor(actor) -> bool:
    if actor is None:
        return False
    if not bool(getattr(actor, "is_alive", True)):
        return False
    try:
        transform = actor.get_transform()
        if is_all_zero_location(transform.location):
            return False
        return True
    except RuntimeError:
        return False


def expected_anchor_category(anchor: CustomWalkerAnchor) -> str | None:
    type_id = (anchor.actor_type_id or "").lower()
    if type_id.startswith("walker.pedestrian."):
        return "pedestrian"
    if type_id.startswith("static.prop."):
        return "prop"
    if type_id.startswith("vehicle.") and any(
        keyword in type_id for keyword in BICYCLE_KEYWORDS
    ):
        return "bicycle"
    if type_id.startswith("vehicle."):
        return "vehicle"
    return None


def anchor_role_names(anchor: CustomWalkerAnchor) -> set[str]:
    role_names = set()
    for snapshot in getattr(anchor, "member_actor_snapshots", ()) or ():
        if not isinstance(snapshot, dict):
            continue
        role_name = snapshot.get("role_name")
        if isinstance(role_name, str) and role_name:
            role_names.add(role_name)
    return role_names


def resolve_live_anchor_actor(
    world,
    anchor: CustomWalkerAnchor,
    *,
    allow_fallback: bool = True,
):
    direct_actor = world.get_actor(anchor.actor_id)
    if is_live_actor(direct_actor):
        return direct_actor
    if not allow_fallback:
        return None

    expected_category = expected_anchor_category(anchor)
    role_names = anchor_role_names(anchor)
    best_actor = None
    best_distance = float("inf")
    try:
        actors = world.get_actors()
    except RuntimeError:
        return None

    for actor in actors:
        if not is_live_actor(actor):
            continue
        if actor.type_id in {DELIVERYBOT_ID, HUMANOID_ID}:
            continue
        if actor.type_id == "controller.ai.walker":
            continue
        if role_names and actor.attributes.get("role_name") not in role_names:
            continue
        classified = classify_anchor_category(actor)
        if classified is None:
            continue
        category, _priority = classified
        if expected_category is not None and category != expected_category:
            continue
        try:
            actor_location = actor.get_transform().location
        except RuntimeError:
            continue
        if is_all_zero_location(actor_location):
            continue
        distance = distance_between(actor_location, anchor.location)
        if (
            distance < best_distance - 0.25
            or (
                abs(distance - best_distance) <= 0.25
                and best_actor is not None
                and int(actor.id) > int(best_actor.id)
            )
            or best_actor is None
        ):
            best_actor = actor
            best_distance = distance

    max_distance = 80.0 if expected_category in {"pedestrian", "bicycle"} else 8.0
    if best_actor is not None and best_distance <= max_distance:
        return best_actor
    return None


def resolve_anchor_location(
    world,
    anchor: CustomWalkerAnchor,
    *,
    anchor_actor=None,
    allow_fallback: bool = True,
):
    if not getattr(anchor, "dynamic_actor_location", True):
        return anchor.location
    if anchor_actor is None:
        anchor_actor = resolve_live_anchor_actor(
            world,
            anchor,
            allow_fallback=allow_fallback,
        )
    if is_live_actor(anchor_actor):
        try:
            location = anchor_actor.get_transform().location
            if not is_all_zero_location(location):
                return location
        except RuntimeError:
            pass
    return anchor.location


def build_observer_metrics(world, spawned_walkers, *, detected_ego=None):
    metrics = []
    ego_location = detected_ego["location"] if detected_ego is not None else None

    for spawned_walker in spawned_walkers:
        metric = {
            "track_label": spawned_walker.track_label,
            "walker_actor_id": spawned_walker.walker.id,
            "blueprint_id": spawned_walker.spec.blueprint_id,
            "attached_sensor_count": len(spawned_walker.sensors),
        }
        location = try_get_actor_location(spawned_walker.walker)
        if location is None:
            metric["status"] = "missing_observer_location"
            metrics.append(metric)
            continue

        metric["observer_location"] = serialize_location(location)
        metric["on_sidewalk"] = bool(
            is_sidewalk_location(
                world,
                location,
                max_project_distance=0.5,
                strict=True,
            )
        )
        if spawned_walker.anchor is None:
            metric["status"] = "missing_anchor"
            metrics.append(metric)
            continue

        anchor = spawned_walker.anchor
        anchor_location = resolve_anchor_location(world, anchor)
        anchor_location_dict = serialize_location(anchor_location)
        observer_transform = spawned_walker.walker.get_transform()
        target_yaw = yaw_toward(location, anchor_location)
        facing_error = abs(normalize_degrees(observer_transform.rotation.yaw - target_yaw))
        observer_to_anchor_distance = distance_between(location, anchor_location)

        metric.update(
            {
                "status": "ok",
                "anchor_actor_id": anchor.actor_id,
                "anchor_index": anchor.anchor_index,
                "observer_role": anchor.observer_role,
                "anchor_kind": anchor.anchor_kind,
                "member_actor_ids": list(anchor.member_actor_ids),
                "member_count": len(anchor.member_actor_ids) or 1,
                "anchor_type_id": anchor.actor_type_id,
                "anchor_label": anchor.label,
                "anchor_location": anchor_location_dict,
                "observer_to_anchor_distance": round(observer_to_anchor_distance, 3),
                "observer_yaw_degrees": round(float(observer_transform.rotation.yaw), 3),
                "target_yaw_degrees": round(float(target_yaw), 3),
                "facing_error_degrees": round(float(facing_error), 3),
            }
        )
        if ego_location is not None:
            metric["ego_to_anchor_distance"] = round(
                distance_between_serialized_locations(ego_location, anchor_location_dict),
                3,
            )
        metrics.append(metric)

    return tuple(metrics)


def orient_observers_to_current_anchors(world, spawned_walkers):
    for spawned_walker in spawned_walkers:
        if spawned_walker.anchor is None:
            continue
        location = try_get_actor_location(spawned_walker.walker)
        if location is None:
            continue
        anchor_location = resolve_anchor_location(world, spawned_walker.anchor)
        try:
            observer_transform = spawned_walker.walker.get_transform()
            observer_transform.rotation.yaw = yaw_toward(location, anchor_location)
            spawned_walker.walker.set_transform(observer_transform)
        except RuntimeError:
            continue


def find_observer_metric_failures(
    observer_metrics,
    *,
    max_anchor_distance: float,
    max_facing_error_degrees: float,
):
    failures = []
    for metric in observer_metrics:
        track_label = metric.get("track_label", "<unknown>")
        if metric.get("status") != "ok":
            failures.append((track_label, str(metric.get("status"))))
            continue
        anchor_distance = float(metric.get("observer_to_anchor_distance", float("inf")))
        facing_error = float(metric.get("facing_error_degrees", float("inf")))
        metric_anchor = type(
            "MetricAnchor",
            (),
            {"anchor_kind": metric.get("anchor_kind")},
        )()
        radius_profile = observer_radius_profile_for_anchor(metric_anchor)
        effective_max_anchor_distance = max(
            float(max_anchor_distance),
            float(radius_profile["max_radius"]),
        )
        if anchor_distance > effective_max_anchor_distance:
            failures.append(
                (
                    track_label,
                    f"observer_to_anchor_distance={anchor_distance:.2f}m",
                )
            )
        if facing_error > max_facing_error_degrees:
            failures.append((track_label, f"facing_error={facing_error:.2f}deg"))
    return failures


def rotation_toward_location(camera_location, target_location):
    dx = float(target_location.x) - float(camera_location.x)
    dy = float(target_location.y) - float(camera_location.y)
    dz = float(target_location.z) - float(camera_location.z)
    horizontal = math.sqrt(dx * dx + dy * dy)
    return carla.Rotation(
        pitch=math.degrees(math.atan2(dz, horizontal)),
        yaw=math.degrees(math.atan2(dy, dx)),
        roll=0.0,
    )


def build_observer_scene_camera_transform(observer_location, anchor_location):
    dx = float(anchor_location.x) - float(observer_location.x)
    dy = float(anchor_location.y) - float(observer_location.y)
    distance = math.sqrt(dx * dx + dy * dy)
    if distance < 1e-3:
        ux, uy = 1.0, 0.0
    else:
        ux, uy = dx / distance, dy / distance
    px, py = -uy, ux
    back = min(6.0, max(2.0, distance * 0.25))
    side = min(18.0, max(8.0, distance * 1.05))
    height = min(10.0, max(5.5, 3.0 + distance * 0.35))
    z_base = max(float(observer_location.z), float(anchor_location.z))
    midpoint_x = (float(observer_location.x) + float(anchor_location.x)) * 0.5
    midpoint_y = (float(observer_location.y) + float(anchor_location.y)) * 0.5
    camera_location = carla.Location(
        x=midpoint_x - ux * back + px * side,
        y=midpoint_y - uy * back + py * side,
        z=z_base + height,
    )
    target_location = carla.Location(
        x=midpoint_x,
        y=midpoint_y,
        z=z_base + 1.0,
    )
    return carla.Transform(
        camera_location,
        rotation_toward_location(camera_location, target_location),
    )


def build_observer_close_camera_transform(observer_location, anchor_location):
    dx = float(anchor_location.x) - float(observer_location.x)
    dy = float(anchor_location.y) - float(observer_location.y)
    distance = math.sqrt(dx * dx + dy * dy)
    if distance < 1e-3:
        ux, uy = 1.0, 0.0
    else:
        ux, uy = dx / distance, dy / distance
    px, py = -uy, ux
    side = min(16.0, max(9.0, distance * 0.95))
    height = min(7.0, max(3.8, 2.8 + distance * 0.22))
    z_base = max(float(observer_location.z), float(anchor_location.z))
    midpoint_x = (float(observer_location.x) + float(anchor_location.x)) * 0.5
    midpoint_y = (float(observer_location.y) + float(anchor_location.y)) * 0.5
    camera_location = carla.Location(
        x=midpoint_x + px * side,
        y=midpoint_y + py * side,
        z=z_base + height,
    )
    target_location = carla.Location(
        x=midpoint_x,
        y=midpoint_y,
        z=z_base + 1.0,
    )
    return carla.Transform(
        camera_location,
        rotation_toward_location(camera_location, target_location),
    )


def build_observer_pair_orbit_camera_transform(
    observer_location,
    anchor_location,
    *,
    angle_degrees: float,
):
    midpoint_x = (float(observer_location.x) + float(anchor_location.x)) * 0.5
    midpoint_y = (float(observer_location.y) + float(anchor_location.y)) * 0.5
    dx = float(anchor_location.x) - float(observer_location.x)
    dy = float(anchor_location.y) - float(observer_location.y)
    distance = math.sqrt(dx * dx + dy * dy)
    orbit_radius = min(28.0, max(12.0, distance * 0.95))
    angle = math.radians(float(angle_degrees))
    z_base = max(float(observer_location.z), float(anchor_location.z))
    camera_location = carla.Location(
        x=midpoint_x + math.cos(angle) * orbit_radius,
        y=midpoint_y + math.sin(angle) * orbit_radius,
        z=z_base + min(9.0, max(5.0, 2.5 + distance * 0.2)),
    )
    target_location = carla.Location(
        x=midpoint_x,
        y=midpoint_y,
        z=z_base + 1.1,
    )
    return carla.Transform(
        camera_location,
        rotation_toward_location(camera_location, target_location),
    )


def build_observer_zoom_camera_transform(observer_location, anchor_location):
    camera_location = carla.Location(
        x=float(observer_location.x),
        y=float(observer_location.y),
        z=float(observer_location.z) + 1.5,
    )
    target_location = carla.Location(
        x=float(anchor_location.x),
        y=float(anchor_location.y),
        z=float(anchor_location.z) + 1.0,
    )
    return carla.Transform(
        camera_location,
        rotation_toward_location(camera_location, target_location),
    )


def build_observer_clear_zoom_camera_transform(observer_location, anchor_location):
    dx = float(anchor_location.x) - float(observer_location.x)
    dy = float(anchor_location.y) - float(observer_location.y)
    distance = math.sqrt(dx * dx + dy * dy)
    if distance < 1e-3:
        ux, uy = 1.0, 0.0
    else:
        ux, uy = dx / distance, dy / distance
    camera_location = carla.Location(
        x=float(observer_location.x) + ux * 1.8,
        y=float(observer_location.y) + uy * 1.8,
        z=float(observer_location.z) + 1.8,
    )
    target_location = carla.Location(
        x=float(anchor_location.x),
        y=float(anchor_location.y),
        z=float(anchor_location.z) + 1.15,
    )
    return carla.Transform(
        camera_location,
        rotation_toward_location(camera_location, target_location),
    )


def anchor_actor_target_height(type_id: str | None) -> float:
    if type_id and type_id.startswith("walker.pedestrian."):
        return 0.95
    if type_id and any(keyword in type_id for keyword in BICYCLE_KEYWORDS):
        return 0.8
    if type_id and type_id.startswith("static.prop."):
        return 0.55
    if type_id and type_id.startswith("vehicle."):
        return 1.15
    return 1.0


def build_anchor_actor_close_camera_transform(
    observer_location,
    anchor_location,
    *,
    type_id: str | None = None,
):
    dx = float(observer_location.x) - float(anchor_location.x)
    dy = float(observer_location.y) - float(anchor_location.y)
    distance = math.sqrt(dx * dx + dy * dy)
    if distance < 1e-3:
        ux, uy = -1.0, 0.0
    else:
        ux, uy = dx / distance, dy / distance

    camera_distance = 5.5
    target_height = anchor_actor_target_height(type_id)
    camera_location = carla.Location(
        x=float(anchor_location.x) + ux * camera_distance,
        y=float(anchor_location.y) + uy * camera_distance,
        z=float(anchor_location.z) + max(3.2, target_height + 2.35),
    )
    target_location = carla.Location(
        x=float(anchor_location.x),
        y=float(anchor_location.y),
        z=float(anchor_location.z) + target_height,
    )
    return carla.Transform(
        camera_location,
        rotation_toward_location(camera_location, target_location),
    )


def build_anchor_member_clean_close_camera_transform(
    observer_location,
    member_location,
    *,
    type_id: str | None = None,
):
    dx = float(observer_location.x) - float(member_location.x)
    dy = float(observer_location.y) - float(member_location.y)
    distance = math.sqrt(dx * dx + dy * dy)
    if distance < 1e-3:
        ux, uy = -1.0, 0.0
    else:
        ux, uy = dx / distance, dy / distance

    if type_id and type_id.startswith("vehicle."):
        camera_distance = 7.0
        camera_height = 1.8
    elif type_id and type_id.startswith("static.prop."):
        camera_distance = 3.4
        camera_height = 1.25
    else:
        camera_distance = 4.6
        camera_height = 1.6

    target_height = anchor_actor_target_height(type_id)
    camera_location = carla.Location(
        x=float(member_location.x) + ux * camera_distance,
        y=float(member_location.y) + uy * camera_distance,
        z=float(member_location.z) + camera_height,
    )
    target_location = carla.Location(
        x=float(member_location.x),
        y=float(member_location.y),
        z=float(member_location.z) + target_height,
    )
    return carla.Transform(
        camera_location,
        rotation_toward_location(camera_location, target_location),
    )


def build_anchor_members_clean_group_camera_transform(
    observer_location,
    anchor_location,
    member_locations,
    *,
    member_type_ids=(),
):
    points = list(member_locations) or [anchor_location]
    xs = [float(point.x) for point in points]
    ys = [float(point.y) for point in points]
    zs = [float(point.z) for point in points]
    center = carla.Location(
        x=sum(xs) / len(xs),
        y=sum(ys) / len(ys),
        z=sum(zs) / len(zs),
    )
    span = max(max(xs) - min(xs), max(ys) - min(ys), 0.8)

    dx = float(observer_location.x) - float(center.x)
    dy = float(observer_location.y) - float(center.y)
    distance = math.sqrt(dx * dx + dy * dy)
    if distance < 1e-3:
        ux, uy = -1.0, 0.0
    else:
        ux, uy = dx / distance, dy / distance

    has_vehicle = any(str(type_id).startswith("vehicle.") for type_id in member_type_ids)
    has_prop = any(str(type_id).startswith("static.prop.") for type_id in member_type_ids)
    if has_vehicle:
        camera_distance = min(26.0, max(10.0, span * 1.25 + 6.0))
        camera_height = min(8.0, max(3.4, span * 0.22 + 2.8))
        target_height = 1.15
    elif has_prop:
        camera_distance = min(10.0, max(4.8, span * 1.2 + 3.0))
        camera_height = min(4.0, max(2.0, span * 0.18 + 1.7))
        target_height = 0.55
    else:
        camera_distance = min(14.0, max(6.0, span * 1.2 + 4.0))
        camera_height = min(5.5, max(2.4, span * 0.2 + 2.0))
        target_height = 0.95

    camera_location = carla.Location(
        x=float(center.x) + ux * camera_distance,
        y=float(center.y) + uy * camera_distance,
        z=max(zs) + camera_height,
    )
    target_location = carla.Location(
        x=float(center.x),
        y=float(center.y),
        z=max(zs) + target_height,
    )
    return carla.Transform(
        camera_location,
        rotation_toward_location(camera_location, target_location),
    )


def build_anchor_actor_overhead_camera_transform(anchor_location):
    camera_location = carla.Location(
        x=float(anchor_location.x),
        y=float(anchor_location.y),
        z=float(anchor_location.z) + 6.0,
    )
    return carla.Transform(
        camera_location,
        carla.Rotation(pitch=-90.0, yaw=0.0, roll=0.0),
    )


def build_anchor_actor_orbit_camera_transform(
    anchor_location,
    *,
    angle_degrees: float,
    type_id: str | None = None,
):
    angle = math.radians(float(angle_degrees))
    camera_distance = 5.5
    target_height = anchor_actor_target_height(type_id)
    camera_location = carla.Location(
        x=float(anchor_location.x) + math.cos(angle) * camera_distance,
        y=float(anchor_location.y) + math.sin(angle) * camera_distance,
        z=float(anchor_location.z) + max(3.2, target_height + 2.35),
    )
    target_location = carla.Location(
        x=float(anchor_location.x),
        y=float(anchor_location.y),
        z=float(anchor_location.z) + target_height,
    )
    return carla.Transform(
        camera_location,
        rotation_toward_location(camera_location, target_location),
    )


def build_points_topdown_camera_transform(
    points,
    *,
    image_width=1280,
    image_height=720,
    fov=85,
    min_height=8.5,
    max_height=50.0,
    scale=1.35,
):
    xs = [float(point.x) for point in points]
    ys = [float(point.y) for point in points]
    zs = [float(point.z) for point in points]
    center_x = (min(xs) + max(xs)) * 0.5
    center_y = (min(ys) + max(ys)) * 0.5
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    horizontal_fov = math.radians(max(1.0, float(fov)))
    aspect = max(0.1, float(image_height) / max(1.0, float(image_width)))
    vertical_fov = 2.0 * math.atan(math.tan(horizontal_fov * 0.5) * aspect)
    height_for_x = span_x / max(0.1, 2.0 * math.tan(horizontal_fov * 0.5))
    height_for_y = span_y / max(0.1, 2.0 * math.tan(vertical_fov * 0.5))
    height = min(
        float(max_height),
        max(float(min_height), max(height_for_x, height_for_y) * float(scale)),
    )
    return carla.Transform(
        carla.Location(
            x=center_x,
            y=center_y,
            z=max(zs) + height,
        ),
        carla.Rotation(pitch=-90.0, yaw=0.0, roll=0.0),
    )


def build_points_oblique_overview_camera_transform(
    points,
    *,
    min_distance=24.0,
    max_distance=60.0,
    min_height=12.0,
    max_height=32.0,
):
    xs = [float(point.x) for point in points]
    ys = [float(point.y) for point in points]
    zs = [float(point.z) for point in points]
    center_x = (min(xs) + max(xs)) * 0.5
    center_y = (min(ys) + max(ys)) * 0.5
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    span = max(span_x, span_y, 1.0)
    distance = min(float(max_distance), max(float(min_distance), span * 0.85))
    height = min(float(max_height), max(float(min_height), 10.0 + span * 0.25))

    if span_x >= span_y:
        camera_location = carla.Location(
            x=center_x,
            y=min(ys) - distance,
            z=max(zs) + height,
        )
    else:
        camera_location = carla.Location(
            x=min(xs) - distance,
            y=center_y,
            z=max(zs) + height,
        )
    target_location = carla.Location(
        x=center_x,
        y=center_y,
        z=max(zs) + 1.2,
    )
    return carla.Transform(
        camera_location,
        rotation_toward_location(camera_location, target_location),
    )


def build_points_oblique_orbit_camera_transform(
    points,
    *,
    angle_degrees: float,
    min_radius=28.0,
    max_radius=80.0,
    min_height=24.0,
    max_height=58.0,
):
    xs = [float(point.x) for point in points]
    ys = [float(point.y) for point in points]
    zs = [float(point.z) for point in points]
    center_x = (min(xs) + max(xs)) * 0.5
    center_y = (min(ys) + max(ys)) * 0.5
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    radius = min(float(max_radius), max(float(min_radius), span * 1.15))
    height = min(float(max_height), max(float(min_height), 12.0 + span * 0.35))
    angle = math.radians(float(angle_degrees))
    camera_location = carla.Location(
        x=center_x + math.cos(angle) * radius,
        y=center_y + math.sin(angle) * radius,
        z=max(zs) + height,
    )
    target_location = carla.Location(
        x=center_x,
        y=center_y,
        z=max(zs) + 1.2,
    )
    return carla.Transform(
        camera_location,
        rotation_toward_location(camera_location, target_location),
    )


def build_observer_topdown_camera_transform(
    observer_location,
    anchor_location,
    member_locations=(),
    *,
    image_width=1280,
    image_height=720,
    fov=85,
    min_height=8.5,
    max_height=50.0,
    scale=1.35,
):
    return build_points_topdown_camera_transform(
        [observer_location, anchor_location, *member_locations],
        image_width=image_width,
        image_height=image_height,
        fov=fov,
        min_height=min_height,
        max_height=max_height,
        scale=scale,
    )


def configure_rgb_camera_blueprint(world, *, width, height, fov=80):
    blueprint = world.get_blueprint_library().find("sensor.camera.rgb")
    if blueprint.has_attribute("image_size_x"):
        blueprint.set_attribute("image_size_x", str(int(width)))
    if blueprint.has_attribute("image_size_y"):
        blueprint.set_attribute("image_size_y", str(int(height)))
    if blueprint.has_attribute("fov"):
        blueprint.set_attribute("fov", str(int(fov)))
    return blueprint


def draw_anchor_actor_debug_marker(world, anchor_actor):
    if not is_live_actor(anchor_actor):
        return
    try:
        actor_transform = anchor_actor.get_transform()
        actor_location = actor_transform.location
        bbox = anchor_actor.bounding_box
        yaw = math.radians(float(actor_transform.rotation.yaw))
        relative_x = float(bbox.location.x)
        relative_y = float(bbox.location.y)
        relative_z = float(bbox.location.z)
        world_bbox_location = carla.Location(
            x=(
                float(actor_location.x)
                + relative_x * math.cos(yaw)
                - relative_y * math.sin(yaw)
            ),
            y=(
                float(actor_location.y)
                + relative_x * math.sin(yaw)
                + relative_y * math.cos(yaw)
            ),
            z=float(actor_location.z) + relative_z,
        )
        world_bbox = carla.BoundingBox(
            world_bbox_location,
            bbox.extent,
        )
        world.debug.draw_box(
            world_bbox,
            actor_transform.rotation,
            thickness=0.08,
            color=carla.Color(255, 0, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
        world.debug.draw_line(
            carla.Location(actor_location.x, actor_location.y, actor_location.z + 0.05),
            carla.Location(actor_location.x, actor_location.y, actor_location.z + 3.2),
            thickness=0.14,
            color=carla.Color(255, 0, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
        world.debug.draw_string(
            carla.Location(actor_location.x, actor_location.y, actor_location.z + 3.35),
            "abnormal actor",
            draw_shadow=True,
            color=carla.Color(255, 0, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
        high_marker = carla.Location(
            actor_location.x,
            actor_location.y,
            actor_location.z + 7.0,
        )
        world.debug.draw_point(
            high_marker,
            size=0.45,
            color=carla.Color(255, 0, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
        world.debug.draw_line(
            carla.Location(high_marker.x - 1.4, high_marker.y, high_marker.z),
            carla.Location(high_marker.x + 1.4, high_marker.y, high_marker.z),
            thickness=0.16,
            color=carla.Color(255, 0, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
        world.debug.draw_line(
            carla.Location(high_marker.x, high_marker.y - 1.4, high_marker.z),
            carla.Location(high_marker.x, high_marker.y + 1.4, high_marker.z),
            thickness=0.16,
            color=carla.Color(255, 0, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
        world.debug.draw_line(
            carla.Location(actor_location.x, actor_location.y, actor_location.z + 0.05),
            high_marker,
            thickness=0.1,
            color=carla.Color(255, 0, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
    except RuntimeError:
        pass


def draw_member_location_debug_markers(world, member_locations):
    for index, location in enumerate(member_locations, start=1):
        marker_base = carla.Location(
            x=float(location.x),
            y=float(location.y),
            z=float(location.z) + 0.05,
        )
        marker_top = carla.Location(
            x=float(location.x),
            y=float(location.y),
            z=float(location.z) + 2.7,
        )
        try:
            world.debug.draw_line(
                marker_base,
                marker_top,
                thickness=0.12,
                color=carla.Color(255, 0, 0),
                life_time=DEBUG_MARKER_LIFETIME_SECONDS,
            )
            world.debug.draw_point(
                marker_top,
                size=0.12,
                color=carla.Color(255, 0, 0),
                life_time=DEBUG_MARKER_LIFETIME_SECONDS,
            )
            world.debug.draw_string(
                carla.Location(marker_top.x, marker_top.y, marker_top.z + 0.35),
                f"member:{index}",
                draw_shadow=True,
                color=carla.Color(255, 0, 0),
                life_time=DEBUG_MARKER_LIFETIME_SECONDS,
            )
        except RuntimeError:
            pass


def live_anchor_member_actors(world, anchor: CustomWalkerAnchor):
    actors = []
    used_actor_ids = set()
    for actor_id in getattr(anchor, "member_actor_ids", ()) or ():
        actor = None
        for persistent_ref in PERSISTENT_MEMBER_ACTOR_REFS:
            if getattr(persistent_ref, "id", None) == int(actor_id):
                actor = persistent_ref
                break
        if is_live_actor(actor):
            actors.append(actor)
            used_actor_ids.add(actor.id)
            continue
        try:
            actor = world.get_actor(int(actor_id))
        except (TypeError, ValueError, RuntimeError):
            actor = None
        if is_live_actor(actor):
            actors.append(actor)
            used_actor_ids.add(actor.id)

    snapshots = tuple(getattr(anchor, "member_actor_snapshots", ()) or ())
    if len(actors) >= len(snapshots):
        return actors

    try:
        world_actors = list(world.get_actors())
    except RuntimeError:
        return actors

    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        location_data = snapshot.get("location")
        if not isinstance(location_data, dict):
            continue
        try:
            snapshot_location = carla.Location(
                x=float(location_data["x"]),
                y=float(location_data["y"]),
                z=float(location_data.get("z", 0.0)),
            )
        except (KeyError, TypeError, ValueError):
            continue
        role_name = snapshot.get("role_name")
        best_actor = None
        best_distance = float("inf")
        for actor in world_actors:
            if not is_live_actor(actor) or actor.id in used_actor_ids:
                continue
            if not actor.type_id.startswith("walker.pedestrian."):
                continue
            if role_name and actor.attributes.get("role_name") != role_name:
                continue
            try:
                actor_location = actor.get_transform().location
            except RuntimeError:
                continue
            distance = distance_between(actor_location, snapshot_location)
            if distance < best_distance:
                best_actor = actor
                best_distance = distance
        if best_actor is not None and best_distance <= 2.5:
            actors.append(best_actor)
            used_actor_ids.add(best_actor.id)
    return actors


def snapshot_location(snapshot) -> carla.Location | None:
    if not isinstance(snapshot, dict):
        return None
    location_data = snapshot.get("location")
    if not isinstance(location_data, dict):
        return None
    try:
        return carla.Location(
            x=float(location_data["x"]),
            y=float(location_data["y"]),
            z=float(location_data.get("z", 0.0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def spawn_persistent_member_actor_from_snapshot(world, snapshot, spawn_location=None):
    location = snapshot_location(snapshot)
    type_id = snapshot.get("type_id") if isinstance(snapshot, dict) else None
    if location is None or not isinstance(type_id, str):
        return None
    is_pedestrian = type_id.startswith("walker.pedestrian.")
    is_static_prop = type_id.startswith("static.prop.")
    if not (is_pedestrian or is_static_prop):
        return None
    base_location = spawn_location or location
    if is_all_zero_location(base_location):
        return None
    try:
        blueprint = world.get_blueprint_library().find(type_id)
    except RuntimeError:
        return None
    if is_pedestrian and blueprint.has_attribute("is_invincible"):
        blueprint.set_attribute("is_invincible", "false")
    if is_pedestrian and blueprint.has_attribute("use_wheelchair"):
        blueprint.set_attribute("use_wheelchair", "false")
    if blueprint.has_attribute("role_name"):
        blueprint.set_attribute("role_name", "damos_persistent_member")
    xy_offsets = (
        (0.0, 0.0),
        (0.65, 0.0),
        (-0.65, 0.0),
        (0.0, 0.65),
        (0.0, -0.65),
        (0.65, 0.65),
        (-0.65, 0.65),
        (0.65, -0.65),
        (-0.65, -0.65),
        (1.0, 0.0),
        (-1.0, 0.0),
        (0.0, 1.0),
        (0.0, -1.0),
    )
    z_offsets = (0.0, 0.05, 0.1, 0.2, 0.5) if is_static_prop else (0.0, 0.2, 0.5, 1.0)
    for offset_x, offset_y in xy_offsets:
        for dz in z_offsets:
            spawn_transform = carla.Transform(
                carla.Location(
                    x=float(base_location.x) + offset_x,
                    y=float(base_location.y) + offset_y,
                    z=float(base_location.z) + dz,
                ),
                carla.Rotation(yaw=0.0),
            )
            try:
                actor = world.try_spawn_actor(blueprint, spawn_transform)
            except RuntimeError:
                actor = None
            if actor is not None:
                try:
                    actor.set_simulate_physics(False)
                    actor.set_transform(spawn_transform)
                except (AttributeError, RuntimeError):
                    pass
                actor_location = try_get_actor_location(actor)
                if actor_location is None or distance_between(
                    actor_location,
                    spawn_transform.location,
                ) > 2.0:
                    try:
                        actor.destroy()
                    except RuntimeError:
                        pass
                    continue

                PERSISTENT_MEMBER_ACTOR_REFS.append(actor)
                if is_pedestrian:
                    try:
                        controller_bp = world.get_blueprint_library().find(
                            "controller.ai.walker",
                        )
                        controller = world.spawn_actor(
                            controller_bp,
                            carla.Transform(),
                            actor,
                        )
                        controller.start()
                        controller.set_max_speed(0.1)
                        controller.stop()
                        PERSISTENT_MEMBER_ACTOR_REFS.append(controller)
                    except RuntimeError:
                        pass
                return actor
    return None


def serialize_member_actor_snapshot(actor, original_snapshot):
    try:
        location = actor.get_transform().location
    except RuntimeError:
        location = snapshot_location(original_snapshot) or carla.Location()
    if actor.type_id.startswith("static.prop."):
        category = "prop"
    elif actor.type_id.startswith("vehicle."):
        category = "vehicle"
    elif actor.type_id.startswith("walker.pedestrian."):
        category = "pedestrian"
    else:
        category = (
            original_snapshot.get("category", "actor")
            if isinstance(original_snapshot, dict)
            else "actor"
        )
    snapshot = dict(original_snapshot) if isinstance(original_snapshot, dict) else {}
    snapshot.update(
        {
            "actor_id": actor.id,
            "type_id": actor.type_id,
            "category": category,
            "location": serialize_location(location),
            "persistent_recreated": True,
        }
    )
    return snapshot


def destroy_original_cluster_member_actor(world, snapshot):
    if not isinstance(snapshot, dict):
        return False
    snapshot_type_id = snapshot.get("type_id")
    if not isinstance(snapshot_type_id, str):
        return False
    original_id = snapshot.get("actor_id")
    try:
        actor = world.get_actor(int(original_id))
    except (TypeError, ValueError, RuntimeError):
        return False
    if not is_live_actor(actor):
        return False
    if actor.type_id != snapshot_type_id:
        return False
    role_name = actor.attributes.get("role_name", "")
    if role_name and not str(role_name).startswith(DAMOS_SCENARIO_ROLE_PREFIX):
        return False
    if not (
        actor.type_id.startswith("walker.pedestrian.")
        or actor.type_id.startswith("static.prop.")
    ):
        return False
    try:
        actor.destroy()
        return True
    except RuntimeError:
        return False


def ensure_persistent_cluster_members(world, anchors):
    updated_anchors = []
    for anchor in anchors:
        anchor_kind = getattr(anchor, "anchor_kind", "")
        if anchor_kind != "pedestrian_cluster":
            updated_anchors.append(anchor)
            continue
        snapshots = tuple(getattr(anchor, "member_actor_snapshots", ()) or ())
        if not snapshots:
            updated_anchors.append(anchor)
            continue

        destroyed_original_count = 0
        for snapshot in snapshots:
            if destroy_original_cluster_member_actor(world, snapshot):
                destroyed_original_count += 1
        if destroyed_original_count:
            try:
                world.tick()
            except RuntimeError:
                pass

        live_by_original_id = {}
        member_actors = []
        member_snapshots = []
        crowd_spawn_locations = []
        if anchor_kind == "pedestrian_cluster":
            crowd_spawn_locations = sidewalk_spawn_locations_near_anchor(
                world,
                anchor.location,
                preferred_radius=1.5,
                min_radius=0.2,
                max_radius=6.0,
                avoid_locations=(),
                avoid_radius=0.8,
            )
        for snapshot in snapshots:
            actor = None
            original_id = snapshot.get("actor_id") if isinstance(snapshot, dict) else None
            try:
                actor = live_by_original_id.get(int(original_id))
            except (TypeError, ValueError):
                actor = None
            if actor is None:
                avoid_locations = [
                    existing.get_transform().location
                    for existing in member_actors
                    if is_live_actor(existing)
                ]
                spawn_locations = []
                snapshot_spawn_location = snapshot_location(snapshot)
                if snapshot_spawn_location is not None and not is_all_zero_location(
                    snapshot_spawn_location,
                ):
                    spawn_locations.append(snapshot_spawn_location)
                while crowd_spawn_locations and len(spawn_locations) < 4:
                    candidate_location = crowd_spawn_locations.pop(0)
                    if is_all_zero_location(candidate_location):
                        continue
                    if any(
                        distance_between(candidate_location, avoid_location) < 0.65
                        for avoid_location in avoid_locations
                    ):
                        continue
                    spawn_locations.append(candidate_location)
                if not spawn_locations and anchor_kind == "pedestrian_cluster":
                    fallback_location = pick_navigation_location_near_anchor(
                        world,
                        anchor.location,
                        preferred_radius=1.8,
                        min_radius=0.2,
                        max_radius=6.0,
                        sample_count=700,
                        avoid_locations=avoid_locations,
                        avoid_radius=0.65,
                        require_sidewalk=True,
                        sidewalk_project_distance=1.0,
                        strict_sidewalk=True,
                    )
                    if fallback_location is not None and not is_all_zero_location(
                        fallback_location,
                    ):
                        spawn_locations.append(fallback_location)
                for spawn_location in spawn_locations:
                    actor = spawn_persistent_member_actor_from_snapshot(
                        world,
                        snapshot,
                        spawn_location=spawn_location,
                    )
                    if actor is not None:
                        break
            if actor is None:
                member_snapshots.append(snapshot)
                continue
            member_actors.append(actor)
            member_snapshots.append(serialize_member_actor_snapshot(actor, snapshot))

        if not member_actors:
            updated_anchors.append(anchor)
            continue
        xs = [actor.get_transform().location.x for actor in member_actors]
        ys = [actor.get_transform().location.y for actor in member_actors]
        zs = [actor.get_transform().location.z for actor in member_actors]
        center = carla.Location(
            x=sum(xs) / len(xs),
            y=sum(ys) / len(ys),
            z=sum(zs) / len(zs),
        )
        representative = min(
            member_actors,
            key=lambda actor: distance_between(actor.get_transform().location, center),
        )
        updated_anchors.append(
            replace(
                anchor,
                actor_id=representative.id,
                actor_type_id=representative.type_id,
                location=center,
                member_actor_ids=tuple(actor.id for actor in member_actors),
                member_actor_snapshots=tuple(member_snapshots),
                dynamic_actor_location=False,
            )
        )
    return tuple(updated_anchors)


def ensure_persistent_members_for_spawned_walkers(world, spawned_walkers):
    anchors = [
        spawned_walker.anchor
        for spawned_walker in spawned_walkers
        if spawned_walker.anchor is not None
    ]
    updated_anchors = ensure_persistent_cluster_members(world, anchors)
    for spawned_walker, updated_anchor in zip(spawned_walkers, updated_anchors):
        if spawned_walker.anchor is not None:
            spawned_walker.anchor = updated_anchor
    return tuple(spawned_walkers)


def draw_observer_capture_markers(
    world,
    observer_location,
    anchor_location,
    *,
    role,
    anchor_actor=None,
    member_locations=(),
):
    observer_marker = carla.Location(
        x=float(observer_location.x),
        y=float(observer_location.y),
        z=float(observer_location.z) + 1.2,
    )
    anchor_marker = carla.Location(
        x=float(anchor_location.x),
        y=float(anchor_location.y),
        z=float(anchor_location.z) + 1.2,
    )
    try:
        world.debug.draw_point(
            observer_marker,
            size=0.08,
            color=carla.Color(0, 220, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
        world.debug.draw_point(
            anchor_marker,
            size=0.08,
            color=carla.Color(255, 0, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
        world.debug.draw_line(
            observer_marker,
            anchor_marker,
            thickness=0.12,
            color=carla.Color(0, 0, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
        world.debug.draw_string(
            carla.Location(observer_marker.x, observer_marker.y, observer_marker.z + 0.55),
            f"observer:{role}",
            draw_shadow=True,
            color=carla.Color(0, 220, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
        world.debug.draw_string(
            carla.Location(anchor_marker.x, anchor_marker.y, anchor_marker.z + 0.55),
            "anchor",
            draw_shadow=True,
            color=carla.Color(255, 0, 0),
            life_time=DEBUG_MARKER_LIFETIME_SECONDS,
        )
        draw_anchor_actor_debug_marker(world, anchor_actor)
        draw_member_location_debug_markers(world, member_locations)
    except RuntimeError:
        pass


def is_actor_anchor(anchor: CustomWalkerAnchor) -> bool:
    return (
        anchor.anchor_kind == "actor"
        or len(anchor.member_actor_ids) == 1
        or bool(getattr(anchor, "dynamic_actor_location", False))
    )


def carla_rgb_image_to_array(image):
    array = np.frombuffer(image.raw_data, dtype=np.uint8)
    array = array.reshape((image.height, image.width, 4))
    return array[:, :, :3][:, :, ::-1].copy()


def save_rgb_array(path, rgb):
    path.parent.mkdir(parents=True, exist_ok=True)
    rgb = np.asarray(rgb, dtype=np.uint8)
    if not cv2.imwrite(str(path), rgb[:, :, ::-1]):
        raise RuntimeError(f"failed to write RGB image: {path}")


def save_processed_rgb_frame(image, path, image_processor):
    rgb = carla_rgb_image_to_array(image)
    processed = image_processor(rgb)
    processed = np.asarray(processed, dtype=np.uint8)
    if processed.shape != rgb.shape:
        raise RuntimeError(
            f"processed RGB frame has invalid shape {processed.shape}; expected {rgb.shape}"
        )
    save_rgb_array(path, processed)


def resolve_ego_front_camera_fault(requested_fault: str) -> str:
    if requested_fault == "random":
        return random.choice(EGO_FRONT_RANDOM_STILL_FAULTS)
    return requested_fault


def build_ego_front_camera_fault_state(fault: str) -> dict[str, object]:
    state: dict[str, object] = {"fault": fault}
    if fault == "blur":
        min_kernel = 15
        max_kernel = 91
        if min_kernel % 2 == 0:
            min_kernel += 1
        if max_kernel % 2 == 0:
            max_kernel -= 1
        state["kernel_size"] = random.choice(list(range(min_kernel, max_kernel + 1, 2)))
        state["sigma"] = 0
    elif fault == "occlusion":
        state["min_w_ratio"] = 0.15
        state["max_w_ratio"] = 0.35
        state["min_h_ratio"] = 0.15
        state["max_h_ratio"] = 0.35
    elif fault == "color_failure":
        channel = random.choice((0, 1, 2))
        state["failed_channel"] = channel
        state["failed_channel_name"] = ("RED", "GREEN", "BLUE")[channel]
    elif fault == "shaking":
        state["max_shift"] = 35
        state["dx"] = random.randint(-35, 35)
        state["dy"] = random.randint(-35, 35)
    elif fault == "freeze_cycle":
        state["note"] = "single-frame capture stores the frozen frame itself"
    elif fault == "blackout":
        state["note"] = "all pixels set to black"
    return state


def apply_ego_front_camera_fault(rgb, fault_state: dict[str, object]):
    fault = str(fault_state.get("fault") or "none")
    if fault == "none" or fault == "misalignment":
        return rgb
    if fault == "blackout":
        return np.zeros_like(rgb)
    if fault == "blur":
        kernel_size = int(fault_state.get("kernel_size") or 51)
        if kernel_size % 2 == 0:
            kernel_size += 1
        sigma = float(fault_state.get("sigma") or 0)
        return cv2.GaussianBlur(rgb, (kernel_size, kernel_size), sigma)
    if fault == "occlusion":
        out = rgb.copy()
        height, width, _ = out.shape
        min_w = int(width * float(fault_state.get("min_w_ratio") or 0.15))
        max_w = int(width * float(fault_state.get("max_w_ratio") or 0.35))
        min_h = int(height * float(fault_state.get("min_h_ratio") or 0.15))
        max_h = int(height * float(fault_state.get("max_h_ratio") or 0.35))
        if "box" not in fault_state:
            box_w = random.randint(max(1, min_w), max(1, max_w))
            box_h = random.randint(max(1, min_h), max(1, max_h))
            x1 = random.randint(0, max(0, width - box_w))
            y1 = random.randint(0, max(0, height - box_h))
            fault_state["box"] = (x1, y1, x1 + box_w, y1 + box_h)
        x1, y1, x2, y2 = fault_state["box"]
        out[int(y1):int(y2), int(x1):int(x2), :] = 0
        return out
    if fault == "color_failure":
        out = rgb.copy()
        out[:, :, int(fault_state.get("failed_channel") or 0)] = 0
        return out
    if fault == "shaking":
        height, width, _ = rgb.shape
        dx = int(fault_state.get("dx") or 0)
        dy = int(fault_state.get("dy") or 0)
        matrix = np.float32([[1, 0, dx], [0, 1, dy]])
        return cv2.warpAffine(rgb, matrix, (width, height), borderMode=cv2.BORDER_REFLECT)
    if fault == "freeze_cycle":
        return rgb
    return rgb


def ego_front_camera_fault_misalignment_transform(transform, fault_state):
    pitch = random.choice((-12.0, -10.0, 10.0, 12.0))
    yaw = random.choice((-25.0, -20.0, 20.0, 25.0))
    roll = random.choice((-10.0, -7.0, 7.0, 10.0))
    fault_state["pitch_delta"] = pitch
    fault_state["yaw_delta"] = yaw
    fault_state["roll_delta"] = roll
    return carla.Transform(
        carla.Location(
            x=float(transform.location.x),
            y=float(transform.location.y),
            z=float(transform.location.z),
        ),
        carla.Rotation(
            pitch=float(transform.rotation.pitch) + pitch,
            yaw=float(transform.rotation.yaw) + yaw,
            roll=float(transform.rotation.roll) + roll,
        ),
    )


def apply_ego_front_camera_misalignment_to_specs(camera_specs, fault_state):
    updated_specs = []
    for spec in camera_specs:
        if spec.name == "cam_front":
            updated_specs.append(
                replace(
                    spec,
                    transform=ego_front_camera_fault_misalignment_transform(
                        spec.transform,
                        fault_state,
                    ),
                )
            )
        else:
            updated_specs.append(spec)
    return tuple(updated_specs)


def is_ego_front_camera_capture(attachment) -> bool:
    return (
        str(attachment.get("track_label") or "") == "ego"
        and str(attachment.get("sensor_name") or "") == "cam_front"
    )


def capture_rgb_sensor_frame(
    world,
    sensor,
    path,
    *,
    timeout_seconds,
    warmup_frames=2,
    image_processor=None,
):
    frames = queue.Queue(maxsize=1)
    skipped_frames = 0

    def on_image(image):
        try:
            frames.put_nowait(image)
        except queue.Full:
            pass

    path.parent.mkdir(parents=True, exist_ok=True)
    sensor.listen(on_image)
    deadline = time.time() + timeout_seconds
    try:
        while time.time() < deadline:
            try:
                image = frames.get(timeout=0.25)
            except queue.Empty:
                try:
                    world.wait_for_tick(0.5)
                except RuntimeError:
                    time.sleep(0.1)
                continue
            if skipped_frames < int(warmup_frames):
                skipped_frames += 1
                continue
            if image_processor is None:
                image.save_to_disk(str(path))
            else:
                save_processed_rgb_frame(image, path, image_processor)
            return True
    finally:
        try:
            sensor.stop()
        except RuntimeError:
            pass
    return False


def wait_for_capture_settle(world, *, ticks=3):
    for _ in range(max(0, int(ticks))):
        try:
            world.wait_for_tick(1.0)
        except RuntimeError:
            time.sleep(0.2)


def wait_for_debug_markers_to_expire(world):
    deadline = time.monotonic() + DEBUG_MARKER_LIFETIME_SECONDS + 0.3
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            world.wait_for_tick(max(0.05, min(0.5, remaining)))
        except RuntimeError:
            time.sleep(min(0.2, remaining))


def find_observer_camera_sensor(world, observer_camera_attachments, track_label, sensor_name):
    for attachment in observer_camera_attachments:
        if attachment.get("track_label") != track_label:
            continue
        if attachment.get("sensor_name") != sensor_name:
            continue
        sensor_actor_id = attachment.get("sensor_actor_id")
        if sensor_actor_id is None:
            continue
        sensor = world.get_actor(int(sensor_actor_id))
        if sensor is not None:
            return sensor
    return None


def save_actor_camera_captures(
    world,
    actor_camera_attachments,
    config: ScenicCustomWalkerConfig,
    *,
    scenario_labels=(),
    ego_front_camera_fault_state=None,
):
    if not actor_camera_attachments:
        return tuple()

    config.report_dir.mkdir(parents=True, exist_ok=True)
    map_name = safe_map_name(world).split("/")[-1]
    scenario_fragment = "base"
    if scenario_labels:
        scenario_fragment = sanitize_filename_fragment("_".join(scenario_labels[:4]))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    capture_dir = config.report_dir / (
        f"actor_camera_captures_{sanitize_filename_fragment(map_name)}_"
        f"{scenario_fragment}_port{config.port}_{stamp}"
    )
    capture_dir.mkdir(parents=True, exist_ok=True)

    wait_for_capture_settle(world, ticks=3)

    captures = []
    ego_front_camera_fault_state = dict(ego_front_camera_fault_state or {"fault": "none"})
    for attachment in actor_camera_attachments:
        track_label = str(attachment.get("track_label") or "actor")
        sensor_name = str(attachment.get("sensor_name") or "camera")
        actor_role = str(attachment.get("actor_role") or "")
        actor_id = attachment.get("actor_id") or attachment.get("walker_actor_id")
        sensor_actor_id = attachment.get("sensor_actor_id")
        is_fault_target = is_ego_front_camera_capture(attachment)
        applied_fault = (
            str(ego_front_camera_fault_state.get("fault") or "none")
            if is_fault_target
            else "none"
        )
        path = capture_dir / (
            f"{sanitize_filename_fragment(track_label)}_"
            f"actor{actor_id}_{sanitize_filename_fragment(sensor_name)}.png"
        )

        record = {
            "capture_type": "attached_actor_camera",
            "track_label": track_label,
            "actor_role": actor_role,
            "actor_id": actor_id,
            "actor_type_id": attachment.get("actor_type_id"),
            "sensor_actor_id": sensor_actor_id,
            "sensor_name": sensor_name,
            "blueprint_id": attachment.get("blueprint_id"),
            "relative_transform": attachment.get("relative_transform"),
            "path": str(path),
            "ego_front_camera_fault_target": is_fault_target,
            "ego_front_camera_fault": applied_fault,
        }
        if is_fault_target and applied_fault != "none":
            record["ego_front_camera_fault_details"] = ego_front_camera_fault_state

        if sensor_actor_id is None:
            record["status"] = "missing_sensor_actor_id"
            captures.append(record)
            continue

        sensor = world.get_actor(int(sensor_actor_id))
        if sensor is None:
            record["status"] = "missing_sensor"
            captures.append(record)
            continue

        try:
            image_processor = None
            if is_fault_target and applied_fault not in {"none", "misalignment"}:
                image_processor = lambda rgb, state=ego_front_camera_fault_state: (
                    apply_ego_front_camera_fault(rgb, state)
                )
            ok = capture_rgb_sensor_frame(
                world,
                sensor,
                path,
                timeout_seconds=config.capture_timeout_seconds,
                image_processor=image_processor,
            )
        except RuntimeError as exc:
            record["status"] = "error"
            record["error"] = str(exc)
        else:
            record["status"] = "saved" if ok else "timeout"
        captures.append(record)

    manifest_path = capture_dir / "actor_camera_captures.json"
    manifest = {
        "map_name": map_name,
        "port": config.port,
        "scenario_labels": list(scenario_labels or ()),
        "capture_count": len(captures),
        "saved_count": sum(1 for capture in captures if capture.get("status") == "saved"),
        "ego_front_camera_fault": ego_front_camera_fault_state,
        "captures": captures,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return tuple(captures)


def capture_rgb_sensor_sequence(
    world,
    sensor,
    *,
    frame_count,
    timeout_seconds,
    warmup_frames=2,
):
    frames = queue.Queue(maxsize=max(4, int(frame_count) + int(warmup_frames) + 2))
    skipped_frames = 0
    captured = []

    def on_image(image):
        try:
            frames.put_nowait((int(image.frame), carla_rgb_image_to_array(image)))
        except queue.Full:
            pass

    sensor.listen(on_image)
    deadline = time.time() + float(timeout_seconds)
    try:
        while time.time() < deadline and len(captured) < int(frame_count):
            try:
                frame = frames.get(timeout=0.25)
            except queue.Empty:
                try:
                    world.wait_for_tick(0.5)
                except RuntimeError:
                    time.sleep(0.1)
                continue
            if skipped_frames < int(warmup_frames):
                skipped_frames += 1
                continue
            captured.append(frame)
    finally:
        try:
            sensor.stop()
        except RuntimeError:
            pass
    return tuple(captured)


def capture_lidar_sensor_frame(world, sensor, *, timeout_seconds, warmup_frames=1):
    frames = queue.Queue(maxsize=4)
    skipped_frames = 0

    def on_lidar(point_cloud):
        try:
            points = np.frombuffer(point_cloud.raw_data, dtype=np.float32).reshape((-1, 4))
            frames.put_nowait(points.copy())
        except queue.Full:
            pass

    sensor.listen(on_lidar)
    deadline = time.time() + float(timeout_seconds)
    try:
        while time.time() < deadline:
            try:
                points = frames.get(timeout=0.25)
            except queue.Empty:
                try:
                    world.wait_for_tick(0.5)
                except RuntimeError:
                    time.sleep(0.1)
                continue
            if skipped_frames < int(warmup_frames):
                skipped_frames += 1
                continue
            return points
    finally:
        try:
            sensor.stop()
        except RuntimeError:
            pass
    return np.empty((0, 4), dtype=np.float32)


def configure_lidar_blueprint(world):
    blueprint = world.get_blueprint_library().find("sensor.lidar.ray_cast")
    attributes = {
        "channels": "32",
        "range": "70",
        "points_per_second": "64000",
        "rotation_frequency": "10",
        "upper_fov": "12",
        "lower_fov": "-30",
        "sensor_tick": "0.05",
    }
    for key, value in attributes.items():
        if blueprint.has_attribute(key):
            blueprint.set_attribute(key, value)
    return blueprint


def spawn_temp_ego_front_camera(world, ego, config: ScenicCustomWalkerConfig):
    blueprint = configure_rgb_camera_blueprint(
        world,
        width=config.capture_image_width,
        height=config.capture_image_height,
        fov=90,
    )
    transform = carla.Transform(
        carla.Location(x=1.60, y=0.0, z=1.70),
        carla.Rotation(pitch=-5.0, yaw=0.0, roll=0.0),
    )
    return world.spawn_actor(
        blueprint,
        transform,
        attach_to=ego,
        attachment_type=carla.AttachmentType.Rigid,
    )


def spawn_temp_ego_lidar(world, ego):
    transform = carla.Transform(
        carla.Location(x=0.0, y=0.0, z=2.10),
        carla.Rotation(pitch=0.0, yaw=0.0, roll=0.0),
    )
    return world.spawn_actor(
        configure_lidar_blueprint(world),
        transform,
        attach_to=ego,
        attachment_type=carla.AttachmentType.Rigid,
    )


def resize_rgb_for_panel(rgb, *, width=640):
    rgb = np.asarray(rgb, dtype=np.uint8)
    if rgb.size == 0:
        return np.zeros((360, int(width), 3), dtype=np.uint8)
    height = max(1, int(round(rgb.shape[0] * (float(width) / max(1, rgb.shape[1])))))
    return cv2.resize(rgb, (int(width), height), interpolation=cv2.INTER_AREA)


def make_labeled_panel(rgb, label, *, width=640, accent=(40, 40, 40)):
    image = resize_rgb_for_panel(rgb, width=width)
    header = np.full((56, image.shape[1], 3), accent, dtype=np.uint8)
    cv2.putText(
        header,
        str(label)[:72],
        (18, 36),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return np.vstack([header, image])


def save_rgb_panel(path, panels):
    rendered = [make_labeled_panel(rgb, label) for label, rgb in panels]
    if not rendered:
        return False
    max_height = max(panel.shape[0] for panel in rendered)
    padded = []
    for panel in rendered:
        if panel.shape[0] < max_height:
            pad = np.full(
                (max_height - panel.shape[0], panel.shape[1], 3),
                245,
                dtype=np.uint8,
            )
            panel = np.vstack([panel, pad])
        padded.append(panel)
    save_rgb_array(path, np.hstack(padded))
    return True


def add_fault_overlay(rgb, text):
    overlay = np.asarray(rgb, dtype=np.uint8).copy()
    if overlay.size == 0:
        return overlay
    cv2.rectangle(overlay, (0, 0), (overlay.shape[1], 64), (0, 0, 170), -1)
    cv2.putText(
        overlay,
        text,
        (24, 42),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.05,
        (255, 255, 255),
        3,
        cv2.LINE_AA,
    )
    return overlay


def make_camera_blackout_frame(rgb):
    blackout = np.zeros_like(np.asarray(rgb, dtype=np.uint8))
    if blackout.size:
        cv2.putText(
            blackout,
            "NO CAMERA SIGNAL",
            (max(24, blackout.shape[1] // 12), max(72, blackout.shape[0] // 2)),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.1,
            (180, 180, 180),
            3,
            cv2.LINE_AA,
        )
    return blackout


def make_camera_degradation_frame(rgb):
    degraded = np.asarray(rgb, dtype=np.uint8).copy()
    if degraded.size == 0:
        return degraded
    degraded = cv2.GaussianBlur(degraded, (31, 31), 0)
    degraded[:, :, 1] = (degraded[:, :, 1].astype(np.float32) * 0.45).astype(np.uint8)
    height, width = degraded.shape[:2]
    x1 = int(width * 0.56)
    y1 = int(height * 0.16)
    x2 = int(width * 0.86)
    y2 = int(height * 0.54)
    cv2.rectangle(degraded, (x1, y1), (x2, y2), (12, 12, 12), -1)
    noise = np.random.default_rng().normal(0.0, 8.0, size=degraded.shape)
    degraded = np.clip(degraded.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return degraded


def make_camera_misalignment_frame(rgb):
    image = np.asarray(rgb, dtype=np.uint8)
    if image.size == 0:
        return image.copy()
    height, width = image.shape[:2]
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, -9.0, 1.0)
    matrix[0, 2] += width * 0.12
    matrix[1, 2] -= height * 0.05
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )


def make_lidar_noise_points(points, *, noise_std=0.35):
    points = np.asarray(points, dtype=np.float32).reshape((-1, 4))
    if points.size == 0:
        return points.copy(), {"gaussian_noise_std_m": noise_std, "input_points": 0}
    noisy = points.copy()
    noisy[:, 0:3] += np.random.default_rng().normal(
        0.0,
        float(noise_std),
        size=noisy[:, 0:3].shape,
    )
    return noisy, {
        "gaussian_noise_std_m": float(noise_std),
        "input_points": int(points.shape[0]),
        "output_points": int(noisy.shape[0]),
    }


def make_lidar_dropout_points(points, *, dropout_ratio=0.45):
    points = np.asarray(points, dtype=np.float32).reshape((-1, 4))
    if points.size == 0:
        return points.copy(), {"dropout_ratio": dropout_ratio, "input_points": 0}
    rng = np.random.default_rng()
    keep_mask = rng.random(points.shape[0]) > float(dropout_ratio)
    dropped = points[keep_mask].copy()
    return dropped, {
        "dropout_ratio": float(dropout_ratio),
        "input_points": int(points.shape[0]),
        "output_points": int(dropped.shape[0]),
    }


def make_lidar_outlier_points(points):
    points = np.asarray(points, dtype=np.float32).reshape((-1, 4))
    rng = np.random.default_rng()
    outlier_count = 30 if points.size == 0 else min(180, max(45, points.shape[0] // 180))
    outliers = np.empty((outlier_count, 4), dtype=np.float32)
    outliers[:, 0] = rng.uniform(-65.0, 65.0, size=outlier_count)
    outliers[:, 1] = rng.uniform(-65.0, 65.0, size=outlier_count)
    outliers[:, 2] = rng.uniform(-1.0, 3.5, size=outlier_count)
    outliers[:, 3] = rng.uniform(0.0, 1.0, size=outlier_count)
    outlier_points = np.vstack([points, outliers]) if points.size else outliers
    return outlier_points, {
        "outlier_count": int(outlier_count),
        "input_points": int(points.shape[0]),
        "output_points": int(outlier_points.shape[0]),
    }


def make_noisy_lidar_points(points):
    points = np.asarray(points, dtype=np.float32)
    if points.size == 0:
        return points.reshape((0, 4)), {
            "gaussian_noise_std_m": 0.35,
            "dropout_ratio": 0.25,
            "outlier_count": 0,
        }

    rng = np.random.default_rng()
    keep_mask = rng.random(points.shape[0]) > 0.25
    noisy = points[keep_mask].copy()
    if noisy.size:
        noisy[:, 0:3] += rng.normal(0.0, 0.35, size=noisy[:, 0:3].shape)

    outlier_count = min(160, max(30, points.shape[0] // 250))
    outliers = np.empty((outlier_count, 4), dtype=np.float32)
    outliers[:, 0] = rng.uniform(-45.0, 45.0, size=outlier_count)
    outliers[:, 1] = rng.uniform(-45.0, 45.0, size=outlier_count)
    outliers[:, 2] = rng.uniform(-1.0, 3.0, size=outlier_count)
    outliers[:, 3] = rng.uniform(0.0, 1.0, size=outlier_count)
    noisy = np.vstack([noisy, outliers])
    return noisy, {
        "gaussian_noise_std_m": 0.35,
        "dropout_ratio": 0.25,
        "outlier_count": int(outlier_count),
        "input_points": int(points.shape[0]),
        "output_points": int(noisy.shape[0]),
    }


def save_lidar_scatter(path, points, *, title):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    points = np.asarray(points, dtype=np.float32).reshape((-1, 4))
    fig, ax = plt.subplots(figsize=(8.0, 8.0), dpi=150)
    ax.set_facecolor("#111111")
    fig.patch.set_facecolor("white")
    ax.set_title(title)
    ax.set_xlabel("ego-local x forward (m)")
    ax.set_ylabel("ego-local y left/right (m)")
    ax.axhline(0.0, color="#999999", linewidth=0.7, alpha=0.6)
    ax.axvline(0.0, color="#999999", linewidth=0.7, alpha=0.6)
    if points.size:
        if points.shape[0] > 30000:
            sample_index = np.random.default_rng().choice(
                points.shape[0], 30000, replace=False
            )
            points = points[sample_index]
        distance = np.linalg.norm(points[:, 0:2], axis=1)
        ax.scatter(
            points[:, 0],
            points[:, 1],
            c=distance,
            s=1.0,
            cmap="viridis",
            alpha=0.85,
            linewidths=0,
        )
        limit = float(np.percentile(distance, 99)) + 5.0 if distance.size else 35.0
        limit = max(20.0, min(75.0, limit))
    else:
        ax.text(
            0.5,
            0.5,
            "No LiDAR points captured",
            transform=ax.transAxes,
            ha="center",
            va="center",
        )
        limit = 35.0
    ax.set_xlim(-limit, limit)
    ax.set_ylim(-limit, limit)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(color="#555555", alpha=0.25)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def save_contact_sheet(path, image_records, *, title):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    existing_records = [
        record for record in image_records if pathlib.Path(record.get("path", "")).exists()
    ]
    if not existing_records:
        return False

    columns = 2
    rows = int(math.ceil(len(existing_records) / float(columns)))
    fig, axes = plt.subplots(rows, columns, figsize=(13.0, 5.2 * rows), dpi=150)
    axes = np.asarray(axes).reshape(-1)
    for axis, record in zip(axes, existing_records):
        image = cv2.imread(str(record["path"]), cv2.IMREAD_COLOR)
        if image is None:
            axis.axis("off")
            continue
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        axis.imshow(image)
        axis.set_title(str(record.get("label") or pathlib.Path(record["path"]).stem))
        axis.axis("off")
    for axis in axes[len(existing_records) :]:
        axis.axis("off")
    fig.suptitle(title, fontsize=18)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path)
    plt.close(fig)
    return True


def fit_rgb_to_canvas(rgb, *, width, height, fill=(248, 248, 248)):
    image = np.asarray(rgb, dtype=np.uint8)
    canvas = np.full((int(height), int(width), 3), fill, dtype=np.uint8)
    if image.size == 0:
        return canvas
    src_height, src_width = image.shape[:2]
    scale = min(float(width) / max(1, src_width), float(height) / max(1, src_height))
    new_width = max(1, int(round(src_width * scale)))
    new_height = max(1, int(round(src_height * scale)))
    resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)
    x0 = (int(width) - new_width) // 2
    y0 = (int(height) - new_height) // 2
    canvas[y0 : y0 + new_height, x0 : x0 + new_width] = resized
    return canvas


def make_fault_grid_tile(record, label, *, tile_width=560, tile_height=390):
    header_height = 54
    content_height = int(tile_height) - header_height
    tile = np.full((int(tile_height), int(tile_width), 3), 245, dtype=np.uint8)
    tile[:header_height, :, :] = np.array([34, 38, 45], dtype=np.uint8)
    cv2.putText(
        tile,
        label,
        (18, 36),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    image = None
    if record is not None and pathlib.Path(str(record.get("path", ""))).exists():
        bgr = cv2.imread(str(record["path"]), cv2.IMREAD_COLOR)
        if bgr is not None:
            image = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if image is None:
        image = np.full((content_height, int(tile_width), 3), 238, dtype=np.uint8)
        cv2.putText(
            image,
            "capture missing",
            (32, max(56, content_height // 2)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (80, 80, 80),
            2,
            cv2.LINE_AA,
        )

    tile[header_height:, :, :] = fit_rgb_to_canvas(
        image,
        width=int(tile_width),
        height=content_height,
    )
    return tile


def save_ego_fault_3x3_grid(path, records):
    by_type = {
        str(record.get("fault_type")): record
        for record in records
        if record.get("status") == "saved"
    }
    ordered_faults = (
        ("camera_blackout", "1. Camera blackout"),
        ("camera_degradation", "2. Camera degradation"),
        ("camera_misalignment", "3. Camera misalignment"),
        ("lidar_noise", "4. LiDAR noise"),
        ("lidar_dropout", "5. LiDAR dropout"),
        ("lidar_outlier", "6. LiDAR outlier"),
        ("sensor_delay", "7. Sensor delay"),
        ("module_stop", "8. Module stop/freeze"),
        ("stale_perception_output", "9. Stale output"),
    )
    tiles = [
        make_fault_grid_tile(by_type.get(fault_type), label)
        for fault_type, label in ordered_faults
    ]
    rows = [np.hstack(tiles[index : index + 3]) for index in range(0, 9, 3)]
    grid = np.vstack(rows)
    save_rgb_array(path, grid)
    return True


def save_ego_lidar_noise_report(world, ego, capture_dir, config: ScenicCustomWalkerConfig):
    records = []
    lidar = None
    try:
        lidar = spawn_temp_ego_lidar(world, ego)
        points = capture_lidar_sensor_frame(
            world,
            lidar,
            timeout_seconds=max(config.capture_timeout_seconds, 8.0),
            warmup_frames=1,
        )
        clean_path = capture_dir / "01_ego_lidar_clean.png"
        save_lidar_scatter(clean_path, points, title="Ego LiDAR clean point cloud")
        records.append(
            {
                "fault_type": "lidar_clean",
                "label": "Ego LiDAR clean",
                "path": str(clean_path),
                "point_count": int(points.shape[0]),
                "status": "saved",
            }
        )

        noise_points, noise_details = make_lidar_noise_points(points)
        noise_path = capture_dir / "fault_04_lidar_noise.png"
        save_lidar_scatter(
            noise_path,
            noise_points,
            title="Ego LiDAR noise",
        )
        records.append(
            {
                "fault_type": "lidar_noise",
                "label": "LiDAR noise",
                "path": str(noise_path),
                "details": noise_details,
                "status": "saved",
            }
        )

        dropout_points, dropout_details = make_lidar_dropout_points(points)
        dropout_path = capture_dir / "fault_05_lidar_dropout.png"
        save_lidar_scatter(
            dropout_path,
            dropout_points,
            title="Ego LiDAR dropout",
        )
        records.append(
            {
                "fault_type": "lidar_dropout",
                "label": "LiDAR dropout",
                "path": str(dropout_path),
                "details": dropout_details,
                "status": "saved",
            }
        )

        outlier_points, outlier_details = make_lidar_outlier_points(points)
        outlier_path = capture_dir / "fault_06_lidar_outlier.png"
        save_lidar_scatter(
            outlier_path,
            outlier_points,
            title="Ego LiDAR outlier",
        )
        records.append(
            {
                "fault_type": "lidar_outlier",
                "label": "LiDAR outlier",
                "path": str(outlier_path),
                "details": outlier_details,
                "status": "saved",
            }
        )

        combined_points, combined_details = make_noisy_lidar_points(points)
        combined_path = capture_dir / "02_ego_lidar_noise_dropout_outliers.png"
        save_lidar_scatter(
            combined_path,
            combined_points,
            title="Ego LiDAR noise + dropout + outliers",
        )
        records.append(
            {
                "fault_type": "lidar_noise_dropout_outliers",
                "label": "Ego LiDAR noise/dropout/outliers",
                "path": str(combined_path),
                "details": combined_details,
                "status": "saved",
            }
        )
    except RuntimeError as exc:
        records.append(
            {
                "fault_type": "lidar_noise",
                "status": "error",
                "error": str(exc),
            }
        )
    finally:
        if lidar is not None:
            try:
                lidar.destroy()
            except RuntimeError:
                pass
    return tuple(records)


def save_ego_camera_temporal_fault_report(
    world,
    ego,
    actor_camera_attachments,
    capture_dir,
    config: ScenicCustomWalkerConfig,
):
    records = []
    temp_camera = None
    front_sensor = find_observer_camera_sensor(
        world,
        actor_camera_attachments,
        "ego",
        "cam_front",
    )
    try:
        if front_sensor is None:
            temp_camera = spawn_temp_ego_front_camera(world, ego, config)
            front_sensor = temp_camera

        frames = capture_rgb_sensor_sequence(
            world,
            front_sensor,
            frame_count=9,
            timeout_seconds=max(config.capture_timeout_seconds, 8.0),
            warmup_frames=2,
        )
        if not frames:
            records.append(
                {
                    "fault_type": "ego_rgb_temporal",
                    "status": "timeout",
                    "error": "no ego front camera frames captured",
                }
            )
            return tuple(records)

        current_frame_no, current_rgb = frames[-1]
        normal_path = capture_dir / "03_ego_front_camera_current.png"
        save_rgb_array(normal_path, current_rgb)
        records.append(
            {
                "fault_type": "ego_front_camera_current",
                "label": "Ego front RGB current",
                "path": str(normal_path),
                "frame": int(current_frame_no),
                "status": "saved",
            }
        )

        camera_fault_specs = (
            (
                "camera_blackout",
                "Camera blackout",
                capture_dir / "fault_01_camera_blackout.png",
                make_camera_blackout_frame(current_rgb),
            ),
            (
                "camera_degradation",
                "Camera degradation",
                capture_dir / "fault_02_camera_degradation.png",
                make_camera_degradation_frame(current_rgb),
            ),
            (
                "camera_misalignment",
                "Camera misalignment",
                capture_dir / "fault_03_camera_misalignment.png",
                make_camera_misalignment_frame(current_rgb),
            ),
        )
        for fault_type, label, path, image in camera_fault_specs:
            save_rgb_array(path, image)
            records.append(
                {
                    "fault_type": fault_type,
                    "label": label,
                    "path": str(path),
                    "source_frame": int(current_frame_no),
                    "status": "saved",
                }
            )

        delay_index = max(0, len(frames) - 6)
        delayed_frame_no, delayed_rgb = frames[delay_index]
        delay_path = capture_dir / "04_ego_sensor_delay_5_frames.png"
        save_rgb_panel(
            delay_path,
            (
                (f"Delayed output frame {delayed_frame_no}", delayed_rgb),
                (f"Current input frame {current_frame_no}", current_rgb),
            ),
        )
        records.append(
            {
                "fault_type": "sensor_delay",
                "label": "Ego RGB sensor delay",
                "path": str(delay_path),
                "delay_frames": int(current_frame_no - delayed_frame_no),
                "status": "saved",
            }
        )

        freeze_frame_no, freeze_rgb = frames[min(2, len(frames) - 1)]
        stopped_output = add_fault_overlay(freeze_rgb, "MODULE STOP: FROZEN OUTPUT")
        module_path = capture_dir / "05_ego_module_stop_freeze.png"
        save_rgb_panel(
            module_path,
            (
                (f"Live input frame {current_frame_no}", current_rgb),
                (f"Frozen module output frame {freeze_frame_no}", stopped_output),
            ),
        )
        records.append(
            {
                "fault_type": "module_stop",
                "label": "Ego module stop/freeze",
                "path": str(module_path),
                "frozen_frame": int(freeze_frame_no),
                "current_frame": int(current_frame_no),
                "status": "saved",
            }
        )

        stale_output = add_fault_overlay(delayed_rgb, "STALE OUTPUT: OLD PERCEPTION")
        stale_path = capture_dir / "fault_09_stale_perception_output.png"
        save_rgb_panel(
            stale_path,
            (
                (f"Current input frame {current_frame_no}", current_rgb),
                (f"Stale output frame {delayed_frame_no}", stale_output),
            ),
        )
        records.append(
            {
                "fault_type": "stale_perception_output",
                "label": "Stale perception output",
                "path": str(stale_path),
                "stale_frame": int(delayed_frame_no),
                "current_frame": int(current_frame_no),
                "status": "saved",
            }
        )
    except RuntimeError as exc:
        records.append(
            {
                "fault_type": "ego_rgb_temporal",
                "status": "error",
                "error": str(exc),
            }
        )
    finally:
        if temp_camera is not None:
            try:
                temp_camera.destroy()
            except RuntimeError:
                pass
    return tuple(records)


def save_ego_fault_report(
    world,
    ego,
    actor_camera_attachments,
    config: ScenicCustomWalkerConfig,
    *,
    scenario_labels=(),
):
    config.report_dir.mkdir(parents=True, exist_ok=True)
    map_name = safe_map_name(world).split("/")[-1]
    scenario_fragment = "base"
    if scenario_labels:
        scenario_fragment = sanitize_filename_fragment("_".join(scenario_labels[:4]))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    capture_dir = config.report_dir / (
        f"ego_fault_report_{sanitize_filename_fragment(map_name)}_"
        f"{scenario_fragment}_port{config.port}_{stamp}"
    )
    capture_dir.mkdir(parents=True, exist_ok=True)

    wait_for_capture_settle(world, ticks=3)
    records = []
    records.extend(save_ego_lidar_noise_report(world, ego, capture_dir, config))
    records.extend(
        save_ego_camera_temporal_fault_report(
            world,
            ego,
            actor_camera_attachments,
            capture_dir,
            config,
        )
    )

    grid_3x3_path = capture_dir / "ego_fault_report_3x3.png"
    grid_3x3_saved = save_ego_fault_3x3_grid(grid_3x3_path, records)
    if grid_3x3_saved:
        records.append(
            {
                "fault_type": "fault_3x3_grid",
                "label": "Ego HW/SW fault 3x3 grid",
                "path": str(grid_3x3_path),
                "status": "saved",
            }
        )

    contact_path = capture_dir / "ego_fault_report_contact_sheet.png"
    contact_saved = save_contact_sheet(
        contact_path,
        records,
        title="DAMOS ego-centric sensor/module faults",
    )
    if contact_saved:
        records.append(
            {
                "fault_type": "contact_sheet",
                "label": "Ego fault report contact sheet",
                "path": str(contact_path),
                "status": "saved",
            }
        )

    manifest_path = capture_dir / "ego_fault_report.json"
    manifest = {
        "map_name": map_name,
        "port": config.port,
        "scenario_labels": list(scenario_labels or ()),
        "ego_actor_id": int(ego.id),
        "ego_type_id": str(ego.type_id),
        "capture_dir": str(capture_dir),
        "records": records,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return tuple(records)


def save_immediate_topdown_overview_capture(
    world,
    spawned_walkers,
    config: ScenicCustomWalkerConfig,
    *,
    scenario_labels=(),
):
    if not spawned_walkers:
        return tuple()

    config.report_dir.mkdir(parents=True, exist_ok=True)
    map_name = safe_map_name(world).split("/")[-1]
    scenario_fragment = "base"
    if scenario_labels:
        scenario_fragment = sanitize_filename_fragment("_".join(scenario_labels[:4]))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    capture_dir = config.report_dir / (
        f"observer_scene_captures_{sanitize_filename_fragment(map_name)}_"
        f"{scenario_fragment}_overview_port{config.port}_{stamp}"
    )
    capture_dir.mkdir(parents=True, exist_ok=True)

    points = []
    anchors = []
    capture_records = []
    for spawned_walker in spawned_walkers:
        observer_location = try_get_actor_location(spawned_walker.walker)
        if observer_location is None or spawned_walker.anchor is None:
            continue
        anchor = spawned_walker.anchor
        actor_anchor = is_actor_anchor(anchor)
        anchor_actor = (
            resolve_live_anchor_actor(world, anchor, allow_fallback=False)
            if actor_anchor
            else None
        )
        anchor_location = resolve_anchor_location(
            world,
            anchor,
            anchor_actor=anchor_actor,
            allow_fallback=False,
        )
        role = anchor.observer_role or spawned_walker.spec.blueprint_id
        member_locations = anchor_member_locations(anchor)
        draw_observer_capture_markers(
            world,
            observer_location,
            anchor_location,
            role=role,
            anchor_actor=anchor_actor,
            member_locations=member_locations,
        )
        points.extend([observer_location, anchor_location])
        points.extend(member_locations)
        anchor_summary = {
            "track_label": spawned_walker.track_label,
            "observer_role": role,
            "anchor_index": anchor.anchor_index,
            "anchor_label": anchor.label,
            "anchor_actor_id": anchor.actor_id,
            "resolved_anchor_actor_id": (
                anchor_actor.id if is_live_actor(anchor_actor) else None
            ),
            "anchor_type_id": anchor.actor_type_id,
            "resolved_anchor_type_id": (
                anchor_actor.type_id
                if is_live_actor(anchor_actor)
                else anchor.actor_type_id
            ),
            "anchor_actor_present": bool(
                not actor_anchor or is_live_actor(anchor_actor)
            ),
            "observer_location": serialize_location(observer_location),
            "anchor_location": serialize_location(anchor_location),
        }
        anchors.append(anchor_summary)
        capture_records.append(
            {
                "summary": anchor_summary,
                "anchor": anchor,
                "observer_location": observer_location,
                "anchor_location": anchor_location,
                "member_locations": member_locations,
                "anchor_actor": anchor_actor,
            }
        )

    if not points:
        return tuple()

    topdown_path = capture_dir / "all_anchors_topdown_immediate.png"
    topdown_bp = configure_rgb_camera_blueprint(
        world,
        width=config.capture_image_width,
        height=config.capture_image_height,
        fov=85,
    )
    side_bp = configure_rgb_camera_blueprint(
        world,
        width=config.capture_image_width,
        height=config.capture_image_height,
        fov=72,
    )
    actor_close_bp = configure_rgb_camera_blueprint(
        world,
        width=config.capture_image_width,
        height=config.capture_image_height,
        fov=58,
    )

    priority_captures = []
    wait_for_debug_markers_to_expire(world)
    for record in capture_records:
        anchor = record["anchor"]
        member_actors = live_anchor_member_actors(world, anchor)
        member_actor_records = []
        for member_actor in member_actors:
            member_location = try_get_actor_location(member_actor)
            if member_location is None:
                continue
            member_actor_records.append(
                {
                    "actor": member_actor,
                    "location": member_location,
                    "type_id": member_actor.type_id,
                }
            )
        if not member_actor_records:
            continue

        summary = record["summary"]
        anchor_index = summary["anchor_index"]
        role = summary["observer_role"]
        prefix = f"anchor{anchor_index}_{sanitize_filename_fragment(role)}"
        member_group_path = (
            capture_dir / f"{prefix}_anchor_members_priority_clean_group_immediate.png"
        )
        member_group_transform = build_anchor_members_clean_group_camera_transform(
            record["observer_location"],
            record["anchor_location"],
            [member_record["location"] for member_record in member_actor_records],
            member_type_ids=[
                member_record["type_id"] for member_record in member_actor_records
            ],
        )
        member_group_sensor = world.spawn_actor(side_bp, member_group_transform)
        try:
            member_group_ok = capture_rgb_sensor_frame(
                world,
                member_group_sensor,
                member_group_path,
                timeout_seconds=config.capture_timeout_seconds,
            )
        finally:
            try:
                member_group_sensor.destroy()
            except RuntimeError:
                pass
        priority_captures.append(
            {
                "capture_type": "anchor_members_priority_clean_group_immediate",
                **summary,
                "anchor_kind": anchor.anchor_kind,
                "member_actor_ids": [
                    member_record["actor"].id for member_record in member_actor_records
                ],
                "member_type_ids": [
                    member_record["type_id"] for member_record in member_actor_records
                ],
                "member_count": len(member_actor_records),
                "path": str(member_group_path),
                "status": "saved" if member_group_ok else "timeout",
                "camera_transform": serialize_transform(member_group_transform),
            }
        )

        for member_index, member_record in enumerate(member_actor_records, start=1):
            member_actor = member_record["actor"]
            member_location = member_record["location"]
            member_type_id = member_record["type_id"]
            member_path = (
                capture_dir
                / (
                    f"{prefix}_member{member_index}_actor{member_actor.id}"
                    "_priority_clean_close_immediate.png"
                )
            )
            member_transform = build_anchor_member_clean_close_camera_transform(
                record["observer_location"],
                member_location,
                type_id=member_type_id,
            )
            member_sensor = world.spawn_actor(actor_close_bp, member_transform)
            try:
                member_ok = capture_rgb_sensor_frame(
                    world,
                    member_sensor,
                    member_path,
                    timeout_seconds=config.capture_timeout_seconds,
                )
            finally:
                try:
                    member_sensor.destroy()
                except RuntimeError:
                    pass
            priority_captures.append(
                {
                    "capture_type": "anchor_member_priority_clean_close_immediate",
                    **summary,
                    "anchor_kind": anchor.anchor_kind,
                    "member_index": member_index,
                    "member_actor_id": member_actor.id,
                    "member_type_id": member_type_id,
                    "member_location": serialize_location(member_location),
                    "path": str(member_path),
                    "status": "saved" if member_ok else "timeout",
                    "camera_transform": serialize_transform(member_transform),
                }
            )

    topdown_transform = build_points_topdown_camera_transform(
        points,
        image_width=config.capture_image_width,
        image_height=config.capture_image_height,
        fov=85,
        max_height=140.0,
    )
    topdown_sensor = world.spawn_actor(topdown_bp, topdown_transform)
    try:
        topdown_ok = capture_rgb_sensor_frame(
            world,
            topdown_sensor,
            topdown_path,
            timeout_seconds=config.capture_timeout_seconds,
        )
    finally:
        try:
            topdown_sensor.destroy()
        except RuntimeError:
            pass

    captures = [
        *priority_captures,
        {
            "capture_type": "external_scene_topdown_overview_immediate",
            "path": str(topdown_path),
            "status": "saved" if topdown_ok else "timeout",
            "anchor_count": len(anchors),
            "anchors": anchors,
            "camera_transform": serialize_transform(topdown_transform),
        },
    ]

    oblique_path = capture_dir / "all_anchors_oblique_overview_immediate.png"
    oblique_transform = build_points_oblique_overview_camera_transform(points)
    for record in capture_records:
        draw_observer_capture_markers(
            world,
            record["observer_location"],
            record["anchor_location"],
            role=record["summary"]["observer_role"],
            anchor_actor=record["anchor_actor"],
            member_locations=record["member_locations"],
        )
    oblique_sensor = world.spawn_actor(side_bp, oblique_transform)
    try:
        oblique_ok = capture_rgb_sensor_frame(
            world,
            oblique_sensor,
            oblique_path,
            timeout_seconds=config.capture_timeout_seconds,
        )
    finally:
        try:
            oblique_sensor.destroy()
        except RuntimeError:
            pass
    captures.append(
        {
            "capture_type": "external_scene_oblique_overview_immediate",
            "path": str(oblique_path),
            "status": "saved" if oblique_ok else "timeout",
            "anchor_count": len(anchors),
            "anchors": anchors,
            "camera_transform": serialize_transform(oblique_transform),
        }
    )
    for orbit_name, angle_degrees in (
        ("east", 0.0),
        ("north", 90.0),
        ("west", 180.0),
        ("south", 270.0),
    ):
        orbit_path = capture_dir / f"all_anchors_oblique_orbit_{orbit_name}_immediate.png"
        orbit_transform = build_points_oblique_orbit_camera_transform(
            points,
            angle_degrees=angle_degrees,
        )
        for record in capture_records:
            draw_observer_capture_markers(
                world,
                record["observer_location"],
                record["anchor_location"],
                role=record["summary"]["observer_role"],
                anchor_actor=record["anchor_actor"],
                member_locations=record["member_locations"],
            )
        orbit_sensor = world.spawn_actor(side_bp, orbit_transform)
        try:
            orbit_ok = capture_rgb_sensor_frame(
                world,
                orbit_sensor,
                orbit_path,
                timeout_seconds=config.capture_timeout_seconds,
            )
        finally:
            try:
                orbit_sensor.destroy()
            except RuntimeError:
                pass
        captures.append(
            {
                "capture_type": "external_scene_oblique_orbit_overview_immediate",
                "orbit_view": orbit_name,
                "path": str(orbit_path),
                "status": "saved" if orbit_ok else "timeout",
                "anchor_count": len(anchors),
                "anchors": anchors,
                "camera_transform": serialize_transform(orbit_transform),
            }
        )

    for record in capture_records:
        summary = record["summary"]
        anchor_index = summary["anchor_index"]
        role = summary["observer_role"]
        draw_observer_capture_markers(
            world,
            record["observer_location"],
            record["anchor_location"],
            role=role,
            anchor_actor=record["anchor_actor"],
            member_locations=record["member_locations"],
        )
        topdown_path = (
            capture_dir
            / f"anchor{anchor_index}_{sanitize_filename_fragment(role)}_topdown_immediate.png"
        )
        topdown_transform = build_observer_topdown_camera_transform(
            record["observer_location"],
            record["anchor_location"],
            record["member_locations"],
            image_width=config.capture_image_width,
            image_height=config.capture_image_height,
            fov=85,
            min_height=18.0,
            max_height=32.0,
            scale=1.6,
        )
        topdown_sensor = world.spawn_actor(topdown_bp, topdown_transform)
        try:
            topdown_ok = capture_rgb_sensor_frame(
                world,
                topdown_sensor,
                topdown_path,
                timeout_seconds=config.capture_timeout_seconds,
            )
        finally:
            try:
                topdown_sensor.destroy()
            except RuntimeError:
                pass
        captures.append(
            {
                "capture_type": "external_scene_topdown_anchor_immediate",
                **summary,
                "path": str(topdown_path),
                "status": "saved" if topdown_ok else "timeout",
                "camera_transform": serialize_transform(topdown_transform),
            }
        )
        side_path = (
            capture_dir
            / f"anchor{anchor_index}_{sanitize_filename_fragment(role)}_side_immediate.png"
        )
        side_transform = build_observer_scene_camera_transform(
            record["observer_location"],
            record["anchor_location"],
        )
        side_sensor = world.spawn_actor(side_bp, side_transform)
        try:
            side_ok = capture_rgb_sensor_frame(
                world,
                side_sensor,
                side_path,
                timeout_seconds=config.capture_timeout_seconds,
            )
        finally:
            try:
                side_sensor.destroy()
            except RuntimeError:
                pass
        captures.append(
            {
                "capture_type": "external_scene_side_immediate",
                **summary,
                "path": str(side_path),
                "status": "saved" if side_ok else "timeout",
                "camera_transform": serialize_transform(side_transform),
            }
        )
        group_points = [
            record["observer_location"],
            record["anchor_location"],
            *record["member_locations"],
        ]
        for orbit_name, angle_degrees in (
            ("east", 0.0),
            ("north", 90.0),
            ("west", 180.0),
            ("south", 270.0),
        ):
            group_orbit_path = (
                capture_dir
                / (
                    f"anchor{anchor_index}_{sanitize_filename_fragment(role)}"
                    f"_group_oblique_orbit_{orbit_name}_immediate.png"
                )
            )
            group_orbit_transform = build_points_oblique_orbit_camera_transform(
                group_points,
                angle_degrees=angle_degrees,
                min_radius=12.0,
                max_radius=24.0,
                min_height=8.0,
                max_height=18.0,
            )
            draw_observer_capture_markers(
                world,
                record["observer_location"],
                record["anchor_location"],
                role=role,
                anchor_actor=record["anchor_actor"],
                member_locations=record["member_locations"],
            )
            group_orbit_sensor = world.spawn_actor(side_bp, group_orbit_transform)
            try:
                group_orbit_ok = capture_rgb_sensor_frame(
                    world,
                    group_orbit_sensor,
                    group_orbit_path,
                    timeout_seconds=config.capture_timeout_seconds,
                )
            finally:
                try:
                    group_orbit_sensor.destroy()
                except RuntimeError:
                    pass
            captures.append(
                {
                    "capture_type": "external_group_oblique_orbit_immediate",
                    "orbit_view": orbit_name,
                    **summary,
                    "path": str(group_orbit_path),
                    "status": "saved" if group_orbit_ok else "timeout",
                    "camera_transform": serialize_transform(group_orbit_transform),
                }
            )
        for orbit_name, angle_degrees in (
            ("east", 0.0),
            ("north", 90.0),
            ("west", 180.0),
            ("south", 270.0),
        ):
            orbit_path = (
                capture_dir
                / (
                    f"anchor{anchor_index}_{sanitize_filename_fragment(role)}"
                    f"_side_orbit_{orbit_name}_immediate.png"
                )
            )
            orbit_transform = build_observer_pair_orbit_camera_transform(
                record["observer_location"],
                record["anchor_location"],
                angle_degrees=angle_degrees,
            )
            orbit_sensor = world.spawn_actor(side_bp, orbit_transform)
            try:
                orbit_ok = capture_rgb_sensor_frame(
                    world,
                    orbit_sensor,
                    orbit_path,
                    timeout_seconds=config.capture_timeout_seconds,
                )
            finally:
                try:
                    orbit_sensor.destroy()
                except RuntimeError:
                    pass
            captures.append(
                {
                    "capture_type": "external_scene_side_orbit_immediate",
                    "orbit_view": orbit_name,
                    **summary,
                    "path": str(orbit_path),
                    "status": "saved" if orbit_ok else "timeout",
                    "camera_transform": serialize_transform(orbit_transform),
                }
            )
        if summary.get("anchor_actor_present"):
            actor_path = (
                capture_dir
                / f"anchor{anchor_index}_{sanitize_filename_fragment(role)}_anchor_actor_side_immediate.png"
            )
            actor_transform = build_anchor_actor_close_camera_transform(
                record["observer_location"],
                record["anchor_location"],
                type_id=summary.get("anchor_type_id"),
            )
            actor_sensor = world.spawn_actor(actor_close_bp, actor_transform)
            try:
                actor_ok = capture_rgb_sensor_frame(
                    world,
                    actor_sensor,
                    actor_path,
                    timeout_seconds=config.capture_timeout_seconds,
                )
            finally:
                try:
                    actor_sensor.destroy()
                except RuntimeError:
                    pass
            captures.append(
                {
                    "capture_type": "anchor_actor_side_immediate",
                    **summary,
                    "path": str(actor_path),
                    "status": "saved" if actor_ok else "timeout",
                    "camera_transform": serialize_transform(actor_transform),
                }
            )
        member_actors = live_anchor_member_actors(world, record["anchor"])
        role_names = anchor_role_names(record["anchor"])
        live_role_actor_ids = []
        try:
            for actor in world.get_actors():
                if (
                    is_live_actor(actor)
                    and actor.type_id.startswith("walker.pedestrian.")
                    and (
                        not role_names
                        or actor.attributes.get("role_name") in role_names
                    )
                ):
                    live_role_actor_ids.append(actor.id)
        except RuntimeError:
            live_role_actor_ids = []
        captures.append(
            {
                "capture_type": "cluster_member_actor_resolution_immediate",
                **summary,
                "requested_member_actor_ids": list(
                    getattr(record["anchor"], "member_actor_ids", ()) or ()
                ),
                "snapshot_count": len(
                    tuple(
                        getattr(record["anchor"], "member_actor_snapshots", ())
                        or ()
                    )
                ),
                "resolved_member_actor_ids": [actor.id for actor in member_actors],
                "live_role_actor_ids": sorted(live_role_actor_ids),
                "status": "saved",
            }
        )
        wait_for_debug_markers_to_expire(world)
        for member_index, member_actor in enumerate(member_actors, start=1):
            member_transform = member_actor.get_transform()
            member_location = member_transform.location
            member_path = (
                capture_dir
                / (
                    f"anchor{anchor_index}_{sanitize_filename_fragment(role)}"
                    f"_member{member_index}_actor{member_actor.id}_clean_close_immediate.png"
                )
            )
            member_close_transform = build_anchor_actor_close_camera_transform(
                record["observer_location"],
                member_location,
                type_id=member_actor.type_id,
            )
            member_sensor = world.spawn_actor(actor_close_bp, member_close_transform)
            try:
                member_ok = capture_rgb_sensor_frame(
                    world,
                    member_sensor,
                    member_path,
                    timeout_seconds=config.capture_timeout_seconds,
                )
            finally:
                try:
                    member_sensor.destroy()
                except RuntimeError:
                    pass
            captures.append(
                {
                    "capture_type": "cluster_member_actor_clean_close_immediate",
                    **summary,
                    "member_index": member_index,
                    "member_actor_id": member_actor.id,
                    "member_type_id": member_actor.type_id,
                    "member_location": serialize_location(member_location),
                    "path": str(member_path),
                    "status": "saved" if member_ok else "timeout",
                    "camera_transform": serialize_transform(member_close_transform),
                }
            )

    return tuple(captures)


def save_observer_scene_captures(
    world,
    spawned_walkers,
    observer_camera_attachments,
    config: ScenicCustomWalkerConfig,
    *,
    scenario_labels=(),
):
    if not spawned_walkers:
        return tuple()

    config.report_dir.mkdir(parents=True, exist_ok=True)
    map_name = safe_map_name(world).split("/")[-1]
    scenario_fragment = "base"
    if scenario_labels:
        scenario_fragment = sanitize_filename_fragment("_".join(scenario_labels[:4]))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    capture_dir = config.report_dir / (
        f"observer_scene_captures_{sanitize_filename_fragment(map_name)}_"
        f"{scenario_fragment}_port{config.port}_{stamp}"
    )
    capture_dir.mkdir(parents=True, exist_ok=True)

    captures = []
    scene_camera_bp = configure_rgb_camera_blueprint(
        world,
        width=config.capture_image_width,
        height=config.capture_image_height,
        fov=82,
    )
    close_camera_bp = configure_rgb_camera_blueprint(
        world,
        width=config.capture_image_width,
        height=config.capture_image_height,
        fov=62,
    )
    zoom_camera_bp = configure_rgb_camera_blueprint(
        world,
        width=config.capture_image_width,
        height=config.capture_image_height,
        fov=42,
    )
    clear_zoom_camera_bp = configure_rgb_camera_blueprint(
        world,
        width=config.capture_image_width,
        height=config.capture_image_height,
        fov=58,
    )
    anchor_actor_close_bp = configure_rgb_camera_blueprint(
        world,
        width=config.capture_image_width,
        height=config.capture_image_height,
        fov=48,
    )
    wait_for_capture_settle(world, ticks=3)

    for spawned_walker in spawned_walkers:
        wait_for_debug_markers_to_expire(world)
        observer_location = try_get_actor_location(spawned_walker.walker)
        if observer_location is None or spawned_walker.anchor is None:
            continue
        anchor = spawned_walker.anchor
        actor_anchor = is_actor_anchor(anchor)
        anchor_actor = (
            resolve_live_anchor_actor(world, anchor, allow_fallback=False)
            if actor_anchor
            else None
        )
        anchor_actor_present = not actor_anchor or is_live_actor(anchor_actor)
        anchor_location = resolve_anchor_location(
            world,
            anchor,
            anchor_actor=anchor_actor,
            allow_fallback=False,
        )
        resolved_anchor_actor_id = anchor_actor.id if is_live_actor(anchor_actor) else None
        resolved_anchor_type_id = (
            anchor_actor.type_id if is_live_actor(anchor_actor) else anchor.actor_type_id
        )
        observer_transform = spawned_walker.walker.get_transform()
        target_yaw = yaw_toward(observer_location, anchor_location)
        facing_error = abs(normalize_degrees(observer_transform.rotation.yaw - target_yaw))
        role = anchor.observer_role or spawned_walker.spec.blueprint_id
        anchor_index = anchor.anchor_index or len(captures) + 1
        prefix = f"anchor{anchor_index}_{sanitize_filename_fragment(role)}"
        member_locations = anchor_member_locations(anchor)
        member_actors = live_anchor_member_actors(world, anchor)
        member_actor_records = []
        for member_actor in member_actors:
            member_location = try_get_actor_location(member_actor)
            if member_location is None:
                continue
            member_actor_records.append(
                {
                    "actor": member_actor,
                    "location": member_location,
                    "type_id": member_actor.type_id,
                }
            )

        if actor_anchor and is_live_actor(anchor_actor):
            clean_actor_close_path = (
                capture_dir / f"{prefix}_anchor_actor_clean_close.png"
            )
            clean_actor_close_transform = build_anchor_actor_close_camera_transform(
                observer_location,
                anchor_location,
                type_id=anchor.actor_type_id,
            )
            clean_actor_close_sensor = world.spawn_actor(
                anchor_actor_close_bp,
                clean_actor_close_transform,
            )
            try:
                clean_actor_close_ok = capture_rgb_sensor_frame(
                    world,
                    clean_actor_close_sensor,
                    clean_actor_close_path,
                    timeout_seconds=config.capture_timeout_seconds,
                )
            finally:
                try:
                    clean_actor_close_sensor.destroy()
                except RuntimeError:
                    pass
            captures.append(
                {
                    "capture_type": "anchor_actor_clean_close",
                    "track_label": spawned_walker.track_label,
                    "observer_role": role,
                    "anchor_index": anchor_index,
                    "anchor_label": anchor.label,
                    "anchor_actor_id": anchor.actor_id,
                    "resolved_anchor_actor_id": resolved_anchor_actor_id,
                    "anchor_type_id": anchor.actor_type_id,
                    "resolved_anchor_type_id": resolved_anchor_type_id,
                    "anchor_actor_present": True,
                    "path": str(clean_actor_close_path),
                    "status": "saved" if clean_actor_close_ok else "timeout",
                    "observer_location": serialize_location(observer_location),
                    "anchor_location": serialize_location(anchor_location),
                    "facing_error_degrees": round(float(facing_error), 3),
                    "camera_transform": serialize_transform(clean_actor_close_transform),
                }
            )
            overhead_actor_path = (
                capture_dir / f"{prefix}_anchor_actor_overhead_close.png"
            )
            overhead_actor_transform = build_anchor_actor_overhead_camera_transform(
                anchor_location
            )
            overhead_actor_sensor = world.spawn_actor(
                anchor_actor_close_bp,
                overhead_actor_transform,
            )
            try:
                overhead_actor_ok = capture_rgb_sensor_frame(
                    world,
                    overhead_actor_sensor,
                    overhead_actor_path,
                    timeout_seconds=config.capture_timeout_seconds,
                )
            finally:
                try:
                    overhead_actor_sensor.destroy()
                except RuntimeError:
                    pass
            captures.append(
                {
                    "capture_type": "anchor_actor_overhead_close",
                    "track_label": spawned_walker.track_label,
                    "observer_role": role,
                    "anchor_index": anchor_index,
                    "anchor_label": anchor.label,
                    "anchor_actor_id": anchor.actor_id,
                    "resolved_anchor_actor_id": resolved_anchor_actor_id,
                    "anchor_type_id": anchor.actor_type_id,
                    "resolved_anchor_type_id": resolved_anchor_type_id,
                    "anchor_actor_present": True,
                    "path": str(overhead_actor_path),
                    "status": "saved" if overhead_actor_ok else "timeout",
                    "observer_location": serialize_location(observer_location),
                    "anchor_location": serialize_location(anchor_location),
                    "facing_error_degrees": round(float(facing_error), 3),
                    "camera_transform": serialize_transform(overhead_actor_transform),
                }
            )

        if member_actor_records:
            member_group_path = capture_dir / f"{prefix}_anchor_members_clean_group.png"
            member_group_transform = build_anchor_members_clean_group_camera_transform(
                observer_location,
                anchor_location,
                [record["location"] for record in member_actor_records],
                member_type_ids=[record["type_id"] for record in member_actor_records],
            )
            member_group_sensor = world.spawn_actor(
                close_camera_bp,
                member_group_transform,
            )
            try:
                member_group_ok = capture_rgb_sensor_frame(
                    world,
                    member_group_sensor,
                    member_group_path,
                    timeout_seconds=config.capture_timeout_seconds,
                )
            finally:
                try:
                    member_group_sensor.destroy()
                except RuntimeError:
                    pass
            captures.append(
                {
                    "capture_type": "anchor_members_clean_group",
                    "track_label": spawned_walker.track_label,
                    "observer_role": role,
                    "anchor_index": anchor_index,
                    "anchor_label": anchor.label,
                    "anchor_kind": anchor.anchor_kind,
                    "member_actor_ids": [
                        record["actor"].id for record in member_actor_records
                    ],
                    "member_type_ids": [
                        record["type_id"] for record in member_actor_records
                    ],
                    "member_count": len(member_actor_records),
                    "path": str(member_group_path),
                    "status": "saved" if member_group_ok else "timeout",
                    "observer_location": serialize_location(observer_location),
                    "anchor_location": serialize_location(anchor_location),
                    "facing_error_degrees": round(float(facing_error), 3),
                    "camera_transform": serialize_transform(member_group_transform),
                }
            )

            for member_index, member_record in enumerate(member_actor_records, start=1):
                member_actor = member_record["actor"]
                member_location = member_record["location"]
                member_type_id = member_record["type_id"]
                member_clean_path = (
                    capture_dir
                    / (
                        f"{prefix}_member{member_index}_actor{member_actor.id}"
                        "_clean_close.png"
                    )
                )
                member_clean_transform = build_anchor_member_clean_close_camera_transform(
                    observer_location,
                    member_location,
                    type_id=member_type_id,
                )
                member_clean_sensor = world.spawn_actor(
                    anchor_actor_close_bp,
                    member_clean_transform,
                )
                try:
                    member_clean_ok = capture_rgb_sensor_frame(
                        world,
                        member_clean_sensor,
                        member_clean_path,
                        timeout_seconds=config.capture_timeout_seconds,
                    )
                finally:
                    try:
                        member_clean_sensor.destroy()
                    except RuntimeError:
                        pass
                captures.append(
                    {
                        "capture_type": "anchor_member_clean_close",
                        "track_label": spawned_walker.track_label,
                        "observer_role": role,
                        "anchor_index": anchor_index,
                        "anchor_label": anchor.label,
                        "anchor_kind": anchor.anchor_kind,
                        "member_index": member_index,
                        "member_actor_id": member_actor.id,
                        "member_type_id": member_type_id,
                        "member_location": serialize_location(member_location),
                        "path": str(member_clean_path),
                        "status": "saved" if member_clean_ok else "timeout",
                        "observer_location": serialize_location(observer_location),
                        "anchor_location": serialize_location(anchor_location),
                        "facing_error_degrees": round(float(facing_error), 3),
                        "camera_transform": serialize_transform(member_clean_transform),
                    }
                )

        context_path = capture_dir / f"{prefix}_scene_context.png"
        context_transform = build_observer_scene_camera_transform(
            observer_location,
            anchor_location,
        )
        draw_observer_capture_markers(
            world,
            observer_location,
            anchor_location,
            role=role,
            anchor_actor=anchor_actor,
            member_locations=member_locations,
        )
        context_sensor = world.spawn_actor(scene_camera_bp, context_transform)
        try:
            context_ok = capture_rgb_sensor_frame(
                world,
                context_sensor,
                context_path,
                timeout_seconds=config.capture_timeout_seconds,
            )
        finally:
            try:
                context_sensor.destroy()
            except RuntimeError:
                pass
        captures.append(
            {
                "capture_type": "external_scene_context",
                "track_label": spawned_walker.track_label,
                "observer_role": role,
                "anchor_index": anchor_index,
                "anchor_label": anchor.label,
                "anchor_actor_id": anchor.actor_id,
                "resolved_anchor_actor_id": resolved_anchor_actor_id,
                "resolved_anchor_type_id": resolved_anchor_type_id,
                "anchor_actor_present": anchor_actor_present,
                "path": str(context_path),
                "status": "saved" if context_ok else "timeout",
                "observer_location": serialize_location(observer_location),
                "anchor_location": serialize_location(anchor_location),
                "observer_yaw_degrees": round(float(observer_transform.rotation.yaw), 3),
                "target_yaw_degrees": round(float(target_yaw), 3),
                "facing_error_degrees": round(float(facing_error), 3),
                "camera_transform": serialize_transform(context_transform),
            }
        )

        close_path = capture_dir / f"{prefix}_scene_close.png"
        close_transform = build_observer_close_camera_transform(
            observer_location,
            anchor_location,
        )
        draw_observer_capture_markers(
            world,
            observer_location,
            anchor_location,
            role=role,
            anchor_actor=anchor_actor,
            member_locations=member_locations,
        )
        close_sensor = world.spawn_actor(close_camera_bp, close_transform)
        try:
            close_ok = capture_rgb_sensor_frame(
                world,
                close_sensor,
                close_path,
                timeout_seconds=config.capture_timeout_seconds,
            )
        finally:
            try:
                close_sensor.destroy()
            except RuntimeError:
                pass
        captures.append(
            {
                "capture_type": "external_scene_close",
                "track_label": spawned_walker.track_label,
                "observer_role": role,
                "anchor_index": anchor_index,
                "anchor_label": anchor.label,
                "anchor_actor_id": anchor.actor_id,
                "resolved_anchor_actor_id": resolved_anchor_actor_id,
                "resolved_anchor_type_id": resolved_anchor_type_id,
                "anchor_actor_present": anchor_actor_present,
                "path": str(close_path),
                "status": "saved" if close_ok else "timeout",
                "observer_location": serialize_location(observer_location),
                "anchor_location": serialize_location(anchor_location),
                "observer_yaw_degrees": round(float(observer_transform.rotation.yaw), 3),
                "target_yaw_degrees": round(float(target_yaw), 3),
                "facing_error_degrees": round(float(facing_error), 3),
                "camera_transform": serialize_transform(close_transform),
            }
        )

        wait_for_debug_markers_to_expire(world)

        zoom_path = capture_dir / f"{prefix}_observer_zoom.png"
        zoom_transform = build_observer_zoom_camera_transform(
            observer_location,
            anchor_location,
        )
        zoom_sensor = world.spawn_actor(zoom_camera_bp, zoom_transform)
        try:
            zoom_ok = capture_rgb_sensor_frame(
                world,
                zoom_sensor,
                zoom_path,
                timeout_seconds=config.capture_timeout_seconds,
            )
        finally:
            try:
                zoom_sensor.destroy()
            except RuntimeError:
                pass
        captures.append(
            {
                "capture_type": "observer_zoom_to_anchor",
                "track_label": spawned_walker.track_label,
                "observer_role": role,
                "anchor_index": anchor_index,
                "anchor_label": anchor.label,
                "anchor_actor_id": anchor.actor_id,
                "resolved_anchor_actor_id": resolved_anchor_actor_id,
                "resolved_anchor_type_id": resolved_anchor_type_id,
                "anchor_actor_present": anchor_actor_present,
                "path": str(zoom_path),
                "status": "saved" if zoom_ok else "timeout",
                "facing_error_degrees": round(float(facing_error), 3),
                "camera_transform": serialize_transform(zoom_transform),
            }
        )

        clear_zoom_path = capture_dir / f"{prefix}_observer_clear_zoom.png"
        clear_zoom_transform = build_observer_clear_zoom_camera_transform(
            observer_location,
            anchor_location,
        )
        clear_zoom_sensor = world.spawn_actor(
            clear_zoom_camera_bp,
            clear_zoom_transform,
        )
        try:
            clear_zoom_ok = capture_rgb_sensor_frame(
                world,
                clear_zoom_sensor,
                clear_zoom_path,
                timeout_seconds=config.capture_timeout_seconds,
            )
        finally:
            try:
                clear_zoom_sensor.destroy()
            except RuntimeError:
                pass
        captures.append(
            {
                "capture_type": "observer_clear_zoom_to_anchor",
                "track_label": spawned_walker.track_label,
                "observer_role": role,
                "anchor_index": anchor_index,
                "anchor_label": anchor.label,
                "anchor_actor_id": anchor.actor_id,
                "resolved_anchor_actor_id": resolved_anchor_actor_id,
                "resolved_anchor_type_id": resolved_anchor_type_id,
                "anchor_actor_present": anchor_actor_present,
                "path": str(clear_zoom_path),
                "status": "saved" if clear_zoom_ok else "timeout",
                "facing_error_degrees": round(float(facing_error), 3),
                "camera_transform": serialize_transform(clear_zoom_transform),
            }
        )

        if actor_anchor:
            actor_close_path = capture_dir / f"{prefix}_anchor_actor_close.png"
            if not is_live_actor(anchor_actor):
                captures.append(
                    {
                        "capture_type": "anchor_actor_close",
                        "track_label": spawned_walker.track_label,
                        "observer_role": role,
                        "anchor_index": anchor_index,
                        "anchor_label": anchor.label,
                        "anchor_actor_id": anchor.actor_id,
                        "resolved_anchor_actor_id": resolved_anchor_actor_id,
                        "anchor_type_id": anchor.actor_type_id,
                        "resolved_anchor_type_id": resolved_anchor_type_id,
                        "anchor_actor_present": False,
                        "path": None,
                        "status": "missing_anchor_actor",
                        "observer_location": serialize_location(observer_location),
                        "anchor_location": serialize_location(anchor_location),
                        "facing_error_degrees": round(float(facing_error), 3),
                    }
                )
            else:
                wait_for_debug_markers_to_expire(world)
                actor_close_transform = build_anchor_actor_close_camera_transform(
                    observer_location,
                    anchor_location,
                    type_id=anchor.actor_type_id,
                )
                actor_close_sensor = world.spawn_actor(
                    anchor_actor_close_bp,
                    actor_close_transform,
                )
                try:
                    actor_close_ok = capture_rgb_sensor_frame(
                        world,
                        actor_close_sensor,
                        actor_close_path,
                        timeout_seconds=config.capture_timeout_seconds,
                    )
                finally:
                    try:
                        actor_close_sensor.destroy()
                    except RuntimeError:
                        pass
                captures.append(
                    {
                        "capture_type": "anchor_actor_close",
                        "track_label": spawned_walker.track_label,
                        "observer_role": role,
                        "anchor_index": anchor_index,
                        "anchor_label": anchor.label,
                        "anchor_actor_id": anchor.actor_id,
                        "resolved_anchor_actor_id": resolved_anchor_actor_id,
                        "anchor_type_id": anchor.actor_type_id,
                        "resolved_anchor_type_id": resolved_anchor_type_id,
                        "anchor_actor_present": True,
                        "path": str(actor_close_path),
                        "status": "saved" if actor_close_ok else "timeout",
                        "observer_location": serialize_location(observer_location),
                        "anchor_location": serialize_location(anchor_location),
                        "facing_error_degrees": round(float(facing_error), 3),
                        "camera_transform": serialize_transform(actor_close_transform),
                    }
                )
                for orbit_name, angle_degrees in (
                    ("east", 0.0),
                    ("north", 90.0),
                    ("west", 180.0),
                    ("south", 270.0),
                ):
                    orbit_path = (
                        capture_dir
                        / f"{prefix}_anchor_actor_orbit_{orbit_name}.png"
                    )
                    orbit_transform = build_anchor_actor_orbit_camera_transform(
                        anchor_location,
                        angle_degrees=angle_degrees,
                        type_id=anchor.actor_type_id,
                    )
                    orbit_sensor = world.spawn_actor(
                        anchor_actor_close_bp,
                        orbit_transform,
                    )
                    try:
                        orbit_ok = capture_rgb_sensor_frame(
                            world,
                            orbit_sensor,
                            orbit_path,
                            timeout_seconds=config.capture_timeout_seconds,
                        )
                    finally:
                        try:
                            orbit_sensor.destroy()
                        except RuntimeError:
                            pass
                    captures.append(
                        {
                            "capture_type": "anchor_actor_orbit",
                            "orbit_view": orbit_name,
                            "track_label": spawned_walker.track_label,
                            "observer_role": role,
                            "anchor_index": anchor_index,
                            "anchor_label": anchor.label,
                            "anchor_actor_id": anchor.actor_id,
                            "resolved_anchor_actor_id": resolved_anchor_actor_id,
                            "anchor_type_id": anchor.actor_type_id,
                            "resolved_anchor_type_id": resolved_anchor_type_id,
                            "anchor_actor_present": True,
                            "path": str(orbit_path),
                            "status": "saved" if orbit_ok else "timeout",
                            "observer_location": serialize_location(observer_location),
                            "anchor_location": serialize_location(anchor_location),
                            "facing_error_degrees": round(float(facing_error), 3),
                            "camera_transform": serialize_transform(orbit_transform),
                        }
                    )

        front_sensor = find_observer_camera_sensor(
            world,
            observer_camera_attachments,
            spawned_walker.track_label,
            "cam_front",
        )
        if front_sensor is None:
            captures.append(
                {
                    "capture_type": "observer_cam_front",
                    "track_label": spawned_walker.track_label,
                    "observer_role": role,
                    "anchor_index": anchor_index,
                    "anchor_label": anchor.label,
                    "anchor_actor_id": anchor.actor_id,
                    "resolved_anchor_actor_id": resolved_anchor_actor_id,
                    "resolved_anchor_type_id": resolved_anchor_type_id,
                    "anchor_actor_present": anchor_actor_present,
                    "path": None,
                    "status": "missing_sensor",
                    "facing_error_degrees": round(float(facing_error), 3),
                }
            )
            continue
        front_path = capture_dir / f"{prefix}_cam_front.png"
        front_ok = capture_rgb_sensor_frame(
            world,
            front_sensor,
            front_path,
            timeout_seconds=config.capture_timeout_seconds,
        )
        captures.append(
            {
                "capture_type": "observer_cam_front",
                "track_label": spawned_walker.track_label,
                "observer_role": role,
                "anchor_index": anchor_index,
                "anchor_label": anchor.label,
                "anchor_actor_id": anchor.actor_id,
                "resolved_anchor_actor_id": resolved_anchor_actor_id,
                "resolved_anchor_type_id": resolved_anchor_type_id,
                "anchor_actor_present": anchor_actor_present,
                "path": str(front_path),
                "status": "saved" if front_ok else "timeout",
                "facing_error_degrees": round(float(facing_error), 3),
            }
        )

        draw_observer_capture_markers(
            world,
            observer_location,
            anchor_location,
            role=role,
            anchor_actor=anchor_actor,
            member_locations=member_locations,
        )
        topdown_debug_path = capture_dir / f"{prefix}_topdown_debug.png"
        topdown_debug_transform = build_observer_topdown_camera_transform(
            observer_location,
            anchor_location,
            member_locations,
            image_width=config.capture_image_width,
            image_height=config.capture_image_height,
            fov=85,
        )
        topdown_debug_bp = configure_rgb_camera_blueprint(
            world,
            width=config.capture_image_width,
            height=config.capture_image_height,
            fov=85,
        )
        topdown_debug_sensor = world.spawn_actor(
            topdown_debug_bp,
            topdown_debug_transform,
        )
        try:
            topdown_debug_ok = capture_rgb_sensor_frame(
                world,
                topdown_debug_sensor,
                topdown_debug_path,
                timeout_seconds=config.capture_timeout_seconds,
            )
        finally:
            try:
                topdown_debug_sensor.destroy()
            except RuntimeError:
                pass
        captures.append(
            {
                "capture_type": "external_scene_topdown_debug",
                "track_label": spawned_walker.track_label,
                "observer_role": role,
                "anchor_index": anchor_index,
                "anchor_label": anchor.label,
                "anchor_actor_id": anchor.actor_id,
                "resolved_anchor_actor_id": resolved_anchor_actor_id,
                "resolved_anchor_type_id": resolved_anchor_type_id,
                "anchor_actor_present": anchor_actor_present,
                "path": str(topdown_debug_path),
                "status": "saved" if topdown_debug_ok else "timeout",
                "facing_error_degrees": round(float(facing_error), 3),
                "camera_transform": serialize_transform(topdown_debug_transform),
            }
        )

        scene_debug_path = capture_dir / f"{prefix}_scene_debug.png"
        scene_debug_transform = build_observer_scene_camera_transform(
            observer_location,
            anchor_location,
        )
        scene_debug_sensor = world.spawn_actor(scene_camera_bp, scene_debug_transform)
        try:
            scene_debug_ok = capture_rgb_sensor_frame(
                world,
                scene_debug_sensor,
                scene_debug_path,
                timeout_seconds=config.capture_timeout_seconds,
            )
        finally:
            try:
                scene_debug_sensor.destroy()
            except RuntimeError:
                pass
        captures.append(
            {
                "capture_type": "external_scene_debug",
                "track_label": spawned_walker.track_label,
                "observer_role": role,
                "anchor_index": anchor_index,
                "anchor_label": anchor.label,
                "anchor_actor_id": anchor.actor_id,
                "resolved_anchor_actor_id": resolved_anchor_actor_id,
                "resolved_anchor_type_id": resolved_anchor_type_id,
                "anchor_actor_present": anchor_actor_present,
                "path": str(scene_debug_path),
                "status": "saved" if scene_debug_ok else "timeout",
                "facing_error_degrees": round(float(facing_error), 3),
                "camera_transform": serialize_transform(scene_debug_transform),
            }
        )

    return tuple(captures)


def terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def collect_process_output(proc: subprocess.Popen, output_lines: list[str]) -> None:
    if proc.stdout is None:
        return
    for line in proc.stdout:
        if line:
            output_lines.append(line.rstrip())


def extract_scenario_labels(output_lines: list[str]) -> tuple[str, ...]:
    labels = []
    seen = set()
    for line in output_lines:
        match = SCENIC_SCENARIO_PATTERN.match(line.strip())
        if not match:
            continue
        label = match.group(1)
        if label in seen:
            continue
        labels.append(label)
        seen.add(label)
    return tuple(labels)


def sanitize_filename_fragment(text: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", text.strip())
    safe = safe.strip("-")
    return safe or "unknown"


def build_scenic_command(config: ScenicCustomWalkerConfig) -> list[str]:
    scenic_time_steps = max(
        1,
        int(math.ceil(float(config.scenic_time) / SCENIC_TIMESTEP_SECONDS)),
    )
    command = [
        str(config.scenic_bin),
        str(config.scenic_file),
        "--count",
        "1",
        "--model",
        "scenic.simulators.carla.model",
        "--simulate",
        "--2d",
        "--scenario",
        "BaseSetup",
        "--time",
        str(scenic_time_steps),
        "--param",
        "timestep",
        str(SCENIC_TIMESTEP_SECONDS),
        "--param",
        "N_SCENARIOS",
        str(config.n_scenarios),
        "--param",
        "render",
        "0",
        "--param",
        "port",
        str(config.port),
        "--param",
        "address",
        config.host,
        "--param",
        "timeout",
        str(config.scenic_timeout_seconds),
        "--param",
        "reload_world",
        "0",
    ]
    if config.selected_scenario:
        command.extend(["--param", "SELECTED_SCENARIO", config.selected_scenario])
    else:
        random_count = max(1, min(int(config.n_scenarios), 3, len(ABNORMAL_SCENARIOS)))
        for index, scenario_label in enumerate(
            random.sample(ABNORMAL_SCENARIOS, random_count),
            start=1,
        ):
            command.extend(["--param", f"SELECTED_SCENARIO_{index}", scenario_label])
    if config.static_ego:
        command.extend(["--param", "EGO_STATIC", "1"])
    if config.ego_start is not None:
        ego_x, ego_y, ego_heading = config.ego_start
        command.extend(
            [
                "--param",
                "EGO_START_FIXED",
                "1",
                "--param",
                "EGO_START_X",
                str(ego_x),
                "--param",
                "EGO_START_Y",
                str(ego_y),
                "--param",
                "EGO_START_HEADING",
                str(ego_heading),
            ]
        )
    if config.realtime_factor > 0:
        command.extend(["--param", "realtime_factor", str(config.realtime_factor)])
    if config.carla_map:
        command.extend(["--param", "carla_map", config.carla_map])
    if config.map_xodr:
        command.extend(["--param", "map", str(pathlib.Path(config.map_xodr).resolve())])
    if config.weather:
        command.extend(["--param", "weather", config.weather])
    return command


def scenario_label_with_description(label: str) -> str:
    scenario_id = str(label).split("#", 1)[0].upper()
    description = ABNORMAL_SCENARIO_DESCRIPTIONS.get(scenario_id)
    if not description:
        return str(label)
    return f"{scenario_id}({description})"


def scenic_param_values(scenic_cmd: list[str]) -> dict[str, str]:
    values = {}
    index = 0
    while index < len(scenic_cmd):
        if scenic_cmd[index] == "--param" and index + 2 < len(scenic_cmd):
            values[str(scenic_cmd[index + 1])] = str(scenic_cmd[index + 2])
            index += 3
        else:
            index += 1
    return values


def selected_abnormal_scenario_summary(scenic_cmd: list[str]) -> str:
    params = scenic_param_values(scenic_cmd)
    numbered_labels = [
        params[key]
        for key in sorted(params)
        if key.startswith("SELECTED_SCENARIO_") and params[key]
    ]
    if numbered_labels:
        return ", ".join(scenario_label_with_description(label) for label in numbered_labels)

    selected_label = params.get("SELECTED_SCENARIO")
    if selected_label:
        try:
            count = int(params.get("N_SCENARIOS", "1"))
        except ValueError:
            count = 1
        summary = scenario_label_with_description(selected_label)
        if count > 1:
            summary = f"{summary} x{count}"
        return summary

    return "random"


def resolve_current_ego_actor(world, *, ego_tracking_state: EgoTrackingState | None = None):
    if ego_tracking_state is None:
        return None

    preferred_actor = world.get_actor(ego_tracking_state.preferred_id)
    if preferred_actor is not None:
        try:
            preferred_location = preferred_actor.get_transform().location
            if (
                preferred_actor.attributes.get("role_name") == "ego"
                and preferred_actor.type_id.startswith("vehicle.")
                and not is_all_zero_location(preferred_location)
            ):
                ego_tracking_state.last_resolved_id = preferred_actor.id
                ego_tracking_state.last_valid_location_xyz = location_to_xyz(preferred_location)
                return preferred_actor
        except RuntimeError:
            preferred_actor = None

    # DAMOS treats Scenic ego as a single vehicle. Do not silently switch to
    # another role_name=ego actor; that creates impossible plotted trajectories.
    return None


def sample_tracked_actors(
    world,
    tracked_actor_ids,
    trajectory_samples,
    timestamp_seconds,
    *,
    ego_tracking_state: EgoTrackingState | None = None,
    skip_labels=(),
    snapshot=None,
):
    skipped = set(skip_labels)
    if snapshot is None:
        try:
            snapshot = world.get_snapshot()
        except RuntimeError:
            snapshot = None

    for label, actor_id in tracked_actor_ids.items():
        if label in skipped:
            continue
        if label == "ego":
            actor = resolve_current_ego_actor(
                world,
                ego_tracking_state=ego_tracking_state,
            )
            if actor is not None:
                tracked_actor_ids["ego"] = actor.id
        else:
            actor = world.get_actor(actor_id)
        if actor is None:
            continue
        try:
            if snapshot is not None:
                actor_snapshot = snapshot.find(actor.id)
            else:
                actor_snapshot = None
            if actor_snapshot is not None:
                location = actor_snapshot.get_transform().location
            else:
                location = actor.get_transform().location
        except RuntimeError:
            continue
        if is_all_zero_location(location):
            continue
        sample_time = round(float(timestamp_seconds), 3)
        previous_samples = trajectory_samples.get(label, [])
        if previous_samples:
            sample_time = max(sample_time, round(float(previous_samples[-1]["t"]) + 0.001, 3))
        if label == "ego" and ego_tracking_state is not None:
            ego_tracking_state.last_valid_location_xyz = location_to_xyz(location)
            ego_tracking_state.last_valid_timestamp = sample_time
        trajectory_samples.setdefault(label, []).append(
            {
                "t": sample_time,
                "actor_id": actor.id,
                "x": float(location.x),
                "y": float(location.y),
                "z": float(location.z),
            }
        )


def start_continuous_ego_sampler(
    host,
    port,
    trajectory_samples,
    ego_tracking_state: EgoTrackingState,
    *,
    interval_seconds: float,
):
    sample_client = carla.Client(host, int(port))
    sample_client.set_timeout(2.0)
    stop_event = threading.Event()
    sample_lock = threading.Lock()
    started_at = time.monotonic()
    interval = max(0.05, min(0.2, float(interval_seconds)))
    world = safe_get_world(sample_client)

    def run_sampler():
        nonlocal world
        tracked_ego = {"ego": ego_tracking_state.preferred_id}
        last_sample_at = None
        while not stop_event.is_set():
            if world is None:
                world = safe_get_world(sample_client)
                if world is None:
                    stop_event.wait(interval)
                    continue
            try:
                snapshot = world.wait_for_tick(1.0)
            except RuntimeError:
                world = safe_get_world(sample_client)
                stop_event.wait(interval)
                continue
            now = time.monotonic()
            if last_sample_at is not None and now - last_sample_at < interval:
                continue
            if world is not None:
                with sample_lock:
                    sample_tracked_actors(
                        world,
                        tracked_ego,
                        trajectory_samples,
                        now - started_at,
                        ego_tracking_state=ego_tracking_state,
                        snapshot=snapshot,
                    )
                last_sample_at = now

    thread = threading.Thread(
        target=run_sampler,
        name="damos-ego-trajectory-sampler",
        daemon=True,
    )
    thread.start()
    return {"stop_event": stop_event, "thread": thread, "lock": sample_lock}


def stop_continuous_ego_sampler(sampler) -> None:
    if sampler is None:
        return
    sampler["stop_event"].set()
    sampler["thread"].join(timeout=2.0)


def build_tracked_actor_map(ego, spawned_walkers):
    tracked = {"ego": ego.id}
    for spawned_walker in spawned_walkers:
        tracked[spawned_walker.track_label] = spawned_walker.walker.id
    return tracked


def is_scenic_bicycle_actor(actor) -> bool:
    type_id = actor.type_id.lower()
    if not type_id.startswith("vehicle."):
        return False
    return any(keyword in type_id for keyword in BICYCLE_KEYWORDS)


def discover_scenic_support_actors(world, ego, spawned_walkers, tracked_actors):
    custom_ids = {spawned_walker.walker.id for spawned_walker in spawned_walkers}
    try:
        actors = world.get_actors()
    except RuntimeError:
        return

    for actor in actors:
        if actor.id == ego.id or actor.id in custom_ids:
            continue
        if actor.type_id in {DELIVERYBOT_ID, HUMANOID_ID}:
            continue
        if actor.type_id.startswith("walker.pedestrian."):
            tracked_actors.setdefault(f"scenic.pedestrian:{actor.id}", actor.id)
        elif is_scenic_bicycle_actor(actor):
            tracked_actors.setdefault(f"scenic.bicycle:{actor.id}", actor.id)
        elif actor.type_id.startswith("vehicle."):
            tracked_actors.setdefault(f"scenic.vehicle:{actor.id}", actor.id)


def group_key_for_track(track_key: str) -> str:
    if track_key.startswith(f"{DELIVERYBOT_ID}:"):
        return DELIVERYBOT_ID
    if track_key.startswith(f"{HUMANOID_ID}:"):
        return HUMANOID_ID
    if track_key.startswith("scenic.pedestrian:"):
        return "scenic.pedestrian"
    if track_key.startswith("scenic.bicycle:"):
        return "scenic.bicycle"
    if track_key.startswith("scenic.vehicle:"):
        return "scenic.vehicle"
    return track_key


def distance_between_samples(first, second) -> float:
    dx = float(second["x"]) - float(first["x"])
    dy = float(second["y"]) - float(first["y"])
    dz = float(second["z"]) - float(first["z"])
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def split_trajectory_segments(samples, *, max_jump_meters: float = 35.0):
    if not samples:
        return []

    segments = [[samples[0]]]
    previous = samples[0]
    for sample in samples[1:]:
        actor_changed = sample.get("actor_id") != previous.get("actor_id")
        time_reversed = float(sample["t"]) < float(previous["t"])
        jumped = distance_between_samples(previous, sample) > max_jump_meters
        if actor_changed or time_reversed or jumped:
            segments.append([sample])
        else:
            segments[-1].append(sample)
        previous = sample
    return segments


def plot_segments_for_track(track_key: str, samples):
    if track_key == "ego":
        return split_trajectory_segments(samples, max_jump_meters=20.0)
    return split_trajectory_segments(samples)


def xy_for_sample(sample) -> tuple[float, float]:
    return (float(sample["x"]), float(sample["y"]))


def location_for_sample(sample):
    return carla.Location(
        x=float(sample["x"]),
        y=float(sample["y"]),
        z=float(sample.get("z", 0.0)),
    )


def polyline_distance(points) -> float:
    distance = 0.0
    for first, second in zip(points, points[1:]):
        dx = float(second[0]) - float(first[0])
        dy = float(second[1]) - float(first[1])
        distance += (dx * dx + dy * dy) ** 0.5
    return distance


def build_ego_route_planner(world, *, sampling_resolution: float = 1.5):
    python_api = SOURCE_ROOT / "PythonAPI" / "carla"
    if python_api.exists() and str(python_api) not in sys.path:
        sys.path.insert(0, str(python_api))
    try:
        from agents.navigation.global_route_planner import GlobalRoutePlanner

        return GlobalRoutePlanner(world.get_map(), sampling_resolution)
    except (ImportError, RuntimeError, AttributeError, TypeError):
        return None


def route_points_between_samples(route_planner, first, second):
    fallback = [xy_for_sample(first), xy_for_sample(second)]
    if first.get("actor_id") != second.get("actor_id"):
        return []
    if float(second["t"]) < float(first["t"]):
        return []

    if route_planner is None:
        return fallback

    direct_distance = distance_between_samples(first, second)
    if direct_distance > 20.0:
        return []
    if direct_distance < 1.0:
        return fallback

    try:
        route = route_planner.trace_route(
            location_for_sample(first),
            location_for_sample(second),
        )
    except (RuntimeError, AttributeError, TypeError, ValueError):
        return fallback

    if not route:
        return fallback

    points = [xy_for_sample(first)]
    for waypoint, _road_option in route:
        location = waypoint.transform.location
        points.append((float(location.x), float(location.y)))
    points.append(xy_for_sample(second))

    # If the route planner returns an implausible detour, keep the raw observed
    # segment instead of drawing a misleading loop across the map.
    if polyline_distance(points) > max(60.0, direct_distance * 3.0):
        return fallback
    return points


def plot_points_for_segment(track_key: str, samples, *, ego_route_planner=None):
    if track_key != "ego" or len(samples) < 2:
        return [xy_for_sample(sample) for sample in samples]

    points = []
    for first, second in zip(samples, samples[1:]):
        segment_points = route_points_between_samples(ego_route_planner, first, second)
        if points and segment_points:
            segment_points = segment_points[1:]
        points.extend(segment_points)
    return points or [xy_for_sample(sample) for sample in samples]


def label_for_track(track_key: str) -> str:
    if track_key.startswith(f"{DELIVERYBOT_ID}:"):
        return f"deliverybot {track_key.rsplit(':', 1)[-1]}"
    if track_key.startswith(f"{HUMANOID_ID}:"):
        return f"humanoid {track_key.rsplit(':', 1)[-1]}"
    labels = {
        "ego": "ego",
        DELIVERYBOT_ID: "deliverybot",
        HUMANOID_ID: "humanoid",
        "scenic.pedestrian": "scenic pedestrians",
        "scenic.bicycle": "scenic bicycles",
        "scenic.vehicle": "scenic vehicles",
    }
    return labels.get(group_key_for_track(track_key), track_key)


def collect_focus_track_keys(trajectory_samples, anchor_assignments):
    keys = ["ego"]
    keys.extend(
        key for key in sorted(trajectory_samples)
        if key.startswith(f"{HUMANOID_ID}:") or key.startswith(f"{DELIVERYBOT_ID}:")
    )
    for anchor in anchor_assignments:
        anchor_key = anchor["anchor_label"]
        if anchor_key in trajectory_samples and anchor_key not in keys:
            keys.append(anchor_key)
    return keys


def compute_focus_bounds(trajectory_samples, anchor_assignments, focus_track_keys):
    xs = []
    ys = []
    for key in focus_track_keys:
        for sample in trajectory_samples.get(key, []):
            xs.append(sample["x"])
            ys.append(sample["y"])
    for anchor in anchor_assignments:
        xs.append(anchor["anchor_location"]["x"])
        ys.append(anchor["anchor_location"]["y"])
        for member in anchor.get("member_actors", []):
            location = member.get("location", {})
            if "x" in location and "y" in location:
                xs.append(location["x"])
                ys.append(location["y"])
    if not xs or not ys:
        return None
    margin = 18.0
    return (
        min(xs) - margin,
        max(xs) + margin,
        min(ys) - margin,
        max(ys) + margin,
    )


def build_cooperation_links(trajectory_samples, anchor_assignments, detected_ego=None):
    links = []

    for anchor in anchor_assignments:
        walker_key = anchor["track_label"]
        walker_samples = trajectory_samples.get(walker_key, [])
        if not walker_samples:
            continue
        walker_start = walker_samples[0]
        anchor_location = anchor["anchor_location"]
        link = {
            "walker_track_label": walker_key,
            "walker_actor_id": walker_start.get("actor_id"),
            "walker_blueprint_id": anchor["blueprint_id"],
            "anchor_index": anchor.get("anchor_index"),
            "observer_role": anchor.get("observer_role"),
            "anchor_kind": anchor.get("anchor_kind"),
            "member_actor_ids": anchor.get("member_actor_ids", []),
            "member_actors": anchor.get("member_actors", []),
            "member_count": anchor.get("member_count", 1),
            "anchor_actor_id": anchor["anchor_actor_id"],
            "anchor_type_id": anchor["anchor_type_id"],
            "anchor_label": anchor["anchor_label"],
            "walker_start": {
                "x": float(walker_start["x"]),
                "y": float(walker_start["y"]),
                "z": float(walker_start["z"]),
            },
            "anchor_location": anchor_location,
            "walker_to_anchor_distance": round(
                (
                    (float(walker_start["x"]) - float(anchor_location["x"])) ** 2
                    + (float(walker_start["y"]) - float(anchor_location["y"])) ** 2
                    + (float(walker_start["z"]) - float(anchor_location["z"])) ** 2
                )
                ** 0.5,
                3,
            ),
        }
        if detected_ego is not None:
            ego_location = detected_ego["location"]
            link["ego_actor_id"] = detected_ego["actor_id"]
            link["ego_start"] = {
                "x": float(ego_location["x"]),
                "y": float(ego_location["y"]),
                "z": float(ego_location["z"]),
            }
            link["ego_to_anchor_distance"] = round(
                (
                    (float(ego_location["x"]) - float(anchor_location["x"])) ** 2
                    + (float(ego_location["y"]) - float(anchor_location["y"])) ** 2
                    + (float(ego_location["z"]) - float(anchor_location["z"])) ** 2
                )
                ** 0.5,
                3,
            )
        links.append(link)

    return links


def expand_xy_bounds(bounds, margin):
    if bounds is None:
        return None
    min_x, max_x, min_y, max_y = bounds
    return (min_x - margin, max_x + margin, min_y - margin, max_y + margin)


def xy_in_bounds(x, y, bounds):
    if bounds is None:
        return True
    min_x, max_x, min_y, max_y = bounds
    return min_x <= x <= max_x and min_y <= y <= max_y


def xy_points_intersect_bounds(points, bounds):
    if bounds is None:
        return True
    return any(xy_in_bounds(x, y, bounds) for x, y in points)


def xy_bounds_for_points(points):
    xs = [float(x) for x, _y in points]
    ys = [float(y) for _x, y in points]
    return (min(xs), max(xs), min(ys), max(ys))


def point_in_polygon(point, polygon):
    x, y = point
    inside = False
    previous_x, previous_y = polygon[-1]
    for current_x, current_y in polygon:
        crosses_y = (current_y > y) != (previous_y > y)
        if crosses_y:
            slope_x = (
                (previous_x - current_x)
                * (y - current_y)
                / ((previous_y - current_y) or 1e-9)
                + current_x
            )
            if x < slope_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def polygon_contains_any_point(polygon, points):
    if not polygon or not points:
        return False
    min_x, max_x, min_y, max_y = xy_bounds_for_points(polygon)
    for point in points:
        x, y = point
        if min_x <= x <= max_x and min_y <= y <= max_y and point_in_polygon(point, polygon):
            return True
    return False


def waypoint_xy(waypoint):
    location = waypoint.transform.location
    return float(location.x), float(location.y)


def waypoint_lane_edge_xy(waypoint):
    x, y = waypoint_xy(waypoint)
    yaw = math.radians(float(waypoint.transform.rotation.yaw))
    left_x = -math.sin(yaw)
    left_y = math.cos(yaw)
    half_width = max(0.1, float(waypoint.lane_width) * 0.5)
    return (
        (x + left_x * half_width, y + left_y * half_width),
        (x - left_x * half_width, y - left_y * half_width),
    )


def split_waypoint_segments(waypoints, *, max_gap=12.0):
    segments = []
    current = []
    previous = None
    for waypoint in waypoints:
        if previous is not None:
            prev_x, prev_y = waypoint_xy(previous)
            x, y = waypoint_xy(waypoint)
            if math.hypot(x - prev_x, y - prev_y) > max_gap:
                if len(current) >= 2:
                    segments.append(current)
                current = []
        current.append(waypoint)
        previous = waypoint
    if len(current) >= 2:
        segments.append(current)
    return segments


def draw_carla_lane_context(ax, carla_map, *, bounds=None):
    groups = {}
    try:
        waypoints = carla_map.generate_waypoints(2.0)
    except RuntimeError:
        return 0

    for waypoint in waypoints:
        try:
            if waypoint.lane_type != carla.LaneType.Driving:
                continue
            key = (waypoint.road_id, waypoint.section_id, waypoint.lane_id)
        except RuntimeError:
            continue
        groups.setdefault(key, []).append(waypoint)

    visible_segments = 0
    draw_bounds = expand_xy_bounds(bounds, 12.0)
    for waypoints in groups.values():
        waypoints.sort(key=lambda waypoint: float(waypoint.s))
        for segment in split_waypoint_segments(waypoints):
            centers = [waypoint_xy(waypoint) for waypoint in segment]
            if not xy_points_intersect_bounds(centers, draw_bounds):
                continue

            left_edges = []
            right_edges = []
            for waypoint in segment:
                left, right = waypoint_lane_edge_xy(waypoint)
                left_edges.append(left)
                right_edges.append(right)

            lane_polygon = [*left_edges, *reversed(right_edges)]
            if len(lane_polygon) >= 3:
                xs = [point[0] for point in lane_polygon]
                ys = [point[1] for point in lane_polygon]
                ax.fill(
                    xs,
                    ys,
                    facecolor="#f3f4f6",
                    edgecolor="none",
                    alpha=0.72,
                    zorder=-3,
                )

            for edge_points in (left_edges, right_edges):
                ax.plot(
                    [point[0] for point in edge_points],
                    [point[1] for point in edge_points],
                    color="#c7cdd7",
                    linewidth=0.45,
                    alpha=0.85,
                    zorder=-1,
                )

            ax.plot(
                [point[0] for point in centers],
                [point[1] for point in centers],
                color="#8796aa",
                linewidth=0.45,
                alpha=0.85,
                linestyle=(0, (4, 5)),
                zorder=0,
            )
            visible_segments += 1
    return visible_segments


def driving_waypoint_points(carla_map, *, bounds=None, sampling_resolution=2.0):
    try:
        waypoints = carla_map.generate_waypoints(sampling_resolution)
    except RuntimeError:
        return []

    draw_bounds = expand_xy_bounds(bounds, 24.0)
    points = []
    for waypoint in waypoints:
        try:
            if waypoint.lane_type != carla.LaneType.Driving:
                continue
            point = waypoint_xy(waypoint)
        except RuntimeError:
            continue
        if xy_in_bounds(point[0], point[1], draw_bounds):
            points.append(point)
    return points


def compute_carla_map_bounds(world, *, margin=10.0):
    try:
        carla_map = world.get_map()
    except RuntimeError:
        return None
    points = driving_waypoint_points(carla_map)
    if not points:
        return None
    return expand_xy_bounds(xy_bounds_for_points(points), margin)


def apply_xy_bounds(ax, bounds):
    if bounds is None:
        return
    min_x, max_x, min_y, max_y = bounds
    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)


def unique_xy_points(points):
    seen = set()
    unique = []
    for x, y in points:
        key = (round(float(x), 2), round(float(y), 2))
        if key in seen:
            continue
        seen.add(key)
        unique.append((float(x), float(y)))
    return unique


def sorted_polygon_points(points):
    center_x = sum(x for x, _y in points) / len(points)
    center_y = sum(y for _x, y in points) / len(points)
    return sorted(points, key=lambda point: math.atan2(point[1] - center_y, point[0] - center_x))


def draw_carla_building_context(
    ax,
    world,
    *,
    bounds=None,
    driving_points=(),
    max_buildings=1800,
):
    try:
        objects = world.get_environment_objects(carla.CityObjectLabel.Buildings)
    except (AttributeError, RuntimeError):
        return 0

    from matplotlib.patches import Polygon

    draw_bounds = expand_xy_bounds(bounds, 20.0)
    drawn = 0
    for environment_object in objects:
        try:
            # EnvironmentObject.bounding_box is already reported in world space.
            # Applying environment_object.transform again shifts the geometry.
            vertices = environment_object.bounding_box.get_world_vertices(
                carla.Transform()
            )
        except RuntimeError:
            continue
        points = unique_xy_points((vertex.x, vertex.y) for vertex in vertices)
        if len(points) < 3:
            continue
        if not xy_points_intersect_bounds(points, draw_bounds):
            continue
        polygon_points = sorted_polygon_points(points)
        # CARLA building EnvironmentObjects can be coarse mesh bounding boxes.
        # If a box covers a driving waypoint, it is not a reliable building
        # footprint for our 2D validation plot.
        if polygon_contains_any_point(polygon_points, driving_points):
            continue
        polygon = Polygon(
            polygon_points,
            closed=True,
            facecolor="#dedbd2",
            edgecolor="#b9b3a9",
            linewidth=0.35,
            alpha=0.75,
            zorder=-4,
        )
        ax.add_patch(polygon)
        drawn += 1
        if drawn >= max_buildings:
            break
    return drawn


def draw_carla_map_context(ax, world, *, bounds=None):
    ax.set_facecolor("#fbfaf7")
    try:
        carla_map = world.get_map()
    except RuntimeError:
        return {"lane_segments": 0, "buildings": 0}

    driving_points = driving_waypoint_points(carla_map, bounds=bounds)
    buildings = draw_carla_building_context(
        ax,
        world,
        bounds=bounds,
        driving_points=driving_points,
    )
    lane_segments = draw_carla_lane_context(ax, carla_map, bounds=bounds)
    return {"lane_segments": lane_segments, "buildings": buildings}


def save_trajectory_report(
    world,
    trajectory_samples,
    config: ScenicCustomWalkerConfig,
    *,
    scenario_labels=(),
    anchor_assignments=(),
    ego_tracking_state: EgoTrackingState | None = None,
    detected_ego=None,
    walker_movements=None,
    observer_metrics=(),
    observer_camera_specs=(),
    observer_camera_attachments=(),
    observer_scene_captures=(),
    s1_crossing_verification=(),
):
    config.report_dir.mkdir(parents=True, exist_ok=True)
    map_name = safe_map_name(world).split("/")[-1]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    scenario_fragment = "base"
    if scenario_labels:
        scenario_fragment = sanitize_filename_fragment("_".join(scenario_labels[:4]))
    stem = (
        f"scenic_custom_walkers_{sanitize_filename_fragment(map_name)}_"
        f"{scenario_fragment}_port{config.port}_{stamp}"
    )
    png_path = config.report_dir / f"{stem}.png"
    focus_png_path = config.report_dir / f"{stem}_focus.png"
    json_path = config.report_dir / f"{stem}.json"

    report = {
        "map": safe_map_name(world),
        "port": config.port,
        "carla_map_param": config.carla_map,
        "map_xodr": config.map_xodr,
        "scenic_time": config.scenic_time,
        "selected_scenario": config.selected_scenario,
        "ego_start": list(config.ego_start) if config.ego_start is not None else None,
        "custom_walker_mode": "observer" if config.observer_mode else "walker",
        "min_move_meters": config.min_move_meters,
        "movement_metric_window": (
            "not_required_in_observer_mode"
            if config.observer_mode
            else "after_spawn_probe_validation_to_scenic_runner_stop"
        ),
        "walker_movements": {
            track_label: round(float(moved), 3)
            for track_label, moved in sorted((walker_movements or {}).items())
        },
        "observer_requirements": {
            "max_observer_anchor_distance": config.max_observer_anchor_distance,
            "max_observer_facing_error_degrees": config.max_observer_facing_error_degrees,
        },
        "observer_metrics": list(observer_metrics or ()),
        "observer_camera_specs": list(observer_camera_specs or ()),
        "observer_camera_attachments": list(observer_camera_attachments or ()),
        "observer_scene_captures": list(observer_scene_captures or ()),
        "s1_crossing_verification": list(s1_crossing_verification or ()),
        "scenario_labels": list(scenario_labels),
        "anchor_assignments": list(anchor_assignments),
        "cooperation_links": build_cooperation_links(
            trajectory_samples,
            anchor_assignments,
            detected_ego=detected_ego,
        ),
        "samples": trajectory_samples,
    }
    if detected_ego is not None:
        report["detected_ego"] = detected_ego
    if ego_tracking_state is not None:
        report["ego_tracking"] = {
            "preferred_id": ego_tracking_state.preferred_id,
            "preferred_type_id": ego_tracking_state.preferred_type_id,
            "last_resolved_id": ego_tracking_state.last_resolved_id,
            "last_valid_location_xyz": ego_tracking_state.last_valid_location_xyz,
            "last_valid_timestamp": ego_tracking_state.last_valid_timestamp,
        }
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ego_route_planner = build_ego_route_planner(world)
    map_bounds = compute_carla_map_bounds(world)

    fig, ax = plt.subplots(figsize=(12, 10))
    draw_carla_map_context(ax, world, bounds=map_bounds)

    drawn_anchor_indices = set()
    drew_anchor_members = False
    for anchor in anchor_assignments:
        anchor_index = anchor.get("anchor_index")
        if anchor_index in drawn_anchor_indices:
            continue
        drawn_anchor_indices.add(anchor_index)
        location = anchor["anchor_location"]
        label = anchor.get("anchor_kind") or anchor.get("anchor_label", "anchor")
        members = anchor.get("member_actors", [])
        if members:
            member_x = [member["location"]["x"] for member in members]
            member_y = [member["location"]["y"] for member in members]
            ax.scatter(
                member_x,
                member_y,
                s=24,
                marker="s",
                c="#6b7280",
                alpha=0.75,
                linewidths=0,
                label="anchor members" if not drew_anchor_members else None,
                zorder=4,
            )
            drew_anchor_members = True
        ax.scatter(
            location["x"],
            location["y"],
            s=90,
            marker="*",
            c="#111111",
            zorder=7,
        )
        ax.annotate(
            f"{label} ({anchor.get('member_count', 1)})",
            (location["x"], location["y"]),
            xytext=(8, 8),
            textcoords="offset points",
            fontsize=8,
            color="#111111",
        )

    colors = {
        "ego": "#1f77b4",
        DELIVERYBOT_ID: "#ff7f0e",
        HUMANOID_ID: "#d62728",
        "scenic.pedestrian": "#2ca02c",
        "scenic.bicycle": "#9467bd",
        "scenic.vehicle": "#8c564b",
    }
    legend_drawn = set()
    ordered_keys = ["ego"]
    ordered_keys.extend(
        key
        for key in sorted(trajectory_samples)
        if key not in ordered_keys
    )

    for key in ordered_keys:
        samples = trajectory_samples.get(key, [])
        if not samples:
            continue
        group_key = group_key_for_track(key)
        color = colors.get(group_key, "#444444")
        label = label_for_track(key) if group_key not in legend_drawn else None
        legend_drawn.add(group_key)
        if group_key == "ego":
            line_width = 2.8
        elif group_key in {DELIVERYBOT_ID, HUMANOID_ID}:
            line_width = 2.0
        else:
            line_width = 1.0
        alpha = 0.95 if group_key in {"ego", DELIVERYBOT_ID, HUMANOID_ID} else 0.4
        segments = plot_segments_for_track(key, samples)
        first_segment = True
        plotted_line = False
        for segment in segments:
            plot_points = plot_points_for_segment(
                key,
                segment,
                ego_route_planner=ego_route_planner,
            )
            if len(plot_points) < 2:
                continue
            xs = [point[0] for point in plot_points]
            ys = [point[1] for point in plot_points]
            ax.plot(
                xs,
                ys,
                color=color,
                linewidth=line_width,
                alpha=alpha,
                label=label if first_segment else None,
            )
            plotted_line = True
            first_segment = False

        if group_key in {"ego", DELIVERYBOT_ID, HUMANOID_ID}:
            xs = [sample["x"] for sample in samples]
            ys = [sample["y"] for sample in samples]
            start_label = f"{label_for_track(key)} start"
            end_label = f"{label_for_track(key)} end"
            if group_key == "ego" and len(xs) > 2:
                ax.scatter(
                    xs[1:-1],
                    ys[1:-1],
                    color=color,
                    s=22,
                    marker=".",
                    alpha=0.75,
                    zorder=6,
                )
            ax.scatter(
                xs[0],
                ys[0],
                color=color,
                s=42,
                marker="o",
                zorder=5,
                label=label if group_key == "ego" and not plotted_line else None,
            )
            ax.scatter(xs[-1], ys[-1], color=color, s=56, marker="X", zorder=6)
            ax.annotate(
                start_label,
                (xs[0], ys[0]),
                xytext=(6, 6),
                textcoords="offset points",
                fontsize=8,
                color=color,
            )
            ax.annotate(
                end_label,
                (xs[-1], ys[-1]),
                xytext=(6, -12),
                textcoords="offset points",
                fontsize=8,
                color=color,
            )

    title_suffix = ""
    if scenario_labels:
        title_suffix = " | " + ", ".join(scenario_labels[:4])
    ax.set_title(f"{map_name} Scenic + Custom Walker Trajectories{title_suffix}")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal", adjustable="box")
    apply_xy_bounds(ax, map_bounds)
    ax.legend(loc="best")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(png_path, dpi=180)
    plt.close(fig)

    focus_track_keys = collect_focus_track_keys(trajectory_samples, anchor_assignments)
    focus_bounds = compute_focus_bounds(trajectory_samples, anchor_assignments, focus_track_keys)
    focus_plot_bounds = map_bounds or focus_bounds

    focus_fig, focus_ax = plt.subplots(figsize=(12, 10))
    draw_carla_map_context(focus_ax, world, bounds=focus_plot_bounds)

    focus_colors = {
        "ego": "#1f77b4",
        "humanoid_anchor": "#2ca02c",
        "deliverybot_anchor": "#9467bd",
        HUMANOID_ID: "#d62728",
        DELIVERYBOT_ID: "#ff7f0e",
    }

    focus_anchor_count = len(
        {anchor.get("anchor_index") for anchor in anchor_assignments}
    )
    drawn_focus_anchor_indices = set()
    focus_legend_drawn = set()
    for anchor in anchor_assignments:
        location = anchor["anchor_location"]
        anchor_key = anchor["anchor_label"]
        if anchor["blueprint_id"] == HUMANOID_ID:
            anchor_color = focus_colors["humanoid_anchor"]
        else:
            anchor_color = focus_colors["deliverybot_anchor"]

        anchor_samples = trajectory_samples.get(anchor_key, [])
        if anchor_samples:
            xs = [sample["x"] for sample in anchor_samples]
            ys = [sample["y"] for sample in anchor_samples]
            focus_ax.plot(
                xs,
                ys,
                color=anchor_color,
                linewidth=1.6,
                alpha=0.9,
                linestyle="--",
                label=f"{anchor['track_label']} anchor track",
            )

        anchor_index = anchor.get("anchor_index")
        if anchor_index not in drawn_focus_anchor_indices:
            drawn_focus_anchor_indices.add(anchor_index)
            members = anchor.get("member_actors", [])
            if members:
                member_x = [member["location"]["x"] for member in members]
                member_y = [member["location"]["y"] for member in members]
                focus_ax.scatter(
                    member_x,
                    member_y,
                    s=34,
                    marker="s",
                    c="#6b7280",
                    alpha=0.8,
                    linewidths=0,
                    label=(
                        "anchor members" if "anchor_members" not in focus_legend_drawn else None
                    ),
                    zorder=4,
                )
                focus_legend_drawn.add("anchor_members")
            focus_ax.scatter(
                location["x"],
                location["y"],
                s=130,
                marker="*",
                c="#111111",
                zorder=7,
                label=(
                    "semantic anchors" if "semantic_anchors" not in focus_legend_drawn else None
                ),
            )
            focus_legend_drawn.add("semantic_anchors")
            if focus_anchor_count <= 8:
                focus_ax.annotate(
                    f"{anchor.get('anchor_kind', 'anchor')} ({anchor.get('member_count', 1)})",
                    (location["x"], location["y"]),
                    xytext=(8, 8),
                    textcoords="offset points",
                    fontsize=8,
                    color="#111111",
                )

        walker_samples = trajectory_samples.get(anchor["track_label"], [])
        if walker_samples:
            walker_start = walker_samples[0]
            focus_ax.plot(
                [walker_start["x"], location["x"]],
                [walker_start["y"], location["y"]],
                color=anchor_color,
                linewidth=1.2,
                alpha=0.85,
                linestyle=":",
            )

    for key in focus_track_keys:
        samples = trajectory_samples.get(key, [])
        if not samples:
            continue
        group_key = group_key_for_track(key)
        color = focus_colors.get(group_key, "#444444")
        if group_key == DELIVERYBOT_ID:
            legend_key = "deliverybot_observers"
            legend_label = "deliverybot observers"
        elif group_key == HUMANOID_ID:
            legend_key = "humanoid_observers"
            legend_label = "humanoid observers"
        elif group_key == "ego":
            legend_key = "ego"
            legend_label = "ego"
        else:
            legend_key = group_key
            legend_label = label_for_track(key)
        segments = plot_segments_for_track(key, samples)
        first_segment = True
        plotted_line = False
        for segment in segments:
            plot_points = plot_points_for_segment(
                key,
                segment,
                ego_route_planner=ego_route_planner,
            )
            if len(plot_points) < 2:
                continue
            xs = [point[0] for point in plot_points]
            ys = [point[1] for point in plot_points]
            focus_ax.plot(
                xs,
                ys,
                color=color,
                linewidth=3.0 if group_key == "ego" else 2.2,
                alpha=0.95,
                label=(
                    legend_label
                    if first_segment and legend_key not in focus_legend_drawn
                    else None
                ),
            )
            if first_segment:
                focus_legend_drawn.add(legend_key)
            plotted_line = True
            first_segment = False
        xs = [sample["x"] for sample in samples]
        ys = [sample["y"] for sample in samples]
        if group_key == "ego" and len(xs) > 2:
            focus_ax.scatter(
                xs[1:-1],
                ys[1:-1],
                color=color,
                s=24,
                marker=".",
                alpha=0.75,
                zorder=6,
            )
        focus_ax.scatter(
            xs[0],
            ys[0],
            color=color,
            s=42,
            marker="o",
            zorder=5,
            label=(
                legend_label
                if group_key == "ego"
                and not plotted_line
                and legend_key not in focus_legend_drawn
                else None
            ),
        )
        if group_key == "ego" and not plotted_line:
            focus_legend_drawn.add(legend_key)
        focus_ax.scatter(xs[-1], ys[-1], color=color, s=56, marker="X", zorder=6)

    apply_xy_bounds(focus_ax, focus_plot_bounds)

    focus_ax.set_title(f"{map_name} Full Map View: ego + anchors + custom walkers{title_suffix}")
    focus_ax.set_xlabel("x (m)")
    focus_ax.set_ylabel("y (m)")
    focus_ax.set_aspect("equal", adjustable="box")
    focus_ax.legend(loc="best", fontsize=8)
    focus_ax.grid(alpha=0.2)
    focus_fig.tight_layout()
    focus_fig.savefig(focus_png_path, dpi=180)
    plt.close(focus_fig)

    return str(png_path), str(focus_png_path), str(json_path)


def launch_scenic_process(
    config: ScenicCustomWalkerConfig,
    scenic_cmd: list[str],
    *,
    logger: Callable[[str], None],
) -> subprocess.Popen:
    scenario_summary = selected_abnormal_scenario_summary(scenic_cmd)
    logger("")
    logger("============================================================")
    logger(f"DAMOS selected abnormal scenarios: {scenario_summary}")
    logger("============================================================")
    if config.verbose:
        logger("Launching Scenic:")
        logger("  " + " ".join(scenic_cmd))
    return subprocess.Popen(
        scenic_cmd,
        cwd=str(scenic_root_for(config)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    )


def inject_custom_walkers_for_anchors(
    world,
    anchors,
    *,
    keep_existing_custom_walkers: bool,
    observer_mode: bool,
    max_observer_anchor_distance: float,
):
    last_invalid = ()
    for attempt in range(1, 4):
        spawned_walkers = spawn_custom_walkers_near_anchors(
            world,
            anchors,
            cleanup_existing=not keep_existing_custom_walkers if attempt == 1 else False,
            prefer_direct_spawn=observer_mode,
        )
        if observer_mode:
            stop_spawned_walker_controllers(spawned_walkers)
            orient_observers_to_current_anchors(world, spawned_walkers)
            try:
                world.wait_for_tick(1.0)
            except RuntimeError:
                time.sleep(0.5)
            invalid = find_invalid_anchor_spawned_walkers(
                world,
                spawned_walkers,
                max_anchor_error=max_observer_anchor_distance,
            )
        else:
            initialize_custom_walker_movement(world, spawned_walkers, random_spawn=True)
            invalid = find_invalid_anchor_spawned_walkers(world, spawned_walkers)
            if not invalid:
                invalid = probe_anchor_spawned_walkers(
                    world,
                    spawned_walkers,
                    probe_seconds=3.0,
                    min_move_meters=0.1,
                )
        if not invalid:
            # Start the metric window after spawn validation so the integration
            # check matches the Scenic observation window.
            initial_locations = snapshot_walker_locations(spawned_walkers)
            return spawned_walkers, initial_locations
        last_invalid = tuple(
            f"{spawned_walker.track_label}:{reason}"
            for spawned_walker, reason in invalid
        )
        destroy_spawned_walkers(spawned_walkers)

    details = ", ".join(last_invalid) if last_invalid else "unknown spawn validation failure"
    role = "custom observers" if observer_mode else "custom walkers"
    raise RuntimeError(
        f"Failed to keep {role} near their Scenic anchors after initialization: "
        f"{details}"
    )


def run_random_walker_routing(
    client,
    world,
    spawned_walkers,
    scenic_proc,
    duration_seconds,
    *,
    tracked_actors=None,
    trajectory_samples=None,
    sample_interval_seconds=0.5,
    ego_tracking_state: EgoTrackingState | None = None,
    observer_mode: bool = False,
    static_ego: bool = False,
    sample_lock=None,
    skip_sample_labels=(),
):
    scenic_deadline = time.monotonic() + max(1.0, duration_seconds + 10.0)
    started_at = time.monotonic()
    last_sample_at = None
    while time.monotonic() < scenic_deadline:
        if scenic_proc.poll() is not None:
            break
        latest_world = safe_get_world(client)
        if latest_world is not None:
            world = latest_world
        if tracked_actors is not None:
            ego_actor = resolve_current_ego_actor(
                world,
                ego_tracking_state=ego_tracking_state,
            )
            if ego_actor is not None:
                tracked_actors["ego"] = ego_actor.id
                if static_ego:
                    freeze_static_ego_actor(ego_actor)
                discover_scenic_support_actors(
                    world,
                    ego_actor,
                    spawned_walkers,
                    tracked_actors,
                )
        if observer_mode:
            orient_observers_to_current_anchors(world, spawned_walkers)
        elif any(spawned_walker.anchor is not None for spawned_walker in spawned_walkers):
            send_walkers_to_anchor_destinations(world, spawned_walkers)
        else:
            for spawned_walker in spawned_walkers:
                destination = world.get_random_location_from_navigation()
                if destination is not None:
                    spawned_walker.controller.go_to_location(destination)
        try:
            world.wait_for_tick(2.0)
        except RuntimeError:
            time.sleep(1.0)
        now = time.monotonic()
        if (
            tracked_actors is not None
            and trajectory_samples is not None
            and (last_sample_at is None or now - last_sample_at >= sample_interval_seconds)
        ):
            if sample_lock is None:
                sample_tracked_actors(
                    world,
                    tracked_actors,
                    trajectory_samples,
                    now - started_at,
                    ego_tracking_state=ego_tracking_state,
                    skip_labels=skip_sample_labels,
                )
            else:
                with sample_lock:
                    sample_tracked_actors(
                        world,
                        tracked_actors,
                        trajectory_samples,
                        now - started_at,
                        ego_tracking_state=ego_tracking_state,
                        skip_labels=skip_sample_labels,
                    )
            last_sample_at = now
    return world


def run_scenic_custom_walker_integration(
    config: ScenicCustomWalkerConfig,
    *,
    logger: Callable[[str], None] = print,
) -> ScenicCustomWalkerResult:
    config = validate_config(config)
    scenic_cmd = build_scenic_command(config)
    scenic_proc = launch_scenic_process(config, scenic_cmd, logger=logger)

    spawned_walkers = []
    output_lines: list[str] = []
    scenic_stopped_by_runner = False
    trajectory_report_png = None
    trajectory_report_focus_png = None
    trajectory_report_json = None
    anchor_assignments = ()
    detected_ego = None
    resolved_scenario_labels = ()
    observer_metrics = ()
    observer_camera_specs = ()
    observer_camera_attachments = ()
    observer_scene_captures = ()
    ego_camera_sensors = []
    s1_crossing_verification = ()
    ego_sampler = None
    tracked_actors = {}
    trajectory_samples = {}
    ego_tracking_state = None
    ego_front_camera_fault_state = build_ego_front_camera_fault_state("none")

    try:
        client, world = connect_to_world(
            config.host, config.port, config.wait_for_server_seconds
        )
        client.set_timeout(max(10.0, float(config.scenic_timeout_seconds)))
        logger(
            f"Connected to CARLA at {config.host}:{config.port} "
            f"map={safe_map_name(world)}"
        )

        try:
            world, ego = wait_for_ego_actor(client, config.wait_for_ego_seconds)
        except RuntimeError as exc:
            terminate_process(scenic_proc)
            collect_process_output(scenic_proc, output_lines)
            scenic_tail = "\n".join(output_lines[-40:]) or "<no Scenic output captured>"
            raise RuntimeError(
                f"{exc}\nRecent Scenic output:\n{scenic_tail}"
            ) from exc

        initial_ego_location_xyz = None
        try:
            ego_location = ego.get_transform().location
            initial_ego_location_xyz = location_to_xyz(ego_location)
            ego_attribute_color = ego.attributes.get("color")
            ego_location_text = (
                f" location=({ego_location.x:.2f}, {ego_location.y:.2f}, {ego_location.z:.2f})"
            )
            ego_color_text = f" color={ego_attribute_color}" if ego_attribute_color else ""
            detected_ego = {
                "actor_id": ego.id,
                "type_id": ego.type_id,
                "color": ego_attribute_color,
                "location": {
                    "x": float(ego_location.x),
                    "y": float(ego_location.y),
                    "z": float(ego_location.z),
                },
            }
        except RuntimeError:
            ego_location_text = ""
            ego_color_text = ""

        logger(
            f"Detected Scenic ego actor id={ego.id} type={ego.type_id} "
            f"map={safe_map_name(world)}{ego_location_text}{ego_color_text}"
        )
        if config.static_ego:
            freeze_static_ego_actor(ego)
            logger("Static ego mode enabled: ego vehicle is braked and hand-braked.")

        tracked_actors = {"ego": ego.id}
        ego_tracking_state = EgoTrackingState(
            preferred_id=ego.id,
            preferred_type_id=ego.type_id,
            last_resolved_id=ego.id,
            last_valid_location_xyz=initial_ego_location_xyz,
            last_valid_timestamp=0.0,
        )
        if initial_ego_location_xyz is not None:
            trajectory_samples.setdefault("ego", []).append(
                {
                    "t": 0.0,
                    "actor_id": ego.id,
                    "x": initial_ego_location_xyz[0],
                    "y": initial_ego_location_xyz[1],
                    "z": initial_ego_location_xyz[2],
                }
            )
        ego_sampler = start_continuous_ego_sampler(
            config.host,
            config.port,
            trajectory_samples,
            ego_tracking_state,
            interval_seconds=config.sample_interval_seconds,
        )

        min_anchor_candidates = config.n_scenarios if config.selected_scenario is None else 1
        readiness_check = None
        readiness_label = None
        accumulate_by = None
        if config.selected_scenario is None:
            accumulate_by = lambda candidate: candidate.get("scenario_instance")
            readiness_check = (
                lambda candidates: len(
                    {
                        candidate.get("scenario_instance")
                        for candidate in candidates
                        if candidate.get("scenario_instance")
                    }
                )
                >= config.n_scenarios
            )
            readiness_label = (
                f"at least {config.n_scenarios} distinct Scenic scenario instances"
            )
        world, raw_anchor_candidates = wait_for_anchor_candidates(
            client,
            ego,
            config.wait_for_support_seconds,
            min_candidates=min_anchor_candidates,
            candidate_filter=lambda candidates: filter_candidates_for_selected_scenario(
                candidates,
                config.selected_scenario,
            ),
            accumulate_by=accumulate_by,
            readiness_check=readiness_check,
            readiness_label=readiness_label,
        )
        anchor_candidates = semantic_anchor_candidates(
            raw_anchor_candidates,
            selected_scenario=config.selected_scenario,
        )
        resolved_scenario_labels = extract_scenario_labels_from_candidates(anchor_candidates)
        max_anchor_pairs = effective_anchor_pair_count(config, len(anchor_candidates))
        logger(
            f"Scenic anchors: raw={len(raw_anchor_candidates)}, "
            f"semantic={len(anchor_candidates)}"
        )
        blocked_anchor_actor_ids = set()
        last_anchor_error = None
        for anchor_attempt in range(1, 5):
            anchors = choose_custom_walker_anchors(
                anchor_candidates,
                max_anchor_pairs=max_anchor_pairs,
                observer_blueprint=config.observer_blueprint,
                blocked_actor_ids=blocked_anchor_actor_ids,
            )
            if not anchors:
                raise RuntimeError(
                    "No usable Scenic anchor pairs were selected for custom observers."
                )
            anchor_assignments = serialize_anchor_assignments(anchors)

            if config.verbose:
                logger("Selected Scenic anchors for custom walkers:")
                for assignment in anchor_assignments:
                    location = assignment["anchor_location"]
                    logger(
                        f"  anchor_pair={assignment['anchor_index']} "
                        f"kind={assignment['anchor_kind']} "
                        f"members={assignment['member_count']} "
                        f"role={assignment['observer_role']} "
                        f"walker={assignment['blueprint_id']} "
                        f"anchor={assignment['anchor_label']} "
                        f"type={assignment['anchor_type_id']} "
                        f"location=({location['x']:.2f}, {location['y']:.2f}, {location['z']:.2f})"
                    )

            try:
                spawned_walkers, initial_locations = inject_custom_walkers_for_anchors(
                    world,
                    anchors,
                    keep_existing_custom_walkers=config.keep_existing_custom_walkers,
                    observer_mode=config.observer_mode,
                    max_observer_anchor_distance=config.max_observer_anchor_distance,
                )
                break
            except RuntimeError as exc:
                last_anchor_error = exc
                if len(anchor_candidates) > max_anchor_pairs:
                    for anchor in anchors:
                        blocked_anchor_actor_ids.add(anchor.actor_id)
                    retry_target = "a different Scenic anchor set"
                else:
                    retry_target = "the required Scenic anchor set"
                logger(
                    "Anchor set failed after spawn validation "
                    f"({exc}); trying {retry_target} ({anchor_attempt}/4)."
                )
        else:
            raise RuntimeError(
                "Failed to place custom walkers near valid Scenic anchors after "
                f"multiple fallback attempts: {last_anchor_error}"
            ) from last_anchor_error

        spawned_walkers = ensure_persistent_members_for_spawned_walkers(
            world,
            spawned_walkers,
        )
        anchor_assignments = serialize_anchor_assignments(
            tuple(
                spawned_walker.anchor
                for spawned_walker in spawned_walkers
                if spawned_walker.anchor is not None
            )
        )

        if config.observer_mode:
            logger(f"Spawned custom observers: {len(spawned_walkers)}")
        else:
            logger(f"Spawned custom walkers: {len(spawned_walkers)}")
        if config.verbose:
            for spawned_walker in spawned_walkers:
                location = spawned_walker.walker.get_transform().location
                logger(
                    f"  walker={spawned_walker.track_label} "
                    f"actor_id={spawned_walker.walker.id} "
                    f"controller_id={spawned_walker.controller.id} "
                    f"location=({location.x:.2f}, {location.y:.2f}, {location.z:.2f})"
                )

        if config.save_observer_scene_captures:
            immediate_captures = save_immediate_topdown_overview_capture(
                world,
                spawned_walkers,
                config,
                scenario_labels=resolved_scenario_labels,
            )
            observer_scene_captures = tuple(
                [*observer_scene_captures, *immediate_captures]
            )
            saved_count = sum(
                1 for capture in immediate_captures if capture.get("status") == "saved"
            )
            logger(
                f"Saved {saved_count}/{len(immediate_captures)} immediate "
                "overview/side captures."
            )

        if config.attach_observer_cameras:
            vehicle_camera_specs = load_vehicle_camera_specs(config.observer_camera_config)
            resolved_ego_front_camera_fault = resolve_ego_front_camera_fault(
                config.ego_front_camera_fault
            )
            ego_front_camera_fault_state = build_ego_front_camera_fault_state(
                resolved_ego_front_camera_fault
            )
            ego_front_camera_fault_state["requested_fault"] = config.ego_front_camera_fault
            ego_front_camera_fault_state["target_track_label"] = "ego"
            ego_front_camera_fault_state["target_sensor_name"] = "cam_front"
            if resolved_ego_front_camera_fault == "misalignment":
                vehicle_camera_specs = apply_ego_front_camera_misalignment_to_specs(
                    vehicle_camera_specs,
                    ego_front_camera_fault_state,
                )
            camera_specs = load_observer_camera_specs(config.observer_camera_config)
            observer_camera_specs = (
                *serialize_observer_camera_specs(
                    vehicle_camera_specs,
                    target_kind="ego_vehicle",
                ),
                *serialize_observer_camera_specs(
                    camera_specs,
                    target_kind="custom_observer",
                ),
            )
            ego_camera_attachments = attach_actor_cameras(
                world,
                ego,
                vehicle_camera_specs,
                track_label="ego",
                actor_role="ego_vehicle",
                sensor_store=ego_camera_sensors,
            )
            observer_camera_attachments = attach_observer_cameras(
                world,
                spawned_walkers,
                camera_specs,
            )
            observer_camera_attachments = (
                *ego_camera_attachments,
                *observer_camera_attachments,
            )
            logger(
                "Attached actor cameras: "
                f"ego={len(ego_camera_attachments)}, "
                f"custom_observers={len(observer_camera_attachments) - len(ego_camera_attachments)} "
                f"using {config.observer_camera_config}."
            )
            if resolved_ego_front_camera_fault != "none":
                logger(
                    "Ego front camera fault target: "
                    f"ego/cam_front fault={resolved_ego_front_camera_fault} "
                    f"(requested={config.ego_front_camera_fault})."
                )
            if config.save_actor_camera_captures:
                actor_camera_captures = save_actor_camera_captures(
                    world,
                    observer_camera_attachments,
                    config,
                    scenario_labels=resolved_scenario_labels,
                    ego_front_camera_fault_state=ego_front_camera_fault_state,
                )
                observer_scene_captures = tuple(
                    [*observer_scene_captures, *actor_camera_captures]
                )
                saved_count = sum(
                    1
                    for capture in actor_camera_captures
                    if capture.get("status") == "saved"
                )
                logger(
                    f"Saved {saved_count}/{len(actor_camera_captures)} "
                    "attached actor camera captures."
                )
        else:
            logger("Ego/custom observer camera attachment disabled for this run.")

        if config.save_ego_fault_report:
            ego_fault_report_captures = save_ego_fault_report(
                world,
                ego,
                observer_camera_attachments,
                config,
                scenario_labels=resolved_scenario_labels,
            )
            observer_scene_captures = tuple(
                [*observer_scene_captures, *ego_fault_report_captures]
            )
            saved_count = sum(
                1
                for capture in ego_fault_report_captures
                if capture.get("status") == "saved"
            )
            logger(
                f"Saved {saved_count}/{len(ego_fault_report_captures)} "
                "ego-centric sensor/module fault report artifacts."
            )

        tracked_actors.update(
            (label, actor_id)
            for label, actor_id in build_tracked_actor_map(ego, spawned_walkers).items()
            if label != "ego"
        )
        for assignment in anchor_assignments:
            anchor_actor_id = assignment.get("anchor_actor_id")
            anchor_label = assignment.get("anchor_label")
            if anchor_actor_id is not None and anchor_label:
                tracked_actors.setdefault(str(anchor_label), int(anchor_actor_id))
            for member in assignment.get("member_actors", ()):
                member_actor_id = member.get("actor_id")
                if member_actor_id is None or member_actor_id == anchor_actor_id:
                    continue
                tracked_actors.setdefault(
                    f"scenic.member:{int(member_actor_id)}",
                    int(member_actor_id),
                )
        discover_scenic_support_actors(world, ego, spawned_walkers, tracked_actors)
        if ego_sampler is None:
            sample_tracked_actors(
                world,
                tracked_actors,
                trajectory_samples,
                0.0,
                ego_tracking_state=ego_tracking_state,
            )
        else:
            with ego_sampler["lock"]:
                sample_tracked_actors(
                    world,
                    tracked_actors,
                    trajectory_samples,
                    0.0,
                    ego_tracking_state=ego_tracking_state,
                    skip_labels=("ego",),
                )

        if config.save_observer_scene_captures:
            later_captures = save_observer_scene_captures(
                world,
                spawned_walkers,
                observer_camera_attachments,
                config,
                scenario_labels=resolved_scenario_labels,
            )
            observer_scene_captures = tuple(
                [*observer_scene_captures, *later_captures]
            )
            saved_count = sum(
                1
                for capture in later_captures
                if capture.get("status") == "saved"
            )
            logger(
                f"Saved {saved_count}/{len(later_captures)} observer "
                "scene/front camera captures."
            )

        if config.verify_s1_crossing_autopilot:
            logger(
                "Verifying S1 pedestrian crossing with Scenic ego autopilot approach."
            )
            s1_crossing_verification = verify_s1_crossing_autopilot(
                client,
                world,
                ego,
                anchor_assignments,
                config,
                logger=logger,
            )

        world = run_random_walker_routing(
            client,
            world,
            spawned_walkers,
            scenic_proc,
            config.scenic_time,
            tracked_actors=tracked_actors,
            trajectory_samples=trajectory_samples,
            sample_interval_seconds=config.sample_interval_seconds,
            ego_tracking_state=ego_tracking_state,
            observer_mode=config.observer_mode,
            static_ego=config.static_ego,
            sample_lock=ego_sampler["lock"] if ego_sampler is not None else None,
            skip_sample_labels=("ego",),
        )

        stop_continuous_ego_sampler(ego_sampler)
        ego_sampler = None
        sample_tracked_actors(
            world,
            tracked_actors,
            trajectory_samples,
            config.scenic_time,
            ego_tracking_state=ego_tracking_state,
        )

        if scenic_proc.poll() is None:
            logger("Scenic still running after the expected window; stopping it now.")
            scenic_stopped_by_runner = True
            terminate_process(scenic_proc)

        collect_process_output(scenic_proc, output_lines)
        scenario_labels = extract_scenario_labels(output_lines)
        if resolved_scenario_labels:
            scenario_labels = resolved_scenario_labels

        movement_failures = []
        walker_movements = {}
        if config.verbose:
            logger("Final custom walker positions after Scenic run:")
        for spawned_walker, final_location, moved in measure_walker_movements(
            spawned_walkers,
            initial_locations,
            trajectory_samples=trajectory_samples,
        ):
            walker_movements[spawned_walker.track_label] = moved
            if config.verbose:
                logger(
                    f"  walker={spawned_walker.track_label} "
                    f"final_location=({final_location.x:.2f}, {final_location.y:.2f}, {final_location.z:.2f}) "
                    f"moved={moved:.2f}m"
                )
            if not config.observer_mode and moved < config.min_move_meters:
                movement_failures.append((spawned_walker.track_label, moved))

        observer_failures = []
        if config.observer_mode:
            observer_metrics = build_observer_metrics(
                world,
                spawned_walkers,
                detected_ego=detected_ego,
            )
            observer_failures = find_observer_metric_failures(
                observer_metrics,
                max_anchor_distance=config.max_observer_anchor_distance,
                max_facing_error_degrees=config.max_observer_facing_error_degrees,
            )
            logger(
                f"Observer coverage: checked={len(observer_metrics)}, "
                f"failures={len(observer_failures)}"
            )
            if config.verbose:
                logger("Final custom observer coverage metrics:")
                for metric in observer_metrics:
                    logger(
                        f"  observer={metric.get('track_label')} "
                        f"anchor={metric.get('anchor_label')} "
                        f"distance={metric.get('observer_to_anchor_distance')}m "
                        f"facing_error={metric.get('facing_error_degrees')}deg "
                        f"ego_to_anchor={metric.get('ego_to_anchor_distance')}m "
                        f"status={metric.get('status')}"
                    )

        allowed_returncodes = {0, None}
        if scenic_stopped_by_runner:
            allowed_returncodes.update({-15, 143})

        if scenic_proc.returncode not in allowed_returncodes:
            scenic_tail = "\n".join(output_lines[-20:])
            raise RuntimeError(
                f"Scenic exited with code {scenic_proc.returncode}.\n"
                f"Recent Scenic output:\n{scenic_tail}"
            )

        if config.save_trajectory_report:
            trajectory_report_png, trajectory_report_focus_png, trajectory_report_json = save_trajectory_report(
                world,
                trajectory_samples,
                config,
                scenario_labels=scenario_labels,
                anchor_assignments=anchor_assignments,
                ego_tracking_state=ego_tracking_state,
                detected_ego=detected_ego,
                walker_movements=walker_movements,
                observer_metrics=observer_metrics,
                observer_camera_specs=observer_camera_specs,
                observer_camera_attachments=observer_camera_attachments,
                observer_scene_captures=observer_scene_captures,
                s1_crossing_verification=s1_crossing_verification,
            )
            if config.verbose:
                logger(f"Saved trajectory PNG: {trajectory_report_png}")
                logger(f"Saved focus trajectory PNG: {trajectory_report_focus_png}")
                logger(f"Saved trajectory JSON: {trajectory_report_json}")

        if observer_failures:
            details = ", ".join(
                f"{track_label}:{reason}" for track_label, reason in observer_failures
            )
            raise RuntimeError(
                "Custom observers did not satisfy Scenic anchor coverage requirements. "
                f"Observed: {details}"
            )

        s1_crossing_failures = [
            result
            for result in s1_crossing_verification
            if not result.get("passed")
        ]
        if s1_crossing_failures:
            details = ", ".join(
                f"anchor{result.get('anchor_index')}:"
                f"min_ego={result.get('min_ego_distance')}m,"
                f"ped_move={result.get('max_pedestrian_movement')}m"
                for result in s1_crossing_failures
            )
            raise RuntimeError(
                "S1 crossing autopilot verification failed. "
                f"Observed: {details}"
            )

        if movement_failures:
            details = ", ".join(
                f"{walker_type}={moved:.2f}m"
                for walker_type, moved in movement_failures
            )
            raise RuntimeError(
                f"Custom walkers did not move enough during Scenic run; "
                f"required at least {config.min_move_meters:.2f}m. Observed: {details}"
            )

        if config.observer_mode:
            logger("Scenic + custom observer integration check passed.")
        else:
            logger("Scenic + custom walker integration check passed.")
        return ScenicCustomWalkerResult(
            scenic_command=tuple(scenic_cmd),
            map_name=safe_map_name(world),
            ego_actor_id=ego.id,
            ego_type_id=ego.type_id,
            scenario_labels=scenario_labels,
            anchor_assignments=anchor_assignments,
            walker_movements=walker_movements,
            observer_metrics=observer_metrics,
            observer_camera_specs=observer_camera_specs,
            observer_camera_attachments=observer_camera_attachments,
            observer_scene_captures=observer_scene_captures,
            scenic_returncode=scenic_proc.returncode,
            scenic_output_tail=tuple(output_lines[-20:]),
            trajectory_report_png=trajectory_report_png,
            trajectory_report_focus_png=trajectory_report_focus_png,
            trajectory_report_json=trajectory_report_json,
        )
    finally:
        stop_continuous_ego_sampler(ego_sampler)
        for sensor in ego_camera_sensors:
            try:
                sensor.destroy()
            except RuntimeError:
                pass
        ego_camera_sensors.clear()
        destroy_spawned_walkers(spawned_walkers)
        terminate_process(scenic_proc)
