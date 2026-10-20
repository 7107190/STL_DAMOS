"""Independently replay saved fusion/states and reverify real Groth16 trial proofs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from bev_fusion import fuse_bevs
from bev_protocol import encode_bev, hashes, validate_manifest
from bev_resilience import ServicePolicy, empty_reference


def load_json(path):
    return json.loads(Path(path).read_text())


def load_npz(path):
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def audit(folder, real_crypto=False):
    protocol = load_json(folder / "protocol.json")
    summary = load_json(folder / "summary.json")
    for source, digest in protocol["source_sha256"].items():
        saved_source = folder / "frozen_source" / Path(source).name
        assert saved_source.is_file() and hashlib.sha256(saved_source.read_bytes()).hexdigest() == digest
    backend = None
    if real_crypto:
        from m2x.security import real_backend
        backend = real_backend(60)
    counts = {"rounds": 0, "packets": 0, "fusion_replays": 0, "state_replays": 0,
              "groth16_proofs_reverified": 0, "case_verdicts": 0, "wire_attacks_checked": 0}
    checks = []
    for execution in summary["executions"]:
        root = folder / execution["weather"] / "resilience"
        registry = load_json(root / "enrollment.json")
        scene = load_json(root / "scene.json")
        assert registry["actor_ids"] == {n: r["actor_id"] for n, r in scene["actors"].items()}
        assert execution["returncode"] == 0 and execution["summary"]["fatal_error"] is None
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
        "planned": summary["planned"], "case_checks": checks,
        "formal_metric2_or_3_case": False,
        "scope": "saved packets/fusion/state/actuator records only; not proof of physical truth or full autonomous safety"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--real-crypto", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.folder, args.real_crypto)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps({"counts": result["counts"], "consistent": True}, indent=2))


if __name__ == "__main__":
    main()
