"""Modality side. Fail-closed Step 0..8 verification, cheapest checks first.

Ordering matters and is measured: object tampering is caught by root
recomputation in Step 5 at ~0.05 ms, long before the ~0.4 s pairing check in
Step 7 is ever reached. The replay watermark advances only in Step 8, after
everything else has passed, so a forged packet cannot burn a legitimate one.

This verifier answers exactly two questions:
  1. did a holder of a registered anchor secret authorize this exact payload,
     for this epoch, sequence and recipient set
  2. is the object list byte-identical to what was committed
It does not answer whether the anchor's fusion was correct, whether any source
modality measured honestly, or whether objects are missing.
"""
import re
import time
from dataclasses import dataclass, field

from anchor.broadcast_commitment import AnchorCommitmentManager
from anchor.broadcast_packet import (
    BROADCAST_TYPE, MANIFEST_FIELDS, build_manifest,
    make_broadcast_context_hash, make_broadcast_payload_hash,
)
from anchor.broadcast_schema import normalize_broadcast_payload
from anchor.broadcast_serializer import BroadcastDecodeError, BroadcastSerializer
from anchor.replay_guard import ReplayGuard
from crypto.groth16_v2v import validate_public_inputs
from perception.schema import require_int, require_text

STEPS = ['schema', 'window', 'identity', 'ordering', 'manifest',
         'integrity', 'statement', 'proof', 'commit']
_HEX64 = re.compile(r'^[0-9a-f]{64}$')
_HEX32 = re.compile(r'^[0-9a-f]{32}$')


@dataclass
class BroadcastReport:
    accepted: bool = False
    rejected_step: int = 0
    reject_reason: str = ''
    anchor_id: str = ''
    epoch_id: str = ''
    seq_no: int = 0
    object_count: int = 0
    broadcast_age_ms: object = None
    timing: dict = field(default_factory=dict)
    result_class: str = ''

    def summary_line(self):
        if self.accepted:
            return 'ACCEPT anchor={} seq={} objects={}'.format(
                self.anchor_id, self.seq_no, self.object_count)
        return 'REJECT step={} ({}) reason={}'.format(
            self.rejected_step, STEPS[self.rejected_step] if 0 <= self.rejected_step < len(STEPS) else '?',
            self.reject_reason)


class _Reject(Exception):
    def __init__(self, step, reason):
        self.step, self.reason = step, reason
        super().__init__(reason)


