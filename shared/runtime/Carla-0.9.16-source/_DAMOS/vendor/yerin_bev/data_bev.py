"""
data_bev.py — BEVForecastDataset
────────────────────────────────────────────────────────────────────────
preprocess_bev_gt.py 로 만든 오프라인 NPZ를 로드하는 Dataset.

[기존 data_bev.py 대비 변경사항]
  FIX [CRITICAL] 생성자 시그니처 불일치
    기존: BEVForecastDataset(nusc, is_train, data_aug_conf, grid_conf, bev_root, split, ...)
    수정: BEVForecastDataset(bev_root, split, nusc_version, nusc_root,
                              data_aug_conf, grid_conf, past_len, future_len, is_train)
    → train_bev.py 호출과 완전 일치

  FIX [HIGH] NuscData 상속 제거
    data_yr.py / NuscData 의존성 완전 제거 — standalone 구현
    nuScenes 공식 train/val split, 이미지 로딩, augmentation 직접 구현

  FIX [MEDIUM] motion_patch shape 명시
    preprocess_bev_gt.py 저장 형식: (T_future-1, Z, Xp, Yp) float32
    UGAFR2DGateSupervisor 에서 bilinear upsample 후 (X, Y) 적용

__getitem__ 반환 키 (train_bev.py 배치 키와 1:1 대응):
    imgs:            (T_past, N, 3, fH, fW)        float32
    rots:            (T_past, N, 3, 3)              float32
    trans:           (T_past, N, 3)                 float32
    intrins:         (T_past, N, 3, 3)              float32
    post_rots:       (T_past, N, 3, 3)              float32
    post_trans:      (T_past, N, 3)                 float32
    bev_cls_future:  (T_future, X, Y)   int64       0~16 or 255(ignore)
    bev_mask_future: (T_future, X, Y)   uint8       1=valid
    motion_patch:    (T_future-1, Z, Xp, Yp) float32
    ego2globals:     (T_past, 4, 4)     float32
    t0_token:        str
"""

from __future__ import annotations

import os
import math
import random
from pathlib import Path
from typing import Optional, List, Tuple

import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader
from torch.utils.data.dataloader import default_collate
from torch.utils.data.distributed import DistributedSampler

from PIL import Image
import torchvision.transforms.functional as TF

from nuscenes.nuscenes import NuScenes
from nuscenes.utils.splits import create_splits_scenes
from pyquaternion import Quaternion


# ──────────────────────────────────────────────────────────────────────
#  Module-level collate_fn
#  — 반드시 모듈 최상위에 있어야 DataLoader worker에서 pickle 가능
# ──────────────────────────────────────────────────────────────────────

def bev_collate_fn(batch):
    """
    기본 collate + voxel_cls_future=None 처리.

    voxel_cls_future는 val split에서 --save_voxel_gt 플래그로 생성된
    경우에만 존재한다.  None이 섞인 배치를 DataLoader가 처리할 수
    있도록 먼저 꺼낸 뒤 default_collate에 나머지를 넘긴다.

    * 모든 샘플이 None  → collated["voxel_cls_future"] = None
    * 전부 Tensor       → torch.stack(vox_list)
    """
    vox_list = [s.pop("voxel_cls_future", None) for s in batch]
    collated = default_collate(batch)
    if all(v is None for v in vox_list):
        collated["voxel_cls_future"] = None
    elif all(torch.is_tensor(v) for v in vox_list):
        collated["voxel_cls_future"] = torch.stack(vox_list)
    else:
        # Mixed optional voxel GT in a single batch is ambiguous for 3D eval.
        # Keep 2D training/validation alive and disable voxel metric for this batch.
        collated["voxel_cls_future"] = None
    return collated


# ──────────────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────────────

CAMS_6 = [
    "CAM_FRONT_LEFT", "CAM_FRONT", "CAM_FRONT_RIGHT",
    "CAM_BACK_LEFT",  "CAM_BACK",  "CAM_BACK_RIGHT",
]

IMG_MEAN = (0.485, 0.456, 0.406)
IMG_STD  = (0.229, 0.224, 0.225)


