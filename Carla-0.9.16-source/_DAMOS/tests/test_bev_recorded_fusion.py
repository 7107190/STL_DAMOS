"""Recorded-policy validation retains baseline behavior and rejects mismatches."""

from pathlib import Path
import unittest

import numpy as np

from test_bev_relative_fusion import prediction, peer_body
from bev_accuracy_batch import reconstruct_recorded_fusion
from bev_fusion import fuse_bevs


class RecordedFusionTest(unittest.TestCase):
    def test_legacy_metadata_keeps_baseline(self):
        own, peer = prediction(), peer_body()
        actual = reconstruct_recorded_fusion(Path("/unused"), own, [peer], {})
        expected = fuse_bevs(own, [peer], mode="ego_observed")
        np.testing.assert_array_equal(actual["probabilities"], expected["probabilities"])

    def test_unknown_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            reconstruct_recorded_fusion(Path("/unused"), prediction(), [peer_body()], {"policy": "unknown"})

    def test_different_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            reconstruct_recorded_fusion(Path("/unused"), prediction(), [peer_body()],
                                        {"policy": "relative-v3", "parameters": {}})


if __name__ == "__main__":
    unittest.main()
