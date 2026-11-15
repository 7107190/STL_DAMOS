#!/usr/bin/env python3

import argparse
import time

from custom_walker_runtime import (
    connect_to_world,
    destroy_spawned_walkers,
    format_location,
    initialize_custom_walker_movement,
    measure_walker_movements,
    run_custom_walker_movement,
    snapshot_walker_locations,
    spawn_all_custom_walkers,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--keep-seconds", type=float, default=10.0)
    parser.add_argument("--wait-for-server-seconds", type=float, default=0.0)
    parser.add_argument(
        "--min-move-meters",
        type=float,
        default=0.0,
        help="Fail if any spawned walker moves less than this distance before cleanup.",
    )
    parser.add_argument(
        "--random-spawn",
        action="store_true",
        help="Use random navigation spawn points instead of the Town01 demo layout.",
    )
    args = parser.parse_args()

    _, world = connect_to_world(args.host, args.port, args.wait_for_server_seconds)
    print(f"Connected to CARLA at {args.host}:{args.port} map={world.get_map().name}")

    spawned_walkers = []
    try:
        spawned_walkers = spawn_all_custom_walkers(
            world,
            random_spawn=args.random_spawn,
            cleanup_existing=True,
            set_spectator=not args.random_spawn,
        )
        initial_locations = snapshot_walker_locations(spawned_walkers)

        print("Spawned custom walkers:")
        for spawned_walker in spawned_walkers:
            walker = spawned_walker.walker
            controller = spawned_walker.controller
            location = walker.get_transform().location
            print(
                f"  walker={walker.type_id} actor_id={walker.id} "
                f"controller_id={controller.id} location={format_location(location)}"
            )
            if not args.random_spawn:
                print(
                    f"    goal={format_location(spawned_walker.spec.demo_destination)}"
                )

        spectator_transform = world.get_spectator().get_transform()
        print(
            "Spectator:"
            f" location={format_location(spectator_transform.location)}"
            f" rotation=({spectator_transform.rotation.pitch:.2f}, "
            f"{spectator_transform.rotation.yaw:.2f}, "
            f"{spectator_transform.rotation.roll:.2f})"
        )

        initialize_custom_walker_movement(
            world, spawned_walkers, random_spawn=args.random_spawn
        )
        run_custom_walker_movement(
            world,
            spawned_walkers,
            args.keep_seconds,
            random_spawn=args.random_spawn,
        )

        print("Final walker positions:")
        movement_failures = []
        for spawned_walker, final_location, moved in measure_walker_movements(
            spawned_walkers, initial_locations
        ):
            walker = spawned_walker.walker
            print(
                f"  walker={walker.type_id} actor_id={walker.id} "
                f"final_location={format_location(final_location)} moved={moved:.2f}m"
            )
            if moved < args.min_move_meters:
                movement_failures.append((walker.type_id, moved))

        if movement_failures:
            details = ", ".join(
                f"{walker_type}={moved:.2f}m" for walker_type, moved in movement_failures
            )
            raise RuntimeError(
                f"Custom walker movement check failed; required at least "
                f"{args.min_move_meters:.2f}m. Observed: {details}"
            )
    finally:
        destroy_spawned_walkers(spawned_walkers)


if __name__ == "__main__":
    main()
