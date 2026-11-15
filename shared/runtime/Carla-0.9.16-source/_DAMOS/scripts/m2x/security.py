"""Authenticate image bytes using the supplied Groth16 backend, not its object schema."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import time

VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "damos_zkp"
sys.path.insert(0, str(VENDOR))
from crypto.groth16_v2v import SnarkjsGroth16Backend, hash_bytes_to_field
from perception.canonical import canonical_json_bytes

PROTOCOL = "DAMOS_FRONT_IMAGE_V1"
MAX_HEADER = 65536
MAX_IMAGE = 8 * 1024 * 1024
MAX_NODES = 32
MANIFEST_KEYS = {
    "protocol", "session_id", "round_id", "sender_id", "recipient_ids",
    "actor_id", "frame_id", "capture_sim_ns", "timestamp_ms", "camera",
    "width", "height", "encoding", "image_size", "image_sha256",
    "registered_commitment",
}
PACKET_KEYS = {"manifest", "public_inputs", "proof"}


def real_backend(timeout_s=18.0):
    # The server's default node may be old; never install or change a global runtime.
    if "V2V_NODE_CMD" not in os.environ:
        candidates = [shutil.which("node")]
        candidates = sorted(Path.home().glob(".nvm/versions/node/v*/bin/node"),
                            key=lambda p: tuple(int(n) for n in p.parent.parent.name[1:].split(".")),
                            reverse=True) + candidates
        import subprocess
        for candidate in candidates:
            if not candidate:
                continue
            try:
                version = subprocess.check_output([str(candidate), "--version"], text=True)
                if int(version.strip().lstrip("v").split(".")[0]) >= 18:
                    os.environ["V2V_NODE_CMD"] = str(candidate)
                    break
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
    from app import config
    config.NODE_CMD = os.environ.get("V2V_NODE_CMD", config.NODE_CMD)
    config.SNARKJS_TIMEOUT_S = float(timeout_s)
    backend = SnarkjsGroth16Backend(base_dir=VENDOR / "assets" / "perception_v2",
                                   require_gate=True)
    if backend.is_mock:
        raise RuntimeError("Real Groth16 required")
    return backend


def hashes(manifest):
    payload_hash = hash_bytes_to_field(b"DAMOS/FRONT_IMAGE/PAYLOAD/v1\0",
                                       canonical_json_bytes(manifest))
    context_hash = hash_bytes_to_field(b"DAMOS/FRONT_IMAGE/CONTEXT/v1\0",
                                       canonical_json_bytes({k: manifest[k] for k in
                                           ("session_id", "round_id", "sender_id", "recipient_ids")}))
    return context_hash, payload_hash


def validate_manifest(m):
    if type(m) is not dict or set(m) != MANIFEST_KEYS:
        raise ValueError("INVALID_MANIFEST_FIELDS")
    if m["protocol"] != PROTOCOL or m["camera"] != "cam_front" or m["encoding"] != "png":
        raise ValueError("INVALID_IMAGE_PROTOCOL")
    for key in ("round_id", "actor_id", "frame_id", "capture_sim_ns", "timestamp_ms",
                "width", "height", "image_size"):
        if type(m[key]) is not int or not 0 <= m[key] < 2**63:
            raise ValueError("INVALID_INTEGER:" + key)
    if not 0 < m["width"] <= 4096 or not 0 < m["height"] <= 4096:
        raise ValueError("INVALID_DIMENSIONS")
    if not 0 < m["image_size"] <= MAX_IMAGE:
        raise ValueError("INVALID_IMAGE_SIZE")
    for key in ("session_id", "sender_id"):
        if type(m[key]) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", m[key]):
            raise ValueError("INVALID_ID:" + key)
    peers = m["recipient_ids"]
    if (type(peers) is not list or not 0 < len(peers) < MAX_NODES
            or any(type(p) is not str for p in peers)
            or peers != sorted(set(peers)) or m["sender_id"] in peers):
        raise ValueError("INVALID_RECIPIENTS")
    if type(m["image_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", m["image_sha256"]):
        raise ValueError("INVALID_IMAGE_DIGEST")


def protect(backend, secret, commitment, image, metadata, recipients):
    m = dict(metadata, protocol=PROTOCOL, camera="cam_front", encoding="png",
             recipient_ids=sorted(recipients), timestamp_ms=int(time.time() * 1000),
             image_size=len(image), image_sha256=hashlib.sha256(image).hexdigest(),
             registered_commitment=commitment)
    validate_manifest(m)
    context_hash, payload_hash = hashes(m)
    proof, public, timing = backend.generate_auth_proof(secret, commitment, context_hash, payload_hash)
    return {"manifest": m, "public_inputs": public, "proof": proof}, timing


class ImageVerifier:
    def __init__(self, backend, self_id, commitments, session_id, max_age_ms=18000):
        self.backend, self.self_id = backend, self_id
        self.commitments, self.session_id = dict(commitments), session_id
        self.max_age_ms = max_age_ms
        self.last_round = {}

    def verify(self, packet, image, *, round_id, frame_id, expected_senders,
               expected_recipients, now_ms=None):
        if type(packet) is not dict or set(packet) != PACKET_KEYS:
            raise ValueError("INVALID_PACKET_FIELDS")
        m = packet["manifest"]
        validate_manifest(m)
        sender = m["sender_id"]
        if (sender not in expected_senders or sender not in self.commitments
                or m["registered_commitment"] != self.commitments[sender]):
            raise ValueError("UNTRUSTED_SENDER")
        if self.self_id not in m["recipient_ids"] or m["recipient_ids"] != sorted(expected_recipients):
            raise ValueError("WRONG_RECIPIENTS")
        if (m["session_id"] != self.session_id or m["round_id"] != round_id
                or m["frame_id"] != frame_id):
            raise ValueError("WRONG_ROUND_OR_FRAME")
        if m["round_id"] <= self.last_round.get(sender, -1):
            raise ValueError("REPLAY")

        def freshness():
            age = (int(time.time() * 1000) if now_ms is None else now_ms) - m["timestamp_ms"]
            if age < -500 or age > self.max_age_ms:
                raise ValueError("EXPIRED_OR_FUTURE_PACKET")

        freshness()
        if len(image) != m["image_size"] or hashlib.sha256(image).hexdigest() != m["image_sha256"]:
            raise ValueError("IMAGE_TAMPERED")
        # Bound decoded allocation before passing untrusted PNG bytes to OpenCV.
        if (len(image) < 24 or image[:8] != b"\x89PNG\r\n\x1a\n"
                or image[12:16] != b"IHDR"
                or struct.unpack("!II", image[16:24]) != (m["width"], m["height"])):
            raise ValueError("INVALID_PNG_DIMENSIONS")
        context_hash, payload_hash = hashes(m)
        if packet["public_inputs"] != [self.commitments[sender], context_hash, payload_hash]:
            raise ValueError("PUBLIC_INPUT_MISMATCH")
        ok, timing = self.backend.verify_auth_proof(packet["proof"], packet["public_inputs"])
        if not ok:
            raise ValueError("INVALID_GROTH16_PROOF:" + timing.get("verification_status", ""))
        freshness()
        self.last_round[sender] = m["round_id"]
        return m, timing


def encode_header(packet):
    header = canonical_json_bytes(packet)
    if len(header) > MAX_HEADER:
        raise ValueError("HEADER_TOO_LARGE")
    return header


def decode_header(header):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("NONFINITE_JSON:" + value)

    return json.loads(header, object_pairs_hook=unique, parse_constant=invalid)