# ──────────────────────────────────────────────────────────────────────
#  Augmentation helpers
# ──────────────────────────────────────────────────────────────────────

def _sample_aug(data_aug_conf: dict, is_train: bool) -> dict:
    """학습/검증 augmentation 파라미터 샘플링 (6 카메라 공유)."""
    H, W = data_aug_conf["H"], data_aug_conf["W"]
    fH, fW = data_aug_conf["final_dim"]

    if is_train:
        resize      = random.uniform(*data_aug_conf["resize_lim"])
        newW, newH  = int(W * resize), int(H * resize)
        crop_h      = max(0, int((1 - random.uniform(*data_aug_conf["bot_pct_lim"])) * newH) - fH)
        crop_w      = int(random.uniform(0, max(0, newW - fW)))
        flip        = data_aug_conf.get("rand_flip", False) and random.random() > 0.5
        rotate      = random.uniform(*data_aug_conf["rot_lim"])
    else:
        resize      = max(data_aug_conf["resize_lim"])
        newW, newH  = int(W * resize), int(H * resize)
        crop_h      = max(0, int((1 - data_aug_conf["bot_pct_lim"][0]) * newH) - fH)
        crop_w      = max(0, newW - fW) // 2
        flip, rotate = False, 0.0

    return {
        "resize":      resize,
        "resize_dims": (newW, newH),
        "crop":        (crop_w, crop_h, crop_w + fW, crop_h + fH),
        "flip":        flip,
        "rotate":      rotate,
    }


def _img_transform(img: Image.Image, aug: dict):
    """단일 이미지 augmentation → (tensor, post_rot 3×3, post_tran 3)."""
    post_rot  = np.eye(3, dtype=np.float32)
    post_tran = np.zeros(3, dtype=np.float32)

    img = img.resize(aug["resize_dims"], resample=Image.BILINEAR)
    post_rot[0, 0] = post_rot[1, 1] = aug["resize"]

    x0, y0, x1, y1 = aug["crop"]
    img = img.crop((x0, y0, x1, y1))
    post_tran[0] -= x0
    post_tran[1] -= y0

    if aug["flip"]:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
        post_rot[0, 0]  = -post_rot[0, 0]
        post_tran[0]    = img.width - post_tran[0]

    if aug["rotate"] != 0.0:
        img   = img.rotate(aug["rotate"])
        theta = math.radians(aug["rotate"])
        c, s  = math.cos(theta), math.sin(theta)
        R2    = np.array([[c, -s], [s, c]], dtype=np.float32)
        cx, cy = img.width / 2.0, img.height / 2.0
        shift = np.array([cx - R2[0,0]*cx - R2[0,1]*cy,
                          cy - R2[1,0]*cx - R2[1,1]*cy], dtype=np.float32)
        post_tran[:2]    = R2 @ post_tran[:2] + shift
        post_rot[:2, :2] = R2 @ post_rot[:2, :2]

    img_t = TF.to_tensor(img)
    img_t = TF.normalize(img_t, IMG_MEAN, IMG_STD)
    return img_t, post_rot, post_tran


# ──────────────────────────────────────────────────────────────────────
#  nuScenes pose / cam helpers
# ──────────────────────────────────────────────────────────────────────

def _pose_to_mat44(pose_rec: dict) -> np.ndarray:
    q = Quaternion(pose_rec["rotation"])
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = q.rotation_matrix
    T[:3,  3] = pose_rec["translation"]
    return T.astype(np.float32)


def _get_ego2global(nusc: NuScenes, sample_rec: dict) -> np.ndarray:
    sd   = nusc.get("sample_data", sample_rec["data"]["LIDAR_TOP"])
    ep   = nusc.get("ego_pose",    sd["ego_pose_token"])
    return _pose_to_mat44(ep)


def _get_cam_data(nusc: NuScenes, sample_rec: dict, cam_name: str, aug: dict):
    """Returns (img_t, rot, tran, intrin, post_rot, post_tran)."""
    sd  = nusc.get("sample_data",       sample_rec["data"][cam_name])
    cs  = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])

    rot    = Quaternion(cs["rotation"]).rotation_matrix.astype(np.float32)
    tran   = np.array(cs["translation"],       dtype=np.float32)
    intrin = np.array(cs["camera_intrinsic"],  dtype=np.float32)

    img_path        = os.path.join(nusc.dataroot, sd["filename"])
    img             = Image.open(img_path).convert("RGB")
    img_t, pr, pt   = _img_transform(img, aug)

    return img_t, rot, tran, intrin, pr, pt


