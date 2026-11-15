import copy
import os
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bev_fusion import fuse_bevs, warp_to_ego
from bev_protocol import BEVVerifier, decode_bev, encode_bev, protect_bev


def bev(cls=8):
    p = np.zeros((17,200,200), np.float16); p[cls] = 1
    return {"probabilities": p, "valid": np.zeros((200,200), np.uint8), "pose": np.eye(4)}


class FusionTests(unittest.TestCase):
    def test_ego_pose_not_identity(self):
        a = bev(); a["pose"][0,3] = 34
        a["valid"][100,100] = 1
        ego_pose = np.eye(4); ego_pose[0,3] = 30
        _, mask = warp_to_ego(a, ego_pose)
        self.assertTrue(mask[110,100])

    def test_identity_and_unknown(self):
        a = bev(); a["valid"][120,130] = 1
        out = fuse_bevs(a, [])
        np.testing.assert_array_equal(out["valid"], a["valid"])
        self.assertEqual(out["probabilities"][:,120,130].argmax(), 8)
        self.assertEqual(out["peer_added_cells"].sum(), 0)
        self.assertEqual(out["valid"][0,0], 0)

    def test_translation(self):
        a = bev(); a["pose"][0,3] = 4; a["valid"][100,100] = 1
        _, mask = warp_to_ego(a, np.eye(4))
        self.assertTrue(mask[110,100]); self.assertEqual(mask.sum(), 1)

    def test_rotation(self):
        a = bev(); a["pose"][:2,:2] = [[0,-1],[1,0]]
        a["valid"][110,100] = 1
        _, mask = warp_to_ego(a, np.eye(4))
        self.assertTrue(mask[99,110]); self.assertEqual(mask.sum(), 1)

    def test_outside_roi_not_clipped_inside(self):
        a = bev(); a["pose"][0,3] = 200; a["valid"][:] = 1
        self.assertFalse(warp_to_ego(a, np.eye(4))[1].any())

    def test_union_and_conflict(self):
        a,b = bev(7),bev(8)
        a["valid"][100,100] = 1; b["valid"][100,100:102] = 1
        out = fuse_bevs(a,[b])
        self.assertEqual(out["contributors"][100,100], 2)
        self.assertEqual(out["probabilities"][7,100,100], .5)
        self.assertEqual(out["peer_added_cells"].sum(), 1)
        self.assertEqual(out["strongest_source"][100,101], 1)


