"""One-way anchor broadcast wire type and its authenticated manifest.

Deliberately incompatible with V2V_PERCEPTION_MESSAGE. There is no challenge
nonce and no session issued by the receiver, because a modality has no return
channel in this deployment. Replay protection is carried by the anchor-issued
(epoch_id, seq_no) pair plus a bounded acceptance window; see replay_guard.py.

All authenticated timestamps are INTEGER Unix milliseconds. anchor_timestamp_ms
inside the payload is the fusion clock and is never compared to Unix time here.
"""
from dataclasses import dataclass, field
from typing import List

from anchor.broadcast_schema import canonical_broadcast_bytes
from app import config
from crypto.groth16_v2v import hash_bytes_to_field
from perception.canonical import canonical_json_bytes

BROADCAST_TYPE = 'ANCHOR_PERCEPTION_BROADCAST'
DOMAIN_ANCHOR_MANIFEST = b'DAMOS/ANCHOR/MANIFEST/v1\x00'
DOMAIN_ANCHOR_CONTEXT = b'DAMOS/ANCHOR/CONTEXT/v1\x00'

MANIFEST_FIELDS = (
    'schema_version', 'anchor_id', 'epoch_id', 'seq_no', 'timestamp',
    'recipient_ids', 'fusion_id', 'anchor_timestamp_ms', 'coordinate_frame',
    'source_ids', 'object_count', 'perception_root', 'frame_commitment',
    'hash_backend', 'registered_commitment',
)


@dataclass
class AnchorBroadcastPacket:
    type: str = BROADCAST_TYPE
    schema_version: int = 1
    anchor_id: str = ''
    epoch_id: str = ''
    seq_no: int = 0
    timestamp: int = 0
    recipient_ids: List[str] = field(default_factory=list)
    perception_payload: dict = field(default_factory=dict)
    fusion_id: int = 0
    anchor_timestamp_ms: int = 0
    coordinate_frame: str = 'world'
    source_ids: List[str] = field(default_factory=list)
    object_count: int = 0
    perception_root: str = ''
    frame_commitment: str = ''
    hash_backend: str = 'SHA-256'
    manifest: dict = field(default_factory=dict)
    registered_commitment: str = ''
    zkp_payload_hash: str = ''
    zkp_context_hash: str = ''
    zkp_public_inputs: List[str] = field(default_factory=list)
    zkp_proof: dict = field(default_factory=dict)
    zkp_backend: str = ''
    zkp_timing: dict = field(default_factory=dict)
    auth_metrics: dict = field(default_factory=dict)

    def payload_size_bytes(self):
        return len(canonical_broadcast_bytes(self.perception_payload))


def build_manifest(packet):
    return {key: getattr(packet, key) for key in MANIFEST_FIELDS}


def make_broadcast_payload_hash(manifest):
    if type(manifest) is not dict or set(manifest) != set(MANIFEST_FIELDS):
        raise ValueError('INVALID_ANCHOR_MANIFEST_FIELDS')
    return hash_bytes_to_field(DOMAIN_ANCHOR_MANIFEST, canonical_json_bytes(manifest))


def make_broadcast_context_hash(anchor_id, epoch_id, seq_no, timestamp, recipient_ids,
                                payload_hash, registered_commitment):
    """Structured context. A distinct domain from ZKP_CONTEXT_DOMAIN means a V2V
    proof can never be replayed as an anchor broadcast proof even though both use
    the same circuit and proving key."""
    body = dict(anchor_id=anchor_id, epoch_id=epoch_id, seq_no=seq_no,
                timestamp=timestamp, recipient_ids=sorted(recipient_ids),
                payload_hash=payload_hash,
                registered_commitment=registered_commitment,
                schema_version=1)
    assert config.SNARK_FIELD_MODULUS  # field reduction happens in hash_bytes_to_field
    return hash_bytes_to_field(DOMAIN_ANCHOR_CONTEXT, canonical_json_bytes(body))