# ──────────────────────────────────────────────────────────────────────
#  train/val split
# ──────────────────────────────────────────────────────────────────────

def _get_scenes(nusc: NuScenes, is_train: bool) -> set:
    splits = create_splits_scenes()
    if "mini" in nusc.version:
        key = "mini_train" if is_train else "mini_val"
    else:
        key = "train" if is_train else "val"
    return set(splits[key])


# ──────────────────────────────────────────────────────────────────────
#  BEVForecastDataset
# ──────────────────────────────────────────────────────────────────────

class BEVForecastDataset(Dataset):
    """
    오프라인 전처리된 BEV GT(npz)를 로드하는 Dataset.

    train_bev.py 호출 형식 (keyword-only):
        BEVForecastDataset(
            bev_root       = ...,
            split          = "train",
            nusc_version   = "v1.0-trainval",
            nusc_root      = ...,
            data_aug_conf  = ...,
            grid_conf      = ...,
            past_len       = 6,
            future_len     = 10,
            is_train       = True,
        )
    """

    def __init__(
        self,
        bev_root:        str,
        split:           str,
        nusc_version:    str,
        nusc_root:       str,
        data_aug_conf:   dict,
        grid_conf:       dict,
        past_len:        int  = 6,
        future_len:      int  = 10,
        is_train:        bool = True,
        cams:            List[str] = None,
        token_list_file: Optional[str] = None,
    ):
        self.data_aug_conf = data_aug_conf
        self.grid_conf     = grid_conf
        self.past_len      = past_len
        self.future_len    = future_len
        self.is_train      = is_train
        self.split         = split
        self.cams          = cams or data_aug_conf.get("cams", CAMS_6)

        self.npz_dir = Path(bev_root) / split / "npz"
        if not self.npz_dir.exists():
            raise FileNotFoundError(
                f"npz_dir not found: {self.npz_dir}\n"
                f"먼저 preprocess_bev_gt.py --split {split} 을 실행하세요."
            )

        print(f"[BEVForecastDataset/{split}] NuScenes {nusc_version} loading...")
        self.nusc   = NuScenes(version=nusc_version, dataroot=nusc_root, verbose=False)
        self.scenes = _get_scenes(self.nusc, is_train)

        self.ixes = self._build_seq_index()

        # ── npz 파일로 필터 ──
        if token_list_file is not None:
            valid_tokens = set(Path(token_list_file).read_text().split())
        else:
            valid_tokens = {p.stem for p in self.npz_dir.glob("*.npz")}

        before = len(self.ixes)
        self.ixes = [x for x in self.ixes if x["t0"]["token"] in valid_tokens]
        after     = len(self.ixes)
        print(
            f"[BEVForecastDataset/{split}] "
            f"seq={after}/{before}  npz={len(valid_tokens)}  "
            f"cams={len(self.cams)}"
        )

    # ── Sequence index ────────────────────────────────────────────────

    def _build_seq_index(self) -> List[dict]:
        seq_items = []
        for scene in self.nusc.scene:
            if scene["name"] not in self.scenes:
                continue
            samples, token = [], scene["first_sample_token"]
            while token:
                rec = self.nusc.get("sample", token)
                samples.append(rec)
                token = rec["next"]
            if len(samples) < self.past_len + self.future_len:
                continue
            for i in range(self.past_len - 1, len(samples) - self.future_len):
                seq_items.append({
                    "t0":     samples[i],
                    "past":   samples[i - (self.past_len - 1): i + 1],
                    "future": samples[i + 1: i + 1 + self.future_len],
                })
        return seq_items

    # ── Dataset interface ─────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self.ixes)

    def __getitem__(self, index: int) -> dict:
        item  = self.ixes[index]
        past  = item["past"]
        future= item["future"]
        token = item["t0"]["token"]
        aug   = _sample_aug(self.data_aug_conf, self.is_train)

        # ── 카메라 데이터 ──
        imgs_l, rots_l, trans_l, intrins_l, pr_l, pt_l = [], [], [], [], [], []
        for rec in past:
            i_list, r_list, t_list, k_list, pr_list, pt_list = [], [], [], [], [], []
            for cam in self.cams:
                img_t, rot, tran, intrin, pr, pt = _get_cam_data(self.nusc, rec, cam, aug)
                i_list.append(img_t)
                r_list.append(torch.from_numpy(rot))
                t_list.append(torch.from_numpy(tran))
                k_list.append(torch.from_numpy(intrin))
                pr_list.append(torch.from_numpy(pr))
                pt_list.append(torch.from_numpy(pt))
            imgs_l.append(torch.stack(i_list))    # (N, 3, fH, fW)
            rots_l.append(torch.stack(r_list))    # (N, 3, 3)
            trans_l.append(torch.stack(t_list))   # (N, 3)
            intrins_l.append(torch.stack(k_list)) # (N, 3, 3)
            pr_l.append(torch.stack(pr_list))     # (N, 3, 3)
            pt_l.append(torch.stack(pt_list))     # (N, 3)

        imgs      = torch.stack(imgs_l)      # (T, N, 3, fH, fW)
        rots      = torch.stack(rots_l)      # (T, N, 3, 3)
        trans     = torch.stack(trans_l)     # (T, N, 3)
        intrins   = torch.stack(intrins_l)   # (T, N, 3, 3)
        post_rots = torch.stack(pr_l)        # (T, N, 3, 3)
        post_trans= torch.stack(pt_l)        # (T, N, 3)

        # ── ego2global ──
        ego2globals = torch.from_numpy(
            np.stack([_get_ego2global(self.nusc, rec) for rec in past], axis=0)
        )   # (T_past, 4, 4)

        # ── BEV GT ──
        data     = np.load(self.npz_dir / f"{token}.npz")
        bev_cls  = torch.from_numpy(data["bev_cls_future"].astype(np.int64))    # (T, X, Y)
        bev_mask = torch.from_numpy(data["bev_mask_future"].astype(np.uint8))   # (T, X, Y)
        mp       = torch.from_numpy(data["motion_patch"].astype(np.float32))    # (T-1, Z, Xp, Yp)

        # ── 3D voxel GT (optional) ──────────────────────────────────────
        # --save_voxel_gt 플래그로 생성된 val npz에만 존재한다.
        # None을 dict에 넣으면 default_collate가 TypeError를 발생시키므로
        # 키 자체를 포함하지 않는다. 로더 쪽에서 batch.get("voxel_cls_future")로
        # 안전하게 접근한다.
        item = {
            "imgs":            imgs,
            "rots":            rots,
            "trans":           trans,
            "intrins":         intrins,
            "post_rots":       post_rots,
            "post_trans":      post_trans,
            "bev_cls_future":  bev_cls,
            "bev_mask_future": bev_mask,
            "motion_patch":    mp,
            "ego2globals":     ego2globals,
            "t0_token":        token,
        }
        # [corridor] 미래 ego pose. 평가 전용 도구이며 모델 입력으로는 절대
        #   전달되지 않는다 (ego safety corridor를 GT 경로로 정의하기 위함).
        #   nuScenes 조회가 샘플당 T_future회 추가되므로 기본 OFF.
        if getattr(self, "return_future_ego", False):
            item["future_ego2globals"] = torch.from_numpy(
                np.stack([_get_ego2global(self.nusc, rec) for rec in future], axis=0)
            )   # (T_future, 4, 4)
        if "voxel_cls_future" in data:
            item["voxel_cls_future"] = torch.from_numpy(
                data["voxel_cls_future"].astype(np.int64)
            )  # (T, Z, X, Y)
        return item

    def __repr__(self) -> str:
        return (
            f"BEVForecastDataset(split={self.split}, n={len(self)}, "
            f"T_past={self.past_len}, T_future={self.future_len}, "
            f"cams={len(self.cams)}, npz={self.npz_dir})"
        )


