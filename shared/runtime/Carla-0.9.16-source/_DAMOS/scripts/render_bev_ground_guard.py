"""Immutable same-frame offline correction videos; no new ZKP claim in the media."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from benchmark_bev_ground_guard import load, masks, add_metrics, ratios
from collections import defaultdict
from bev_fusion import fuse_bevs, warp_to_ego
from bev_ground_guard import apply_ground_guard
from bev_occupancy import ego_visible_crop, obstacle_presence, project_ego_pose, render_presence_focus

WIDTH, HEIGHT, SIZE = 1964, 1320, 640
BACKGROUND = (16, 23, 28)


def font(size, bold=False):
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    local = Path("/System/Library/Fonts/Supplemental") / name
    if local.exists():
        return ImageFont.truetype(str(local), size)
    return ImageFont.truetype("DejaVuSans.ttf", size)


def text(image, xy, value, size=21, color=(235, 240, 242), bold=False):
    draw = ImageDraw.Draw(image)
    f = font(size, bold)
    if draw.textbbox((0, 0), value, font=f)[2] > WIDTH - xy[0] - 12:
        raise ValueError("Diagnostic text exceeds image width.")
    draw.text(xy, value, font=f, fill=color)


def view(prediction, mask, crop, *, ego_pose=None):
    pixels = render_presence_focus(prediction, mask, crop, SIZE, ego_pose=ego_pose)
    return Image.fromarray(pixels[:, :, ::-1])


def read_frame(stream):
    length = WIDTH * HEIGHT * 3
    data = bytearray()
    while len(data) < length:
        chunk = stream.read(length - len(data))
        if not chunk:
            break
        data.extend(chunk)
    if len(data) != length:
        raise ValueError("Original RGB video frame missing.")
    return Image.frombytes("RGB", (WIDTH, HEIGHT), bytes(data))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    profile = json.loads(args.profile.read_text())
    ffmpeg = shutil.which("ffmpeg")
    results, metrics = [], defaultdict(lambda: defaultdict(int))
    for row in json.loads(args.index.read_text())["rows"]:
        root = Path(row["source_archive"])
        observer = next(n for n in row["actors"] if n != "ego")
        source_video = args.index.parent / row["file"]
        source_hash = hashlib.sha256(source_video.read_bytes()).hexdigest()
        folders = sorted(root.glob("round_*"))
        output = args.output / f"{row['scenario']}_ground_guard_2x3.mp4"
        decode = subprocess.Popen([ffmpeg, "-v", "error", "-i", str(source_video), "-f", "rawvideo",
            "-pix_fmt", "rgb24", "pipe:1"], stdout=subprocess.PIPE)
        encode = subprocess.Popen([ffmpeg, "-v", "error", "-n", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", "5", "-i", "pipe:0", "-an", "-c:v",
            "libx264", "-threads", "2", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(output)], stdin=subprocess.PIPE)
        records = []
        try:
            for i, folder in enumerate(folders):
                metadata = json.loads((folder / "video_frame.json").read_text())
                own = load(folder / "ego_own.npz")
                peer = load(folder / f"ego_received_{observer}.npz")
                original = load(folder / "ego_fused.npz")
                corrected = []
                changed_counts = []
                for node, prediction in (("ego", own), (observer, peer)):
                    with np.load(folder / f"{node}_model_inputs.npz", allow_pickle=False) as data:
                        if int(data["frame_ids"][-1]) != metadata["frame_id"]:
                            raise ValueError("Saved ordinary LiDAR/video frame mismatch.")
                        lidar = data["lidar_bev"]
                    fixed, changed = apply_ground_guard(prediction, lidar, profile)
                    corrected.append(fixed); changed_counts.append(int(changed.sum()))
                ego, remote = corrected
                fused = fuse_bevs(ego, [remote], mode="ego_observed")
                probabilities, valid = warp_to_ego(remote, ego["pose"])
                aligned = {"probabilities": probabilities, "valid": valid, "pose": ego["pose"]}
                display_crop = ego_visible_crop(metadata["crop_grid"], ego["pose"], ego["pose"])
                evidence = [load(folder / f"{node}_eval_only_semantic.npz") for node in ("ego", observer)]
                truth = masks(evidence, ego["pose"], row["target_ids"])
                x, y, width = metadata["crop_grid"]
                roi = np.zeros((200, 200), bool); roi[x:x+width, y:y+width] = True
                for name, prediction in (("before", original), ("after", fused)):
                    occupancy = obstacle_presence(prediction["probabilities"], prediction["valid"])["occupancy"]
                    add_metrics(metrics[row["scenario"], name], occupancy, truth, roi)
                image = read_frame(decode.stdout)
                # All lower camera pixels come directly from the same original frame.
                bottom_before = image.crop((0, 846, WIDTH, 1268)).tobytes()
                image.paste(BACKGROUND, (0, 0, WIDTH, 192))
                image.paste(BACKGROUND, (0, 1268, WIDTH, HEIGHT))
                text(image, (12, 10), f"{row['scenario']} | F{metadata['frame_id']} | ORDINARY LIDAR GROUND GUARD", 29, bold=True)
                text(image, (12, 52), "Same actors/time/RGB | saved model output -> sensor-only ground correction -> ego fusion", 22)
                text(image, (12, 87), "OFFLINE COMPARISON: no new inference or ZKP in this video; source predictions/recordings unchanged", 20, (255, 198, 96))
                titles = ("EGO BEV", f"OBSERVER {row['actors'][observer]['actor_id']} BEV", "FUSED BEV")
                subtitles = ("Corrected own prediction", "Corrected saved peer / ego axes", "Corrected ego + peer fusion")
                for col, prediction in enumerate((ego, aligned, fused)):
                    left = 12 + col * 650
                    text(image, (left, 127), titles[col], 23, bold=True)
                    text(image, (left, 160), subtitles[col], 19, (182, 194, 204))
                    image.paste(view(prediction, truth["target"], display_crop, ego_pose=ego["pose"]), (left, 192))
                text(image, (12, 1276), "Orange=all predicted obstacles; gray=ground; dark=unobserved; red EGO+arrow=recorded ego pose. White marks=target GT, not detection.", 18)
                if bottom_before != image.crop((0, 846, WIDTH, 1268)).tobytes():
                    raise AssertionError("Original synchronized camera row changed.")
                encode.stdin.write(image.tobytes())
                if i == len(folders) // 2:
                    image.save(args.output / f"{row['scenario']}_preview.png")
                    compare = Image.new("RGB", (1320, 772), BACKGROUND)
                    compare.paste(view(original, truth["target"], display_crop, ego_pose=ego["pose"]), (12, 80))
                    compare.paste(view(fused, truth["target"], display_crop, ego_pose=ego["pose"]), (668, 80))
                    text(compare, (12, 10), f"{row['scenario']} F{metadata['frame_id']} | BEFORE", 25, bold=True)
                    text(compare, (668, 10), "AFTER: SENSOR-ONLY CORRECTION", 22, bold=True)
                    text(compare, (12, 735), "All obstacles orange. Red EGO=recorded pose. White circle=GT only. Predictions unchanged.", 18)
                    compare.save(args.output / f"{row['scenario']}_before_after.png")
                records.append({"frame_id": metadata["frame_id"], "capture_sim_ns": metadata["capture_sim_ns"],
                    "source_folder": str(folder), "changed_cells_ego_observer": changed_counts,
                    "same_original_camera_row": True, "GT_used_for_inference": False,
                    "display_crop_grid": list(display_crop),
                    "ego_marker": project_ego_pose(ego["pose"], ego["pose"], display_crop, SIZE),
                    "new_proof_or_model_inference_in_video": False})
            encode.stdin.close()
            if encode.wait(timeout=120) or decode.wait(timeout=120):
                raise RuntimeError("Video encoding/decoding failed.")
        finally:
            for process in (encode, decode):
                if process.poll() is None:
                    process.kill(); process.wait()
        if hashlib.sha256(source_video.read_bytes()).hexdigest() != source_hash:
            raise AssertionError("Original video changed.")
        results.append({"scenario": row["scenario"], "file": output.name, "frames": len(records),
            "duration_seconds": len(records) / 5, "source_video_sha256": source_hash,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "records": records})
        print(row["scenario"] + " corrected video complete", flush=True)
    report = {"ground_guard": profile, "videos": results,
        "metrics": [{"scenario": scenario, "version": name, **ratios(total)}
                    for (scenario, name), total in metrics.items()],
        "live_latency_benchmark": False, "formal_metric2_pass": None}
    (args.output / "report.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
