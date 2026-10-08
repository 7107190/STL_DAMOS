"""Groth16 authentication of bounded BEV probabilities, coverage and pose."""
from __future__ import annotations

import hashlib
import re
import time

import numpy as np

from m2x.security import canonical_json_bytes, hash_bytes_to_field

PROTOCOL = "DAMOS_BEV_V1"
BODY_SIZE = 17 * 200 * 200 * 2 + 200 * 200
MANIFEST_KEYS = {
    "protocol", "session_id", "round_id", "sender_id", "recipient_ids", "actor_id",
    "frame_id", "capture_sim_ns", "timestamp_ms", "registered_commitment", "sha256",
    "body_size", "encoding", "grid_mm", "pose", "pose_encoding", "checkpoint_sha256", "checkpoint_step",
    "coverage", "coordinate_convention",
}


def encode_bev(result):
    p = np.asarray(result["probabilities"])
    v = np.asarray(result["valid"])
    if p.shape != (17, 200, 200) or v.shape != (200, 200):
        raise ValueError("INVALID_BEV_SHAPE")
    body = p.astype("<f2").tobytes() + v.astype(np.uint8).tobytes()
    decode_bev(body)
    return body


def decode_bev(body):
    if len(body) != BODY_SIZE:
        raise ValueError("INVALID_BEV_BODY_SIZE")
    probs = np.frombuffer(body, "<f2", count=17*200*200).reshape(17,200,200)
    valid = np.frombuffer(body, np.uint8, offset=17*200*200*2).reshape(200,200)
    if (not np.isfinite(probs).all() or (probs < 0).any() or (probs > 1).any()
            or not np.allclose(probs.sum(0, dtype=np.float32), 1, atol=.003)
            or not np.isin(valid, [0,1]).all()):
        raise ValueError("INVALID_BEV_VALUES")
    return {"probabilities": probs, "valid": valid}


