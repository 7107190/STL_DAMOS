#!/usr/bin/env python3

import argparse
import json
import math
import pathlib
import subprocess
import sys
import threading
import time

import carla


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run Scenic S1 and verify that each S1 pedestrian starts crossing "
            "when the Scenic ego vehicle approaches under CARLA autopilot."
        )
    )
    parser.add_argument("--root", default="/home/vvu/vv/DAMOS/Carla-0.9.16-source")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2431)
    parser.add_argument("--scenic-time", type=int, default=90)
    parser.add_argument("--wait-seconds", type=float, default=180.0)
    parser.add_argument("--per-anchor-seconds", type=float, default=12.0)
    parser.add_argument("--trigger-distance", type=float, default=11.0)
    parser.add_argument("--ego-upstream-distance", type=float, default=8.0)
    parser.add_argument("--pass-move-meters", type=float, default=2.0)
    parser.add_argument("--output-json", default="")
    parser.add_argument(
        "--attach-only",
        action="store_true",
        help="Do not launch the runner; attach to an already running S1 scenario.",
    )
    parser.add_argument("--keep-running", action="store_true")
    return parser.parse_args()


def actor_location(actor):
    loc = actor.get_transform().location
    return (float(loc.x), float(loc.y), float(loc.z))


def distance_xy(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def wait_for_world(host, port, timeout_seconds):
    client = carla.Client(host, port)
    client.set_timeout(5.0)
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            world = client.get_world()
            world.get_map()
            return client, world
        except RuntimeError as exc:
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(f"Timed out waiting for CARLA world: {last_error}")


def matching_actors(world):
    actors = list(world.get_actors())
    ego = None
    pedestrians = []
    observers = []
    for actor in actors:
        role_name = actor.attributes.get("role_name", "")
        if actor.type_id.startswith("vehicle.") and role_name == "ego":
            ego = actor
        elif actor.type_id.startswith("walker.pedestrian.damos_"):
            observers.append(actor)
        elif actor.type_id.startswith("walker.pedestrian.") and role_name == "damos.S1.1":
            pedestrians.append(actor)
    pedestrians.sort(key=lambda actor: (actor.get_location().x, actor.get_location().y))
    return ego, pedestrians, observers


def wait_for_s1_actors(world, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ego, pedestrians, observers = matching_actors(world)
        if ego is not None and len(pedestrians) >= 3 and len(observers) >= 3:
            return ego, pedestrians[:3], observers
        time.sleep(0.5)
    ego, pedestrians, observers = matching_actors(world)
    raise RuntimeError(
        "Timed out waiting for S1 actors "
        f"(ego={ego is not None}, pedestrians={len(pedestrians)}, observers={len(observers)})."
    )


def waypoint_forward(transform):
    forward = transform.get_forward_vector()
    return (float(forward.x), float(forward.y), float(forward.z))


def place_ego_upstream(world, ego, pedestrian, upstream_distance):
    carla_map = world.get_map()
    ped_loc = pedestrian.get_transform().location
    waypoint = carla_map.get_waypoint(
        ped_loc,
        project_to_road=True,
        lane_type=carla.LaneType.Driving,
    )
    if waypoint is None:
        raise RuntimeError(f"No driving waypoint near pedestrian {pedestrian.id}.")

    wp_transform = waypoint.transform
    forward = waypoint_forward(wp_transform)
    ego_loc = carla.Location(
        x=wp_transform.location.x - forward[0] * upstream_distance,
        y=wp_transform.location.y - forward[1] * upstream_distance,
        z=wp_transform.location.z + 0.5,
    )
    ego_transform = carla.Transform(ego_loc, wp_transform.rotation)
    ego.set_autopilot(False)
    ego.set_target_velocity(carla.Vector3D(0, 0, 0))
    ego.set_target_angular_velocity(carla.Vector3D(0, 0, 0))
    ego.set_transform(ego_transform)
    ego.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0, hand_brake=True))
    time.sleep(0.2)
    ego.apply_control(carla.VehicleControl(throttle=0.0, brake=0.0, hand_brake=False))
    try:
        world.tick()
    except RuntimeError:
        world.wait_for_tick(1.0)
    return waypoint


def enable_autopilot(client, ego):
    tm = client.get_trafficmanager()
    tm.ignore_lights_percentage(ego, 100.0)
    tm.ignore_signs_percentage(ego, 100.0)
    tm.distance_to_leading_vehicle(ego, 2.0)
    tm.vehicle_percentage_speed_difference(ego, -20.0)
    ego.set_autopilot(True, tm.get_port())
    return tm.get_port()


