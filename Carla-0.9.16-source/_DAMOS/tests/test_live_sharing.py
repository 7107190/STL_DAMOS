"""Protocol tests; real proof/mesh tests opt in with DAMOS_TEST_REAL_ZKP=1."""
import copy
import hashlib
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from m2x.live import FrameBuffer, FrontSensors, run_live_sharing
from m2x.mesh import PeerMesh, neighbour_graph, pending_detector
from m2x.security import ImageVerifier, MAX_HEADER, decode_header, hashes, protect, real_backend
from m2x.transport import Inbox, send_image
from crypto.groth16_v2v import MockGroth16Backend


def image_bytes():
    return cv2.imencode(".png", np.full((24, 32, 3), 95, np.uint8))[1].tobytes()


def samples():
    raw = np.full((24, 32, 4), 95, np.uint8).tobytes()
    return {node: {"raw": raw, "actor_id": i, "frame_id": 1,
                   "capture_sim_ns": 100000000, "width": 32, "height": 24,
                   "position": [i * 5, 0, 0]}
            for i, node in enumerate(("ego", "observer_1", "observer_2"))}


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.backend = MockGroth16Backend(test_only=True)
        self.commitment = self.backend.compute_commitment("123")
        self.image = image_bytes()
        self.packet, _ = protect(self.backend, "123", self.commitment, self.image, {
            "session_id": "session1", "round_id": 1, "sender_id": "observer_1",
            "actor_id": 4, "frame_id": 60, "capture_sim_ns": 6000000000,
            "width": 32, "height": 24}, ["ego"])
        self.verifier = ImageVerifier(self.backend, "ego", {"observer_1": self.commitment}, "session1")

    def verify(self, packet=None, image=None, **kwargs):
        return self.verifier.verify(packet or self.packet, self.image if image is None else image,
                                   round_id=1, frame_id=60, expected_senders=["observer_1"],
                                   expected_recipients=["ego"], **kwargs)

    def test_valid_and_replay(self):
        self.verify()
        with self.assertRaisesRegex(ValueError, "REPLAY"):
            self.verify()

    def test_tampered_image_does_not_consume_replay_slot(self):
        with self.assertRaisesRegex(ValueError, "IMAGE_TAMPERED"):
            self.verify(image=self.image[:-1] + b"!")
        self.verify()

    def test_metadata_tamper(self):
        self.packet["manifest"]["actor_id"] += 1
        with self.assertRaisesRegex(ValueError, "PUBLIC_INPUT_MISMATCH"):
            self.verify()

    def test_wrong_frame_session_recipient_sender(self):
        for key, value, error in (("frame_id", 61, "WRONG_ROUND"),
                                  ("session_id", "other", "WRONG_ROUND"),
                                  ("recipient_ids", ["other"], "WRONG_RECIPIENTS"),
                                  ("sender_id", "intruder", "UNTRUSTED_SENDER")):
            packet = copy.deepcopy(self.packet)
            packet["manifest"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, error):
                self.verify(packet)

    def test_expired_future(self):
        now = self.packet["manifest"]["timestamp_ms"]
        for delta in (18001, -501):
            with self.subTest(delta=delta), self.assertRaisesRegex(ValueError, "EXPIRED"):
                self.verify(now_ms=now + delta)

    def test_forged_proof(self):
        self.packet["proof"]["proof_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "INVALID_GROTH16"):
            self.verify()

    def test_oversized_png_header(self):
        bad = self.image[:16] + struct.pack("!II", 2**31, 24) + self.image[24:]
        self.packet["manifest"]["image_sha256"] = hashlib.sha256(bad).hexdigest()
        with self.assertRaisesRegex(ValueError, "INVALID_PNG"):
            self.verify(image=bad)

    def test_duplicate_and_nonfinite_json(self):
        for header in (b'{"x":1,"x":2}', b'{"x":NaN}'):
            with self.assertRaises(ValueError):
                decode_header(header)

    def test_tcp_transmits_exact_bytes(self):
        inbox = Inbox()
        try:
            send_image(inbox.address, self.packet, self.image, 2)
            packet, image = inbox.items.get(timeout=2)
            self.assertEqual(packet, self.packet)
            self.assertEqual(image, self.image)
        finally:
            inbox.close()

    def test_oversized_wire_header_rejected(self):
        inbox = Inbox()
        try:
            with socket.create_connection(inbox.address, timeout=2) as sock:
                sock.sendall(struct.pack("!II", MAX_HEADER + 1, 1))
                self.assertEqual(sock.recv(1), b"")
            self.assertEqual(inbox.errors, 1)
        finally:
            inbox.close()


class BufferTests(unittest.TestCase):
    def test_latest_common_bounded(self):
        buffer = FrameBuffer(["ego", "observer"], capacity=2)
        for frame in range(1, 6):
            buffer.push("ego", {"frame_id": frame})
        buffer.push("observer", {"frame_id": 3})
        self.assertIsNone(buffer.take_latest())
        buffer.push("observer", {"frame_id": 4})
        self.assertEqual(buffer.take_latest()["ego"]["frame_id"], 4)
        self.assertIsNone(buffer.take_latest(after_frame=4))
        self.assertEqual(buffer.stats()["ego"]["dropped"], 3)

    def test_radius(self):
        graph = neighbour_graph(samples(), 6)
        self.assertEqual(graph["ego"], ["observer_1"])
        self.assertEqual(len(neighbour_graph(samples(), 0)["ego"]), 2)
        self.assertEqual(neighbour_graph(samples(), 0)["observer_1"], ["ego"])
        self.assertEqual(neighbour_graph(samples(), 6)["observer_2"], [])

    def test_far_camera_never_blocks_matching(self):
        buffer = FrameBuffer(["ego", "near", "far"])
        buffer.push("ego", {"frame_id": 10})
        buffer.push("near", {"frame_id": 10})
        result = buffer.take_latest(node_ids=["ego", "near"])
        self.assertEqual(set(result), {"ego", "near"})
        self.assertEqual(buffer.stats()["far"]["received"], 0)

    def test_sensor_leave_and_reentry_do_not_respawn(self):
        nodes = {n: {"actor_id": i, "sensor_actor_id": i + 10}
                 for i, n in enumerate(["ego", "near", "far"])}
        sensors = {n: Mock() for n in nodes}
        world = Mock()
        world.get_actor.side_effect = lambda ident: sensors[list(nodes)[ident - 10]]
        manager = FrontSensors(world, nodes, FrameBuffer(nodes))
        manager.set_active(["ego", "near"])
        manager.set_active(["ego"])
        sensors["near"].stop.assert_called_once()
        manager.set_active(["ego", "near"])
        self.assertEqual(sensors["near"].listen.call_count, 2)
        sensors["far"].listen.assert_not_called()
        world.spawn_actor.assert_not_called()
        manager.set_active([])

    def test_no_neighbours_session_is_normal_idle(self):
        attachments = [{"sensor_name": "cam_front", "track_label": name,
                        "actor_id": i, "sensor_actor_id": i + 10}
                       for i, name in enumerate(["ego", "robot"])]
        positions = {"ego": {"position": [0, 0, 0]}, "observer_1": {"position": [100, 0, 0]}}
        with tempfile.TemporaryDirectory() as folder, \
             patch("m2x.live.PeerMesh") as mesh_class, \
             patch("m2x.live.camera_positions", return_value=(positions, 1)):
            mesh_class.return_value.session_id = "test"
            summary = run_live_sharing(Mock(), attachments, folder, rounds=0, duration_s=0.02,
                                       radius_m=10, logger=lambda _: None)
            mesh_class.return_value.exchange.assert_not_called()
        self.assertEqual(summary["sharing_failures"], 0)
        self.assertEqual(summary["sharing_successes"], 0)
        self.assertGreater(summary["idle_polls"], 0)
        self.assertIsNone(summary["sharing_mean_ms"])
        self.assertIsNone(summary["metric2_pass"])

    def test_detector_never_claims_success(self):
        self.assertEqual(pending_detector(None, {}),
                         {"status": "not_connected", "result": None, "elapsed_ms": None})

    def test_scenic_rejected_ego_not_bound_to_cameras(self):
        import scenic_custom_walker_injector as injector
        clock = [0.0]
        world, process = Mock(), Mock()
        process.poll.return_value = None
        first, accepted = Mock(id=24), Mock(id=100)
        world.get_snapshot.side_effect = lambda: Mock(frame=round(clock[0] * 10))

        def sleep(seconds):
            clock[0] += seconds

        def candidates(_world, ego):
            return [{"actor_id": ego.id + i + 1, "scenario_instance": f"S{i+1}#1"}
                    for i in range(3)]

        with patch.object(injector.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(injector.time, "sleep", side_effect=sleep), \
             patch.object(injector, "safe_get_world", return_value=world), \
             patch.object(injector, "select_latest_ego_actor",
                          side_effect=lambda _: first if clock[0] < 0.2 else accepted), \
             patch.object(injector, "collect_scenic_anchor_candidates", side_effect=candidates):
            _, ego = injector.wait_for_live_scene(Mock(), process, 3, 3)
        self.assertEqual(ego.id, 100)
        self.assertGreaterEqual(clock[0], 0.7)


@unittest.skipUnless(os.environ.get("DAMOS_TEST_REAL_ZKP") == "1", "real crypto opt-in")
class RealTests(unittest.TestCase):
    def test_real_star_moving_ego_leaves_and_reenters(self):
        mesh = PeerMesh(["ego", "observer_1", "observer_2"], timeout_s=8)
        try:
            for round_id, (ego_x, selected) in enumerate(
                    [(0, "observer_1"), (20, "observer_2"), (0, "observer_1")], 1):
                data = samples()
                data["ego"]["position"] = [ego_x, 0, 0]
                data["observer_1"]["position"] = [5, 0, 0]
                data["observer_2"]["position"] = [20, 0, 0]
                for sample in data.values():
                    sample["frame_id"] = round_id
                # Far nodes deliberately have no image input at all.
                rows = mesh.exchange({n: data[n] for n in ["ego", selected]}, round_id, radius_m=6)
                self.assertEqual(set(rows), {"ego", selected})
                self.assertEqual(rows["ego"]["verified_peer_ids"], [selected])
                self.assertEqual(rows[selected]["verified_peer_ids"], ["ego"])
                self.assertTrue(all(r["status"] == "sharing_complete_detection_pending" for r in rows.values()))
        finally:
            mesh.close()

    def test_real_proof_binds_every_public_input(self):
        backend = real_backend()
        commitment = backend.compute_commitment("987654321")
        packet, _ = protect(backend, "987654321", commitment, image_bytes(), {
            "session_id": "real_test", "round_id": 1, "sender_id": "observer_1",
            "actor_id": 1, "frame_id": 1, "capture_sim_ns": 100000000,
            "width": 32, "height": 24}, ["ego"])
        self.assertTrue(backend.verify_auth_proof(packet["proof"], packet["public_inputs"])[0])
        for index in range(3):
            public = list(packet["public_inputs"])
            public[index] = str(int(public[index]) + 1)
            with self.subTest(index=index):
                self.assertFalse(backend.verify_auth_proof(packet["proof"], public)[0])
        packet["manifest"]["actor_id"] += 1
        context, payload = hashes(packet["manifest"])
        self.assertFalse(backend.verify_auth_proof(packet["proof"], [commitment, context, payload])[0])

    def test_missing_peer_and_no_neighbours_are_not_passes(self):
        mesh = PeerMesh(["ego", "observer_1", "observer_2"], timeout_s=3)
        try:
            data = samples()
            data["observer_2"]["raw"] = b"invalid"
            rows = mesh.exchange(data, 1)
            self.assertEqual(rows["ego"]["status"], "timeout")
            self.assertIn("observer_2", rows["ego"]["missing_peer_ids"])
            self.assertFalse(rows["ego"]["transport_budget_pass"])
            self.assertIsNone(rows["ego"]["metric2_pass"])
            data = samples()
            for sample in data.values():
                sample["frame_id"] = 2
            rows = mesh.exchange(data, 2, radius_m=1)
            self.assertEqual(rows["ego"]["status"], "no_peers")
            self.assertFalse(rows["ego"]["transport_budget_pass"])
        finally:
            mesh.close()


if __name__ == "__main__":
    unittest.main()
