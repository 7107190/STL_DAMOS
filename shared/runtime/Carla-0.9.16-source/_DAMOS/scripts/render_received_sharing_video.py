#!/usr/bin/env python3
"""Add ego's actual verified incoming images to a legacy 1440x900 sharing video."""
import argparse
import json
from pathlib import Path
import subprocess

import cv2

from m2x.video_views import SharingDashboard


def frame_timeline(rows, fps, total):
    """Mirror the recorder's floor(elapsed*fps) catch-up writes, not nearest time."""
    next_frame = 0
    for row in rows:
        target = min(int(row["video_wall_seconds"] * fps), total - 1)
        while next_frame <= target:
            yield next_frame, row
            next_frame += 1
    if next_frame != total:
        raise ValueError(f"TELEMETRY_FRAME_COUNT_MISMATCH: {next_frame} != {total}")


def render(folder):
    summary = json.loads((folder / "summary.json").read_text())
    source = folder / "ego_neighbour_sharing.mp4"
    output = folder / "ego_neighbour_received_views.mp4"
    cap = cv2.VideoCapture(str(source))
    width, height = (round(cap.get(prop)) for prop in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT))
    if (width, height) != (1440, 900):
        cap.release()
        raise ValueError("This migration requires the legacy 1440x900 layout")
    fps, total = cap.get(cv2.CAP_PROP_FPS), round(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    rounds = {row["round_id"]: row for row in
              (json.loads(line) for line in (folder / "ego_rounds.jsonl").read_text().splitlines())}
    timeline = [json.loads(line) for line in (folder / "video_telemetry.jsonl").read_text().splitlines()]
    dashboard = SharingDashboard(folder, summary["nodes"], summary["radius_m"], summary["scenario_labels"])
    frames, verified_views, errors = 0, 0, []
    with (folder / "received_views_ffmpeg.log").open("w") as log, \
         (folder / "received_views_telemetry.jsonl").open("w") as audit:
        encoder = subprocess.Popen([
            "ffmpeg", "-y", "-loglevel", "warning", "-f", "rawvideo", "-pixel_format", "bgr24",
            "-video_size", f"{dashboard.WIDTH}x{dashboard.HEIGHT}", "-framerate", str(fps),
            "-i", "pipe:0", "-an", "-c:v", "libx264", "-threads", "2", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        try:
            for index, telemetry in frame_timeline(timeline, fps, total):
                success, original = cap.read()
                if not success:
                    raise ValueError(f"SOURCE_VIDEO_READ_FAILED: {index}")
                state = {"selected": telemetry["selected"], "phase": telemetry["phase"],
                         "last_result": rounds.get(telemetry["last_completed_round"])}
                # Reuse the original live ego and map pixels from this exact recorded frame.
                image, views = dashboard.compose(original[120:720, 16:816],
                                                  original[120:720, 824:1424], state, telemetry)
                encoder.stdin.write(image.tobytes())
                audit.write(json.dumps({"video_frame": index, **telemetry, "received_views": views}) + "\n")
                verified_views += sum(v["status"] == "verified" for v in views.values())
                errors.extend({"video_frame": index, "node": node, "status": view["status"]}
                              for node, view in views.items() if view["status"] not in {"verified", "waiting"})
                frames += 1
            encoder.stdin.close()
            if encoder.wait(timeout=60) != 0:
                raise RuntimeError("Video encoding failed; see received_views_ffmpeg.log")
        finally:
            cap.release()
            if encoder.poll() is None:
                encoder.kill()
                encoder.wait()
    result = {"source_video": str(source), "output": str(output), "frames": frames, "fps": fps,
              "duration_seconds": frames / fps, "verified_view_instances": verified_views,
              "artifact_errors": errors, "source": "same_run_recomposition_no_simulation_restart",
              "received_source": "ego worker PNG files written after Groth16 verification",
              "integrity_check": "SHA256 / actor_id / frame_id matched against ego round records",
              "timing": "original video frame-to-telemetry association preserved",
              "benchmark": "original measurement unchanged; no new performance measurement"}
    (folder / "received_views_summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if errors:
        raise RuntimeError("Received image artifacts failed validation")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_folder", type=Path)
    render(parser.parse_args().report_folder)
