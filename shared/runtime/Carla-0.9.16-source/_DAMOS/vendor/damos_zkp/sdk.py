"""Small integration API for the DAMOS central-anchor security module.

This package intentionally starts AFTER perception fusion. The caller supplies a
fused object list and metadata. The module authenticates the exact fused payload
and returns one broadcast byte string that may be delivered unchanged to every
recipient listed in recipient_ids.
"""
import time

from anchor.broadcaster import AnchorBroadcaster
from anchor.broadcast_verifier import BroadcastVerifier
from crypto.groth16_v2v import SnarkjsGroth16Backend


class DAMOSSecuritySender(object):
    def __init__(self, anchor_id, recipient_ids, anchor_secret,
                 registered_commitment=None, assets_dir=None, require_gate=True):
        self.backend = SnarkjsGroth16Backend(base_dir=assets_dir, require_gate=require_gate)
        computed = self.backend.compute_commitment(str(anchor_secret))
        if registered_commitment is not None and str(registered_commitment) != computed:
            raise ValueError('REGISTERED_COMMITMENT_DOES_NOT_MATCH_SECRET')
        self.registered_commitment = computed
        self.broadcaster = AnchorBroadcaster(
            anchor_id=str(anchor_id),
            groth16_backend=self.backend,
            vehicle_secret=str(anchor_secret),
            registered_commitment=computed,
            recipient_ids=list(recipient_ids),
        )

    def make_payload(self, objects, source_ids, fusion_id,
                     anchor_timestamp_ms=None, coordinate_frame='world'):
        return {
            'schema_version': 1,
            'anchor_id': self.broadcaster.anchor_id,
            'fusion_id': int(fusion_id),
            'anchor_timestamp_ms': int(time.time() * 1000) if anchor_timestamp_ms is None else int(anchor_timestamp_ms),
            'coordinate_frame': str(coordinate_frame),
            'source_ids': list(source_ids),
            'objects': list(objects),
        }

    def protect(self, payload, now_ms=None):
        """Return one authenticated byte string for all configured recipients."""
        return self.broadcaster.build_wire(payload, now_ms=now_ms)


class DAMOSSecurityReceiver(object):
    def __init__(self, self_id, anchor_id, registered_commitments,
                 assets_dir=None, require_gate=True, replay_guard=None, clock=None):
        self.backend = SnarkjsGroth16Backend(base_dir=assets_dir, require_gate=require_gate)
        if isinstance(registered_commitments, (str, int)):
            registered_commitments = [str(registered_commitments)]
        known = {str(anchor_id): set(str(x) for x in registered_commitments)}
        self.verifier = BroadcastVerifier(
            self_id=str(self_id),
            groth16_backend=self.backend,
            known_anchor_commitments=known,
            replay_guard=replay_guard,
            clock=clock,
        )

    def verify(self, wire):
        """Return (payload_or_None, report).

        Application code should use the payload only when report.accepted is True.
        """
        pkt, report = self.verifier.verify_wire(wire)
        if not report.accepted or pkt is None:
            return None, report
        return pkt.perception_payload, report
