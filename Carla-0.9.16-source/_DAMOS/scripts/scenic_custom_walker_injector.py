#!/usr/bin/env python3

from __future__ import annotations

import argparse
import queue
from dataclasses import dataclass
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

from custom_walker_runtime import (
    CustomWalkerAnchor,
    DELIVERYBOT_ID,
    HUMANOID_ID,
    attach_observer_cameras,
    connect_to_world,
    distance_between,
    destroy_spawned_walkers,
    find_invalid_anchor_spawned_walkers,
    initialize_custom_walker_movement,
    is_sidewalk_location,
    load_observer_camera_specs,
    measure_walker_movements,
    probe_anchor_spawned_walkers,
    send_walkers_to_anchor_destinations,
    serialize_transform,
    serialize_observer_camera_specs,
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
BICYCLE_KEYWORDS = ("crossbike", "omafiets", "diamondback", "gazelle", "century")
DAMOS_SCENARIO_ROLE_PREFIX = "damos."


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
    save_observer_scene_captures: bool = False
    capture_image_width: int = 1280
    capture_image_height: int = 720
    capture_timeout_seconds: float = 6.0


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
    parser.add_argument("--n-scenarios", type=int, default=1)
    parser.add_argument(
        "--selected-scenario",
        choices=("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"),
        default=None,
        help="Force BaseSetup to compose one specific Scenic abnormal scenario.",
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
        help="Attach six RGB cameras to each custom observer (default).",
    )
    camera_group.add_argument(
        "--no-observer-cameras",
        dest="attach_observer_cameras",
        action="store_false",
        help="Spawn observers without attaching camera sensor actors.",
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
    parser.add_argument("--capture-image-width", type=int, default=1280)
    parser.add_argument("--capture-image-height", type=int, default=720)
    parser.add_argument("--capture-timeout-seconds", type=float, default=6.0)
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
        save_observer_scene_captures=args.save_observer_scene_captures,
        capture_image_width=args.capture_image_width,
        capture_image_height=args.capture_image_height,
        capture_timeout_seconds=args.capture_timeout_seconds,
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


def wait_for_ego_actor(client, timeout_seconds: float):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        world = safe_get_world(client)
        if world is None:
            time.sleep(1.0)
            continue
        try:
            for actor in world.get_actors():
                if (
                    actor.attributes.get("role_name") == "ego"
                    and actor.type_id.startswith("vehicle.")
                ):
                    try:
                        location = actor.get_transform().location
                    except RuntimeError:
                        continue
                    if is_all_zero_location(location):
                        continue
                    return world, actor
        except RuntimeError:
            time.sleep(1.0)
            continue
        time.sleep(0.5)
    raise RuntimeError(
        f"Timed out after {timeout_seconds:.0f}s waiting for Scenic ego actor."
    )


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

    candidates.sort(
        key=lambda item: (item["priority"], item["distance_to_ego"], item["actor_id"])
    )
    return candidates


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
            if scenario_label == "S4":
                semantic.extend(
                    make_individual_anchor_candidates(
                        group,
                        label_prefix=f"scenic.random_scenario:{scenario_instance}.road_obstacle",
                        kind="scenario_s4_road_obstacle",
                    )
                )
                continue
            semantic.append(
                make_semantic_anchor_candidate(
                    group,
                    label=f"scenic.random_scenario:{scenario_instance}",
                    kind=f"scenario_{scenario_label.lower()}",
                    category=group[0]["category"],
                )
            )
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
            if "trashbag" in type_id:
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
        semantic.append(
            make_semantic_anchor_candidate(
                vehicles,
                label="scenic.vehicle_region:1",
                kind="vehicle_region",
                category="vehicle",
            )
        )

    if construction_props:
        semantic.append(
            make_semantic_anchor_candidate(
                construction_props,
                label="scenic.construction_region:1",
                kind="construction_region",
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
    if selected_scenario in {"S6", "S7", "S9"}:
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
            return world, candidates_to_check
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


def resolve_anchor_location(world, anchor: CustomWalkerAnchor):
    if not getattr(anchor, "dynamic_actor_location", True):
        return anchor.location
    anchor_actor = world.get_actor(anchor.actor_id)
    if anchor_actor is not None:
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
            is_sidewalk_location(world, location, max_project_distance=2.0)
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
        if anchor_distance > max_anchor_distance:
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


def configure_rgb_camera_blueprint(world, *, width, height, fov=80):
    blueprint = world.get_blueprint_library().find("sensor.camera.rgb")
    if blueprint.has_attribute("image_size_x"):
        blueprint.set_attribute("image_size_x", str(int(width)))
    if blueprint.has_attribute("image_size_y"):
        blueprint.set_attribute("image_size_y", str(int(height)))
    if blueprint.has_attribute("fov"):
        blueprint.set_attribute("fov", str(int(fov)))
    return blueprint


def draw_observer_capture_markers(world, observer_location, anchor_location, *, role):
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
            size=0.28,
            color=carla.Color(0, 220, 0),
            life_time=8.0,
        )
        world.debug.draw_point(
            anchor_marker,
            size=0.28,
            color=carla.Color(255, 0, 0),
            life_time=8.0,
        )
        world.debug.draw_line(
            observer_marker,
            anchor_marker,
            thickness=0.08,
            color=carla.Color(0, 220, 0),
            life_time=8.0,
        )
        world.debug.draw_string(
            carla.Location(observer_marker.x, observer_marker.y, observer_marker.z + 0.55),
            f"observer:{role}",
            draw_shadow=True,
            color=carla.Color(0, 220, 0),
            life_time=8.0,
        )
        world.debug.draw_string(
            carla.Location(anchor_marker.x, anchor_marker.y, anchor_marker.z + 0.55),
            "anchor",
            draw_shadow=True,
            color=carla.Color(255, 0, 0),
            life_time=8.0,
        )
    except RuntimeError:
        pass


def capture_rgb_sensor_frame(world, sensor, path, *, timeout_seconds):
    frames = queue.Queue(maxsize=1)

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
            image.save_to_disk(str(path))
            return True
    finally:
        try:
            sensor.stop()
        except RuntimeError:
            pass
    return False


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

    for spawned_walker in spawned_walkers:
        observer_location = try_get_actor_location(spawned_walker.walker)
        if observer_location is None or spawned_walker.anchor is None:
            continue
        anchor_location = resolve_anchor_location(world, spawned_walker.anchor)
        observer_transform = spawned_walker.walker.get_transform()
        target_yaw = yaw_toward(observer_location, anchor_location)
        facing_error = abs(normalize_degrees(observer_transform.rotation.yaw - target_yaw))
        role = spawned_walker.anchor.observer_role or spawned_walker.spec.blueprint_id
        anchor_index = spawned_walker.anchor.anchor_index or len(captures) + 1
        prefix = f"anchor{anchor_index}_{sanitize_filename_fragment(role)}"
        draw_observer_capture_markers(
            world,
            observer_location,
            anchor_location,
            role=role,
        )

        scene_path = capture_dir / f"{prefix}_scene.png"
        scene_transform = build_observer_scene_camera_transform(
            observer_location,
            anchor_location,
        )
        scene_sensor = world.spawn_actor(scene_camera_bp, scene_transform)
        try:
            scene_ok = capture_rgb_sensor_frame(
                world,
                scene_sensor,
                scene_path,
                timeout_seconds=config.capture_timeout_seconds,
            )
        finally:
            try:
                scene_sensor.destroy()
            except RuntimeError:
                pass
        captures.append(
            {
                "capture_type": "external_scene",
                "track_label": spawned_walker.track_label,
                "observer_role": role,
                "anchor_index": anchor_index,
                "anchor_label": spawned_walker.anchor.label,
                "path": str(scene_path),
                "status": "saved" if scene_ok else "timeout",
                "observer_location": serialize_location(observer_location),
                "anchor_location": serialize_location(anchor_location),
                "observer_yaw_degrees": round(float(observer_transform.rotation.yaw), 3),
                "target_yaw_degrees": round(float(target_yaw), 3),
                "facing_error_degrees": round(float(facing_error), 3),
                "camera_transform": serialize_transform(scene_transform),
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
                    "anchor_label": spawned_walker.anchor.label,
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
                "anchor_label": spawned_walker.anchor.label,
                "path": str(front_path),
                "status": "saved" if front_ok else "timeout",
                "facing_error_degrees": round(float(facing_error), 3),
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
    scenic_time = max(1, int(round(config.scenic_time)))
    command = [
        str(config.scenic_bin),
        str(config.scenic_file),
        "--model",
        "scenic.simulators.carla.model",
        "--simulate",
        "--2d",
        "--scenario",
        "BaseSetup",
        "--time",
        str(scenic_time),
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
    if config.carla_map:
        command.extend(["--param", "carla_map", config.carla_map])
    if config.map_xodr:
        command.extend(["--param", "map", str(pathlib.Path(config.map_xodr).resolve())])
    if config.weather:
        command.extend(["--param", "weather", config.weather])
    return command


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
    ego_sampler = None
    tracked_actors = {}
    trajectory_samples = {}
    ego_tracking_state = None

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
            f"Found {len(raw_anchor_candidates)} raw Scenic support actors; "
            f"using {len(anchor_candidates)} semantic anchor candidates."
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
                for anchor in anchors:
                    blocked_anchor_actor_ids.add(anchor.actor_id)
                logger(
                    "Anchor set failed after spawn validation; trying a different Scenic "
                    f"anchor set ({anchor_attempt}/4)."
                )
        else:
            raise RuntimeError(
                "Failed to place custom walkers near valid Scenic anchors after "
                f"multiple fallback attempts: {last_anchor_error}"
            ) from last_anchor_error

        if config.attach_observer_cameras:
            camera_specs = load_observer_camera_specs(config.observer_camera_config)
            observer_camera_specs = serialize_observer_camera_specs(camera_specs)
            observer_camera_attachments = attach_observer_cameras(
                world,
                spawned_walkers,
                camera_specs,
            )
            logger(
                f"Attached {len(observer_camera_attachments)} observer cameras "
                f"using {config.observer_camera_config}."
            )
        else:
            logger("Observer camera attachment disabled for this run.")

        tracked_actors.update(
            (label, actor_id)
            for label, actor_id in build_tracked_actor_map(ego, spawned_walkers).items()
            if label != "ego"
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

        if config.observer_mode:
            logger("Spawned custom observers during Scenic simulation:")
        else:
            logger("Spawned custom walkers during Scenic simulation:")
        for spawned_walker in spawned_walkers:
            location = spawned_walker.walker.get_transform().location
            logger(
                f"  walker={spawned_walker.track_label} "
                f"actor_id={spawned_walker.walker.id} "
                f"controller_id={spawned_walker.controller.id} "
                f"location=({location.x:.2f}, {location.y:.2f}, {location.z:.2f})"
            )

        if config.save_observer_scene_captures:
            observer_scene_captures = save_observer_scene_captures(
                world,
                spawned_walkers,
                observer_camera_attachments,
                config,
                scenario_labels=resolved_scenario_labels,
            )
            saved_count = sum(
                1
                for capture in observer_scene_captures
                if capture.get("status") == "saved"
            )
            logger(
                f"Saved {saved_count}/{len(observer_scene_captures)} observer "
                "scene/front camera captures."
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
        logger("Final custom walker positions after Scenic run:")
        for spawned_walker, final_location, moved in measure_walker_movements(
            spawned_walkers,
            initial_locations,
            trajectory_samples=trajectory_samples,
        ):
            walker_movements[spawned_walker.track_label] = moved
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
            )
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
        destroy_spawned_walkers(spawned_walkers)
        terminate_process(scenic_proc)
