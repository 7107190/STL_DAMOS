"""Anchor side. Takes a fused perception result, commits it, and produces ONE
signed broadcast usable by every listed recipient.

Design note on cost: the Groth16 statement binds the sorted recipient_ids list,
not a single dst_id. That is why a broadcast to three modalities costs one proof
(~0.5 s measured) instead of three (~1.6 s). The trade-off is explicit: a packet
addressed to {car, robot, humanoid} is equally valid at all three, so relaying it
between them is NOT treated as an attack. Delivery to a party outside the list
fails at the recipient check.

This class does not fuse anything and makes no claim about fusion correctness.
It authenticates whatever payload it is handed.
"""
import secrets
import threading
import time

from anchor.broadcast_commitment import AnchorCommitmentManager
from anchor.broadcast_packet import (
    AnchorBroadcastPacket, build_manifest,
    make_broadcast_context_hash, make_broadcast_payload_hash,
)
from anchor.broadcast_schema import normalize_broadcast_payload
from anchor.broadcast_serializer import BroadcastSerializer


def new_epoch_id():
    return secrets.token_hex(16)


class AnchorBroadcaster:
    def __init__(self, anchor_id, groth16_backend, vehicle_secret,
                 registered_commitment, recipient_ids, epoch_id=None):
        if not anchor_id or type(anchor_id) is not str:
            raise ValueError('INVALID_ANCHOR_ID')
        if not recipient_ids or len(set(recipient_ids)) != len(recipient_ids):
            raise ValueError('INVALID_RECIPIENT_LIST')
        self.anchor_id = anchor_id
        self.backend = groth16_backend
        self._secret = vehicle_secret
        self.registered_commitment = registered_commitment
        self.recipient_ids = sorted(recipient_ids)
        self.epoch_id = epoch_id or new_epoch_id()
        self._seq = 0
        self._lock = threading.Lock()

    def rotate_epoch(self, vehicle_secret=None, registered_commitment=None):
        """Redraw the epoch. Optionally swap in a different pre-registered
        (secret, commitment) pair so consecutive epochs are not linkable by the
        commitment value alone. Receivers must already hold the new commitment."""
        with self._lock:
            self.epoch_id = new_epoch_id()
            self._seq = 0
            if vehicle_secret is not None:
                self._secret = vehicle_secret
                self.registered_commitment = registered_commitment
        return self.epoch_id

    def _next_seq(self):
        with self._lock:
            self._seq += 1
            return self._seq

    def build(self, payload, now_ms=None):
        payload = normalize_broadcast_payload(payload)
        if payload['anchor_id'] != self.anchor_id:
            raise ValueError('PAYLOAD_ANCHOR_ID_MISMATCH')
        commitment = AnchorCommitmentManager(payload)
        started = time.perf_counter()

        pkt = AnchorBroadcastPacket()
        pkt.anchor_id = self.anchor_id
        pkt.epoch_id = self.epoch_id
        pkt.seq_no = self._next_seq()
        pkt.timestamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
        pkt.recipient_ids = list(self.recipient_ids)
        pkt.perception_payload = payload
        pkt.fusion_id = payload['fusion_id']
        pkt.anchor_timestamp_ms = payload['anchor_timestamp_ms']
        pkt.coordinate_frame = payload['coordinate_frame']
        pkt.source_ids = list(payload['source_ids'])
        pkt.object_count = commitment.object_count
        pkt.perception_root = commitment.perception_root
        pkt.frame_commitment = commitment.frame_commitment
        pkt.registered_commitment = self.registered_commitment

        pkt.manifest = build_manifest(pkt)
        payload_hash = make_broadcast_payload_hash(pkt.manifest)
        context_hash = make_broadcast_context_hash(
            pkt.anchor_id, pkt.epoch_id, pkt.seq_no, pkt.timestamp,
            pkt.recipient_ids, payload_hash, pkt.registered_commitment)

        proof, public, timing = self.backend.generate_auth_proof(
            self._secret, self.registered_commitment, context_hash, payload_hash)

        pkt.zkp_payload_hash = payload_hash
        pkt.zkp_context_hash = context_hash
        pkt.zkp_public_inputs = list(public)
        pkt.zkp_proof = proof
        pkt.zkp_backend = self.backend.backend_name()
        pkt.zkp_timing = dict(timing)
        pkt.auth_metrics = {'packet_build_ms': (time.perf_counter() - started) * 1000,
                            'recipient_count': len(pkt.recipient_ids)}
        return pkt

    def build_wire(self, payload, now_ms=None):
        """One encode, reused for every recipient. Returns identical bytes to all
        listed modalities."""
        return BroadcastSerializer.to_bytes_measured(self.build(payload, now_ms))
