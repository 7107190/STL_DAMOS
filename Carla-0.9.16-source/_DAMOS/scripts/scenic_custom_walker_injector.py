#!/usr/bin/env python3

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import pathlib
import re
import subprocess
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
    load_observer_camera_specs,
    measure_walker_movements,
    probe_anchor_spawned_walkers,
    send_walkers_to_anchor_destinations,
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
SCENIC_SCENARIO_PATTERN = re.compile(r"^(S[_0-9]+):")
BICYCLE_KEYWORDS = ("crossbike", "omafiets", "diamondback", "gazelle", "century")


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
        "--max-anchor-pairs",
        type=int,
        default=None,
        help=(
            "Maximum Scenic anchors to cover. Each selected anchor gets one "
            "humanoid observer and one deliverybot observer. When omitted, all "
            "semantic anchor candidates are covered."
        ),
    )
    parser.add_argument(
        "--max-deliverybots",
        type=int,
        default=0,
        help="Legacy cap kept for compatibility; use --max-anchor-pairs instead.",
    )
    parser.add_argument(
        "--max-humanoids",
        type=int,
        default=0,
        help="Legacy cap kept for compatibility; use --max-anchor-pairs instead.",
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
    if not 0.0 <= config.max_observer_facing_error_degrees <= 180.0:
        raise ValueError("--max-observer-facing-error-degrees must be between 0 and 180.")
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

        category, priority = classified
        location = actor.get_transform().location
        candidates.append(
            {
                "actor": actor,
                "actor_id": actor.id,
                "type_id": actor.type_id,
                "category": category,
                "priority": priority,
                "distance_to_ego": distance_between(location, ego_location),
                "location": location,
            }
        )

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
    return semantic_candidate


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
            candidate for candidate in candidates if candidate["category"] == "prop"
        ]
        if road_obstacles:
            return [
                make_semantic_anchor_candidate(
                    road_obstacles,
                    label="scenic.obstacle_region:1",
                    kind="obstacle_region",
                    category="prop",
                )
            ]

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
        return [candidate for candidate in candidates if candidate["category"] == "prop"]
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
):
    deadline = time.monotonic() + timeout_seconds
    required_candidates = max(1, min_candidates)
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
        if len(filtered_candidates) >= required_candidates:
            return world, filtered_candidates
        time.sleep(0.5)
    raise RuntimeError(
        f"Timed out after {timeout_seconds:.0f}s waiting for at least "
        f"{required_candidates} Scenic support actors."
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


def choose_custom_walker_anchors(
    candidates,
    *,
    max_anchor_pairs: int,
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

    for anchor_index, candidate in enumerate(selected_anchor_candidates, start=1):
        for blueprint_id, role_label in (
            (HUMANOID_ID, "humanoid"),
            (DELIVERYBOT_ID, "deliverybot"),
        ):
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

    candidates = []
    try:
        actors = world.get_actors()
    except RuntimeError:
        return None

    for actor in actors:
        try:
            if actor.attributes.get("role_name") != "ego":
                continue
            if not actor.type_id.startswith("vehicle."):
                continue
            location = actor.get_transform().location
        except RuntimeError:
            continue
        if is_all_zero_location(location):
            continue
        candidates.append(
            (
                0 if actor.type_id == ego_tracking_state.preferred_type_id else 1,
                distance_to_xyz(location, ego_tracking_state.last_valid_location_xyz),
                actor.id,
                actor,
            )
        )

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    best_type_penalty, best_distance, _, actor = candidates[0]
    if (
        ego_tracking_state.last_valid_location_xyz is not None
        and best_distance != float("inf")
        and best_distance > 35.0
        and best_type_penalty >= 0
    ):
        return None
    try:
        location = actor.get_transform().location
    except RuntimeError:
        return None
    ego_tracking_state.last_resolved_id = actor.id
    ego_tracking_state.last_valid_location_xyz = location_to_xyz(location)
    return actor


def sample_tracked_actors(
    world,
    tracked_actor_ids,
    trajectory_samples,
    timestamp_seconds,
    *,
    ego_tracking_state: EgoTrackingState | None = None,
):
    try:
        snapshot = world.get_snapshot()
    except RuntimeError:
        snapshot = None

    for label, actor_id in tracked_actor_ids.items():
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

    fig, ax = plt.subplots(figsize=(10, 10))

    road_x = []
    road_y = []
    try:
        for waypoint in world.get_map().generate_waypoints(8.0):
            road_x.append(waypoint.transform.location.x)
            road_y.append(waypoint.transform.location.y)
    except RuntimeError:
        road_x = []
        road_y = []

    if road_x and road_y:
        ax.scatter(road_x, road_y, s=1, c="#d7d7d7", alpha=0.35, linewidths=0)

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
        line_width = 2.0 if group_key in {"ego", DELIVERYBOT_ID, HUMANOID_ID} else 1.0
        alpha = 0.95 if group_key in {"ego", DELIVERYBOT_ID, HUMANOID_ID} else 0.4
        segments = split_trajectory_segments(samples)
        first_segment = True
        for segment in segments:
            xs = [sample["x"] for sample in segment]
            ys = [sample["y"] for sample in segment]
            ax.plot(
                xs,
                ys,
                color=color,
                linewidth=line_width,
                alpha=alpha,
                label=label if first_segment else None,
            )
            first_segment = False

        if group_key in {"ego", DELIVERYBOT_ID, HUMANOID_ID}:
            xs = [sample["x"] for sample in samples]
            ys = [sample["y"] for sample in samples]
            start_label = f"{label_for_track(key)} start"
            end_label = f"{label_for_track(key)} end"
            ax.scatter(xs[0], ys[0], color=color, s=42, marker="o", zorder=5)
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
    ax.legend(loc="best")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(png_path, dpi=180)
    plt.close(fig)

    focus_track_keys = collect_focus_track_keys(trajectory_samples, anchor_assignments)
    focus_bounds = compute_focus_bounds(trajectory_samples, anchor_assignments, focus_track_keys)

    focus_fig, focus_ax = plt.subplots(figsize=(10, 10))
    if road_x and road_y and focus_bounds is not None:
        min_x, max_x, min_y, max_y = focus_bounds
        filtered_x = []
        filtered_y = []
        for x, y in zip(road_x, road_y):
            if min_x <= x <= max_x and min_y <= y <= max_y:
                filtered_x.append(x)
                filtered_y.append(y)
        if filtered_x and filtered_y:
            focus_ax.scatter(
                filtered_x, filtered_y, s=2, c="#d7d7d7", alpha=0.45, linewidths=0
            )

    focus_colors = {
        "ego": "#1f77b4",
        "humanoid_anchor": "#2ca02c",
        "deliverybot_anchor": "#9467bd",
        HUMANOID_ID: "#d62728",
        DELIVERYBOT_ID: "#ff7f0e",
    }

    drawn_focus_anchor_indices = set()
    drew_focus_anchor_members = False
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
                    label="anchor members" if not drew_focus_anchor_members else None,
                    zorder=4,
                )
                drew_focus_anchor_members = True
            focus_ax.scatter(
                location["x"],
                location["y"],
                s=130,
                marker="*",
                c="#111111",
                zorder=7,
                label="semantic anchor",
            )
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
        segments = split_trajectory_segments(samples)
        first_segment = True
        for segment in segments:
            xs = [sample["x"] for sample in segment]
            ys = [sample["y"] for sample in segment]
            focus_ax.plot(
                xs,
                ys,
                color=color,
                linewidth=2.2,
                alpha=0.95,
                label=label_for_track(key) if first_segment else None,
            )
            first_segment = False
        xs = [sample["x"] for sample in samples]
        ys = [sample["y"] for sample in samples]
        focus_ax.scatter(xs[0], ys[0], color=color, s=42, marker="o", zorder=5)
        focus_ax.scatter(xs[-1], ys[-1], color=color, s=56, marker="X", zorder=6)

    if focus_bounds is not None:
        min_x, max_x, min_y, max_y = focus_bounds
        focus_ax.set_xlim(min_x, max_x)
        focus_ax.set_ylim(min_y, max_y)

    focus_ax.set_title(f"{map_name} Focus View: ego + anchors + custom walkers{title_suffix}")
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
                spawned_walkers,
                max_anchor_error=max_observer_anchor_distance,
            )
        else:
            initialize_custom_walker_movement(world, spawned_walkers, random_spawn=True)
            invalid = find_invalid_anchor_spawned_walkers(spawned_walkers)
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
            sample_tracked_actors(
                world,
                tracked_actors,
                trajectory_samples,
                now - started_at,
                ego_tracking_state=ego_tracking_state,
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
    observer_metrics = ()
    observer_camera_specs = ()
    observer_camera_attachments = ()

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

        try:
            ego_location = ego.get_transform().location
            ego_location_text = (
                f" location=({ego_location.x:.2f}, {ego_location.y:.2f}, {ego_location.z:.2f})"
            )
            detected_ego = {
                "actor_id": ego.id,
                "type_id": ego.type_id,
                "location": {
                    "x": float(ego_location.x),
                    "y": float(ego_location.y),
                    "z": float(ego_location.z),
                },
            }
        except RuntimeError:
            ego_location_text = ""

        logger(
            f"Detected Scenic ego actor id={ego.id} type={ego.type_id} "
            f"map={safe_map_name(world)}{ego_location_text}"
        )

        min_anchor_candidates = 1
        world, raw_anchor_candidates = wait_for_anchor_candidates(
            client,
            ego,
            config.wait_for_support_seconds,
            min_candidates=min_anchor_candidates,
            candidate_filter=lambda candidates: filter_candidates_for_selected_scenario(
                candidates,
                config.selected_scenario,
            ),
        )
        anchor_candidates = semantic_anchor_candidates(
            raw_anchor_candidates,
            selected_scenario=config.selected_scenario,
        )
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

        tracked_actors = build_tracked_actor_map(ego, spawned_walkers)
        ego_tracking_state = EgoTrackingState(
            preferred_id=ego.id,
            preferred_type_id=ego.type_id,
            last_resolved_id=ego.id,
            last_valid_location_xyz=location_to_xyz(ego.get_transform().location),
            last_valid_timestamp=0.0,
        )
        trajectory_samples = {}
        discover_scenic_support_actors(world, ego, spawned_walkers, tracked_actors)
        sample_tracked_actors(
            world,
            tracked_actors,
            trajectory_samples,
            0.0,
            ego_tracking_state=ego_tracking_state,
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
        )

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
            scenic_returncode=scenic_proc.returncode,
            scenic_output_tail=tuple(output_lines[-20:]),
            trajectory_report_png=trajectory_report_png,
            trajectory_report_focus_png=trajectory_report_focus_png,
            trajectory_report_json=trajectory_report_json,
        )
    finally:
        destroy_spawned_walkers(spawned_walkers)
        terminate_process(scenic_proc)
