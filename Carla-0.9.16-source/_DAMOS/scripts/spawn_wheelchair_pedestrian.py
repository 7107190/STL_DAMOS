#!/usr/bin/env python3

import argparse
import sys
import time
import zipfile
from pathlib import Path


def parse_args():
    repo_sibling = Path(__file__).resolve().parents[3]
    default_carla_root = repo_sibling / "Carla-0.9.16"

    parser = argparse.ArgumentParser(
        description="Spawn a wheelchair pedestrian in CARLA 0.9.16."
    )
    parser.add_argument("--host", default="127.0.0.1", help="CARLA server host")
    parser.add_argument("--port", type=int, default=2000, help="CARLA RPC port")
    parser.add_argument(
        "--carla-root",
        type=Path,
        default=default_carla_root,
        help="Path to the CARLA 0.9.16 package root",
    )
    parser.add_argument(
        "--blueprint",
        default="walker.pedestrian.0028",
        help="Wheelchair-compatible walker blueprint",
    )
    parser.add_argument(
        "--keep-seconds",
        type=float,
        default=10.0,
        help="How long to keep the spawned actors alive before cleanup",
    )
    return parser.parse_args()


def ensure_carla_client(carla_root: Path) -> None:
    wheel_dir = carla_root / "PythonAPI" / "carla" / "dist"
    wheel_name = f"carla-0.9.16-cp{sys.version_info.major}{sys.version_info.minor}"
    matches = sorted(wheel_dir.glob(f"{wheel_name}-*.whl"))
    if not matches:
        raise FileNotFoundError(
            f"Could not find a CARLA 0.9.16 Python wheel under {wheel_dir}"
        )

    wheel_path = matches[0]
    cache_dir = Path.home() / ".cache" / "damos" / "carla_client" / wheel_path.stem
    libcarla_glob = list((cache_dir / "carla").glob("libcarla*.so"))

    if not libcarla_glob:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(wheel_path) as archive:
            archive.extractall(cache_dir)

    sys.path.insert(0, str(cache_dir))


def import_carla(carla_root: Path):
    ensure_carla_client(carla_root)
    import carla  # pylint: disable=import-outside-toplevel

    return carla


def pick_spawn_transform(world, carla):
    location = world.get_random_location_from_navigation()
    if location is not None:
        location.z += 0.5
        return carla.Transform(location)

    spawn_points = world.get_map().get_spawn_points()
    if not spawn_points:
        raise RuntimeError("Could not find a valid spawn point.")

    transform = spawn_points[0]
    transform.location.z += 1.0
    return transform


def choose_speed(blueprint) -> float:
    if not blueprint.has_attribute("speed"):
        return 1.0

    values = []
    for value in blueprint.get_attribute("speed").recommended_values:
        try:
            values.append(float(value))
        except ValueError:
            continue

    if len(values) >= 2:
        return values[1]
    if values:
        return values[0]
    return 1.0


def main():
    args = parse_args()
    carla = import_carla(args.carla_root)

    client = carla.Client(args.host, args.port)
    client.set_timeout(20.0)
    world = client.get_world()
    bp_lib = world.get_blueprint_library()

    walker_bp = bp_lib.find(args.blueprint)
    if not walker_bp.has_attribute("can_use_wheelchair"):
        raise RuntimeError(
            f"{args.blueprint} does not expose the can_use_wheelchair attribute."
        )
    if not walker_bp.has_attribute("use_wheelchair"):
        raise RuntimeError(
            f"{args.blueprint} does not expose the use_wheelchair attribute."
        )

    walker_bp.set_attribute("use_wheelchair", "True")
    if walker_bp.has_attribute("is_invincible"):
        walker_bp.set_attribute("is_invincible", "False")
    if walker_bp.has_attribute("role_name"):
        walker_bp.set_attribute("role_name", "damos_wheelchair_demo")

    spawn_transform = pick_spawn_transform(world, carla)
    pedestrian = None
    controller = None

    try:
        pedestrian = world.spawn_actor(walker_bp, spawn_transform)
        controller_bp = bp_lib.find("controller.ai.walker")
        controller = world.spawn_actor(controller_bp, carla.Transform(), pedestrian)

        controller.start()
        destination = world.get_random_location_from_navigation()
        if destination is not None:
            controller.go_to_location(destination)
        controller.set_max_speed(choose_speed(walker_bp))

        print(f"Connected to map: {world.get_map().name}")
        print(f"Spawned: {walker_bp.id}")
        print(f"Pedestrian actor id: {pedestrian.id}")
        print(f"Controller actor id: {controller.id}")
        print(
            f"Wheelchair mode: "
            f"{walker_bp.get_attribute('use_wheelchair').as_bool()}"
        )
        print(f"Keeping actors alive for {args.keep_seconds:.1f} seconds...")
        time.sleep(args.keep_seconds)
    finally:
        if controller is not None:
            controller.stop()
            controller.destroy()
        if pedestrian is not None:
            pedestrian.destroy()
        print("Cleaned up spawned actors.")


if __name__ == "__main__":
    main()
