"""Strict live-source checks, independent of CARLA and CUDA imports."""
import hashlib


def raw_live_inputs(tasks, previous_frame, expected_actors):
    if set(tasks) != set(expected_actors) or "ego" not in tasks or len(tasks) != 2:
        raise ValueError("LIVE_REQUIRES_EGO_AND_ONE_OBSERVER")
    windows = {tuple(task["window"]) for task in tasks.values()}
    if len(windows) != 1:
        raise ValueError("LIVE_PEERS_HAVE_DIFFERENT_FRAMES")
    window = next(iter(windows))
    if len(window) != 4 or list(window) != list(range(window[0], window[0] + 4)):
        raise ValueError("LIVE_REQUIRES_FOUR_CONSECUTIVE_FRAMES")
    if window[0] <= previous_frame:
        raise ValueError("LIVE_INPUT_REUSED_OR_OVERLAPS_PREVIOUS_ROUND")
    sources, stamps = {}, set()
    for node, task in tasks.items():
        if "arrays" in task or task.get("input_source") != "live_raw_memory":
            raise ValueError("RECORDED_INPUTS_NOT_ALLOWED_IN_LIVE_TRIAL")
        if task["actor_id"] != expected_actors[node]:
            raise ValueError("LIVE_ACTOR_CHANGED")
        sensors = task["packets"]
        names = {key[1] for key in sensors}
        if names != {"cam_front_left", "cam_front", "cam_front_right", "cam_back_left", "cam_back", "cam_back_right", "lidar"}:
            raise ValueError("LIVE_SENSOR_SET_MISMATCH")
        evidence = {}
        times = None
        for (_, name), packets in sensors.items():
            if [packet["frame"] for packet in packets] != list(window):
                raise ValueError("LIVE_PACKET_FRAME_MISMATCH")
            current = [round(packet["timestamp"] * 1e9) for packet in packets]
            if times is not None and any(abs(a - b) > 1000 for a, b in zip(times, current)):
                raise ValueError("LIVE_SENSOR_TIMESTAMP_MISMATCH")
            times = current
            evidence[name] = [{"frame": packet["frame"], "capture_sim_ns": current[i],
                               "bytes": len(packet["raw"]), "raw_sha256": hashlib.sha256(packet["raw"]).hexdigest()}
                              for i, packet in enumerate(packets)]
        stamps.add(tuple(times))
        sources[node] = {"actor_id": task["actor_id"], "actor_type_id": task["actor_type_id"],
                         "sensors": evidence, "source": "current_carla_sensor_callback"}
    if len(stamps) != 1:
        raise ValueError("LIVE_PEERS_HAVE_DIFFERENT_CAPTURE_TIMES")
    return {"frames": list(window), "capture_sim_ns": next(iter(stamps))[-1], "nodes": sources}


def round_checks(peers, expected_actors, frame, checkpoint_hash, budget_ms=18000):
    problems = []
    for node, row in peers.items():
        if row.get("actor_id") != expected_actors.get(node) or row.get("frame_id") != frame:
            problems.append(node + ": wrong live actor/frame")
        if row.get("input", {}).get("input_source") != "live_raw_memory":
            problems.append(node + ": not current raw sensor input")
        if row.get("status") != "fusion_complete" or row.get("artifact_error"):
            problems.append(node + ": incomplete pipeline/artifacts")
        if sorted(row.get("peer_ids", [])) != sorted(row.get("verified_peer_ids", [])) or row.get("missing_peer_ids"):
            problems.append(node + ": missing verified peer")
        if row.get("proof", {}).get("result_class") != "REAL_GROTH16":
            problems.append(node + ": real proof missing")
        if row.get("inference", {}).get("checkpoint_sha256") != checkpoint_hash:
            problems.append(node + ": wrong model")
        start, end = row.get("started_ns", 0), row.get("ended_ns", 0)
        if end <= start or abs((end - start) / 1e6 - row.get("elapsed_ms", -1)) > 1e-6:
            problems.append(node + ": invalid measured interval")
    if set(peers) != set(expected_actors) or len({r.get("started_ns") for r in peers.values()}) != 1:
        problems.append("incomplete peer set or mismatched clock origins")
    good = not problems
    return {"functional_pass": good, "speed_pass": good and peers["ego"]["elapsed_ms"] <= budget_ms,
            "problems": problems}


def post_distance_diagnostic_only(error, metrics, failures, rows, expected):
    if not str(error).startswith("Custom observers did not satisfy Scenic anchor coverage requirements."):
        return False
    if not failures or any(not reason.startswith("observer_to_anchor_distance=") for _, reason in failures):
        return False
    if not metrics or any(m.get('status') != 'ok' or not m.get('on_sidewalk') for m in metrics):
        return False
    return len(rows) == expected and all(row.get('functional_pass') and
        row.get('audit', {}).get('proofs_reverified') == 2 for row in rows)
