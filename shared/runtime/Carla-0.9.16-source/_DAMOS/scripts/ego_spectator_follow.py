#!/usr/bin/env python3

from __future__ import annotations

import argparse
import time

import carla


def find_ego(world: carla.World):
    for actor in world.get_actors():
        if actor.type_id.startswith("vehicle.") and actor.attributes.get("role_name") == "ego":
            return actor
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--distance", type=float, default=8.0)
    parser.add_argument("--height", type=float, default=3.2)
    parser.add_argument("--pitch", type=float, default=-14.0)
    args = parser.parse_args()

    client = carla.Client(args.host, args.port)
    client.set_timeout(10.0)
    world = client.get_world()
    spectator = world.get_spectator()
    print(
        f"ego spectator follow connected: {args.host}:{args.port} "
        f"distance={args.distance} height={args.height}",
        flush=True,
    )

    while True:
        try:
            try:
                world.wait_for_tick(2.0)
            except Exception:
                pass

            ego = find_ego(world)
            if ego is None:
                print("ego not found", flush=True)
                time.sleep(0.2)
                continue

            transform = ego.get_transform()
            forward = transform.get_forward_vector()
            location = transform.location + carla.Location(
                x=-args.distance * forward.x,
                y=-args.distance * forward.y,
                z=args.height,
            )
            rotation = carla.Rotation(
                pitch=args.pitch,
                yaw=transform.rotation.yaw,
                roll=0.0,
            )
            spectator.set_transform(carla.Transform(location, rotation))

            ego_location = ego.get_location()
            ego_speed = ego.get_velocity().length()
            print(
                f"follow ego={ego.id} loc=({ego_location.x:.1f},{ego_location.y:.1f}) "
                f"speed={ego_speed:.2f}mps",
                flush=True,
            )
            time.sleep(0.05)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"follow error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(0.5)


if __name__ == "__main__":
    main()