def verify_one(client, world, ego, pedestrian, index, args):
    start = actor_location(pedestrian)
    waypoint = place_ego_upstream(world, ego, pedestrian, args.ego_upstream_distance)
    tm_port = enable_autopilot(client, ego)

    min_ego_distance = float("inf")
    max_ped_movement = 0.0
    samples = []
    deadline = time.monotonic() + args.per_anchor_seconds
    while time.monotonic() < deadline:
        try:
            world.wait_for_tick(1.0)
        except RuntimeError:
            time.sleep(0.1)
        ego_pos = actor_location(ego)
        ped_pos = actor_location(pedestrian)
        ego_distance = distance_xy(ego_pos, ped_pos)
        ped_movement = distance_xy(start, ped_pos)
        min_ego_distance = min(min_ego_distance, ego_distance)
        max_ped_movement = max(max_ped_movement, ped_movement)
        samples.append(
            {
                "ego": {"x": ego_pos[0], "y": ego_pos[1], "z": ego_pos[2]},
                "pedestrian": {"x": ped_pos[0], "y": ped_pos[1], "z": ped_pos[2]},
                "ego_distance": round(ego_distance, 3),
                "pedestrian_movement": round(ped_movement, 3),
            }
        )
        if min_ego_distance <= args.trigger_distance and max_ped_movement >= args.pass_move_meters:
            break

    ego.set_autopilot(False)
    passed = (
        min_ego_distance <= args.trigger_distance
        and max_ped_movement >= args.pass_move_meters
    )
    return {
        "anchor_index": index,
        "pedestrian_actor_id": pedestrian.id,
        "traffic_manager_port": tm_port,
        "start": {"x": start[0], "y": start[1], "z": start[2]},
        "nearest_driving_waypoint": {
            "x": float(waypoint.transform.location.x),
            "y": float(waypoint.transform.location.y),
            "z": float(waypoint.transform.location.z),
            "yaw": float(waypoint.transform.rotation.yaw),
            "road_id": waypoint.road_id,
            "lane_id": waypoint.lane_id,
        },
        "min_ego_distance": round(min_ego_distance, 3),
        "max_pedestrian_movement": round(max_ped_movement, 3),
        "passed": passed,
        "samples": samples,
    }


def main():
    args = parse_args()
    root = pathlib.Path(args.root).resolve()
    runner = root / "_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh"
    if not runner.exists():
        raise RuntimeError(f"Missing runner: {runner}")

    cmd = [
        str(runner),
        "--restart",
        "--headless",
        "--port",
        str(args.port),
        "--scenic-time",
        str(args.scenic_time),
        "--n-scenarios",
        "1",
        "--selected-scenario",
        "S1",
        "--realtime-factor",
        "1",
        "--observer-mode",
        "--no-observer-cameras",
    ]
    output_lines = []
    proc = None
    reader = None

    if args.attach_only:
        print(f"Attaching to existing S1 Scenic run on {args.host}:{args.port}.")
    else:
        print("Launching S1 Scenic run:")
        print("  " + " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
        )

        def read_runner_output():
            if proc.stdout is None:
                return
            for line in proc.stdout:
                output_lines.append(line)
                if len(output_lines) > 200:
                    del output_lines[:100]

        reader = threading.Thread(target=read_runner_output, daemon=True)
        reader.start()

    try:
        client, world = wait_for_world(args.host, args.port, args.wait_seconds)
        ego, pedestrians, observers = wait_for_s1_actors(world, args.wait_seconds)
        print(
            f"Detected ego={ego.id}, S1 pedestrians={[p.id for p in pedestrians]}, "
            f"observers={[o.id for o in observers]}."
        )

        results = []
        for index, pedestrian in enumerate(pedestrians, start=1):
            result = verify_one(client, world, ego, pedestrian, index, args)
            results.append(result)
            status = "PASS" if result["passed"] else "FAIL"
            print(
                f"[{status}] anchor {index}: min ego distance="
                f"{result['min_ego_distance']}m, pedestrian moved="
                f"{result['max_pedestrian_movement']}m"
            )

        summary = {
            "scenario": "S1",
            "port": args.port,
            "passed": all(item["passed"] for item in results),
            "results": results,
        }
        if args.output_json:
            output_path = pathlib.Path(args.output_json)
        else:
            output_dir = root / "_DAMOS/reports"
            output_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            output_path = output_dir / f"s1_crossing_autopilot_verify_port{args.port}_{stamp}.json"
        output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Saved verification JSON: {output_path}")
        return 0 if summary["passed"] else 1
    finally:
        if proc is not None and not args.keep_running and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if reader is not None:
            reader.join(timeout=2)
        if output_lines:
            print("Recent runner output:")
            for line in output_lines[-40:]:
                sys.stdout.write(line)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