class BroadcastVerifier:
    def __init__(self, self_id, groth16_backend, known_anchor_commitments,
                 replay_guard=None, clock=None):
        """known_anchor_commitments: {anchor_id: set_of_accepted_commitments}.
        A set, not a single value, so epoch-level commitment rotation works
        without re-running trusted setup. Enrollment itself is out of scope."""
        self.self_id = self_id
        self.backend = groth16_backend
        self.known = {k: set(v) for k, v in known_anchor_commitments.items()}
        self.guard = replay_guard or ReplayGuard()
        self.clock = clock or (lambda: int(time.time() * 1000))

    def verify_wire(self, wire):
        report = BroadcastReport()
        timing = report.timing
        started = time.perf_counter()
        try:
            step0 = time.perf_counter()
            try:
                pkt = BroadcastSerializer.from_bytes_measured(wire)
            except BroadcastDecodeError as exc:
                raise _Reject(0, 'MALFORMED_PACKET:' + str(exc))
            self._check_schema(pkt)
            report.anchor_id = pkt.anchor_id
            report.epoch_id = pkt.epoch_id
            report.seq_no = pkt.seq_no
            timing['schema_ms'] = (time.perf_counter() - step0) * 1000

            now_ms = self.clock()
            report.broadcast_age_ms = now_ms - pkt.timestamp

            # Step 1: bounded acceptance window (read-only).
            reason = self.guard.check(pkt.anchor_id, pkt.epoch_id, pkt.seq_no,
                                      pkt.timestamp, now_ms)
            if reason in ('INVALID_SEQ_NO', 'INVALID_TIMESTAMP',
                          'FUTURE_BROADCAST', 'STALE_BROADCAST'):
                raise _Reject(1, reason)

            # Step 2: is this anchor known, and are we an intended recipient?
            accepted = self.known.get(pkt.anchor_id)
            if not accepted:
                raise _Reject(2, 'UNKNOWN_ANCHOR')
            if pkt.registered_commitment not in accepted:
                raise _Reject(2, 'REGISTERED_COMMITMENT_MISMATCH')
            if self.self_id not in pkt.recipient_ids:
                raise _Reject(2, 'NOT_AN_INTENDED_RECIPIENT')

            # Step 3: anchor-issued ordering (replaces challenge-response).
            if reason:
                raise _Reject(3, reason)

            # Step 4: manifest must equal the top-level fields exactly.
            step4 = time.perf_counter()
            if pkt.manifest != build_manifest(pkt):
                raise _Reject(4, 'MANIFEST_FIELD_MISMATCH')
            timing['manifest_ms'] = (time.perf_counter() - step4) * 1000

            # Step 5: independent recomputation from the received object list.
            step5 = time.perf_counter()
            try:
                payload = normalize_broadcast_payload(pkt.perception_payload)
                recomputed = AnchorCommitmentManager(payload)
            except ValueError as exc:
                raise _Reject(5, 'INVALID_PAYLOAD:' + str(exc))
            if recomputed.object_count != pkt.object_count:
                raise _Reject(5, 'OBJECT_COUNT_MISMATCH')
            if recomputed.perception_root != pkt.perception_root:
                raise _Reject(5, 'PERCEPTION_ROOT_MISMATCH')
            if recomputed.frame_commitment != pkt.frame_commitment:
                raise _Reject(5, 'FRAME_COMMITMENT_MISMATCH')
            for name in ('fusion_id', 'anchor_timestamp_ms', 'coordinate_frame'):
                if payload[name] != getattr(pkt, name):
                    raise _Reject(5, 'PAYLOAD_METADATA_MISMATCH:' + name)
            if payload['source_ids'] != sorted(pkt.source_ids):
                raise _Reject(5, 'SOURCE_LIST_MISMATCH')
            report.object_count = recomputed.object_count
            timing['integrity_verify_ms'] = (time.perf_counter() - step5) * 1000

            # Step 6: rebuild both public hashes; never trust the sender's copies.
            step6 = time.perf_counter()
            payload_hash = make_broadcast_payload_hash(pkt.manifest)
            context_hash = make_broadcast_context_hash(
                pkt.anchor_id, pkt.epoch_id, pkt.seq_no, pkt.timestamp,
                pkt.recipient_ids, payload_hash, pkt.registered_commitment)
            if pkt.zkp_payload_hash != payload_hash:
                raise _Reject(6, 'PAYLOAD_HASH_MISMATCH')
            if pkt.zkp_context_hash != context_hash:
                raise _Reject(6, 'CONTEXT_HASH_MISMATCH')
            try:
                public = validate_public_inputs(pkt.zkp_public_inputs)
            except ValueError as exc:
                raise _Reject(6, 'INVALID_PUBLIC_INPUTS:' + str(exc))
            if public != [pkt.registered_commitment, context_hash, payload_hash]:
                raise _Reject(6, 'PUBLIC_INPUTS_MISMATCH')
            timing['statement_ms'] = (time.perf_counter() - step6) * 1000

            # Step 7: the only expensive check.
            step7 = time.perf_counter()
            ok, proof_timing = self.backend.verify_auth_proof(pkt.zkp_proof, public)
            timing['proof_verify_ms'] = (time.perf_counter() - step7) * 1000
            report.result_class = proof_timing.get('result_class', '')
            if not ok:
                raise _Reject(7, 'GROTH16_VERIFY_FAIL')

            # Step 8: re-check freshness, then advance the watermark atomically.
            now_ms = self.clock()
            reason = self.guard.check(pkt.anchor_id, pkt.epoch_id, pkt.seq_no,
                                      pkt.timestamp, now_ms)
            if reason:
                raise _Reject(8, 'EXPIRED_DURING_VERIFICATION:' + reason)
            if not self.guard.commit(pkt.anchor_id, pkt.epoch_id, pkt.seq_no, pkt.timestamp):
                raise _Reject(8, 'CONCURRENT_REPLAY')

            report.accepted = True
            report.rejected_step = -1
            timing['verify_total_ms'] = (time.perf_counter() - started) * 1000
            return pkt, report
        except _Reject as rej:
            report.rejected_step = rej.step
            report.reject_reason = rej.reason
            timing['verify_total_ms'] = (time.perf_counter() - started) * 1000
            return None, report

    @staticmethod
    def _check_schema(pkt):
        if pkt.type != BROADCAST_TYPE:
            raise _Reject(0, 'WRONG_PACKET_TYPE')
        if pkt.schema_version != 1:
            raise _Reject(0, 'UNSUPPORTED_SCHEMA')
        if pkt.hash_backend != 'SHA-256':
            raise _Reject(0, 'INVALID_HASH_BACKEND')
        try:
            require_text(pkt.anchor_id, 'anchor_id')
            require_int(pkt.seq_no, 'seq_no')
            require_int(pkt.timestamp, 'timestamp')
            require_int(pkt.fusion_id, 'fusion_id')
            require_int(pkt.anchor_timestamp_ms, 'anchor_timestamp_ms')
            require_int(pkt.object_count, 'object_count')
        except ValueError as exc:
            raise _Reject(0, 'MALFORMED_PACKET:' + str(exc))
        if not _HEX32.match(pkt.epoch_id or ''):
            raise _Reject(0, 'INVALID_EPOCH_ID')
        for name in ('perception_root', 'frame_commitment'):
            if not _HEX64.match(getattr(pkt, name) or ''):
                raise _Reject(0, 'INVALID_DIGEST:' + name)
        if type(pkt.recipient_ids) is not list or not pkt.recipient_ids or len(pkt.recipient_ids) > 16:
            raise _Reject(0, 'INVALID_RECIPIENT_LIST')
        if sorted(pkt.recipient_ids) != pkt.recipient_ids or len(set(pkt.recipient_ids)) != len(pkt.recipient_ids):
            raise _Reject(0, 'RECIPIENT_LIST_NOT_CANONICAL')
        if type(pkt.manifest) is not dict or set(pkt.manifest) != set(MANIFEST_FIELDS):
            raise _Reject(0, 'INVALID_MANIFEST_FIELDS')
        if type(pkt.zkp_proof) is not dict or type(pkt.zkp_public_inputs) is not list:
            raise _Reject(0, 'INVALID_PROOF_CONTAINER')
