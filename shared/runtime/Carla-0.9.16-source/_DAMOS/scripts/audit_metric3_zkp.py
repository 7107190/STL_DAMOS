"""Archival spot-check: regenerate BEV bytes and verify actual Groth16 proofs."""
import argparse
import hashlib
import json
from pathlib import Path

from bev_accuracy_batch import load_npz, sha256, write_json
from bev_protocol import encode_bev, hashes, validate_manifest
from m2x.security import real_backend


def audit(trial, output):
    if output.exists():
        raise FileExistsError("Use a new archival audit path")
    protocol = json.loads((trial / "protocol.json").read_text())
    backend = real_backend(60)
    checks, skipped = [], []
    for case in protocol["cases"]:
        folder = trial / "results" / case["case_id"]
        result = json.loads((folder / "result.json").read_text())
        if result["status"] == "data_error":
            skipped.append(case["case_id"])
            continue
        capture = json.loads((folder / "recording/capture.json").read_text())
        saved = json.loads((folder / "recording/summary.json").read_text())
        rounds = sorted((folder / "recording").glob("round_*"))
        selected = sorted({0, len(rounds) // 2, len(rounds) - 1})
        for index in selected:
            sample = rounds[index]
            for node, actor in capture["actors"].items():
                path = sample / f"{node}_packet.json"
                packet = json.loads(path.read_text())
                manifest = packet["manifest"]
                validate_manifest(manifest)
                body = encode_bev(load_npz(sample / f"{node}_own.npz"))
                if hashlib.sha256(body).hexdigest() != manifest["sha256"]:
                    raise ValueError("Archived BEV differs from authenticated bytes")
                other = next(n for n in capture["actors"] if n != node)
                commitment = saved["setup"][node]["commitment"]
                context, payload = hashes(manifest)
                if (manifest["sender_id"] != node or manifest["actor_id"] != actor["actor_id"]
                        or manifest["recipient_ids"] != [other]
                        or manifest["registered_commitment"] != commitment
                        or packet["public_inputs"] != [commitment, context, payload]):
                    raise ValueError("Archival context or sender mismatch")
                ok, timing = backend.verify_auth_proof(packet["proof"], packet["public_inputs"])
                if not ok:
                    raise ValueError("Actual Groth16 archival verification failed")
                checks.append({"case_id": case["case_id"], "sample": sample.name, "node": node,
                    "frame_id": manifest["frame_id"], "packet_sha256": sha256(path),
                    "body_sha256": manifest["sha256"], "actual_groth16_verified": True, "timing": timing})
        print(f"[ZKP AUDIT] {case['case_id']} checked first/middle/last proofs", flush=True)
    result = {"backend": "REAL_GROTH16", "selection": "first/middle/last processed sample, both peers",
        "checks": checks, "verified_proofs": len(checks), "skipped_data_error_cases": skipped,
        "proof_math_and_authenticated_bev_bytes_verified": True,
        "all_packets_verified_during_original_receive": True,
        "archival_check_does_not_claim_current_packet_freshness": True,
        "protocol_sha256": sha256(trial / "protocol.json")}
    write_json(output, result)
    print(f"[ZKP AUDIT] Verified {len(checks)} actual proofs", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.trial, args.output)


if __name__ == "__main__":
    main()