class StubBackend:
    """Schema test only; real crypto is tested separately below."""
    def generate_auth_proof(self, secret, commitment, context, payload):
        return {"test_only": True}, [commitment,context,payload], {}

    def verify_auth_proof(self, proof, public):
        return proof == {"test_only": True}, {}


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.backend = StubBackend()
        self.metadata = {"session_id": "session", "round_id": 1, "sender_id": "peer",
                         "actor_id": 42, "frame_id": 10, "capture_sim_ns": 100,
                         "checkpoint_sha256": "a"*64, "checkpoint_step": 100000}
        self.packet,self.body,_ = protect_bev(self.backend,"1","2",bev(),self.metadata,["ego"])

    def verify(self, packet=None, body=None, verifier=None, **kwargs):
        verifier = verifier or BEVVerifier(self.backend,"ego",{"peer":"2"},"session",{"peer":42})
        return verifier.verify(packet or self.packet, self.body if body is None else body,
                               round_id=1, frame_id=10, expected_senders=["peer"],
                               expected_recipients=["ego"], **kwargs)

    def test_roundtrip(self):
        out,_ = self.verify()
        np.testing.assert_array_equal(out["probabilities"], bev()["probabilities"])

    def test_digest_tampering(self):
        with self.assertRaisesRegex(ValueError,"TAMPERED"):
            self.verify(body=self.body[:-1]+b"\x01")

    def test_pose_frame_identity_binding(self):
        for key,value in [("pose", list(np.eye(4).reshape(-1)+[0,0,0,1]*4)),
                          ("frame_id",11),("actor_id",43),("session_id","other"),
                          ("checkpoint_sha256","b"*64),("recipient_ids",["other"])]:
            packet = copy.deepcopy(self.packet); packet["manifest"][key] = value
            with self.assertRaises(ValueError):
                self.verify(packet)

    def test_replay(self):
        verifier = BEVVerifier(self.backend,"ego",{"peer":"2"},"session",{"peer":42})
        self.verify(verifier=verifier)
        with self.assertRaisesRegex(ValueError,"REPLAY"):
            self.verify(verifier=verifier)

    def test_expiry(self):
        packet = copy.deepcopy(self.packet); packet["manifest"]["timestamp_ms"] = 0
        with self.assertRaisesRegex(ValueError,"EXPIRED"):
            self.verify(packet)

    def test_invalid_probabilities_and_mask(self):
        for key,index,value in [("probabilities",(0,0,0),float("nan")),
                                ("probabilities",(8,0,0),.5),("valid",(0,0),2)]:
            a = bev(); a[key][index] = value
            with self.assertRaises(ValueError):
                encode_bev(a)
        with self.assertRaises(ValueError):
            decode_bev(b"short")

    def test_bad_proof(self):
        packet = copy.deepcopy(self.packet); packet["proof"] = {"test_only": False}
        with self.assertRaisesRegex(ValueError,"INVALID_GROTH16"):
            self.verify(packet)

    def test_fixed_point_pose(self):
        a = bev(); a["pose"][0,3] = 12.3456784
        angle = .37
        a["pose"][:2,:2] = [[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]]
        packet,body,_ = protect_bev(self.backend,"1","2",a,self.metadata,["ego"])
        self.assertTrue(all(type(v) is int for v in packet["manifest"]["pose"]))
        out,_ = self.verify(packet,body)
        np.testing.assert_allclose(out["pose"],a["pose"],atol=1e-6)


class ConfigTests(unittest.TestCase):
    def test_mode_constraints(self):
        from dataclasses import replace
        from scenic_custom_walker_injector import ScenicCustomWalkerConfig, validate_config
        base = ScenicCustomWalkerConfig(attach_bev_sensors=True, live_bev_fusion=True, realtime_factor=1.)
        validate_config(base)
        for changed in [{"live_ego_sharing":True},{"save_bev_inputs":True},
                        {"bev_rounds":0},{"bev_radius_m":-1},{"bev_timeout_seconds":121},
                        {"realtime_factor":0},{"ego_front_camera_fault":"random"}]:
            with self.assertRaises(ValueError):
                validate_config(replace(base,**changed))

    def test_cli_implies_sensors(self):
        import argparse
        from scenic_custom_walker_injector import add_integration_args,config_from_args
        parser = argparse.ArgumentParser(); add_integration_args(parser)
        args = parser.parse_args(["--live-bev-fusion","--realtime-factor","1"])
        self.assertTrue(config_from_args(args).attach_bev_sensors)

class RealCryptoTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("DAMOS_TEST_REAL_ZKP") == "1", "opt-in real Groth16")
    def test_original_groth16_bev(self):
        from m2x.security import real_backend
        backend = real_backend(60)
        secret = "123456789"
        commitment = backend.compute_commitment(secret)
        metadata = {"session_id":"session", "round_id":1, "sender_id":"peer", "actor_id":42,
                    "frame_id":10, "capture_sim_ns":100, "checkpoint_sha256":"a"*64,
                    "checkpoint_step":100000}
        packet,body,_ = protect_bev(backend,secret,commitment,bev(),metadata,["ego"])
        verifier = BEVVerifier(backend,"ego",{"peer":commitment},"session",{"peer":42})
        out,_ = verifier.verify(packet,body,round_id=1,frame_id=10,
                                expected_senders=["peer"],expected_recipients=["ego"])
        self.assertEqual(out["probabilities"].shape,(17,200,200))


if __name__ == "__main__":
    unittest.main()
