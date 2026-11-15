#!/usr/bin/env python3

import ast
import re
import time
from dataclasses import dataclass, field
from math import atan2, cos, degrees, radians, sin, sqrt

import carla


DELIVERYBOT_ID = "walker.pedestrian.damos_deliverybot"
HUMANOID_ID = "walker.pedestrian.damos_humanoid"
TOWN01_NAME = "Town01"
CUSTOM_WALKER_ORDER = (DELIVERYBOT_ID, HUMANOID_ID)
OBSERVER_CAMERA_NAMES = (
    "cam_front",
    "cam_front_left",
    "cam_front_right",
    "cam_back",
    "cam_back_left",
    "cam_back_right",
)
SENSOR_CONFIG_LINE_PATTERN = re.compile(
    r"^sensor : (?P<name>[^,]+) ,bp : ActorBlueprint\(id=(?P<blueprint_id>[^,]+),"
    r".* transform : (?P<transform>\{.*\})$"
)
WIDE_REGION_ANCHOR_KINDS = {
    "vehicle_region",
    "construction_region",
}
S5_OPPOSITE_SIDEWALK_ANCHOR_KINDS = {
    "s5_vehicle_region",
    "scenario_s5",
}
S6_OPPOSITE_SIDEWALK_ANCHOR_KINDS = {
    "s6_construction_region",
    "scenario_s6",
}
S7_CLEAR_SIDEWALK_ANCHOR_KINDS = {
    "s7_construction_region",
    "scenario_s7",
}
DEFAULT_OBSERVER_RADIUS_PROFILE = {
    "preferred_radius": 7.0,
    "min_radius": 3.0,
    "max_radius": 22.0,
    "navigation_max_radius": 18.0,
}
WIDE_REGION_OBSERVER_RADIUS_PROFILE = {
    "preferred_radius": 18.0,
    "min_radius": 12.0,
    "max_radius": 22.0,
    "navigation_max_radius": 22.0,
}
S5_OPPOSITE_SIDEWALK_OBSERVER_RADIUS_PROFILE = {
    "preferred_radius": 25.0,
    "min_radius": 20.0,
    "max_radius": 32.0,
    "navigation_max_radius": 32.0,
}
S6_OPPOSITE_SIDEWALK_OBSERVER_RADIUS_PROFILE = {
    "preferred_radius": 18.0,
    "min_radius": 8.0,
    "max_radius": 32.0,
    "navigation_max_radius": 32.0,
}
S7_CLEAR_SIDEWALK_OBSERVER_RADIUS_PROFILE = {
    "preferred_radius": 20.0,
    "min_radius": 10.0,
    "max_radius": 32.0,
    "navigation_max_radius": 32.0,
}
CONSTRUCTION_REGION_OBSERVER_RADIUS_PROFILE = {
    "preferred_radius": 16.0,
    "min_radius": 8.0,
    "max_radius": 32.0,
    "navigation_max_radius": 32.0,
}


@dataclass(frozen=True)
class CustomWalkerSpec:
    blueprint_id: str
    speed: float
    use_wheelchair: bool
    demo_spawn: carla.Transform
    demo_destination: carla.Location


@dataclass(frozen=True)
class CustomWalkerAnchor:
    blueprint_id: str
    track_label: str
    actor_id: int
    actor_type_id: str
    label: str
    location: carla.Location
    anchor_index: int = 0
    observer_role: str = ""
    anchor_kind: str = "actor"
    member_actor_ids: tuple[int, ...] = ()
    member_actor_snapshots: tuple[dict[str, object], ...] = ()
    dynamic_actor_location: bool = True


@dataclass(frozen=True)
class ObserverCameraSpec:
    name: str
    blueprint_id: str
    transform: carla.Transform


@dataclass
class SpawnedWalker:
    spec: CustomWalkerSpec
    walker: carla.Actor
    controller: carla.Actor
    anchor: CustomWalkerAnchor | None = None
    track_label: str = ""
    sensors: list[carla.Actor] = field(default_factory=list)


CUSTOM_WALKER_SPECS = {
    DELIVERYBOT_ID: CustomWalkerSpec(
        blueprint_id=DELIVERYBOT_ID,
        speed=1.2,
        use_wheelchair=False,
        demo_spawn=carla.Transform(
            carla.Location(x=95.603027, y=293.695221, z=0.111192)
        ),
        demo_destination=carla.Location(x=94.728455, y=333.889038, z=0.105408),
    ),
    HUMANOID_ID: CustomWalkerSpec(
        blueprint_id=HUMANOID_ID,
        speed=1.4,
        use_wheelchair=False,
        demo_spawn=carla.Transform(
            carla.Location(x=82.159874, y=334.348389, z=0.105408)
        ),
        demo_destination=carla.Location(x=82.547691, y=322.888763, z=0.111335),
    ),
}

DEFAULT_OBSERVER_CAMERA_SPECS = (
    ObserverCameraSpec(
        name="cam_front",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=0.0, y=0.0, z=1.5),
            carla.Rotation(pitch=0.0, yaw=0.0, roll=0.0),
        ),
    ),
    ObserverCameraSpec(
        name="cam_front_left",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=0.0, y=-0.1, z=1.5),
            carla.Rotation(pitch=0.0, yaw=-55.0, roll=0.0),
        ),
    ),
    ObserverCameraSpec(
        name="cam_front_right",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=0.0, y=0.1, z=1.5),
            carla.Rotation(pitch=0.0, yaw=55.0, roll=0.0),
        ),
    ),
    ObserverCameraSpec(
        name="cam_back",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=0.0, y=0.0, z=1.5),
            carla.Rotation(pitch=0.0, yaw=180.0, roll=0.0),
        ),
    ),
    ObserverCameraSpec(
        name="cam_back_left",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=0.0, y=-0.1, z=1.5),
            carla.Rotation(pitch=0.0, yaw=-110.0, roll=0.0),
        ),
    ),
    ObserverCameraSpec(
        name="cam_back_right",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=0.0, y=0.1, z=1.5),
            carla.Rotation(pitch=0.0, yaw=110.0, roll=0.0),
        ),
    ),
)

