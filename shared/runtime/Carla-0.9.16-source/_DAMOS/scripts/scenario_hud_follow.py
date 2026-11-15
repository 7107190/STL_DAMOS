#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
import time

import carla


def axis_vectors(rotation: carla.Rotation):
    yaw = math.radians(rotation.yaw)
    pitch = math.radians(rotation.pitch)
    forward = carla.Location(
        x=math.cos(pitch) * math.cos(yaw),
        y=math.cos(pitch) * math.sin(yaw),
        z=math.sin(pitch),
    )
    right = carla.Location(
        x=math.cos(yaw + math.pi / 2.0),
        y=math.sin(yaw + math.pi / 2.0),
        z=0.0,
    )
    return forward, right, carla.Location(0.0, 0.0, 1.0)


def find_ego(world: carla.World):
    for actor in world.get_actors():
        if actor.type_id.startswith("vehicle.") and actor.attributes.get("role_name") == "ego":
            return actor
    return None


def set_chase_spectator(world: carla.World, ego: carla.Actor):
    transform = ego.get_transform()
    forward = transform.get_forward_vector()
    location = transform.location + carla.Location(
        x=-8.0 * forward.x,
        y=-8.0 * forward.y,
        z=3.2,
    )
    rotation = carla.Rotation(
        pitch=-14.0,
        yaw=transform.rotation.yaw,
        roll=0.0,
    )
    world.get_spectator().set_transform(carla.Transform(location, rotation))
    return carla.Transform(location, rotation)


def draw_hud(
    world: carla.World,
    text: str,
    spectator_transform: carla.Transform,
    *,
    corner: str,
    large: bool,
):
    forward, right, up = axis_vectors(spectator_transform.rotation)
    base = spectator_transform.location
    if corner == "lower-left":
        distance = 9.0 if large else 13.0
        horizontal = -5.8
        vertical = -1.8
    else:
        distance = 10.0 if large else 16.0
        horizontal = 6.5
        vertical = 2.8

    location = base + carla.Location(
        x=forward.x * distance + right.x * horizontal + up.x * vertical,
        y=forward.y * distance + right.y * horizontal + up.y * vertical,
        z=forward.z * distance + right.z * horizontal + up.z * vertical,
    )
    outline_offsets = [
        carla.Location(x=right.x * -0.08, y=right.y * -0.08, z=-0.08),
        carla.Location(x=right.x * 0.08, y=right.y * 0.08, z=0.08),
        carla.Location(x=right.x * -0.08, y=right.y * -0.08, z=0.08),
        carla.Location(x=right.x * 0.08, y=right.y * 0.08, z=-0.08),
    ]
    for offset in outline_offsets:
        world.debug.draw_string(
            location + offset,
            text,
            draw_shadow=True,
            color=carla.Color(0, 0, 0),
            life_time=0.2,
            persistent_lines=False,
        )
    world.debug.draw_string(
        location,
        text,
        draw_shadow=True,
        color=carla.Color(255, 230, 0),
        life_time=0.2,
        persistent_lines=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--no-follow-ego", action="store_true")
    parser.add_argument(
        "--corner",
        choices=("upper-right", "lower-left"),
        default="upper-right",
    )
    parser.add_argument("--large", action="store_true")
    args = parser.parse_args()

    client = carla.Client(args.host, args.port)
    client.set_timeout(10.0)
    world = client.get_world()
    label = f"DAMOS  |  {args.scenarios}"
    print(f"scenario HUD connected: port={args.port} scenarios={args.scenarios}", flush=True)

    while True:
        try:
            try:
                world.wait_for_tick(2.0)
            except Exception:
                pass

            spectator_transform = world.get_spectator().get_transform()
            ego = find_ego(world)
            if ego is not None and not args.no_follow_ego:
                spectator_transform = set_chase_spectator(world, ego)

            draw_hud(
                world,
                label,
                spectator_transform,
                corner=args.corner,
                large=args.large,
            )
            if ego is not None:
                ego_location = ego.get_location()
                ego_speed = ego.get_velocity().length()
                print(
                    f"hud ego={ego.id} loc=({ego_location.x:.1f},{ego_location.y:.1f}) "
                    f"speed={ego_speed:.2f} scenarios={args.scenarios}",
                    flush=True,
                )
            else:
                print(f"hud ego=missing scenarios={args.scenarios}", flush=True)
            time.sleep(0.05)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"hud error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(0.5)


if __name__ == "__main__":
    main()
