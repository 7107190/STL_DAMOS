import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gpu_resources import binding, renderer_args, verify_engine_gpu, worker_slot, check_observations


GPU0 = "GPU-b485c857-5a57-3757-9ec8-9390d6f548d5"
GPU1 = "GPU-8b0a6653-4627-d716-d46f-760168270801"


def env(gpu, adapter):
    return {"DAMOS_GPU_UUID": gpu, "DAMOS_RENDER_ADAPTER": str(adapter), "CUDA_VISIBLE_DEVICES": gpu}


class GPUResources(unittest.TestCase):
    def test_legacy_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(binding())
            self.assertEqual(renderer_args(), [])

    def test_gpu1_renderer_and_cuda_binding(self):
        with patch.dict(os.environ, env(GPU1, 1), clear=True):
            self.assertEqual(renderer_args(), ["-graphicsadapter=1"])
            self.assertEqual(binding()["gpu_uuid"], GPU1)

    def test_mismatched_cuda_or_invalid_adapter_rejected(self):
        for change in ({"CUDA_VISIBLE_DEVICES": GPU1}, {"DAMOS_RENDER_ADAPTER": "-1"}, {"DAMOS_GPU_UUID": "../bad"}):
            with patch.dict(os.environ, {**env(GPU0, 0), **change}, clear=True):
                with self.assertRaises(ValueError):
                    binding()

    def test_two_gpus_can_run_simultaneously(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, env(GPU0, 0), clear=True), worker_slot(Path(folder)):
                with patch.dict(os.environ, env(GPU1, 1), clear=True), worker_slot(Path(folder)):
                    pass

    def test_same_gpu_exclusive(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env(GPU0, 0), clear=True):
            with worker_slot(Path(folder)):
                with self.assertRaises(BlockingIOError):
                    with worker_slot(Path(folder)):
                        pass

    def test_legacy_and_bound_jobs_never_overlap(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {}, clear=True), worker_slot(Path(folder)):
                with patch.dict(os.environ, env(GPU1, 1), clear=True):
                    with self.assertRaises(BlockingIOError):
                        with worker_slot(Path(folder)):
                            pass

    def test_renderer_pid_physical_gpu_checked(self):
        xml = f"<nvidia_smi_log><gpu><uuid>{GPU1}</uuid><processes><process_info><pid>123</pid><type>C+G</type><used_memory>5500 MiB</used_memory></process_info></processes></gpu></nvidia_smi_log>"
        with patch.dict(os.environ, env(GPU1, 1), clear=True), patch("gpu_resources.subprocess.check_output", return_value=xml):
            self.assertTrue(verify_engine_gpu(123)["physical_gpu_verified"])
        with patch.dict(os.environ, env(GPU0, 0), clear=True), patch("gpu_resources.subprocess.check_output", return_value=xml):
            with self.assertRaises(RuntimeError):
                verify_engine_gpu(123)

    def test_small_graphics_context_preserved_not_misclassified(self):
        records = [{"gpu_uuid": GPU1, "type": "C+G", "memory": "5500 MiB"},
                   {"gpu_uuid": GPU0, "type": "G", "memory": "6 MiB"}]
        checked = check_observations(records, GPU1)
        self.assertEqual(checked["active_gpu_uuids"], [GPU1])
        self.assertEqual(checked["secondary_graphics_contexts"], [records[1]])
        records[1]["type"] = "C"
        with self.assertRaises(RuntimeError):
            check_observations(records, GPU1)


if __name__ == "__main__":
    unittest.main()
