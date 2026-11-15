import copy
import time
import unittest

from anchor.broadcaster import AnchorBroadcaster
from anchor.broadcast_serializer import BroadcastSerializer
from anchor.broadcast_verifier import BroadcastVerifier
from anchor.replay_guard import ReplayGuard
from crypto.groth16_v2v import MockGroth16Backend


def obj(x=1000):
    return {
        'object_id': 1, 'semantic_tag': 14,
        'center_x_mm': x, 'center_y_mm': 0, 'center_z_mm': 100,
        'length_mm': 4000, 'width_mm': 1800, 'height_mm': 1500,
        'min_distance_mm': 900, 'point_count': 100,
    }


def payload(now_ms):
    return {
        'schema_version': 1,
        'anchor_id': 'main-computer',
        'fusion_id': 1,
        'anchor_timestamp_ms': now_ms,
        'coordinate_frame': 'world',
        'source_ids': ['vehicle_lidar', 'delivery_robot_lidar', 'humanoid_lidar'],
        'objects': [obj()],
    }


class HandoffMockTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time() * 1000)
        self.backend = MockGroth16Backend(test_only=True)
        self.secret = '123456'
        self.commitment = self.backend.compute_commitment(self.secret)
        self.tx = AnchorBroadcaster('main-computer', self.backend, self.secret,
                                    self.commitment,
                                    ['vehicle', 'delivery-robot', 'humanoid'],
                                    epoch_id='a' * 32)

    def rx(self):
        return BroadcastVerifier('vehicle', self.backend,
                                 {'main-computer': {self.commitment}},
                                 replay_guard=ReplayGuard(max_age_ms=10000),
                                 clock=lambda: self.now)

    def test_normal(self):
        wire = self.tx.build_wire(payload(self.now), now_ms=self.now)
        pkt, rep = self.rx().verify_wire(wire)
        self.assertTrue(rep.accepted, rep.summary_line())
        self.assertEqual(pkt.perception_payload['objects'][0]['object_id'], 1)

    def test_object_tamper_rejected(self):
        wire = self.tx.build_wire(payload(self.now), now_ms=self.now)
        d = BroadcastSerializer.to_dict(BroadcastSerializer.from_bytes(wire))
        d['perception_payload']['objects'][0]['center_x_mm'] += 1
        forged = BroadcastSerializer.to_bytes(BroadcastSerializer.from_dict(d))
        _, rep = self.rx().verify_wire(forged)
        self.assertFalse(rep.accepted)

    def test_exact_replay_rejected(self):
        rx = self.rx()
        wire = self.tx.build_wire(payload(self.now), now_ms=self.now)
        _, first = rx.verify_wire(wire)
        _, second = rx.verify_wire(wire)
        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(second.reject_reason, 'REPLAY_OR_OUT_OF_ORDER')

    def test_unlisted_recipient_rejected(self):
        rx = BroadcastVerifier('drone', self.backend,
                               {'main-computer': {self.commitment}},
                               replay_guard=ReplayGuard(max_age_ms=10000),
                               clock=lambda: self.now)
        wire = self.tx.build_wire(payload(self.now), now_ms=self.now)
        _, rep = rx.verify_wire(wire)
        self.assertFalse(rep.accepted)
        self.assertEqual(rep.reject_reason, 'NOT_AN_INTENDED_RECIPIENT')


if __name__ == '__main__':
    unittest.main(verbosity=2)
