#!/usr/bin/env python3
"""Immutable audit of saved actual candidate BEVs and historical Groth16 proofs."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from bev_fusion import fuse_bevs
from bev_fusion_diagnostic import load_npz
from bev_protocol import encode_bev,hashes,validate_manifest
from m2x.security import real_backend


def audit_round(folder,summary,checkpoint_hash,backend,*,fusion_mode,calibrated):
    peers = json.loads((folder/"peers.json").read_text())
    packets,own = {},{}
    for node,row in peers.items():
        if (row["status"]!="fusion_complete" or row.get("artifact_error")
                or sorted(row["peer_ids"])!=sorted(row["verified_peer_ids"])
                or row["inference"]["checkpoint_sha256"]!=checkpoint_hash
                or row["fusion_mode"]!=fusion_mode
                or row["inference"]["occupancy_calibration_enabled"]!=calibrated):
            raise ValueError("Incomplete or wrong-model live round.")
        if node!="ego" and row["peer_ids"]!=["ego"]:
            raise ValueError("Unexpected observer-to-observer transmission.")
        packet = json.loads((folder/f"{node}_packet.json").read_text())
        manifest = packet["manifest"]; validate_manifest(manifest)
        prediction = load_npz(folder/f"{node}_own.npz")
        body_digest = hashlib.sha256(encode_bev(prediction)).hexdigest()
        context,payload = hashes(manifest)
        if (body_digest!=manifest["sha256"] or body_digest!=row["own_bev_sha256"]
                or manifest["checkpoint_sha256"]!=checkpoint_hash
                or manifest["sender_id"]!=node or manifest["round_id"]!=row["round_id"]
                or manifest["actor_id"]!=summary["actors"][node]["actor_id"]
                or manifest["frame_id"]!=row["frame_id"]
                or manifest["recipient_ids"]!=sorted(row["peer_ids"])
                or packet["public_inputs"]!=[summary["setup"][node]["commitment"],context,payload]
                or manifest["registered_commitment"]!=summary["setup"][node]["commitment"]):
            raise ValueError("Saved packet does not bind the actual model/frame/body.")
        valid,_ = backend.verify_auth_proof(packet["proof"],packet["public_inputs"])
        if not valid:
            raise ValueError("Saved real Groth16 proof failed reverification.")
        packets[node],own[node] = packet,prediction
    if len({p["manifest"]["frame_id"] for p in packets.values()})!=1:
        raise ValueError("Cross-peer frame mismatch.")
    shared = []
    for node in sorted(peers):
        for sender in peers[node]["peer_ids"]:
            received = peers[node]["received_bevs"][sender]
            if received["sha256"]!=packets[sender]["manifest"]["sha256"]:
                raise ValueError("Receiver verified a different BEV body.")
        if node=="ego":
            continue
        incoming = load_npz(folder/f"ego_received_{node}.npz")
        for field in ("probabilities","valid"):
            np.testing.assert_array_equal(incoming[field],own[node][field])
        wire_pose = (np.asarray(packets[node]["manifest"]["pose"],np.float64).reshape(4,4)/1e6).astype(np.float32)
        np.testing.assert_array_equal(incoming["pose"],wire_pose)
        shared.append(incoming)
    replay = fuse_bevs(own["ego"],shared,mode=fusion_mode)
    saved = load_npz(folder/"ego_fused.npz")
    for field in ("probabilities","valid","occupancy","contributors","peer_added_cells"):
        np.testing.assert_array_equal(replay[field],saved[field])
    return {"proofs_reverified":len(packets),"verified_directions":sum(len(r["peer_ids"]) for r in peers.values()),
            "elapsed_ms":peers["ego"]["elapsed_ms"],"candidate_sha256":checkpoint_hash}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep",type=Path,required=True)
    parser.add_argument("--checkpoint",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Use a new audit output path.")
    import torch
    state = torch.load(args.checkpoint,map_location="cpu",weights_only=True)
    mode,calibrated = state.get("fusion_mode","weighted"),state.get("occupancy_calibration") is not None
    del state
    checkpoint_hash = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    backend = real_backend(60)
    sweep = json.loads(args.sweep.read_text())
    hashes_before = {str(args.sweep):hashlib.sha256(args.sweep.read_bytes()).hexdigest(),str(args.checkpoint):checkpoint_hash}
    rows = []
    for run in sweep["rows"]:
        if "report" not in run:
            rows.append({"scenario":run["scenario"],"wrapper_returncode":run["returncode"],"rounds":0})
            continue
        report = Path(run["report"])
        source = json.loads((report/"summary.json").read_text())
        if source["backend"]!="REAL_GROTH16" or source["topology"]!="ego_star_no_observer_to_observer":
            raise ValueError("Not the declared real backend/topology.")
        rounds = []
        for folder in sorted(report.glob("round_*")):
            if not (folder/"peers.json").exists():
                continue
            for path in folder.glob("*"):
                if path.suffix in (".json",".npz"):
                    hashes_before[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            rounds.append(audit_round(folder,source,checkpoint_hash,backend,fusion_mode=mode,calibrated=calibrated))
        rows.append({"scenario":run["scenario"],"wrapper_returncode":run["returncode"],"rounds":len(rounds),
            "requested_rounds":source["rounds_requested"],"proofs_reverified":sum(r["proofs_reverified"] for r in rounds),
            "verified_directions":sum(r["verified_directions"] for r in rounds),
            "elapsed_ms":[r["elapsed_ms"] for r in rounds],"report":str(report),
            "region_frames_assessable":run["region_frames_assessable"],
            "region_frames_fused_detected":run["region_frames_fused_detected"],
            "hidden_regions":run["anchors_hidden_parts_recovered"],"new_regions":run["anchors_new_detections"],
            "ego_driving":source["ego_driving"]})
    unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==digest for p,digest in hashes_before.items())
    report = {"candidate_sha256":checkpoint_hash,"fusion_mode":mode,"calibrated":calibrated,
        "source_files_unchanged":unchanged,"rows":rows,"metric2_pass":None,
        "audit_scope":"Saved body/frame/model/receiver equality, exact fusion replay and historical real Groth16 reverification",
        "limitations":["Offline reverification does not retest live freshness or network transport",
                       "Single host/loopback; recognition accuracy and formal 18s approval are separate"]}
    args.output.write_text(json.dumps(report,indent=2))
    if not unchanged:
        raise RuntimeError("Live source changed during audit.")
    print(json.dumps({"scenarios":len(rows),"proofs":sum(r.get("proofs_reverified",0) for r in rows),
                      "rounds":sum(r["rounds"] for r in rows),"source_files_unchanged":unchanged}),flush=True)


if __name__=="__main__":
    main()