# ──────────────────────────────────────────────────────────────────────
#  compile helper
# ──────────────────────────────────────────────────────────────────────

def compile_data_bev(
    bev_root:         str,
    nusc_version:     str,
    nusc_root:        str,
    data_aug_conf:    dict,
    grid_conf:        dict,
    bsz:              int,
    nworkers:         int,
    past_len:         int  = 6,
    future_len:       int  = 10,
    distributed:      bool = False,
    rank:             int  = 0,
    world_size:       int  = 1,
    train_token_file: Optional[str] = None,
    val_token_file:   Optional[str] = None,
):
    """trainloader, valloader, train_sampler, val_sampler 반환."""

    def _make(split, is_train, token_file):
        return BEVForecastDataset(
            bev_root=bev_root, split=split,
            nusc_version=nusc_version, nusc_root=nusc_root,
            data_aug_conf=data_aug_conf, grid_conf=grid_conf,
            past_len=past_len, future_len=future_len,
            is_train=is_train, token_list_file=token_file,
        )

    traindata = _make("train", True,  train_token_file)
    valdata   = _make("val",   False, val_token_file)

    if distributed:
        train_sampler = DistributedSampler(
            traindata, num_replicas=world_size, rank=rank, shuffle=True,  drop_last=True)
        val_sampler   = DistributedSampler(
            valdata,   num_replicas=world_size, rank=rank, shuffle=False, drop_last=False)
        train_shuffle = False
    else:
        train_sampler = val_sampler = None
        train_shuffle = True

    def _loader(ds, shuffle, sampler, drop_last):
        # bev_collate_fn: 모듈 최상위 함수 — worker pickle 가능
        return DataLoader(ds, batch_size=bsz, shuffle=shuffle, sampler=sampler,
                          num_workers=nworkers, drop_last=drop_last, pin_memory=True,
                          persistent_workers=(nworkers > 0),
                          collate_fn=bev_collate_fn)

    return (
        _loader(traindata, train_shuffle, train_sampler, True),
        _loader(valdata,   False,          val_sampler,   False),
        train_sampler, val_sampler,
    )