DEFAULT_VEHICLE_CAMERA_SPECS = (
    ObserverCameraSpec(
        name="cam_front",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=1.5, y=0.0, z=1.75),
            carla.Rotation(pitch=0.0, yaw=0.0, roll=0.0),
        ),
    ),
    ObserverCameraSpec(
        name="cam_front_left",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=1.5, y=-0.58, z=1.75),
            carla.Rotation(pitch=0.0, yaw=-55.0, roll=0.0),
        ),
    ),
    ObserverCameraSpec(
        name="cam_front_right",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=1.5, y=0.54, z=1.75),
            carla.Rotation(pitch=0.0, yaw=55.0, roll=0.0),
        ),
    ),
    ObserverCameraSpec(
        name="cam_back",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=-1.5, y=0.0, z=1.75),
            carla.Rotation(pitch=0.0, yaw=180.0, roll=0.0),
        ),
    ),
    ObserverCameraSpec(
        name="cam_back_left",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=-0.75, y=-0.58, z=1.75),
            carla.Rotation(pitch=0.0, yaw=-110.0, roll=0.0),
        ),
    ),
    ObserverCameraSpec(
        name="cam_back_right",
        blueprint_id="sensor.camera.rgb",
        transform=carla.Transform(
            carla.Location(x=-0.75, y=0.58, z=1.75),
            carla.Rotation(pitch=0.0, yaw=110.0, roll=0.0),
        ),
    ),
)


def format_location(location):
    return f"({location.x:.2f}, {location.y:.2f}, {location.z:.2f})"


def distance_between(a, b):
    dx = a.x - b.x
    dy = a.y - b.y
    dz = a.z - b.z
    return sqrt(dx * dx + dy * dy + dz * dz)


def clone_location(location):
    return carla.Location(location.x, location.y, location.z)


def is_all_zero_location(location):
    return (
        abs(float(location.x)) < 1e-4
        and abs(float(location.y)) < 1e-4
        and abs(float(location.z)) < 1e-4
    )


def try_get_actor_location(actor):
    try:
        location = actor.get_transform().location
    except RuntimeError:
        return None
    if is_all_zero_location(location):
        return None
    return location


def actor_is_alive(actor):
    if not bool(getattr(actor, "is_alive", True)):
        return False
    try:
        actor.get_transform()
        return True
    except RuntimeError:
        return False


def location_from_sample(sample):
    return carla.Location(
        x=float(sample["x"]),
        y=float(sample["y"]),
        z=float(sample["z"]),
    )


def transform_from_config(transform_config):
    location_values = transform_config["location"]
    rotation_values = transform_config["rotation"]
    return carla.Transform(
        carla.Location(
            x=float(location_values[0]),
            y=float(location_values[1]),
            z=float(location_values[2]),
        ),
        carla.Rotation(
            pitch=float(rotation_values[0]),
            yaw=float(rotation_values[1]),
            roll=float(rotation_values[2]),
        ),
    )


def serialize_transform(transform):
    return {
        "location": {
            "x": float(transform.location.x),
            "y": float(transform.location.y),
            "z": float(transform.location.z),
        },
        "rotation": {
            "pitch": float(transform.rotation.pitch),
            "yaw": float(transform.rotation.yaw),
            "roll": float(transform.rotation.roll),
        },
    }


def serialize_observer_camera_specs(camera_specs, *, target_kind=None):
    serialized = []
    for spec in camera_specs:
        item = {
            "name": spec.name,
            "blueprint_id": spec.blueprint_id,
            "transform": serialize_transform(spec.transform),
        }
        if target_kind is not None:
            item["target_kind"] = target_kind
        serialized.append(item)
    return tuple(serialized)


def load_camera_specs_from_sensor_config(
    sensor_config_path,
    *,
    actor_type_token,
    fallback_specs,
):
    if sensor_config_path is None:
        return fallback_specs

    try:
        config_text = sensor_config_path.read_text(encoding="utf-8")
    except OSError:
        return fallback_specs

    in_selected_block = False
    specs_by_name = {}
    for raw_line in config_text.splitlines():
        line = raw_line.strip()
        if line.startswith("Initializing Mobility"):
            in_selected_block = actor_type_token in line
            specs_by_name = {}
            continue
        if not in_selected_block:
            continue
        if line.startswith("Successfully attached"):
            if all(name in specs_by_name for name in OBSERVER_CAMERA_NAMES):
                return tuple(specs_by_name[name] for name in OBSERVER_CAMERA_NAMES)
            in_selected_block = False
            specs_by_name = {}
            continue

        match = SENSOR_CONFIG_LINE_PATTERN.match(line)
        if match is None:
            continue
        sensor_name = match.group("name").strip()
        if sensor_name not in OBSERVER_CAMERA_NAMES:
            continue
        blueprint_id = match.group("blueprint_id").strip()
        try:
            transform_config = ast.literal_eval(match.group("transform"))
            transform = transform_from_config(transform_config)
        except (ValueError, SyntaxError, KeyError, TypeError, IndexError):
            continue
        specs_by_name[sensor_name] = ObserverCameraSpec(
            name=sensor_name,
            blueprint_id=blueprint_id,
            transform=transform,
        )

    return fallback_specs


def load_observer_camera_specs(sensor_config_path=None):
    return load_camera_specs_from_sensor_config(
        sensor_config_path,
        actor_type_token="walker.",
        fallback_specs=DEFAULT_OBSERVER_CAMERA_SPECS,
    )


def load_vehicle_camera_specs(sensor_config_path=None):
    return load_camera_specs_from_sensor_config(
        sensor_config_path,
        actor_type_token="vehicle.",
        fallback_specs=DEFAULT_VEHICLE_CAMERA_SPECS,
    )


def safe_sensor_role_name(track_label, sensor_name):
    safe_track = re.sub(r"[^A-Za-z0-9_.-]+", "_", track_label)
    safe_sensor = re.sub(r"[^A-Za-z0-9_.-]+", "_", sensor_name)
    return f"damos_{safe_track}_{safe_sensor}"


def yaw_toward(source_location, target_location):
    return degrees(
        atan2(target_location.y - source_location.y, target_location.x - source_location.x)
    )


def pick_spawn(world):
    for _ in range(50):
        location = world.get_random_location_from_navigation()
        if location is not None:
            return carla.Transform(location)
    raise RuntimeError("No navigation spawn point available")


def pick_destination(world):
    for _ in range(20):
        location = world.get_random_location_from_navigation()
        if location is not None:
            return location
    return None


def pick_destination_away_from(world, current_location, *, min_distance=8.0, attempts=80):
    fallback_location = None
    fallback_distance = -1.0
    for _ in range(attempts):
        location = world.get_random_location_from_navigation()
        if location is None:
            continue

        distance = distance_between(location, current_location)
        if distance >= min_distance:
            return location
        if distance > fallback_distance:
            fallback_location = location
            fallback_distance = distance

    return fallback_location


