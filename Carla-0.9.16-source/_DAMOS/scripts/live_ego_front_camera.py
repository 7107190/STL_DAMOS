#!/usr/bin/env python3

from __future__ import annotations

import argparse
import random
import time

import carla
import cv2
import numpy as np
import pygame


FAULT_CHOICES = (
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
RANDOM_FAULTS = (
    "blackout",
    "blur",
    "occlusion",
    "color_failure",
    "misalignment",
    "shaking",
    "freeze_cycle",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Show a live ego cam_front RGB view with an optional camera fault.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--fov", type=float, default=90.0)
    parser.add_argument("--sensor-tick", type=float, default=0.05)
    parser.add_argument("--fault", choices=FAULT_CHOICES, default="none")
    parser.add_argument("--fault-start-sec", type=float, default=0.0)
    parser.add_argument("--occlusion-update-sec", type=float, default=3.0)
    parser.add_argument("--no-overlay", action="store_true")
    return parser.parse_args()


def find_ego_vehicle(world):
    for actor in world.get_actors():
        if actor.type_id.startswith("vehicle.") and actor.attributes.get("role_name") == "ego":
            return actor
    return None


def resolve_fault(fault):
    if fault == "random":
        return random.choice(RANDOM_FAULTS)
    return fault


def make_fault_state(fault, args):
    state = {
        "fault": fault,
        "requested_fault": args.fault,
        "fault_start_sec": float(args.fault_start_sec),
        "occlusion_update_sec": float(args.occlusion_update_sec),
        "program_start_time": None,
        "last_occlusion_update_time": None,
        "occlusion_box": None,
        "frozen_frame": None,
        "previous_freeze_state": False,
        "current_dx": 0.0,
        "current_dy": 0.0,
    }
    if fault == "blur":
        state["kernel_size"] = random.choice(list(range(15, 92, 2)))
        state["sigma"] = 0
    elif fault == "color_failure":
        channel = random.choice((0, 1, 2))
        state["failed_channel"] = channel
        state["failed_channel_name"] = ("RED", "GREEN", "BLUE")[channel]
    elif fault == "misalignment":
        state["pitch_delta"] = random.choice((-12.0, -10.0, 10.0, 12.0))
        state["yaw_delta"] = random.choice((-25.0, -20.0, 20.0, 25.0))
        state["roll_delta"] = random.choice((-10.0, -7.0, 7.0, 10.0))
    elif fault == "shaking":
        state["max_shift"] = 35
        state["smoothing"] = 0.35
    elif fault == "freeze_cycle":
        state["normal_duration"] = 5.0
        state["freeze_duration"] = 3.0
    return state


def apply_blackout(rgb, _state):
    return np.zeros_like(rgb)


def apply_blur(rgb, state):
    kernel_size = int(state.get("kernel_size") or 51)
    if kernel_size % 2 == 0:
        kernel_size += 1
    return cv2.GaussianBlur(rgb, (kernel_size, kernel_size), float(state.get("sigma") or 0))


def apply_occlusion(rgb, state):
    out = rgb.copy()
    height, width, _ = out.shape
    now = time.time()
    if state["occlusion_box"] is None:
        box_w = random.randint(int(width * 0.15), int(width * 0.35))
        box_h = random.randint(int(height * 0.15), int(height * 0.35))
        x1 = random.randint(0, max(0, width - box_w))
        y1 = random.randint(0, max(0, height - box_h))
        state["occlusion_box"] = (x1, y1, x1 + box_w, y1 + box_h)
        state["last_occlusion_update_time"] = now
        print(f"[INFO] Initial occlusion box: {state['occlusion_box']}")
    elif (
        state["occlusion_update_sec"] > 0.0
        and now - float(state["last_occlusion_update_time"] or now)
        >= state["occlusion_update_sec"]
    ):
        box_w = random.randint(int(width * 0.15), int(width * 0.35))
        box_h = random.randint(int(height * 0.15), int(height * 0.35))
        x1 = random.randint(0, max(0, width - box_w))
        y1 = random.randint(0, max(0, height - box_h))
        state["occlusion_box"] = (x1, y1, x1 + box_w, y1 + box_h)
        state["last_occlusion_update_time"] = now
        print(f"[INFO] Updated occlusion box: {state['occlusion_box']}")

    x1, y1, x2, y2 = state["occlusion_box"]
    out[int(y1):int(y2), int(x1):int(x2), :] = 0
    return out


def apply_color_failure(rgb, state):
    out = rgb.copy()
    out[:, :, int(state.get("failed_channel") or 0)] = 0
    return out


def apply_shaking(rgb, state):
    height, width, _ = rgb.shape
    max_shift = int(state.get("max_shift") or 35)
    smoothing = float(state.get("smoothing") or 0.35)
    target_dx = random.randint(-max_shift, max_shift)
    target_dy = random.randint(-max_shift, max_shift)
    state["current_dx"] = (1.0 - smoothing) * float(state["current_dx"]) + smoothing * target_dx
    state["current_dy"] = (1.0 - smoothing) * float(state["current_dy"]) + smoothing * target_dy
    matrix = np.float32([[1, 0, int(state["current_dx"])], [0, 1, int(state["current_dy"])]])
    return cv2.warpAffine(rgb, matrix, (width, height), borderMode=cv2.BORDER_REFLECT)


def apply_freeze_cycle(rgb, state, elapsed):
    if elapsed < state["fault_start_sec"]:
        return rgb
    cycle_time = float(state["normal_duration"]) + float(state["freeze_duration"])
    phase_time = (elapsed - float(state["fault_start_sec"])) % cycle_time
    freeze_active = phase_time >= float(state["normal_duration"])

    if not freeze_active:
        state["previous_freeze_state"] = False
        state["frozen_frame"] = None
        return rgb

    if freeze_active and not state["previous_freeze_state"]:
        state["frozen_frame"] = rgb.copy()
        state["previous_freeze_state"] = True
        print("[INFO] Freeze started.")

    return state["frozen_frame"].copy() if state["frozen_frame"] is not None else rgb


def apply_fault(rgb, state):
    now = time.time()
    if state["program_start_time"] is None:
        state["program_start_time"] = now
    elapsed = now - float(state["program_start_time"])
    if elapsed < float(state["fault_start_sec"]):
        return rgb

    fault = state["fault"]
    if fault in {"none", "misalignment"}:
        return rgb
    if fault == "blackout":
        return apply_blackout(rgb, state)
    if fault == "blur":
        return apply_blur(rgb, state)
    if fault == "occlusion":
        return apply_occlusion(rgb, state)
    if fault == "color_failure":
        return apply_color_failure(rgb, state)
    if fault == "shaking":
        return apply_shaking(rgb, state)
    if fault == "freeze_cycle":
        return apply_freeze_cycle(rgb, state, elapsed)
    return rgb


def camera_transform_for_fault(state):
    rotation = carla.Rotation(pitch=0.0, yaw=0.0, roll=0.0)
    if state["fault"] == "misalignment":
        rotation = carla.Rotation(
            pitch=float(state["pitch_delta"]),
            yaw=float(state["yaw_delta"]),
            roll=float(state["roll_delta"]),
        )
    return carla.Transform(
        carla.Location(x=1.5, y=0.0, z=1.75),
        rotation,
    )


def overlay_lines(state):
    fault = state["fault"]
    lines = [
        f"ego cam_front live | fault={fault}",
        f"requested={state['requested_fault']} | starts={state['fault_start_sec']:.1f}s",
    ]
    if fault == "blur":
        lines.append(f"blur kernel={state['kernel_size']}")
    elif fault == "color_failure":
        lines.append(f"failed channel={state['failed_channel_name']}")
    elif fault == "misalignment":
        lines.append(
            f"pitch={state['pitch_delta']:.1f} yaw={state['yaw_delta']:.1f} roll={state['roll_delta']:.1f}"
        )
    elif fault == "shaking":
        lines.append(f"shake max shift={state['max_shift']}px")
    elif fault == "freeze_cycle":
        lines.append(
            f"normal={state['normal_duration']:.1f}s freeze={state['freeze_duration']:.1f}s"
        )
    return lines


def main():
    args = parse_args()
    fault = resolve_fault(args.fault)
    fault_state = make_fault_state(fault, args)

    pygame.init()
    pygame.font.init()
    screen = pygame.display.set_mode((args.width, args.height))
    pygame.display.set_caption(f"DAMOS Ego Front Camera - {fault}")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Arial", 24)

    client = carla.Client(args.host, args.port)
    client.set_timeout(10.0)
    world = client.get_world()

    print(f"[INFO] Connecting to CARLA {args.host}:{args.port}")
    print("[INFO] Waiting for Scenic ego vehicle role_name=ego...")
    ego = None
    while ego is None:
        ego = find_ego_vehicle(world)
        if ego is None:
            time.sleep(1.0)
            world = client.get_world()
    print(f"[INFO] Ego found: id={ego.id}, type={ego.type_id}")
    print(f"[INFO] Ego cam_front live fault: {fault} (requested={args.fault})")

    bp = world.get_blueprint_library().find("sensor.camera.rgb")
    bp.set_attribute("image_size_x", str(args.width))
    bp.set_attribute("image_size_y", str(args.height))
    bp.set_attribute("fov", str(args.fov))
    bp.set_attribute("sensor_tick", str(args.sensor_tick))
    if bp.has_attribute("role_name"):
        bp.set_attribute("role_name", "damos_live_ego_cam_front")

    camera = world.spawn_actor(bp, camera_transform_for_fault(fault_state), attach_to=ego)
    surface_holder = {"surface": None}
    fault_state["program_start_time"] = time.time()

    def on_image(image):
        array = np.frombuffer(image.raw_data, dtype=np.uint8).reshape((image.height, image.width, 4))
        rgb = array[:, :, :3][:, :, ::-1].copy()
        rgb = apply_fault(rgb, fault_state)
        surface_holder["surface"] = pygame.surfarray.make_surface(rgb.swapaxes(0, 1))

    camera.listen(on_image)

    try:
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            if surface_holder["surface"] is None:
                screen.fill((0, 0, 0))
            else:
                screen.blit(surface_holder["surface"], (0, 0))

            if not args.no_overlay:
                for index, line in enumerate(overlay_lines(fault_state)):
                    color = (255, 70, 70) if index == 0 and fault != "none" else (255, 255, 255)
                    text = font.render(line, True, color)
                    screen.blit(text, (20, 20 + index * 30))

            pygame.display.flip()
            clock.tick(args.fps)
    finally:
        print("[INFO] Closing live ego front camera viewer.")
        try:
            camera.stop()
        except RuntimeError:
            pass
        try:
            camera.destroy()
        except RuntimeError:
            pass
        pygame.quit()


if __name__ == "__main__":
    main()
