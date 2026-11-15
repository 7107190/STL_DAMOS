"""Independently reconcile live frames, native images, peer proofs and timings."""
import argparse
import hashlib
import json
from pathlib import Path

from live_checks import round_checks
from report_data import Reports, STAGES, artifact, atomic_json


def verify(root, run, require_full=False):
    reports = Reports(root)
    snap = reports.snapshot(run)
    folder = reports.folder(run)
    protocol = artifact(folder / "protocol.json")
    expected = snap["expected"]
    if require_full and (not snap["full_scope"] or expected != 135):
        raise ValueError("This is not the full 135-round live trial")
    if snap["status"] != "complete" or snap["processed"] != expected:
        raise ValueError("Live trial is not complete")
    checkpoint = protocol["checkpoint_sha256"]
    previous, count, stage_count, actor_types = {}, 0, 0, set()
    rows = [json.loads(line) for line in (folder / "rounds.jsonl").read_text().splitlines()]
    for row in rows:
        target = reports.target(folder, row)
        source = artifact(target / "live_input.json")
        scene = artifact(target / "scene_capture.json")
        peers = artifact(target / "peers.json")
        actors = {node: value["actor_id"] for node, value in source["nodes"].items()}
        result = round_checks(peers, actors, row["frame_id"], checkpoint)
        if not result["functional_pass"] or not result["speed_pass"]:
            raise ValueError("Live pipeline check failed: " + str(result))
        if source["frames"][-1] != row["frame_id"] or scene["frame_id"] != row["frame_id"]:
            raise ValueError("Scene and sensor capture frame differ")
        if scene["capture_sim_ns"] != source["capture_sim_ns"] or source["capture_sim_ns"] != row["capture_sim_ns"]:
            raise ValueError("Scene and sensor capture times differ")
        if scene.get("source") != "current_carla_rgb_sensor":
            raise ValueError("Scene is not a current CARLA RGB sensor capture")
        if hashlib.sha256((target / "scene_rgb.png").read_bytes()).hexdigest() != scene["image_sha256"]:
            raise ValueError("Scene image hash mismatch")
        key = row["session"], row["scenario"]
        if source["frames"][0] <= previous.get(key, -1):
            raise ValueError("Live four-frame input reused between timed rounds")
        previous[key] = source["frames"][-1]
        clock = artifact(target / "ui_clock.json")["peers"]
        for node, peer in peers.items():
            if peer["input"]["frames"] != source["frames"]:
                raise ValueError("Model received different source frames")
            if peer["input"]["camera_count"] != 6 or peer["input"]["lidar_count"] != 1:
                raise ValueError("Measured model sensor count mismatch")
            packet = artifact(target / f"{node}_packet.json")
            if packet["manifest"]["capture_sim_ns"] != source["capture_sim_ns"]:
                raise ValueError("Groth16 packet refers to a different live capture")
            if int(clock[node]["ended_utc_ns"]) - int(clock[node]["started_utc_ns"]) != peer["ended_ns"] - peer["started_ns"]:
                raise ValueError("Displayed wall-clock interval changed the measured duration")
            stage_count += sum(key in peer["stages_ms"] for key, _ in STAGES)
            actor_types.add(peer["input"]["actor_type_id"])
        if artifact(target / "audit.json")["proofs_reverified"] != 2:
            raise ValueError("Post-run real proof reverification incomplete")
        count += 1
    return {"source": "live_carla", "run": run, "actual_live_input_rounds": count,
            "expected": expected, "full_135_scope": snap["full_scope"],
            "same_frame_scene_and_inputs": True, "fresh_disjoint_windows": True,
            "real_proofs_reverified": count * 2, "verified_stage_fields": stage_count,
            "clock_arithmetic_preserved": True, "actor_types": sorted(actor_types),
            "max_seconds": max(row["elapsed_ms"] for row in rows) / 1000,
            "hostname": artifact(folder / "environment.json", {}).get("hostname"),
            "all_pass": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--require-full", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.root, args.run, args.require_full)
    atomic_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
