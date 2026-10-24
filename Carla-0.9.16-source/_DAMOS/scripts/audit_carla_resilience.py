"""Independently replay saved fusion/states and reverify real Groth16 trial proofs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from bev_fusion import fuse_bevs
from bev_protocol import encode_bev, hashes, validate_manifest
from bev_resilience import ServicePolicy, empty_reference, inject_input, inspect_input
from bev_fault_quality import (QUALITY_FAULTS, INFERENCE_REPLAY_LIMITS,
                               load_raw_task, nominal_calibration_task)


def load_json(path):
    return json.loads(Path(path).read_text())


def load_npz(path):
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def audit(folder, real_crypto=False, replay_inference=False, inference_repeats=1):
    if not 1 <= inference_repeats <= 10:
        raise ValueError("Inference repeats must be in 1..10")
    protocol = load_json(folder / "protocol.json")
    summary = load_json(folder / "summary.json")
    for source, digest in protocol["source_sha256"].items():
        saved_source = folder / "frozen_source" / Path(source).name
        assert saved_source.is_file() and hashlib.sha256(saved_source.read_bytes()).hexdigest() == digest
    backend = None
    if real_crypto:
        from m2x.security import real_backend
        backend = real_backend(60)
    predictor = None
    if replay_inference:
        from bev_model_runtime import BEVPredictor
        predictor = BEVPredictor(protocol["checkpoint_path"])
        assert predictor.checkpoint_hash == protocol["checkpoint_sha256"]
        if "inference_policy" in protocol:
            assert predictor.inference_policy == protocol["inference_policy"]
    counts = {"rounds": 0, "packets": 0, "fusion_replays": 0, "state_replays": 0,
              "groth16_proofs_reverified": 0, "case_verdicts": 0, "wire_attacks_checked": 0,
              "quality_raw_replays": 0, "corrupted_inference_replays": 0}
    checks, inference_checks = [], []
    inference_limits = protocol.get("inference_replay_limits", INFERENCE_REPLAY_LIMITS)
    for execution in summary["executions"]:
        root = folder / execution["weather"] / "resilience"
        registry = load_json(root / "enrollment.json")
        scene = load_json(root / "scene.json")
        assert registry["actor_ids"] == {n: r["actor_id"] for n, r in scene["actors"].items()}
        assert execution["returncode"] == 0 and execution["summary"]["fatal_error"] is None
        camera_events = scene.get("physical_camera_replacements", [])
        if any(case["case"] == "camera_misalignment" for case in execution["summary"]["cases"]):
            assert [event["misaligned"] for event in camera_events] == [True, False]
            assert all(event["old_sensor_id"] != event["new_sensor_id"] for event in camera_events)
            np.testing.assert_allclose(camera_events[-1]["actual_relative_matrix"],
                                       camera_events[-1]["declared_relative_matrix"], atol=1e-6)
        for case in execution["summary"]["cases"]:
            policy = ServicePolicy(3)
            for record in case["rounds"]:
                output = root / record["path"]
                rows = record["peers"]
                for node, row in rows.items():
                    node_dir = output / node
                    packet = load_json(node_dir / f"{node}_packet.json")
                    body = (node_dir / f"{node}_body.bin").read_bytes()
                    manifest = packet["manifest"]
                    validate_manifest(manifest)
                    assert hashlib.sha256(body).hexdigest() == manifest["sha256"]
                    assert encode_bev(load_npz(node_dir / f"{node}_own.npz")) == body
                    assert manifest["sender_id"] == node
                    assert manifest["actor_id"] == registry["actor_ids"][node]
                    assert manifest["session_id"] == registry["session_id"]
                    assert manifest["checkpoint_sha256"] == protocol["checkpoint_sha256"]
                    commitment = registry["peers"][node]["commitment"]
                    assert manifest["registered_commitment"] == commitment
                    assert packet["public_inputs"] == [commitment, *hashes(manifest)]
                    if backend is not None:
                        valid, _ = backend.verify_auth_proof(packet["proof"], packet["public_inputs"])
                        assert valid
                        counts["groth16_proofs_reverified"] += 1
                    wire = row.get("wire_action", "none")
                    if wire in ("tamper", "replay", "bad_proof"):
                        sent = load_json(node_dir / f"{node}_sent_packet.json")
                        sent_body = (node_dir / f"{node}_sent_body.bin").read_bytes()
                        if wire == "tamper":
                            assert hashlib.sha256(sent_body).hexdigest() != sent["manifest"]["sha256"]
                        elif wire == "replay":
                            assert sent["manifest"]["round_id"] < manifest["round_id"]
                            assert sent["manifest"]["frame_id"] < manifest["frame_id"]
                        else:
                            assert sent["proof"] != packet["proof"]
                            if backend is not None:
                                try:
                                    accepted, _ = backend.verify_auth_proof(sent["proof"], sent["public_inputs"])
                                except Exception:
                                    accepted = False
                                assert not accepted
                        counts["wire_attacks_checked"] += 1
                    counts["packets"] += 1
                ego_dir = output / "ego"
                ego_row = rows["ego"]
                own = load_npz(ego_dir / "ego_own.npz")
                local = own if ego_row["health"]["usable"] else empty_reference(own["pose"])
                # Frozen output has an older pose; use the saved current fused reference.
                saved = load_npz(ego_dir / "ego_fused.npz")
                if record["phase"] == "fault" and case["case"] in QUALITY_FAULTS:
                    from bev_sensor_rig import prepare_raw_task
                    original = load_raw_task(ego_dir / "ego_raw_before.npz")
                    applied = load_raw_task(ego_dir / "ego_raw_applied.npz")
                    reference = load_raw_task(ego_dir / "ego_quality_reference.npz")
                    replayed_input = inject_input(original, case["case"], None)
                    for key, packets in applied["packets"].items():
                        for actual, replayed in zip(packets, replayed_input["packets"][key]):
                            assert actual["raw"] == replayed["raw"]
                            assert actual["frame"] == replayed["frame"] and actual["timestamp"] == replayed["timestamp"]
                            np.testing.assert_array_equal(actual["transform"], replayed["transform"])
                    health = inspect_input(applied, record["frame_id"], reference)
                    assert health == ego_row["health"]
                    assert not health["usable"] and not ego_row["local_usable"]
                    if case["case"] == "camera_misalignment":
                        angle = health["quality"]["evidence"]["mounts"]["cam_front"]["rotation_error_deg"]
                        assert 24.9 <= angle <= 25.1
                    assert not own["valid"].any()
                    candidate_info = ego_row["quality_candidate"]
                    assert candidate_info["actual_inference_executed"] and candidate_info["rejected_before_publish_or_fusion"]
                    model_task = nominal_calibration_task(applied) if case["case"] == "camera_misalignment" else applied
                    rebuilt, _ = prepare_raw_task(model_task)
                    stored_inputs = load_npz(ego_dir / "ego_model_inputs.npz")
                    for key, values in rebuilt.items():
                        np.testing.assert_array_equal(values, stored_inputs[key])
                    candidate = load_npz(ego_dir / "ego_rejected_candidate.npz")
                    assert np.isfinite(candidate["probabilities"]).all()
                    assert ego_row["inference"] is not None
                    if "inference_policy" in protocol:
                        assert ego_row["inference"]["inference_policy"] == protocol["inference_policy"]
                    for repeat_index in range(inference_repeats if predictor is not None else 0):
                        repeated, timing = predictor.infer(stored_inputs)
                        error = np.abs(repeated["probabilities"].astype(np.float64) - candidate["probabilities"].astype(np.float64))
                        maximum, mean = float(error.max()), float(error.mean())
                        numeric_pass = (maximum <= inference_limits["maximum_probability_error"]
                                        and mean <= inference_limits["mean_probability_error"])
                        np.testing.assert_array_equal(repeated["valid"], candidate["valid"])
                        np.testing.assert_array_equal(repeated["pose"], candidate["pose"])
                        inference_checks.append({"weather": case["weather"], "case": case["case"],
                            "repeat_index": repeat_index, "inference_policy": timing.get("inference_policy"),
                            "maximum_probability_error": maximum, "mean_probability_error": mean,
                            "within_predeclared_limits": numeric_pass,
                            "valid_mask_and_pose_exact": True, "probabilities_bitwise_equal": bool(not error.any())})
                        counts["corrupted_inference_replays"] += 1
                    counts["quality_raw_replays"] += 1
                if not ego_row["health"]["usable"]:
                    local = empty_reference(saved["pose"])
                peers = []
                for sender in sorted(ego_row["usable_peer_ids"]):
                    assert sender in ego_row["verified_peer_ids"]
                    decoded = load_npz(ego_dir / f"ego_received_{sender}.npz")
                    assert hashlib.sha256(encode_bev(decoded)).hexdigest() == ego_row["received_bevs"][sender]["sha256"]
                    source_packet = load_json(output / sender / f"{sender}_packet.json")
                    assert source_packet["manifest"]["sha256"] == ego_row["received_bevs"][sender]["sha256"]
                    assert source_packet["manifest"]["frame_id"] == record["frame_id"]
                    peers.append(decoded)
                replay = fuse_bevs(local, peers, mode=ego_row["fusion_mode"])
                for key in replay:
                    np.testing.assert_array_equal(replay[key], saved[key])
                assert int(replay["valid"].sum()) == ego_row["output_cells"]
                decision = policy.update(local_usable=ego_row["local_usable"],
                    trusted_peer_count=len(peers), output_cells=ego_row["output_cells"],
                    fresh=record["parent_round_seconds_including_artifact_wait"] <= protocol["maximum_round_age_s"]
                          and ego_row["status"] == "service_complete")
                assert decision == record["decision"]
                if "brake_check" in record:
                    brake = record["brake_check"]
                    passed = (brake["initial_speed_mps"] >= 1 and brake["stopped"]
                              and brake["stop_sim_seconds"] <= 5 and brake["control"]["brake"] >= .99)
                    assert passed == brake["criterion_pass"]
                    assert brake["trace"][-1]["speed_mps"] <= .2
                counts["rounds"] += 1
                counts["fusion_replays"] += 1
                counts["state_replays"] += 1
            assert case["criterion_pass"] == all(case["checks"].values())
            phases = {phase: [r for r in case["rounds"] if r["phase"] == phase]
                      for phase in ("baseline", "fault", "recovery")}
            assert [len(phases[p]) for p in phases] == [3, 1, 3]
            assert phases["baseline"][-1]["decision"]["state"] == "NORMAL"
            assert phases["fault"][0]["decision"]["state"] == case["expected_fault_state"]
            assert [r["decision"]["state"] for r in phases["recovery"]] == ["RECOVERING", "RECOVERING", "NORMAL"]
            counts["case_verdicts"] += 1
            checks.append({"weather": case["weather"], "case": case["case"],
                           "criterion_pass": case["criterion_pass"]})
    assert counts["case_verdicts"] == summary["completed"]
    assert sum(c["criterion_pass"] for c in checks) == summary["successful"]
    return {"all_saved_arrays_and_states_consistent": True, "counts": counts,
        "real_crypto_enabled": real_crypto, "completed": summary["completed"],
        "corrupted_inference_replay_enabled": replay_inference,
        "inference_repeats": inference_repeats,
        "source_files_checked": len(protocol["source_sha256"]),
        "audit_tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "inference_replay_limits": inference_limits, "inference_replay_checks": inference_checks,
        "inference_replay_within_limits": all(c["within_predeclared_limits"] for c in inference_checks) if replay_inference else None,
        "planned": summary["planned"], "case_checks": checks,
        "formal_metric2_or_3_case": False,
        "scope": "saved packets/fusion/state/actuator records only; not proof of physical truth or full autonomous safety"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--real-crypto", action="store_true")
    parser.add_argument("--replay-inference", action="store_true")
    parser.add_argument("--inference-repeats", type=int, choices=range(1, 11), default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.folder, args.real_crypto, args.replay_inference, args.inference_repeats)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps({"counts": result["counts"], "saved_arrays_and_states_consistent": True,
                      "inference_replay_within_limits": result["inference_replay_within_limits"]}, indent=2))
    if result["inference_replay_within_limits"] is False:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
