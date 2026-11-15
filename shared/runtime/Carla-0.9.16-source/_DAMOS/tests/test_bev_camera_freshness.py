from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bev_camera_freshness import CAMERAS, CameraContentAudit


class CameraContentTests(unittest.TestCase):
    def record(self, *, observer_changes=True, ego_changes=True):
        audit = CameraContentAudit()
        for i in range(10):
            images = {"ego": {name: bytes([i if ego_changes else 0]) for name in CAMERAS},
                      "observer": {name: bytes([i if observer_changes and name == "cam_back" else 0])
                                   for name in CAMERAS}}
            audit.observe(100 + i * 2, 1_000_000_000 + i * 200_000_000, images)
        return audit

    def test_changing_metadata_does_not_prove_fresh_pixels(self):
        audit = self.record(observer_changes=False)
        self.assertEqual(audit.result()["nodes"]["observer"]["cameras"]["cam_front"]["unique_images"], 1)
        with self.assertRaisesRegex(ValueError, "observer"):
            audit.require_passby_content()

    def test_static_front_is_allowed_when_another_direction_changes(self):
        result = self.record().require_passby_content()
        self.assertEqual(result["nodes"]["observer"]["cameras"]["cam_front"]["unique_images"], 1)
        self.assertEqual(result["nodes"]["observer"]["cameras"]["cam_back"]["unique_images"], 10)

    def test_ego_is_also_checked(self):
        with self.assertRaisesRegex(ValueError, "ego"):
            self.record(ego_changes=False).require_passby_content()

    def test_missing_camera_rejected(self):
        with self.assertRaisesRegex(ValueError, "six"):
            CameraContentAudit().observe(1, 1, {"ego": {"cam_front": b"rgb"}})

    def test_duplicate_frame_rejected(self):
        audit = self.record()
        with self.assertRaisesRegex(ValueError, "increasing"):
            audit.observe(118, 2_800_000_000, {})

    def test_actor_substitution_rejected(self):
        with self.assertRaisesRegex(ValueError, "actor set"):
            self.record().observe(120, 3_000_000_000, {"replacement": {name: b"x" for name in CAMERAS}})

    def test_insufficient_samples_not_marked_verified(self):
        with self.assertRaisesRegex(ValueError, "10"):
            CameraContentAudit().require_passby_content()


if __name__ == "__main__":
    unittest.main()
