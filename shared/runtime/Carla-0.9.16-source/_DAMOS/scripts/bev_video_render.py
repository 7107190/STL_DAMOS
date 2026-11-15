"""Actual synchronized six-panel video; no temporal or prediction interpolation."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import cv2
import numpy as np
from PIL import Image

from bev_evaluation import decode_instance_cameras, decode_semantic, local_points, points_mask
from bev_fusion import fuse_bevs, warp_to_ego
from bev_fusion_diagnostic import load_npz
from bev_scene_camera import save_overhead
from bev_sensor_rig import CAMERAS
from bev_occupancy import ego_visible_crop, project_ego_pose
from report_bev_camera_triptych import bev_view, overhead_view, SIZE, GAP, MARGIN, WIDTH, HEIGHT
from report_bev_occlusion_triptych import BACKGROUND, MUTED, TEXT, label


def save_video_evidence(folder, tasks, truth, packets, overhead, node_labels, manifest):
    frame, stamp = truth["frame_id"], truth["capture_sim_ns"]
    evidence = {"semantic": {}, "instance": {}, "rgb": {}}
    for node, track in node_labels.items():
        task = tasks[node]
        if task["window"][-1] != frame or task["actor_id"] != manifest["actors"][node]["actor_id"]:
            raise ValueError("Video actor/input frame changed.")
        packet = packets[track]
        if packet["frame_id"] != frame or abs(packet["capture_sim_ns"] - stamp) > 1000:
            raise ValueError("Wrong-frame semantic evidence.")
        xyz, ids, tags = decode_semantic(packet)
        evidence["semantic"][node] = (xyz, ids, tags)
        np.savez_compressed(folder / f"{node}_eval_only_semantic.npz", world_xyz=xyz, actor_ids=ids, tags=tags)
        instances = decode_instance_cameras(packet["instance_cameras"], frame)
        evidence["instance"][node] = instances
        names = list(CAMERAS)
        np.savez_compressed(folder / f"{node}_eval_only_instance.npz", frame_id=frame,
            names=np.array(names), ids=np.stack([instances[n]["ids"] for n in names]),
            tags=np.stack([instances[n]["tags"] for n in names]))
        evidence["rgb"][node] = {}
        for name in CAMERAS:
            camera = task["packets"][track, name][-1]
            if camera["frame"] != frame or abs(round(camera["timestamp"] * 1e9) - stamp) > 1000:
                raise ValueError("Wrong-frame video RGB.")
            pixels = np.frombuffer(camera["raw"], np.uint8).reshape(450, 800, 4)[:, :, :3]
            evidence["rgb"][node][name] = pixels
            if not cv2.imwrite(str(folder / f"{node}_rgb_{name}.png"), pixels):
                raise OSError("Failed to save actual video RGB.")
    save_overhead(folder, overhead, frame, stamp)
    (folder / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    return evidence


def projection_counts(points, pose, arrays):
    local = local_points(points, pose)
    counts = {}
    for k, name in enumerate(CAMERAS):
        optical = (local - arrays["trans"][0, -1, k]) @ arrays["rots"][0, -1, k]
        q = optical @ arrays["intrins"][0, -1, k].T
        uv = q[:, :2] / np.maximum(q[:, 2:3], 1e-8)
        counts[name] = int(((optical[:, 2] > .1) & (uv[:, 0] >= 0) & (uv[:, 0] < 800)
                           & (uv[:, 1] >= 0) & (uv[:, 1] < 450)).sum())
    return counts


def stable_camera(counts, previous):
    best = max(CAMERAS, key=lambda name: (counts[name], name == "cam_front"))
    if previous is not None and counts[previous] >= max(3, counts[best] * .35):
        return previous
    return best if counts[best] else previous or "cam_front"


class VideoRenderer:
    def __init__(self, folder, manifest):
        self.folder, self.manifest = Path(folder), manifest
        self.previous_cameras, self.first_stamp = {}, None
        self.closed = False
        self.metadata = {"fps": manifest["fps"], "width": WIDTH, "height": HEIGHT,
                         "interpolation": False, "playback_clock": "CARLA simulation timestamp, 1x",
                         "frame_count": 0}
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg required; no placeholder video.")
        self.log = (self.folder / "ffmpeg.log").open("w")
        self.process = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "warning", "-n",
            "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{WIDTH}x{HEIGHT}",
            "-framerate", str(manifest["fps"]), "-i", "pipe:0", "-an", "-c:v", "libx264",
            "-threads", "2", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(self.folder / "passby_2x3.mp4")],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.log)

    def append(self, folder, truth, evidence, index):
        frame, stamp = truth["frame_id"], truth["capture_sim_ns"]
        if self.first_stamp is None:
            self.first_stamp = stamp
        if abs(stamp - self.first_stamp - round(index / self.manifest["fps"] * 1e9)) > 2000:
            raise ValueError("Cannot encode asynchronous sources as a synchronized video.")
        own, fused = load_npz(folder / "ego_own.npz"), load_npz(folder / "ego_fused.npz")
        observer = next(n for n in self.manifest["actors"] if n != "ego")
        peer = load_npz(folder / f"ego_received_{observer}.npz")
        peer_own = load_npz(folder / f"{observer}_own.npz")
        for node in ("ego", observer):
            packet = json.loads((folder / f"{node}_packet.json").read_text())["manifest"]
            if (packet["frame_id"] != frame or packet["actor_id"] != self.manifest["actors"][node]["actor_id"]
                    or abs(packet["capture_sim_ns"] - stamp) > 1000):
                raise ValueError("Video packet actor/frame/timestamp mismatch.")
        np.testing.assert_array_equal(peer["probabilities"], peer_own["probabilities"])
        np.testing.assert_array_equal(peer["valid"], peer_own["valid"])
        timing = json.loads((folder / "peers.json").read_text())
        replay = fuse_bevs(own, [peer], mode=timing["ego"]["fusion_mode"])
        np.testing.assert_array_equal(replay["occupancy"], fused["occupancy"])
        np.testing.assert_allclose(replay["probabilities"], fused["probabilities"], atol=1e-5, rtol=0)
        target_ids = self.manifest["target_ids"]
        hits = np.concatenate([xyz[np.isin(ids, target_ids)] for xyz, ids, _ in evidence["semantic"].values()])
        mask = points_mask(hits, own["pose"])
        target_locations = [t["location_model_world"] for t in truth["targets"] if t["actor_id"] in target_ids]
        if len(target_locations) != len(target_ids):
            raise ValueError("Pinned video target missing; no replacement actor allowed.")
        current_anchor = np.mean(target_locations, axis=0)
        centre = local_points(current_anchor[None], own["pose"])[0]
        start = np.clip(np.floor((centre[:2] + 40) / .4).astype(int) - 50, 0, 100)
        crop = ego_visible_crop((int(start[0]), int(start[1]), 100), own["pose"], own["pose"])
        probabilities, valid = warp_to_ego(peer, own["pose"])
        aligned = {"probabilities": probabilities, "valid": valid, "pose": own["pose"]}
        bev_images = [bev_view(p, mask, crop, context=True, ego_pose=own["pose"]) for p in (own, aligned, fused)]
        rgb_images, cameras = [], {}
        for node, pose in (("ego", own["pose"]), (observer, peer_own["pose"])):
            arrays = load_npz(folder / f"{node}_model_inputs.npz")
            if int(arrays["frame_ids"][-1]) != frame:
                raise ValueError("Video model/RGB frame mismatch.")
            name = stable_camera(projection_counts(hits, pose, arrays), self.previous_cameras.get(node))
            self.previous_cameras[node] = name
            pixels = evidence["rgb"][node][name].copy()
            visible = np.isin(evidence["instance"][node][name]["ids"], target_ids).astype(np.uint8)
            contours, _ = cv2.findContours(visible, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(pixels, contours, -1, (0, 225, 255), 2)
            rgb_images.append(Image.fromarray(pixels[:, :, ::-1]).resize((SIZE, 360), Image.Resampling.LANCZOS))
            path = folder / f"{node}_rgb_{name}.png"
            cameras[node] = {"name": name, "frame_id": frame, "target_pixels": int(visible.sum()),
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        overhead, overhead_record = overhead_view(folder, own, peer,
            {"location_model_world": current_anchor}, frame, stamp)
        rgb_images.append(overhead)
        image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
        label(image, (MARGIN, 10), f"{self.manifest['scenario']}  |  anchor {self.manifest['anchor_index']}  |  F{frame}  |  t={(stamp-self.first_stamp)/1e9:.2f} s", 29, bold=True)
        state = truth["ego_road_state"]
        label(image, (MARGIN, 52), f"SAME-FRAME REPLAY  |  ego {state['speed_mps']*3.6:.1f} km/h  |  anchor {np.linalg.norm(centre[:2]):.1f} m  |  BEV window {crop[2]*.4:g} m", 22, MUTED)
        label(image, (MARGIN, 87), "Recorded driving sensors -> actual BEV + Groth16 + fusion; not a live latency benchmark", 21, (255, 198, 96))
        titles = ("EGO BEV", f"OBSERVER {self.manifest['actors'][observer]['actor_id']} BEV", "FUSED BEV")
        subtitles = ("Own prediction", "Verified received BEV / ego axes", "Actual ego + assigned observer fusion")
        camera_titles = (f"EGO RGB / {cameras['ego']['name']}",
                         f"OBSERVER RGB / {cameras[observer]['name']}", "ACTUAL OVERHEAD RGB / SAME FRAME")
        clock = f"F{frame} t={(stamp-self.first_stamp)/1e9:.1f}s"
        notes = (f"{clock} | target pixels: {cameras['ego']['target_pixels']}",
                 f"{clock} | target pixels: {cameras[observer]['target_pixels']}",
                 "E=ego / O=observer / T=anchor centre")
        for i in range(3):
            x = MARGIN + i * (SIZE + GAP)
            label(image, (x, 127), titles[i], 23, bold=True)
            label(image, (x, 160), subtitles[i], 19, MUTED)
            image.paste(bev_images[i], (x, 192))
            label(image, (x, 846), camera_titles[i], 19, bold=True)
            label(image, (x, 876), notes[i], 18, MUTED)
            image.paste(rgb_images[i], (x, 908))
        label(image, (MARGIN, 1279), "Orange=all predicted obstacles; gray=ground; dark=unobserved; red EGO+arrow=recorded ego pose. White/yellow outlines=GT, not detections.", 18, MUTED)
        self.process.stdin.write(image.tobytes())
        if index == 0 or index == self.manifest["selected_frames"] // 2 or index == self.manifest["selected_frames"] - 1:
            image.save(self.folder / f"preview_{index:03d}.png")
        self.metadata["frame_count"] += 1
        record = {"frame_id": frame, "capture_sim_ns": stamp, "video_time_s": index / self.manifest["fps"],
                  "actors": self.manifest["actors"], "cameras": cameras,
                  "overhead_sha256": overhead_record["sha256"], "crop_grid": list(crop),
                  "ego_marker": project_ego_pose(own["pose"], own["pose"], crop, SIZE),
                  "current_region_centre_model_world": current_anchor.tolist(),
                  "ground_truth_fed_to_model_or_fusion": False, "gt_region_return_cells": int(mask.sum())}
        (folder / "video_frame.json").write_text(json.dumps(record, indent=2))
        return record

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.process.stdin.close()
            code = self.process.wait(timeout=120)
            if code:
                raise RuntimeError(f"Video encoder failed: {code}")
            path = self.folder / "passby_2x3.mp4"
            self.metadata.update(video_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 video_bytes=path.stat().st_size,
                                 duration_seconds=self.metadata["frame_count"] / self.manifest["fps"])
        finally:
            if self.process.poll() is None:
                self.process.kill(); self.process.wait()
            self.log.close()