# ──────────────────────────────────────────────────────────────────────
#  Sanity check
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--bev_root",     required=True)
    p.add_argument("--nusc_root",    required=True)
    p.add_argument("--nusc_version", default="v1.0-mini")
    p.add_argument("--split",        default="val")
    p.add_argument("--n",            type=int, default=3)
    args = p.parse_args()

    aug_conf = {
        "cams": CAMS_6, "Ncams": 6,
        "H": 900, "W": 1600, "final_dim": (224, 480),
        "resize_lim": (0.386, 0.55), "bot_pct_lim": (0.0, 0.22),
        "rot_lim": (0.0, 0.0), "rand_flip": False,
    }
    grid_conf = {
        "xbound": [-40.0, 40.0, 0.4], "ybound": [-40.0, 40.0, 0.4],
        "zbound": [-1.0,  5.4,  0.4], "dbound": [4.0, 45.0, 1.0],
    }

    ds = BEVForecastDataset(
        bev_root=args.bev_root, split=args.split,
        nusc_version=args.nusc_version, nusc_root=args.nusc_root,
        data_aug_conf=aug_conf, grid_conf=grid_conf,
        past_len=6, future_len=10, is_train=False,
    )
    print(ds)
    for i in range(min(args.n, len(ds))):
        s = ds[i]
        print(f"\n[{i}] {s['t0_token']}")
        for k, v in s.items():
            if isinstance(v, torch.Tensor):
                print(f"  {k:20s}: {tuple(v.shape)} {v.dtype}")
            else:
                print(f"  {k:20s}: {v}")
        # shape 검증
        T, N = ds.past_len, len(ds.cams)
        fH, fW = aug_conf["final_dim"]
        assert s["imgs"].shape      == (T, N, 3, fH, fW), s["imgs"].shape
        assert s["ego2globals"].shape == (T, 4, 4),         s["ego2globals"].shape
        assert s["bev_cls_future"].shape[0]  == ds.future_len
        assert s["bev_mask_future"].shape[0] == ds.future_len
        print("  ✓ shapes OK")
    print("\nAll checks passed.")