"""Standard-library independent cell and continuous-duration verification."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

EXPECTED_CHECKPOINT = "3600d8e29f602f77ca142637c0336d267e0d041a2d5486739fca0070e9e7646e"


def check(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def cells(values):
    check(isinstance(values, list) and all(type(v) is int and 0 <= v < 40000 for v in values), "Invalid cell IDs")
    check(values == sorted(set(values)), "Repeated or unsorted cell IDs")
    return set(values)


def largest_overlap(truth, predictions):
    remaining = set(truth) & set(predictions)
    best = 0
    while remaining:
        seed = remaining.pop()
        stack, size = [seed], 1
        while stack:
            row, column = divmod(stack.pop(), 200)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    x, y = row + dx, column + dy
                    if (dx or dy) and 0 <= x < 200 and 0 <= y < 200:
                        neighbour = x * 200 + y
                        if neighbour in remaining:
                            remaining.remove(neighbour)
                            stack.append(neighbour)
                            size += 1
        best = max(best, size)
    return best


def independent_duration(rows, name):
    spans, start = [], None
    previous = None
    for row in rows:
        timestamp = int(row["capture_sim_ns"])
        check(previous is None or (row["frame_id"] > previous["frame_id"]
            and timestamp > int(previous["capture_sim_ns"])), "Clock reversal")
        continuous = previous is not None and (
            row["round_index"] - previous["round_index"] == 1
            and row["frame_id"] - previous["frame_id"] == 2
            and abs(timestamp - int(previous["capture_sim_ns"]) - 200000000) <= 1000)
        valid = row["eligible"] is True and row[name + "_detected"] is True
        if not valid:
            start = None
        elif not continuous or start is None:
            start = timestamp
        if start is not None:
            spans.append(timestamp - start)
        previous = row
    return max(spans, default=0)


def verify_case(folder, case, protocol):
    invocation = read(folder / "invocation.json")
    check(invocation["case_sha256"] == protocol["case_sha256"][case["case_id"]], "Invocation case mismatch")
    result = read(folder / "result.json")
    check(result["case_id"] == case["case_id"] and result["scenario"] == case["scenario"], "Result identity mismatch")
    check(result["status"] in ("success", "failure", "not_evaluable", "data_error"), "Unknown final status")
    recording = folder / "recording"
    if result["status"] == "data_error":
        check(result["successful"] is False, "Data error cannot be successful")
        return result
    check(result["returncode"] == 0 and result["error"] is None, "Execution incomplete")
    capture = read(recording / "capture.json")
    check(capture["capture_complete"] is True and capture["error"] is None, "Capture incomplete")
    check(capture["case"] == case and capture["case_sha256"] == invocation["case_sha256"], "Captured case differs from plan")
    check(capture["fps"] == 5 and len(capture["timeline"]) == 30, "Fixed acquisition window changed")
    check(len(capture["actors"]) == 2 and "ego" in capture["actors"], "Peer identities invalid")
    check(capture["target_ids"] == result["member_actor_ids"], "Pinned actors mismatch")
    check(capture["anchor_index"] == result["anchor_index"], "Anchor identity mismatch")
    check(capture["timeline"][0]["distance_m"] <= case["entry_radius_m"], "Invalid acquisition onset")
    check(capture["camera_content_audit"]["frames_checked"] == 30, "Missing camera audit")
    check(all(not r["all_six_identical_for_entire_clip"] for r in capture["camera_content_audit"]["nodes"].values()), "Camera content frozen")
    rounds = sorted(recording.glob("round_*"))
    check(len(rounds) == result["samples"] and 1 <= len(rounds) <= 30, "Round count invalid")
    early_stop = case.get("early_success_stop", False)
    check(result.get("early_success_stop", False) is early_stop, "Sequential decision rule changed")
    if "captured_samples" in result:
        check(result["captured_samples"] == 30 and result["unprocessed_samples"] == 30 - len(rounds), "Acquired/processed sample counts mismatch")
    rows = []
    for index, path in enumerate(rounds, 1):
        evidence, truth, peers = read(path / "evidence.json"), read(path / "ground_truth.json"), read(path / "peers.json")
        tick = capture["timeline"][index - 1]
        check(evidence["case_id"] == case["case_id"] and evidence["round_index"] == index, "Evidence case/index mismatch")
        check(evidence["frame_id"] == truth["frame_id"] == tick["frame_id"], "Frame mismatch")
        check(int(evidence["capture_sim_ns"]) == truth["capture_sim_ns"] == tick["capture_sim_ns"], "Timestamp mismatch")
        check(evidence["member_actor_ids"] == capture["target_ids"] == sorted(t["actor_id"] for t in truth["targets"]), "Target replacement")
        check(all(t["anchor_index"] == capture["anchor_index"]
                  and t["scenario"].split("#")[0] == case["scenario"]
                  for t in truth["targets"]), "Target scenario or anchor changed")
        road = truth["ego_road_state"]
        check(road["frame_id"] == truth["frame_id"]
              and road["capture_sim_ns"] == truth["capture_sim_ns"]
              and road["actor_id"] == capture["actors"]["ego"]["actor_id"], "Road-state clock or ego changed")
        pose = evidence["ego_pose_model_world"]
        check(all(abs(pose[axis][3] - road["location_carla"][axis] * (1 if axis != 1 else -1)) < 0.0001
                  for axis in range(3)), "Pose/road coordinate mismatch")
        check(set(peers) == set(capture["actors"]), "Unregistered peer")
        for node, peer in peers.items():
            check(peer["status"] == "fusion_complete" and not peer.get("artifact_error"), "Replay failure")
            check(peer["actor_id"] == capture["actors"][node]["actor_id"] and peer["frame_id"] == tick["frame_id"], "Peer frame/actor mismatch")
            check(peer["inference"]["checkpoint_sha256"] == EXPECTED_CHECKPOINT and peer["fusion_mode"] == "ego_observed", "Model or fusion changed")
            check(set(peer["verified_peer_ids"]) == set(peers) - {node} and not peer["missing_peer_ids"], "Peer not verified")
            packet = read(path / (node + "_packet.json"))["manifest"]
            check(packet["frame_id"] == tick["frame_id"] and abs(packet["capture_sim_ns"] - tick["capture_sim_ns"]) <= 1000, "ZKP packet clock mismatch")
            check(packet["actor_id"] == peer["actor_id"] and packet["checkpoint_sha256"] == EXPECTED_CHECKPOINT, "ZKP packet model/actor mismatch")
        gt = cells(evidence["gt_target_cell_indices"])
        check(len(gt) == evidence["gt_return_cells"], "GT cell count mismatch")
        check(not gt or evidence["in_ego_roi"] is True, "Exact GT return cannot be outside ROI")
        check(evidence["on_driving_lane"] is truth["ego_road_state"]["on_driving_lane"], "Road status changed")
        check(evidence["ground_truth_fed_to_model_or_fusion"] is False, "GT-fed prediction")
        expected_eligible = evidence["on_driving_lane"] is True and evidence["in_ego_roi"] is True and len(gt) >= 3
        check(evidence["eligible"] is expected_eligible, "Eligibility mismatch")
        for name in ("ego", "fused"):
            largest = largest_overlap(gt, cells(evidence[name + "_obstacle_cell_indices"]))
            detected = largest >= 3 if len(gt) >= 3 else None
            check(largest == evidence[name + "_connected_overlap_cells"] and detected is evidence[name + "_detected"], "Cell detection mismatch")
        if rows:
            previous = rows[-1]
            check(evidence["frame_id"] == previous["frame_id"] + 2 and abs(
                int(evidence["capture_sim_ns"]) - int(previous["capture_sim_ns"]) - 200000000) <= 1000, "Missing synchronized sample")
        rows.append(evidence)
    duration = independent_duration(rows, "fused")
    if early_stop and result["successful"]:
        check(duration >= 500000000 and independent_duration(rows[:-1], "fused") < 500000000,
              "Stopped prefix is not the first sufficient success witness")
    else:
        check(len(rounds) == 30, "Failure verdict requires the entire planned window")
    check(duration == int(result["fused_longest_span_ns"]), "Sustained duration mismatch")
    check(independent_duration(rows, "ego") == int(result["ego_longest_span_ns"]), "Ego duration mismatch")
    check(result["successful"] is (duration >= 500000000), "Anchor verdict mismatch")
    eligible_samples = sum(r["eligible"] for r in rows)
    check(result["eligible_samples"] == eligible_samples, "Eligible sample count mismatch")
    expected_status = "success" if result["successful"] else "failure" if eligible_samples else "not_evaluable"
    check(result["status"] == expected_status, "Status mismatch")
    return result


def verify(root, allow_partial=False):
    protocol = read(root / "protocol.json")
    check(digest(root / "protocol.json") == read(root / "protocol_lock.json")["sha256"], "Plan hash mismatch")
    cases = protocol["cases"]
    check(len(cases) == 100 and len({c["case_id"] for c in cases}) == 100, "100 distinct planned cases required")
    check(len({c["scene_seed"] for c in cases}) == 100, "Duplicate generation seeds")
    check(Counter(c["scenario"] for c in cases) == Counter({**{f"S{i}": 11 for i in range(1, 9)}, "S9": 12}), "Scenario allocation changed")
    check(protocol["minimum_contiguous_span_ns"] == "500000000" and protocol["minimum_connected_overlap_cells"] == 3, "Criterion changed")
    check(protocol["checkpoint_sha256"] == EXPECTED_CHECKPOINT, "Frozen model mismatch")
    if (root / "manifest.json").exists():
        entries = read(root / "manifest.json")["files"]
        actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"}
        check(actual == set(entries), "Package membership mismatch")
        for name, expected in entries.items():
            path = root / name
            check(path.stat().st_size == expected["bytes"] and digest(path) == expected["sha256"], "Package hash mismatch: " + name)
    rows = []
    for case in cases:
        case_path = root / "cases" / (case["case_id"] + ".json")
        check(read(case_path) == case and digest(case_path) == protocol["case_sha256"][case["case_id"]], "Case plan mismatch")
        folder = root / "results" / case["case_id"]
        if not (folder / "result.json").exists():
            check(allow_partial, "Missing planned case: " + case["case_id"])
            continue
        rows.append(verify_case(folder, case, protocol))
    summary = read(root / "summary.json")
    successful = sum(r["successful"] for r in rows)
    check(summary["planned_cases"] == 100 and summary["completed_cases"] == len(rows)
          and summary["successful_cases"] == successful and summary["success_rate_percent"] == successful, "Aggregate count mismatch")
    check(summary["samples"] == sum(r.get("samples", 0) for r in rows), "Sample aggregate mismatch")
    check(summary["criterion_pass"] is (len(rows) == 100 and successful >= 70), "Final goal verdict mismatch")
    return {"verified": True, "planned_cases": 100, "completed_cases": len(rows),
        "successful_cases": successful, "success_rate_percent": successful,
        "criterion_pass": summary["criterion_pass"], "statuses": dict(Counter(r["status"] for r in rows))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.root, args.allow_partial), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