def validate_manifest(m):
    if type(m) is not dict or set(m) != MANIFEST_KEYS:
        raise ValueError("INVALID_BEV_MANIFEST")
    if (m["protocol"] != PROTOCOL or m["encoding"] != "float16le-probs+uint8-mask"
            or m["grid_mm"] != [-40000,40000,400,-40000,40000,400,17,200,200]
            or m["pose_encoding"] != "row-major-fixed1e6"
            or m["coverage"] != "current-lidar-return-cells"
            or m["coordinate_convention"] != "x-forward_y-left_z-up"
            or m["body_size"] != BODY_SIZE):
        raise ValueError("INVALID_BEV_SCHEMA")
    for key in ("round_id", "actor_id", "frame_id", "capture_sim_ns", "timestamp_ms", "checkpoint_step"):
        if type(m[key]) is not int or not 0 <= m[key] < 2**63:
            raise ValueError("INVALID_INTEGER:" + key)
    for key in ("session_id", "sender_id"):
        if type(m[key]) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", m[key]):
            raise ValueError("INVALID_ID")
    recipients = m["recipient_ids"]
    if (type(recipients) is not list or not 0 < len(recipients) < 32
            or any(type(p) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", p) for p in recipients)
            or recipients != sorted(set(recipients)) or m["sender_id"] in recipients):
        raise ValueError("INVALID_RECIPIENTS")
    for key in ("sha256", "checkpoint_sha256"):
        if type(m[key]) is not str or not re.fullmatch(r"[0-9a-f]{64}", m[key]):
            raise ValueError("INVALID_DIGEST")
    if (type(m["pose"]) is not list or len(m["pose"]) != 16
            or any(type(x) is not int or abs(x) > 10**12 for x in m["pose"])):
        raise ValueError("INVALID_POSE")
    pose = np.asarray(m["pose"], np.float64).reshape(4,4) / 1e6
    if (not np.allclose(pose[3], [0,0,0,1], atol=1e-5)
            or not np.allclose(pose[:3,:3].T @ pose[:3,:3], np.eye(3), atol=1e-4)
            or not np.isclose(np.linalg.det(pose[:3,:3]), 1, atol=1e-4)):
        raise ValueError("NONRIGID_POSE")


def hashes(m):
    context = canonical_json_bytes({k: m[k] for k in ("session_id", "round_id", "sender_id", "recipient_ids")})
    return (hash_bytes_to_field(b"DAMOS/BEV/CONTEXT/v1\0", context),
            hash_bytes_to_field(b"DAMOS/BEV/PAYLOAD/v1\0", canonical_json_bytes(m)))


def protect_bev(backend, secret, commitment, result, metadata, recipients):
    body = encode_bev(result)
    m = dict(metadata, protocol=PROTOCOL, recipient_ids=sorted(recipients),
             timestamp_ms=int(time.time()*1000), registered_commitment=commitment,
             sha256=hashlib.sha256(body).hexdigest(), body_size=len(body),
             encoding="float16le-probs+uint8-mask", grid_mm=[-40000,40000,400,-40000,40000,400,17,200,200],
             pose=np.rint(np.asarray(result["pose"]).reshape(-1)*1e6).astype(np.int64).tolist(),
             pose_encoding="row-major-fixed1e6",
             coverage="current-lidar-return-cells", coordinate_convention="x-forward_y-left_z-up")
    validate_manifest(m)
    context, payload = hashes(m)
    proof, public, timing = backend.generate_auth_proof(secret, commitment, context, payload)
    return {"manifest": m, "proof": proof, "public_inputs": public}, body, timing


class BEVVerifier:
    def __init__(self, backend, self_id, commitments, session_id, actor_ids, max_age_ms=120000):
        self.backend, self.self_id, self.session_id = backend, self_id, session_id
        self.commitments, self.actor_ids = dict(commitments), dict(actor_ids)
        self.max_age_ms, self.last_round = max_age_ms, {}

    def verify(self, packet, body, *, round_id, frame_id, expected_senders, expected_recipients):
        if type(packet) is not dict or set(packet) != {"manifest", "proof", "public_inputs"}:
            raise ValueError("INVALID_PACKET")
        m = packet["manifest"]
        validate_manifest(m)
        sender = m["sender_id"]
        if (sender not in expected_senders or sender not in self.commitments
                or m["registered_commitment"] != self.commitments[sender]
                or m["actor_id"] != self.actor_ids.get(sender)):
            raise ValueError("UNTRUSTED_ACTOR_OR_SENDER")
        if m["recipient_ids"] != sorted(expected_recipients) or self.self_id not in m["recipient_ids"]:
            raise ValueError("WRONG_RECIPIENTS")
        if m["session_id"] != self.session_id or m["round_id"] != round_id or m["frame_id"] != frame_id:
            raise ValueError("WRONG_ROUND_OR_FRAME")
        if round_id <= self.last_round.get(sender, -1):
            raise ValueError("REPLAY")

        def fresh():
            age = int(time.time()*1000) - m["timestamp_ms"]
            if not -500 <= age <= self.max_age_ms:
                raise ValueError("EXPIRED_OR_FUTURE")

        fresh()
        if len(body) != BODY_SIZE or hashlib.sha256(body).hexdigest() != m["sha256"]:
            raise ValueError("BEV_TAMPERED")
        context, payload = hashes(m)
        if packet["public_inputs"] != [self.commitments[sender], context, payload]:
            raise ValueError("PUBLIC_INPUT_MISMATCH")
        ok, timing = self.backend.verify_auth_proof(packet["proof"], packet["public_inputs"])
        if not ok:
            raise ValueError("INVALID_GROTH16_PROOF")
        fresh()
        result = decode_bev(body)
        result["pose"] = (np.asarray(m["pose"], np.float64).reshape(4,4)/1e6).astype(np.float32)
        self.last_round[sender] = round_id
        return result, timing