def sidewalk_waypoint_for_location(
    world,
    location,
    *,
    project_to_road=True,
    max_project_distance=None,
):
    try:
        world_map = world.get_map()
        waypoint = world_map.get_waypoint(
            location,
            project_to_road=project_to_road,
            lane_type=carla.LaneType.Sidewalk,
        )
    except RuntimeError:
        return None
    if waypoint is None:
        return None
    if max_project_distance is not None:
        sidewalk_location = waypoint.transform.location
        if distance_between(sidewalk_location, location) > max_project_distance:
            return None
    return waypoint


def is_sidewalk_location(
    world,
    location,
    *,
    max_project_distance=1.5,
    strict=False,
):
    if strict:
        return (
            sidewalk_waypoint_for_location(
                world,
                location,
                project_to_road=False,
            )
            is not None
        )
    return (
        sidewalk_waypoint_for_location(
            world,
            location,
            project_to_road=True,
            max_project_distance=max_project_distance,
        )
        is not None
    )


def nearby_driving_waypoints(world, location, *, sample_distance=5.0, samples_each_way=4):
    try:
        waypoint = world.get_map().get_waypoint(
            location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
    except RuntimeError:
        return []
    if waypoint is None:
        return []

    waypoints = [waypoint]
    for step in range(1, samples_each_way + 1):
        distance = sample_distance * step
        for next_waypoint in waypoint.next(distance):
            waypoints.append(next_waypoint)
        try:
            previous_waypoints = waypoint.previous(distance)
        except AttributeError:
            previous_waypoints = []
        for previous_waypoint in previous_waypoints:
            waypoints.append(previous_waypoint)

    deduped = []
    seen = set()
    for candidate in waypoints:
        key = (
            getattr(candidate, "road_id", None),
            getattr(candidate, "section_id", None),
            getattr(candidate, "lane_id", None),
            round(float(getattr(candidate, "s", 0.0)), 1),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def adjacent_sidewalk_waypoints(driving_waypoint, *, max_lane_steps=8):
    sidewalk_waypoints = []
    for direction in ("left", "right"):
        waypoint = driving_waypoint
        for _ in range(max_lane_steps):
            try:
                waypoint = (
                    waypoint.get_left_lane()
                    if direction == "left"
                    else waypoint.get_right_lane()
                )
            except RuntimeError:
                waypoint = None
            if waypoint is None:
                break
            if waypoint.lane_type == carla.LaneType.Sidewalk:
                sidewalk_waypoints.append(waypoint)
                break
    return sidewalk_waypoints


def sidewalk_spawn_locations_near_anchor(
    world,
    anchor_location,
    *,
    preferred_radius=7.0,
    min_radius=3.0,
    max_radius=22.0,
    avoid_locations=(),
    avoid_radius=2.5,
):
    candidates = []
    seen = set()

    def add_candidate(sidewalk_location, probe_location, *, priority):
        anchor_distance = distance_between(sidewalk_location, anchor_location)
        if anchor_distance < min_radius or anchor_distance > max_radius:
            return
        if any(
            distance_between(sidewalk_location, avoid_location) < avoid_radius
            for avoid_location in avoid_locations
        ):
            return

        dedupe_key = (
            round(float(sidewalk_location.x), 1),
            round(float(sidewalk_location.y), 1),
            round(float(sidewalk_location.z), 1),
        )
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        spawn_location = carla.Location(
            x=float(sidewalk_location.x),
            y=float(sidewalk_location.y),
            z=float(sidewalk_location.z) + 0.8,
        )
        projection_error = distance_between(sidewalk_location, probe_location)
        score = (
            priority
            + abs(anchor_distance - preferred_radius)
            + projection_error * 0.35
        )
        candidates.append((score, spawn_location))

    for driving_waypoint in nearby_driving_waypoints(world, anchor_location):
        for sidewalk_waypoint in adjacent_sidewalk_waypoints(driving_waypoint):
            sidewalk_location = sidewalk_waypoint.transform.location
            add_candidate(
                sidewalk_location,
                driving_waypoint.transform.location,
                priority=0.0,
            )

    for radius in (4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 18.0, 22.0, 26.0):
        if radius < min_radius or radius > max_radius + 4.0:
            continue
        for angle_degrees in range(0, 360, 15):
            angle = radians(angle_degrees)
            probe_location = carla.Location(
                x=anchor_location.x + radius * cos(angle),
                y=anchor_location.y + radius * sin(angle),
                z=anchor_location.z,
            )
            waypoint = sidewalk_waypoint_for_location(
                world,
                probe_location,
                project_to_road=True,
                max_project_distance=max(2.5, min(radius * 0.65, 7.0)),
            )
            if waypoint is None:
                continue

            sidewalk_location = waypoint.transform.location
            add_candidate(
                sidewalk_location,
                probe_location,
                priority=4.0,
            )

    candidates.sort(key=lambda item: item[0])
    return [location for _score, location in candidates]


def opposite_sidewalk_locations_near_anchor(
    world,
    anchor_location,
    *,
    preferred_radius=16.0,
    min_radius=8.0,
    max_radius=32.0,
    avoid_locations=(),
    avoid_radius=2.5,
):
    try:
        center_waypoint = world.get_map().get_waypoint(
            anchor_location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
    except RuntimeError:
        center_waypoint = None
    if center_waypoint is None:
        return []

    candidates = []
    seen = set()
    center_location = center_waypoint.transform.location
    side_x = float(anchor_location.x) - float(center_location.x)
    side_y = float(anchor_location.y) - float(center_location.y)
    side_norm = sqrt(side_x * side_x + side_y * side_y)

    def add_candidate(sidewalk_location, *, priority):
        anchor_distance = distance_between(sidewalk_location, anchor_location)
        if anchor_distance < min_radius or anchor_distance > max_radius:
            return
        if any(
            distance_between(sidewalk_location, avoid_location) < avoid_radius
            for avoid_location in avoid_locations
        ):
            return

        dedupe_key = (
            round(float(sidewalk_location.x), 1),
            round(float(sidewalk_location.y), 1),
            round(float(sidewalk_location.z), 1),
        )
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        spawn_location = carla.Location(
            x=float(sidewalk_location.x),
            y=float(sidewalk_location.y),
            z=float(sidewalk_location.z) + 0.8,
        )
        score = priority + abs(anchor_distance - preferred_radius)
        candidates.append((score, spawn_location))

    if side_norm >= 1.0:
        sample_limit = int(max(max_radius + 6.0, preferred_radius + 8.0))
        for dx in range(-sample_limit, sample_limit + 1, 2):
            for dy in range(-sample_limit, sample_limit + 1, 2):
                probe_location = carla.Location(
                    x=float(anchor_location.x) + dx,
                    y=float(anchor_location.y) + dy,
                    z=float(anchor_location.z),
                )
                waypoint = sidewalk_waypoint_for_location(
                    world,
                    probe_location,
                    project_to_road=True,
                    max_project_distance=1.0,
                )
                if waypoint is None:
                    continue
                sidewalk_location = waypoint.transform.location
                candidate_x = float(sidewalk_location.x) - float(center_location.x)
                candidate_y = float(sidewalk_location.y) - float(center_location.y)
                side_dot = candidate_x * side_x + candidate_y * side_y
                if side_dot >= -(side_norm * side_norm * 0.2):
                    continue
                add_candidate(sidewalk_location, priority=0.0)

    if candidates:
        candidates.sort(key=lambda item: item[0])
        return [location for _score, location in candidates]

    anchor_lane_sign = 1 if int(getattr(center_waypoint, "lane_id", 0)) >= 0 else -1
    for driving_waypoint in nearby_driving_waypoints(
        world,
        anchor_location,
        sample_distance=6.0,
        samples_each_way=6,
    ):
        lane_sign = 1 if int(getattr(driving_waypoint, "lane_id", 0)) >= 0 else -1
        if lane_sign == anchor_lane_sign:
            continue
        for sidewalk_waypoint in adjacent_sidewalk_waypoints(
            driving_waypoint,
            max_lane_steps=10,
        ):
            sidewalk_location = sidewalk_waypoint.transform.location
            anchor_distance = distance_between(sidewalk_location, anchor_location)
            if anchor_distance < min_radius or anchor_distance > max_radius:
                continue
            if any(
                distance_between(sidewalk_location, avoid_location) < avoid_radius
                for avoid_location in avoid_locations
            ):
                continue

            add_candidate(sidewalk_location, priority=4.0)

    candidates.sort(key=lambda item: item[0])
    return [location for _score, location in candidates]


def s6_opposite_sidewalk_locations_near_anchor(
    world,
    anchor_location,
    *,
    preferred_radius=18.0,
    min_radius=8.0,
    max_radius=32.0,
    avoid_locations=(),
    avoid_radius=2.5,
):
    candidates = []
    seen = set()
    sample_limit = int(max(max_radius + 8.0, preferred_radius + 12.0))
    world_map = world.get_map()

    def sidewalk_location_shifted_toward_road(sidewalk_location, lane_width):
        try:
            driving_waypoint = world_map.get_waypoint(
                sidewalk_location,
                project_to_road=True,
                lane_type=carla.LaneType.Driving,
            )
        except RuntimeError:
            driving_waypoint = None
        if driving_waypoint is None:
            return sidewalk_location

        driving_location = driving_waypoint.transform.location
        dx = float(driving_location.x) - float(sidewalk_location.x)
        dy = float(driving_location.y) - float(sidewalk_location.y)
        norm = sqrt(dx * dx + dy * dy)
        if norm < 1e-3:
            return sidewalk_location

        shift = min(2.4, max(1.2, float(lane_width) * 0.4))
        shifted_location = carla.Location(
            x=float(sidewalk_location.x) + dx / norm * shift,
            y=float(sidewalk_location.y) + dy / norm * shift,
            z=float(sidewalk_location.z),
        )
        if is_sidewalk_location(world, shifted_location, strict=True):
            return shifted_location
        return sidewalk_location

    for dx in range(-sample_limit, sample_limit + 1, 2):
        for dy in range(-sample_limit, sample_limit + 1, 2):
            probe_location = carla.Location(
                x=float(anchor_location.x) + dx,
                y=float(anchor_location.y) + dy,
                z=float(anchor_location.z),
            )
            waypoint = sidewalk_waypoint_for_location(
                world,
                probe_location,
                project_to_road=True,
                max_project_distance=1.0,
            )
            if waypoint is None:
                continue
            if int(getattr(waypoint, "lane_id", 0)) >= 0:
                continue

            sidewalk_location = waypoint.transform.location
            sidewalk_location = sidewalk_location_shifted_toward_road(
                sidewalk_location,
                getattr(waypoint, "lane_width", 0.0),
            )
            anchor_distance = distance_between(sidewalk_location, anchor_location)
            if anchor_distance < min_radius or anchor_distance > max_radius:
                continue
            if any(
                distance_between(sidewalk_location, avoid_location) < avoid_radius
                for avoid_location in avoid_locations
            ):
                continue

            dedupe_key = (
                round(float(sidewalk_location.x), 1),
                round(float(sidewalk_location.y), 1),
                round(float(sidewalk_location.z), 1),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            spawn_location = carla.Location(
                x=float(sidewalk_location.x),
                y=float(sidewalk_location.y),
                z=float(sidewalk_location.z) + 0.8,
            )
            score = abs(anchor_distance - preferred_radius)
            candidates.append((score, spawn_location))

    candidates.sort(key=lambda item: item[0])
    return [location for _score, location in candidates]


def s7_clear_sidewalk_locations_near_anchor(
    world,
    anchor_location,
    *,
    preferred_radius=20.0,
    min_radius=10.0,
    max_radius=32.0,
    avoid_locations=(),
    avoid_radius=2.5,
):
    # S7 is a fixed sidewalk-construction cluster. Place the observer on the
    # northwest diagonal sidewalk around x=40, y=75 so the construction area is
    # visible without looking along the tree/lamp line.
    offset_candidates = (
        (-17.0, 17.0),
        (-15.0, 17.0),
        (-19.0, 17.0),
        (-17.0, 15.0),
        (-17.0, 19.0),
        (-15.0, 15.0),
        (-19.0, 19.0),
        (19.0, -1.0),
    )

    candidates = []
    seen = set()

    def add_candidate(location, *, priority):
        anchor_distance = distance_between(location, anchor_location)
        if anchor_distance < min_radius or anchor_distance > max_radius:
            return False
        if any(
            distance_between(location, avoid_location) < avoid_radius
            for avoid_location in avoid_locations
        ):
            return False
        if not is_sidewalk_location(world, location, strict=True):
            return False

        dedupe_key = (
            round(float(location.x), 1),
            round(float(location.y), 1),
            round(float(location.z), 1),
        )
        if dedupe_key in seen:
            return False
        seen.add(dedupe_key)
        spawn_location = carla.Location(
            x=float(location.x),
            y=float(location.y),
            z=float(location.z) + 0.8,
        )
        score = priority + abs(anchor_distance - preferred_radius) * 0.05
        candidates.append((score, spawn_location))
        return True

    for priority, (dx, dy) in enumerate(offset_candidates):
        target_location = carla.Location(
            x=float(anchor_location.x) + dx,
            y=float(anchor_location.y) + dy,
            z=float(anchor_location.z),
        )
        target_waypoint = sidewalk_waypoint_for_location(
            world,
            target_location,
            project_to_road=True,
            max_project_distance=3.5,
        )
        if target_waypoint is None:
            continue
        add_candidate(
            target_waypoint.transform.location,
            priority=float(priority) * 0.2,
        )

    for location in sidewalk_spawn_locations_near_anchor(
        world,
        anchor_location,
        preferred_radius=preferred_radius,
        min_radius=min_radius,
        max_radius=max_radius,
        avoid_locations=avoid_locations,
        avoid_radius=avoid_radius,
    ):
        lateral_offset = abs(float(location.x) - float(anchor_location.x))
        if lateral_offset < 8.0:
            continue
        add_candidate(location, priority=8.0)

    candidates.sort(key=lambda item: item[0])
    return [location for _score, location in candidates]


def pick_navigation_location_near_anchor(
    world,
    anchor_location,
    *,
    preferred_radius=6.0,
    min_radius=2.0,
    max_radius=18.0,
    sample_count=300,
    avoid_locations=(),
    avoid_radius=2.5,
    require_sidewalk=False,
    sidewalk_project_distance=2.0,
    strict_sidewalk=False,
):
    best_location = None
    best_score = None
    search_radii = (max_radius, max(max_radius * 1.5, 24.0), max(max_radius * 2.0, 32.0))

    for radius_limit in search_radii:
        for _ in range(sample_count):
            location = world.get_random_location_from_navigation()
            if location is None:
                continue
            if require_sidewalk and not is_sidewalk_location(
                world,
                location,
                max_project_distance=sidewalk_project_distance,
                strict=strict_sidewalk,
            ):
                continue

            anchor_distance = distance_between(location, anchor_location)
            if anchor_distance < min_radius or anchor_distance > radius_limit:
                continue

            too_close_to_other_spawn = False
            for avoid_location in avoid_locations:
                if distance_between(location, avoid_location) < avoid_radius:
                    too_close_to_other_spawn = True
                    break
            if too_close_to_other_spawn:
                continue

            score = abs(anchor_distance - preferred_radius)
            if best_score is None or score < best_score:
                best_location = location
                best_score = score

        if best_location is not None:
            return best_location

    return None


def direct_observer_locations_near_anchor(
    anchor_location,
    *,
    avoid_locations=(),
    avoid_radius=2.5,
):
    for radius in (4.0, 6.0, 8.0, 10.0, 14.0, 18.0):
        for angle_degrees in (0, 45, 90, 135, 180, 225, 270, 315):
            angle = radians(angle_degrees)
            location = carla.Location(
                x=anchor_location.x + radius * cos(angle),
                y=anchor_location.y + radius * sin(angle),
                z=anchor_location.z + 0.8,
            )
            if any(
                distance_between(location, avoid_location) < avoid_radius
                for avoid_location in avoid_locations
            ):
                continue
            yield location


def observer_radius_profile_for_anchor(anchor):
    anchor_kind = getattr(anchor, "anchor_kind", "actor")
    if anchor_kind in S5_OPPOSITE_SIDEWALK_ANCHOR_KINDS:
        return S5_OPPOSITE_SIDEWALK_OBSERVER_RADIUS_PROFILE
    if anchor_kind in S6_OPPOSITE_SIDEWALK_ANCHOR_KINDS:
        return S6_OPPOSITE_SIDEWALK_OBSERVER_RADIUS_PROFILE
    if anchor_kind in S7_CLEAR_SIDEWALK_ANCHOR_KINDS:
        return S7_CLEAR_SIDEWALK_OBSERVER_RADIUS_PROFILE
    if anchor_kind == "construction_region":
        return CONSTRUCTION_REGION_OBSERVER_RADIUS_PROFILE
    if anchor_kind in WIDE_REGION_ANCHOR_KINDS:
        return WIDE_REGION_OBSERVER_RADIUS_PROFILE
    return DEFAULT_OBSERVER_RADIUS_PROFILE


def anchor_member_locations(anchor):
    locations = []
    for snapshot in getattr(anchor, "member_actor_snapshots", ()) or ():
        location = snapshot.get("location") if isinstance(snapshot, dict) else None
        if not isinstance(location, dict):
            continue
        try:
            locations.append(
                carla.Location(
                    x=float(location["x"]),
                    y=float(location["y"]),
                    z=float(location.get("z", 0.0)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return locations


def require_town01(world):
    map_name = world.get_map().name
    if map_name.endswith(f"/{TOWN01_NAME}") or map_name == TOWN01_NAME:
        return
    raise RuntimeError(
        f"Town01 demo mode only supports {TOWN01_NAME}; current map is {map_name}. "
        "Use --random-spawn to run on other maps."
    )


def build_demo_spectator_transform():
    delivery = CUSTOM_WALKER_SPECS[DELIVERYBOT_ID].demo_spawn.location
    humanoid = CUSTOM_WALKER_SPECS[HUMANOID_ID].demo_spawn.location
    midpoint_x = (delivery.x + humanoid.x) / 2.0
    midpoint_y = (delivery.y + humanoid.y) / 2.0
    separation = sqrt((delivery.x - humanoid.x) ** 2 + (delivery.y - humanoid.y) ** 2)
    height = max(38.0, separation * 1.2)
    return carla.Transform(
        carla.Location(x=midpoint_x, y=midpoint_y, z=height),
        carla.Rotation(pitch=-82.0, yaw=0.0, roll=0.0),
    )


def connect_to_world(host, port, wait_for_server_seconds):
    client = carla.Client(host, port)
    client.set_timeout(5.0)

    deadline = time.monotonic() + max(0.0, wait_for_server_seconds)
    announced_wait = False

    while True:
        try:
            world = client.get_world()
            return client, world
        except RuntimeError as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Timed out waiting for CARLA server at {host}:{port}. "
                    "Make sure the simulator is ready and reachable."
                ) from exc
            if not announced_wait:
                print(
                    f"Waiting for CARLA server at {host}:{port} "
                    f"(up to {wait_for_server_seconds:.0f}s)..."
                )
                announced_wait = True
            time.sleep(1.0)


def cleanup_existing_custom_walkers(world):
    actors = world.get_actors()
    custom_walkers = [
        actor
        for actor in actors
        if actor.type_id in CUSTOM_WALKER_ORDER
    ]
    if not custom_walkers:
        return 0, 0

    walker_ids = {actor.id for actor in custom_walkers}
    controllers = []
    sensors = []
    for actor in actors:
        parent = getattr(actor, "parent", None)
        if actor.type_id == "controller.ai.walker" and (parent is not None) and (parent.id in walker_ids):
            controllers.append(actor)
        elif actor.type_id.startswith("sensor.") and (parent is not None) and (parent.id in walker_ids):
            sensors.append(actor)

    for sensor in sensors:
        if not actor_is_alive(sensor):
            continue
        try:
            sensor.destroy()
        except RuntimeError:
            pass

    for controller in controllers:
        if not actor_is_alive(controller):
            continue
        try:
            controller.stop()
        except RuntimeError:
            pass

    for controller in controllers:
        if not actor_is_alive(controller):
            continue
        try:
            controller.destroy()
        except RuntimeError:
            pass

    for walker in custom_walkers:
        if not actor_is_alive(walker):
            continue
        try:
            walker.destroy()
        except RuntimeError:
            pass

    return len(custom_walkers), len(controllers)


def ensure_custom_walker_blueprints(world):
    library = world.get_blueprint_library()
    for blueprint_id in CUSTOM_WALKER_ORDER:
        library.find(blueprint_id)


def get_demo_spawn_transforms():
    return {
        blueprint_id: CUSTOM_WALKER_SPECS[blueprint_id].demo_spawn
        for blueprint_id in CUSTOM_WALKER_ORDER
    }


def get_random_spawn_transforms(world):
    return {
        blueprint_id: pick_spawn(world)
        for blueprint_id in CUSTOM_WALKER_ORDER
    }


def spawn_custom_walker(
    world,
    blueprint_id,
    spawn_transform,
    *,
    speed=None,
    use_wheelchair=None,
    anchor=None,
    track_label=None,
):
    spec = CUSTOM_WALKER_SPECS[blueprint_id]
    walker_speed = spec.speed if speed is None else speed
    should_use_wheelchair = spec.use_wheelchair if use_wheelchair is None else use_wheelchair

    bp = world.get_blueprint_library().find(blueprint_id)
    if bp.has_attribute("is_invincible"):
        bp.set_attribute("is_invincible", "false")
    if bp.has_attribute("use_wheelchair"):
        bp.set_attribute("use_wheelchair", "true" if should_use_wheelchair else "false")

    walker = world.try_spawn_actor(bp, spawn_transform)
    if walker is None:
        raise RuntimeError(f"Failed to spawn {blueprint_id}")

    controller_bp = world.get_blueprint_library().find("controller.ai.walker")
    controller = world.spawn_actor(controller_bp, carla.Transform(), walker)
    controller.start()
    controller.set_max_speed(walker_speed)
    return SpawnedWalker(
        spec=spec,
        walker=walker,
        controller=controller,
        anchor=anchor,
        track_label=track_label or blueprint_id,
    )


def freeze_spawned_observer(world, spawned_walker, spawn_transform):
    try:
        spawned_walker.controller.stop()
    except RuntimeError:
        pass
    try:
        spawned_walker.walker.set_simulate_physics(False)
    except (AttributeError, RuntimeError):
        pass
    try:
        spawned_walker.walker.set_transform(spawn_transform)
    except RuntimeError:
        pass
    for _ in range(2):
        try:
            world.wait_for_tick(1.0)
        except RuntimeError:
            time.sleep(0.1)


def attach_actor_cameras(
    world,
    actor,
    camera_specs,
    *,
    track_label,
    actor_role,
    sensor_store=None,
):
    attached = []
    library = world.get_blueprint_library()
    for spec in camera_specs:
        bp = library.find(spec.blueprint_id)
        if bp.has_attribute("role_name"):
            bp.set_attribute(
                "role_name",
                safe_sensor_role_name(track_label, spec.name),
            )
        sensor = world.spawn_actor(
            bp,
            spec.transform,
            attach_to=actor,
        )
        if sensor_store is not None:
            sensor_store.append(sensor)
        item = {
            "track_label": track_label,
            "actor_role": actor_role,
            "actor_id": actor.id,
            "actor_type_id": actor.type_id,
            "sensor_actor_id": sensor.id,
            "sensor_name": spec.name,
            "blueprint_id": spec.blueprint_id,
            "relative_transform": serialize_transform(spec.transform),
        }
        if actor.type_id.startswith("walker."):
            item["walker_actor_id"] = actor.id
        attached.append(item)
    return tuple(attached)


def attach_observer_cameras(world, spawned_walkers, camera_specs):
    attached = []
    for spawned_walker in spawned_walkers:
        actor_role = (
            spawned_walker.anchor.observer_role
            if spawned_walker.anchor is not None
            else spawned_walker.spec.blueprint_id
        )
        attached.extend(
            attach_actor_cameras(
                world,
                spawned_walker.walker,
                camera_specs,
                track_label=spawned_walker.track_label,
                actor_role=actor_role,
                sensor_store=spawned_walker.sensors,
            )
        )
    return tuple(attached)


def spawn_all_custom_walkers(
    world,
    *,
    random_spawn=False,
    cleanup_existing=False,
    set_spectator=True,
):
    if cleanup_existing:
        removed_walkers, removed_controllers = cleanup_existing_custom_walkers(world)
        if removed_walkers or removed_controllers:
            print(
                f"Cleaned up {removed_walkers} existing custom walkers "
                f"and {removed_controllers} controllers before spawning."
            )

    ensure_custom_walker_blueprints(world)

    if random_spawn:
        spawn_transforms = get_random_spawn_transforms(world)
    else:
        require_town01(world)
        spawn_transforms = get_demo_spawn_transforms()
        if set_spectator:
            world.get_spectator().set_transform(build_demo_spectator_transform())

    return [
        spawn_custom_walker(world, blueprint_id, spawn_transforms[blueprint_id])
        for blueprint_id in CUSTOM_WALKER_ORDER
    ]


def spawn_custom_walkers_near_anchors(
    world,
    anchors,
    *,
    cleanup_existing=False,
    prefer_direct_spawn=False,
):
    if cleanup_existing:
        removed_walkers, removed_controllers = cleanup_existing_custom_walkers(world)
        if removed_walkers or removed_controllers:
            print(
                f"Cleaned up {removed_walkers} existing custom walkers "
                f"and {removed_controllers} controllers before spawning."
            )

    ensure_custom_walker_blueprints(world)

    spawned_walkers = []
    used_locations = []
    for anchor in anchors:
        blueprint_id = anchor.blueprint_id
        spawned_walker = None
        radius_profile = observer_radius_profile_for_anchor(anchor)
        blocked_locations = [*used_locations, *anchor_member_locations(anchor)]
        opposite_sidewalk_locations = []
        anchor_kind = getattr(anchor, "anchor_kind", "actor")
        if anchor_kind in S6_OPPOSITE_SIDEWALK_ANCHOR_KINDS:
            opposite_sidewalk_locations = s6_opposite_sidewalk_locations_near_anchor(
                world,
                anchor.location,
                preferred_radius=radius_profile["preferred_radius"],
                min_radius=radius_profile["min_radius"],
                max_radius=radius_profile["max_radius"],
                avoid_locations=blocked_locations,
                avoid_radius=3.0,
            )
        elif anchor_kind in S7_CLEAR_SIDEWALK_ANCHOR_KINDS:
            opposite_sidewalk_locations = s7_clear_sidewalk_locations_near_anchor(
                world,
                anchor.location,
                preferred_radius=radius_profile["preferred_radius"],
                min_radius=radius_profile["min_radius"],
                max_radius=radius_profile["max_radius"],
                avoid_locations=blocked_locations,
                avoid_radius=3.0,
            )
        elif anchor_kind in {
            "construction_region",
            *S5_OPPOSITE_SIDEWALK_ANCHOR_KINDS,
        }:
            opposite_sidewalk_locations = opposite_sidewalk_locations_near_anchor(
                world,
                anchor.location,
                preferred_radius=radius_profile["preferred_radius"],
                min_radius=radius_profile["min_radius"],
                max_radius=radius_profile["max_radius"],
                avoid_locations=blocked_locations,
                avoid_radius=3.0,
            )
        sidewalk_locations = sidewalk_spawn_locations_near_anchor(
            world,
            anchor.location,
            preferred_radius=radius_profile["preferred_radius"],
            min_radius=radius_profile["min_radius"],
            max_radius=radius_profile["max_radius"],
            avoid_locations=blocked_locations,
            avoid_radius=3.0,
        )
        navigation_locations = []
        for _ in range(12):
            navigation_location = pick_navigation_location_near_anchor(
                world,
                anchor.location,
                preferred_radius=radius_profile["preferred_radius"],
                min_radius=radius_profile["min_radius"],
                max_radius=radius_profile["navigation_max_radius"],
                sample_count=400,
                avoid_locations=[*blocked_locations, *navigation_locations],
                avoid_radius=3.0,
                require_sidewalk=True,
                sidewalk_project_distance=0.5,
                strict_sidewalk=True,
            )
            if navigation_location is not None:
                navigation_locations.append(navigation_location)

        if prefer_direct_spawn:
            candidate_locations = [
                *opposite_sidewalk_locations,
                *sidewalk_locations,
                *navigation_locations,
            ]
        else:
            candidate_locations = [
                *opposite_sidewalk_locations,
                *navigation_locations,
                *sidewalk_locations,
            ]

        for spawn_location in candidate_locations:
            spawn_transform = carla.Transform(
                spawn_location,
                carla.Rotation(yaw=yaw_toward(spawn_location, anchor.location)),
            )
            try:
                candidate = spawn_custom_walker(
                    world,
                    blueprint_id,
                    spawn_transform,
                    anchor=anchor,
                    track_label=anchor.track_label,
                )
            except RuntimeError:
                continue
            if prefer_direct_spawn:
                freeze_spawned_observer(world, candidate, spawn_transform)
            actual_location = candidate.walker.get_transform().location
            spawn_error = distance_between(actual_location, spawn_location)
            anchor_error = distance_between(actual_location, anchor.location)
            sidewalk_ok = is_sidewalk_location(
                world,
                actual_location,
                max_project_distance=0.5,
                strict=True,
            )
            if (
                spawn_error <= 5.0
                and anchor_error <= radius_profile["max_radius"]
                and sidewalk_ok
            ):
                spawned_walker = candidate
                used_locations.append(actual_location)
                break

            destroy_spawned_walkers([candidate])
            used_locations.append(spawn_location)

        if spawned_walker is None:
            destroy_spawned_walkers(spawned_walkers)
            raise RuntimeError(
                f"Failed to spawn {blueprint_id} near anchor "
                f"{anchor.label} ({anchor.actor_type_id}, actor_id={anchor.actor_id}) "
                f"within the allowed distance."
            )

        spawned_walkers.append(spawned_walker)

    cleanup_untracked_custom_walkers(world, spawned_walkers)
    return spawned_walkers


def cleanup_untracked_custom_walkers(world, keep_spawned_walkers):
    keep_walker_ids = {spawned_walker.walker.id for spawned_walker in keep_spawned_walkers}
    actors = list(world.get_actors())
    orphan_walkers = [
        actor
        for actor in actors
        if actor.type_id in CUSTOM_WALKER_ORDER and actor.id not in keep_walker_ids
    ]
    orphan_walker_ids = {actor.id for actor in orphan_walkers}

    orphan_controllers = []
    for actor in actors:
        if actor.type_id != "controller.ai.walker":
            continue
        parent = getattr(actor, "parent", None)
        if parent is not None and parent.id in orphan_walker_ids:
            orphan_controllers.append(actor)

    for controller in orphan_controllers:
        try:
            controller.stop()
        except RuntimeError:
            pass
        try:
            controller.destroy()
        except RuntimeError:
            pass
    for walker in orphan_walkers:
        try:
            walker.destroy()
        except RuntimeError:
            pass

    if orphan_walkers or orphan_controllers:
        # In synchronous CARLA worlds, destroy requests are applied on later ticks.
        for _ in range(3):
            try:
                world.wait_for_tick(1.0)
            except RuntimeError:
                time.sleep(0.2)


def orient_spawned_walkers_to_anchors(spawned_walkers):
    for spawned_walker in spawned_walkers:
        if spawned_walker.anchor is None:
            continue
        location = try_get_actor_location(spawned_walker.walker)
        if location is None:
            continue
        try:
            transform = spawned_walker.walker.get_transform()
            transform.rotation.yaw = yaw_toward(location, spawned_walker.anchor.location)
            spawned_walker.walker.set_transform(transform)
        except RuntimeError:
            continue


def stop_spawned_walker_controllers(spawned_walkers):
    for spawned_walker in spawned_walkers:
        try:
            spawned_walker.controller.stop()
        except RuntimeError:
            pass


def find_invalid_anchor_spawned_walkers(
    world,
    spawned_walkers,
    *,
    max_anchor_error=22.0,
):
    invalid = []
    for spawned_walker in spawned_walkers:
        location = try_get_actor_location(spawned_walker.walker)
        if location is None:
            invalid.append((spawned_walker, "missing_location"))
            continue
        if spawned_walker.anchor is None:
            continue
        anchor_error = distance_between(location, spawned_walker.anchor.location)
        radius_profile = observer_radius_profile_for_anchor(spawned_walker.anchor)
        effective_max_anchor_error = max(
            float(max_anchor_error),
            float(radius_profile["max_radius"]),
        )
        if anchor_error > effective_max_anchor_error:
            invalid.append((spawned_walker, f"anchor_error={anchor_error:.2f}"))
            continue
        if not is_sidewalk_location(
            world,
            location,
            max_project_distance=0.5,
            strict=True,
        ):
            invalid.append((spawned_walker, "not_on_sidewalk"))
    return invalid


def probe_anchor_spawned_walkers(
    world,
    spawned_walkers,
    *,
    probe_seconds=3.0,
    min_move_meters=0.1,
):
    initial_locations = snapshot_walker_locations(spawned_walkers)
    deadline = time.time() + max(1.0, probe_seconds)

    while time.time() < deadline:
        send_walkers_to_anchor_destinations(world, spawned_walkers)
        try:
            world.wait_for_tick(1.0)
        except RuntimeError:
            time.sleep(0.5)

    invalid = []
    for spawned_walker, _final_location, moved in measure_walker_movements(
        spawned_walkers,
        initial_locations,
    ):
        if moved < min_move_meters:
            invalid.append((spawned_walker, f"probe_move={moved:.2f}"))
    return invalid


def wait_for_controller_init(world, ticks=2, timeout_seconds=2.0):
    for _ in range(ticks):
        world.wait_for_tick(timeout_seconds)


def initialize_custom_walker_movement(world, spawned_walkers, *, random_spawn=False):
    wait_for_controller_init(world, ticks=2, timeout_seconds=2.0)
    if any(spawned_walker.anchor is not None for spawned_walker in spawned_walkers):
        send_walkers_to_anchor_destinations(world, spawned_walkers)
    elif not random_spawn:
        send_walkers_to_demo_destinations(spawned_walkers)


def send_walkers_to_demo_destinations(spawned_walkers):
    for spawned_walker in spawned_walkers:
        spawned_walker.controller.go_to_location(spawned_walker.spec.demo_destination)


def send_walkers_to_random_destination(world, spawned_walkers):
    destination = pick_destination(world)
    if destination is None:
        return None
    for spawned_walker in spawned_walkers:
        spawned_walker.controller.go_to_location(destination)
    return destination


def send_walkers_to_anchor_destinations(world, spawned_walkers):
    destinations = {}
    used_destinations = []
    for spawned_walker in spawned_walkers:
        anchor_location = None
        if spawned_walker.anchor is not None:
            anchor_actor = world.get_actor(spawned_walker.anchor.actor_id)
            if anchor_actor is not None:
                try:
                    anchor_location = anchor_actor.get_transform().location
                except RuntimeError:
                    anchor_location = None
            if anchor_location is None:
                anchor_location = spawned_walker.anchor.location
        if anchor_location is None:
            continue

        current_location = spawned_walker.walker.get_transform().location
        destination = pick_navigation_location_near_anchor(
            world,
            anchor_location,
            preferred_radius=14.0,
            min_radius=6.0,
            max_radius=32.0,
            sample_count=400,
            avoid_locations=[current_location, *used_destinations],
            avoid_radius=8.0,
            require_sidewalk=True,
            sidewalk_project_distance=0.5,
            strict_sidewalk=True,
        )
        if destination is None:
            destination = pick_destination_away_from(
                world,
                current_location,
                min_distance=8.0,
            )
        if destination is None:
            continue
        used_destinations.append(destination)
        destinations[spawned_walker.walker.id] = destination
        spawned_walker.controller.go_to_location(destination)

    return destinations


def run_custom_walker_movement(world, spawned_walkers, duration_seconds, *, random_spawn=False):
    end_time = time.time() + duration_seconds
    while time.time() < end_time:
        if any(spawned_walker.anchor is not None for spawned_walker in spawned_walkers):
            send_walkers_to_anchor_destinations(world, spawned_walkers)
            world.wait_for_tick(2.0)
        elif random_spawn:
            send_walkers_to_random_destination(world, spawned_walkers)
            world.wait_for_tick(2.0)
        else:
            world.wait_for_tick(1.0)


def snapshot_walker_locations(spawned_walkers):
    snapshots = {}
    for spawned_walker in spawned_walkers:
        location = try_get_actor_location(spawned_walker.walker)
        if location is None:
            continue
        snapshots[spawned_walker.walker.id] = clone_location(location)
    return snapshots


def measure_walker_movements(spawned_walkers, initial_locations, trajectory_samples=None):
    movement = []
    for spawned_walker in spawned_walkers:
        final_location = try_get_actor_location(spawned_walker.walker)
        if final_location is None and trajectory_samples is not None:
            samples = trajectory_samples.get(spawned_walker.track_label, [])
            if samples:
                final_location = location_from_sample(samples[-1])
        if final_location is None:
            final_location = carla.Location(0.0, 0.0, 0.0)
        initial_location = initial_locations.get(spawned_walker.walker.id)
        moved = 0.0
        if initial_location is not None:
            moved = distance_between(final_location, initial_location)
        movement.append((spawned_walker, final_location, moved))
    return movement


def destroy_spawned_walkers(spawned_walkers):
    for spawned_walker in spawned_walkers:
        for sensor in spawned_walker.sensors:
            if not actor_is_alive(sensor):
                continue
            try:
                sensor.destroy()
            except RuntimeError:
                pass
        spawned_walker.sensors.clear()
        if actor_is_alive(spawned_walker.controller):
            try:
                spawned_walker.controller.stop()
            except RuntimeError:
                pass
            try:
                spawned_walker.controller.destroy()
            except RuntimeError:
                pass
        if actor_is_alive(spawned_walker.walker):
            try:
                spawned_walker.walker.destroy()
            except RuntimeError:
                pass
        try:
            time.sleep(0.05)
        except RuntimeError:
            pass
