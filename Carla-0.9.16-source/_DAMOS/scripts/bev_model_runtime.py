"""Strict adapter for the supplied current-frame camera/LiDAR BEV checkpoint."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "yerin_bev"
CHECKPOINT = ROOT / "models" / "yerin_bev" / "best_people_cam+lidar_scratch_100k.pt"
INPUT_SHAPES = {
    "imgs": (1, 4, 6, 3, 128, 320), "rots": (1, 4, 6, 3, 3),
    "trans": (1, 4, 6, 3), "intrins": (1, 4, 6, 3, 3),
    "post_rots": (1, 4, 6, 3, 3), "post_trans": (1, 4, 6, 3),
    "ego2globals": (1, 4, 4, 4), "lidar_bev": (1, 6, 200, 200),
}
CUDA_INFERENCE_POLICY = {
    "version": "deterministic-cuda-v1", "deterministic_algorithms": True,
    "warn_only": False, "cudnn_benchmark": False, "cudnn_deterministic": True,
    "cublas_workspace_config": ":4096:8", "autocast_dtype": "float16",
}
CLASS_NAMES = ["others", "barrier", "bicycle", "bus", "car", "construction",
               "motorcycle", "pedestrian", "object", "trailer", "truck", "road",
               "other_flat", "sidewalk", "terrain", "manmade", "vegetation"]
# RGB; taxonomy exactly matches the handed-over CARLA model.
COLORS = np.array([(128,128,128),(147,20,255),(0,255,0),(0,69,255),(255,165,0),
                   (240,32,160),(255,200,0),(0,0,255),(255,255,0),(43,90,139),
                   (255,99,71),(0,255,255),(0,215,180),(128,0,128),(34,139,34),
                   (70,130,180),(0,100,0)], np.uint8)


def validate_inputs(inputs):
    for name, shape in INPUT_SHAPES.items():
        array = inputs[name]
        if array.shape != shape or array.dtype != np.float32 or not np.isfinite(array).all():
            raise ValueError(f"Invalid model input {name}: {array.shape}/{array.dtype}.")
    if not inputs["lidar_bev"][0, 0].any():
        raise ValueError("LiDAR has no observed cells.")


def configure_cuda_inference(torch):
    expected = CUDA_INFERENCE_POLICY["cublas_workspace_config"]
    workspace = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
    if workspace is None:
        if torch.cuda.is_initialized():
            raise RuntimeError("Configure BEV reproducibility before CUDA initialization; restart this process.")
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = expected
    elif workspace != expected:
        raise ValueError(f"BEV inference requires CUBLAS_WORKSPACE_CONFIG={expected}")
    torch.use_deterministic_algorithms(True, warn_only=False)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    return dict(CUDA_INFERENCE_POLICY)


def require_cuda_inference_policy(torch):
    if (not torch.are_deterministic_algorithms_enabled()
            or torch.is_deterministic_algorithms_warn_only_enabled()
            or torch.backends.cudnn.benchmark
            or not torch.backends.cudnn.deterministic
            or os.environ.get("CUBLAS_WORKSPACE_CONFIG") != CUDA_INFERENCE_POLICY["cublas_workspace_config"]):
        raise RuntimeError("BEV deterministic inference policy changed after initialization.")


class BEVPredictor:
    def __init__(self, checkpoint=None):
        checkpoint = Path(checkpoint or os.environ.get("DAMOS_BEV_CHECKPOINT",CHECKPOINT))
        import torch
        self.torch = torch
        # Each service/audit process must choose the same kernels before its first forward.
        self.inference_policy = configure_cuda_inference(torch)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA required; no mock or CPU model fallback.")
        torch.set_num_threads(2)
        sys.path.insert(0, str(VENDOR))
        from models_bev import compile_model_bev
        cfg = json.loads((VENDOR / "prism_v2.json").read_text())
        mc = cfg["model"]
        grid = {"xbound": [-40,40,.4], "ybound": [-40,40,.4], "zbound": [-1,5.4,.4], "dbound": [4,45,1.]}
        aug = {"final_dim": (128,320)}
        kw = dict(voxel_featC=32, num_classes=17, T_past=4, T_future=1,
                  patch_size=4, backbone_pretrained=False, tc_enabled=False,
                  ugafr_enabled=True, two_pass_enabled=False, motion_head_loss_w=0.,
                  gate_policy="ugafr", lidar_fusion=True, lidar_in_ch=6)
        for key in ("use_spatial_backbone", "spatial_backbone_blocks", "backbone_checkpoint",
                    "use_ego_warping", "mamba_n_stages", "mamba_spatial_layers",
                    "mamba_temporal_layers", "scan_mode", "gate_topk", "zpc_tau_start",
                    "zpc_tau_end", "zpc_tau_anneal_steps", "zpc_dyn_gate_thresh"):
            if key in mc:
                kw[key] = mc[key]
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        self.step = int(state["step"])
        self.calibration = state.get("occupancy_calibration")
        self.ground_guard = state.get("ground_guard")
        self.fusion_mode = state.get("fusion_mode","weighted")
        from bev_fusion import FUSION_MODES
        if self.fusion_mode not in FUSION_MODES:
            raise ValueError("Unsupported checkpoint fusion mode.")
        if self.calibration is not None:
            from bev_lidar_calibration import validate_calibration
            validate_calibration(self.calibration)
        if self.ground_guard is not None:
            from bev_ground_guard import validate_ground_guard
            validate_ground_guard(self.ground_guard)
        weights = {k.removeprefix("module."): v for k, v in state["model"].items()}
        # train_bev applies model config after wrapper arguments: TC-STOP is present
        # in this checkpoint despite viz_carla disabling it with strict=False.
        kw.update({key: value for key, value in mc.items() if not key.startswith("_")})
        kw.update(backbone_pretrained=False, T_past=4, T_future=1,
                  lidar_fusion=True, lidar_in_ch=6,
                  tc_enabled=any(key.startswith("tcstop.") for key in weights))
        self.model = compile_model_bev(grid, aug, **kw)
        self.model.load_state_dict(weights, strict=True)
        # The training curriculum step is nonpersistent; step=0 disables fine refinement.
        self.model.set_step(self.step)
        self.model.cuda().eval()
        self.checkpoint_hash = hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest()

    def infer(self, inputs):
        torch = self.torch
        require_cuda_inference_policy(torch)
        start = time.perf_counter_ns()
        validate_inputs(inputs)
        tensors = {name: torch.from_numpy(np.ascontiguousarray(inputs[name])).cuda()
                   for name in INPUT_SHAPES}
        torch.cuda.synchronize()
        transfer_end = time.perf_counter_ns()
        torch.cuda.reset_peak_memory_stats()
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
            logits, aux = self.model(tensors["imgs"], tensors["rots"], tensors["trans"],
                                    tensors["intrins"], tensors["post_rots"], tensors["post_trans"],
                                    ego2globals=tensors["ego2globals"], lidar_bev=tensors["lidar_bev"],
                                    return_aux=True)
            probabilities = logits[0, 0].float().softmax(0).cpu().numpy()
        torch.cuda.synchronize()
        calibration_start = time.perf_counter_ns()
        probabilities = probabilities.astype(np.float16)
        geometry = None
        if self.calibration is not None or self.ground_guard is not None:
            from bev_lidar_calibration import geometry_features
            geometry = geometry_features(inputs["lidar_bev"])
        if self.calibration is not None:
            from bev_lidar_calibration import apply_calibration
            probabilities = apply_calibration({"probabilities":probabilities,
                "valid":inputs["lidar_bev"][0,0]>0},self.calibration,geometry=geometry)["probabilities"]
        calibration_end = time.perf_counter_ns()
        guard_changed = 0
        guard_reference = None
        if self.ground_guard is not None:
            if os.environ.get("DAMOS_BEV_AUDIT_GROUND_GUARD") == "1":
                guard_reference = probabilities.copy()
            from bev_ground_guard import apply_ground_guard
            guarded, changed = apply_ground_guard({"probabilities": probabilities,
                "valid": inputs["lidar_bev"][0, 0] > 0}, inputs["lidar_bev"],
                self.ground_guard, geometry=geometry)
            probabilities = guarded["probabilities"]
            guard_changed = int(changed.sum())
        end = time.perf_counter_ns()
        if probabilities.shape != (17, 200, 200) or not np.isfinite(probabilities).all():
            raise ValueError("Invalid actual BEV model output.")
        result = {"probabilities": probabilities.astype(np.float16),
                  "valid": (inputs["lidar_bev"][0, 0] > 0).astype(np.uint8),
                  "pose": inputs["ego2globals"][0, -1].copy()}
        if guard_reference is not None:
            # Audit-only pre-filter output from this exact CUDA forward; not sent.
            result["pre_ground_guard_probabilities"] = guard_reference
        timing = {"input_to_output_ms": (end-start)/1e6, "inference_and_readback_ms": (end-transfer_end)/1e6,
                  "occupancy_calibration_ms":(calibration_end-calibration_start)/1e6,
                  "occupancy_calibration_enabled":self.calibration is not None,"fusion_mode":self.fusion_mode,
                  "ground_guard_enabled":self.ground_guard is not None,
                  "ground_guard_ms":(end-calibration_end)/1e6,"ground_guard_changed_cells":guard_changed,
                  "ground_guard_same_forward_reference_saved":guard_reference is not None,
                  "peak_allocated_bytes": torch.cuda.max_memory_allocated(), "checkpoint_step": self.step,
                  "selected_refinement_patches": aux["n_sel_patches"], "checkpoint_sha256": self.checkpoint_hash,
                  "inference_policy": dict(self.inference_policy)}
        return result, timing


def render_bev(probabilities, valid=None, size=600):
    import cv2
    classes = probabilities.argmax(0)
    rgb = COLORS[classes]
    if valid is not None:
        rgb = rgb.copy(); rgb[~valid.astype(bool)] = (28, 28, 28)
    # Forward x up, left y left. Arrays are indexed (x,y), not image (y,x).
    image = np.ascontiguousarray(rgb[::-1, ::-1, ::-1])
    image = cv2.resize(image, (size, size), interpolation=cv2.INTER_NEAREST)
    cv2.drawMarker(image, (size//2, size//2), (255,255,255), cv2.MARKER_TRIANGLE_UP, 15, 2)
    return image


def main():
    import argparse
    import cv2
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with np.load(args.input, allow_pickle=False) as data:
        inputs = {key: data[key] for key in data.files}
    predictor = BEVPredictor()
    result, timing = predictor.infer(inputs)
    np.savez_compressed(args.output / "bev.npz", **result)
    cv2.imwrite(str(args.output / "bev_raw.png"), render_bev(result["probabilities"]))
    cv2.imwrite(str(args.output / "bev_observed.png"), render_bev(result["probabilities"], result["valid"]))
    (args.output / "timing.json").write_text(json.dumps(timing, indent=2))
    print(json.dumps(timing), flush=True)


if __name__ == "__main__":
    main()
