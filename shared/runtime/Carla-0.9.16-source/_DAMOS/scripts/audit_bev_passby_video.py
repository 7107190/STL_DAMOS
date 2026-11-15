#!/usr/bin/env python3
"""Read-only synchronization audit and sampled historical real-proof verification."""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess

from audit_bev_candidate_live import audit_round
from bev_camera_freshness import audit_saved_report
from m2x.security import real_backend


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(report, checkpoint):
    capture = json.loads((report / "capture.json").read_text())
    summary = json.loads((report / "summary.json").read_text())
    if not capture["capture_complete"] or summary["error"] is not None:
        raise ValueError("Incomplete capture or actual model/proof/video failure.")
    if summary["backend"] != "REAL_GROTH16" or summary["live_latency_benchmark"]:
        raise ValueError("Not the declared offline real-proof replay.")
    start, end = capture["selected_indices"]
    selected = capture["timeline"][start:end]
    frames = summary["frames"]
    count = summary["frame_count"]
    if len(frames) != count or len(selected) != count or count < 10:
        raise ValueError("Video/capture frame count mismatch.")
    if selected[0]["anchor_forward_m"] <= 0 or selected[-1]["anchor_forward_m"] >= 0:
        raise ValueError("No approach and departure in actual recorded trajectory.")
    if any(c["other_actor_id"] in capture["target_ids"] or
           (selected[0]["frame_id"] <= c["frame_id"] <= selected[-1]["frame_id"] and c["impulse"] > 100)
           for c in capture["collisions"]):
        raise ValueError("Collision in the declared normal pass-by.")
    video = report / "passby_2x3.mp4"
    if digest(video) != summary["video_sha256"]:
        raise ValueError("Video content changed after rendering.")
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-count_frames",
        "-select_streams", "v:0", "-show_entries",
        "stream=width,height,r_frame_rate,nb_read_frames,duration", "-of", "json", str(video)]))
    stream = probe["streams"][0]
    if (int(stream["nb_read_frames"]) != count or Fraction(stream["r_frame_rate"]) != capture["fps"]
            or abs(float(stream["duration"]) - count / capture["fps"]) > .001
            or (stream["width"], stream["height"]) != (summary["width"], summary["height"])):
        raise ValueError("Encoded playback clock/dimensions differ from the actual frame sequence.")
    for i, (row, source) in enumerate(zip(frames, selected)):
        folder = report / f"round_{i+1:03d}"
        truth = json.loads((folder / "ground_truth.json").read_text())
        if (row["frame_id"] != source["frame_id"] or row["capture_sim_ns"] != source["capture_sim_ns"]
                or truth["frame_id"] != row["frame_id"] or truth["capture_sim_ns"] != row["capture_sim_ns"]
                or not source["on_driving_lane"] or row["actors"] != capture["actors"]
                or sorted(t["actor_id"] for t in truth["targets"]) != sorted(capture["target_ids"])):
            raise ValueError("Frame, timestamp, pinned actors or Driving-lane mismatch.")
        if abs(row["capture_sim_ns"] - frames[0]["capture_sim_ns"] - round(i / capture["fps"] * 1e9)) > 2000:
            raise ValueError("Nonuniform capture time; no interpolation allowed.")
        for node, camera in row["cameras"].items():
            packet = json.loads((folder / f"{node}_packet.json").read_text())["manifest"]
            if (packet["frame_id"] != row["frame_id"] or camera["frame_id"] != row["frame_id"]
                    or abs(packet["capture_sim_ns"] - row["capture_sim_ns"]) > 1000
                    or packet["actor_id"] != capture["actors"][node]["actor_id"]
                    or digest(folder / f"{node}_rgb_{camera['name']}.png") != camera["sha256"]):
                raise ValueError("Displayed RGB is not the authenticated actor/frame.")
        if digest(folder / "overhead_rgb.png") != row["overhead_sha256"]:
            raise ValueError("Actual overhead RGB changed.")
    checkpoint_hash = digest(checkpoint)
    content = audit_saved_report(report)
    if not content["content_gate_passed"]:
        raise ValueError(content["error"])
    backend = real_backend(60)
    sample_indices = sorted({0, count // 2, count - 1})
    proofs = [audit_round(report / f"round_{i+1:03d}", summary, checkpoint_hash, backend,
                         fusion_mode="ego_observed", calibrated=True) for i in sample_indices]
    return {"scenario": capture["scenario"], "report": str(report), "frames_checked": count,
        "duration_seconds": count / capture["fps"], "fps": capture["fps"],
        "dimensions": [summary["width"], summary["height"]], "video_sha256": digest(video),
        "sample_indices": sample_indices, "historical_proofs_reverified": sum(p["proofs_reverified"] for p in proofs),
        "all_selected_frames_same_actor_time_and_driving_lane": True,
        "target_collisions": 0, "checkpoint_sha256": checkpoint_hash,
        "camera_content_audit": content,
        "live_latency_benchmark": False, "metric2_pass": None,
        "scope": "All displayed frames/time/IDs/RGB hashes and encoder clock; first/middle/last BEV bodies, fusion and real proofs",
        "limitations": "Offline proof reverification does not retest network freshness, all-anchor coverage or detection accuracy"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Use a new audit output path.")
    result = audit(args.report, args.checkpoint)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
