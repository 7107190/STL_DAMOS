"""Policy fixtures only; actual CUDA replay evidence is stored with the trial."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bev_model_runtime import (CUDA_INFERENCE_POLICY, configure_cuda_inference,
                               require_cuda_inference_policy)


def backend(initialized=False):
    return SimpleNamespace(cuda=SimpleNamespace(is_initialized=lambda: initialized),
        use_deterministic_algorithms=Mock(),
        are_deterministic_algorithms_enabled=lambda: True,
        is_deterministic_algorithms_warn_only_enabled=lambda: False,
        backends=SimpleNamespace(cudnn=SimpleNamespace(benchmark=True, deterministic=False)))


class InferencePolicyTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_configures_before_cuda_and_keeps_fp16(self):
        torch = backend()
        policy = configure_cuda_inference(torch)
        self.assertEqual(os.environ["CUBLAS_WORKSPACE_CONFIG"], ":4096:8")
        torch.use_deterministic_algorithms.assert_called_once_with(True, warn_only=False)
        self.assertFalse(torch.backends.cudnn.benchmark)
        self.assertTrue(torch.backends.cudnn.deterministic)
        self.assertEqual(policy, CUDA_INFERENCE_POLICY)
        self.assertEqual(policy["autocast_dtype"], "float16")
        policy["version"] = "changed"
        self.assertEqual(CUDA_INFERENCE_POLICY["version"], "deterministic-cuda-v1")
        require_cuda_inference_policy(torch)

    @patch.dict(os.environ, {}, clear=True)
    def test_late_workspace_configuration_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "before CUDA initialization"):
            configure_cuda_inference(backend(initialized=True))

    @patch.dict(os.environ, {"CUBLAS_WORKSPACE_CONFIG": ":4096:8"}, clear=True)
    def test_already_configured_cuda_is_supported(self):
        configure_cuda_inference(backend(initialized=True))

    @patch.dict(os.environ, {"CUBLAS_WORKSPACE_CONFIG": ":16:8"}, clear=True)
    def test_conflicting_external_workspace_is_not_silently_replaced(self):
        with self.assertRaisesRegex(ValueError, "CUBLAS_WORKSPACE_CONFIG"):
            configure_cuda_inference(backend())
        self.assertEqual(os.environ["CUBLAS_WORKSPACE_CONFIG"], ":16:8")

    @patch.dict(os.environ, {}, clear=True)
    def test_changed_backend_flags_fail_closed(self):
        torch = backend()
        configure_cuda_inference(torch)
        torch.backends.cudnn.benchmark = True
        with self.assertRaisesRegex(RuntimeError, "policy changed"):
            require_cuda_inference_policy(torch)

    @patch.dict(os.environ, {}, clear=True)
    def test_warning_only_algorithms_are_not_accepted(self):
        torch = backend()
        configure_cuda_inference(torch)
        torch.is_deterministic_algorithms_warn_only_enabled = lambda: True
        with self.assertRaisesRegex(RuntimeError, "policy changed"):
            require_cuda_inference_policy(torch)


if __name__ == "__main__":
    unittest.main()
