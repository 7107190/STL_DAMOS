#!/usr/bin/env python3

import time
from dataclasses import dataclass
from math import atan2, degrees, sqrt

import carla


DELIVERYBOT_ID = "walker.pedestrian.damos_deliverybot"
HUMANOID_ID = "walker.pedestrian.damos_humanoid"
TOWN01_NAME = "Town01"
CUSTOM_WALKER_ORDER = (DELIVERYBOT_ID, HUMANOID_ID)


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


@dataclass
class SpawnedWalker:
    spec: CustomWalkerSpec
    walker: carla.Actor
    controller: carla.Actor
    anchor: CustomWalkerAnchor | None = None
    track_label: str = ""


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


def location_from_sample(sample):
    return carla.Location(
        x=float(sample["x"]),
        y=float(sample["y"]),
        z=float(sample["z"]),
    )


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
):
    best_location = None
    best_score = None
    search_radii = (max_radius, max(max_radius * 1.5, 24.0), max(max_radius * 2.0, 32.0))

    for radius_limit in search_radii:
        for _ in range(sample_count):
            location = world.get_random_location_from_navigation()
            if location is None:
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
    for actor in actors:
        if actor.type_id != "controller.ai.walker":
            continue
        parent = getattr(actor, "parent", None)
        if (parent is not None) and (parent.id in walker_ids):
            controllers.append(actor)

    for controller in controllers:
        try:
            controller.stop()
        except RuntimeError:
            pass

    for controller in controllers:
        try:
            controller.destroy()
        except RuntimeError:
            pass

    for walker in custom_walkers:
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
        for _ in range(12):
            spawn_location = pick_navigation_location_near_anchor(
                world,
                anchor.location,
                preferred_radius=7.0,
                min_radius=3.0,
                max_radius=18.0,
                sample_count=400,
                avoid_locations=used_locations,
                avoid_radius=3.0,
            )
            if spawn_location is None:
                continue

            spawn_transform = carla.Transform(
                spawn_location,
                carla.Rotation(yaw=yaw_toward(spawn_location, anchor.location)),
            )
            candidate = spawn_custom_walker(
                world,
                blueprint_id,
                spawn_transform,
                anchor=anchor,
                track_label=anchor.track_label,
            )
            actual_location = candidate.walker.get_transform().location
            spawn_error = distance_between(actual_location, spawn_location)
            anchor_error = distance_between(actual_location, anchor.location)
            if spawn_error <= 5.0 and anchor_error <= 22.0:
                spawned_walker = candidate
                used_locations.append(actual_location)
                break

            destroy_spawned_walkers([candidate])
            used_locations.append(spawn_location)

        if spawned_walker is None:
            raise RuntimeError(
                f"Failed to spawn {blueprint_id} near anchor "
                f"{anchor.label} ({anchor.actor_type_id}, actor_id={anchor.actor_id}) "
                f"within the allowed distance."
            )

        spawned_walkers.append(spawned_walker)

    return spawned_walkers


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
        if anchor_error > max_anchor_error:
            invalid.append((spawned_walker, f"anchor_error={anchor_error:.2f}"))
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
        try:
            spawned_walker.controller.stop()
        except RuntimeError:
            pass
        try:
            spawned_walker.controller.destroy()
        except RuntimeError:
            pass
        try:
            spawned_walker.walker.destroy()
        except RuntimeError:
            pass
