"""
models_bev_v2.py  —  4D BEV Semantic Segmentation (ver.2)
==========================================================
[ver.2 구조 변경]

논문 기여도 재구성:
  Main:
    1. Dense BEV forecasting (planning-oriented representation)
    2. Coarse-to-Fine Mamba (efficient long-horizon forecasting)
    3. UGAFR — Uncertainty-Guided Adaptive Fine Refinement
       for small dynamic object preservation
  Implementation detail (main contribution에서 제외):
    - ZPC   (alignment analysis component)
    - TC-STOP (dynamic initialization, auxiliary)

[NEW] GatePolicyRouter:
  6가지 gate policy를 중앙화 — Exp 1/2/3 직접 실행 가능
    "coarse_only" / "all_fine" / "random" / "entropy" / "motion" / "ugafr"

[NEW] _select_patches():
  gate_policy 파라미터 기반 patch 선택 dispatch
  (기존 _compute_safety_net_idx 대체)

[NEW] patch_hit_rate_{small,dyn} in _last_aux:
  UGAFR selection quality → Exp 2 테이블 자동 생성

[ZPC-v2 유지]:
  Soft-ZPC (tau annealing) + Z-axis context conv (3×1×1)
  + Height-prior Lvox (class별 물리 높이 범위만 감독)
  + Dynamic-selective gating (FG 확률 기반 ZPC/mag 혼합)
"""

import math
import torch
from torch import nn
import torch.nn.functional as F
from torch.amp import autocast
from torch.utils.checkpoint import checkpoint as grad_checkpoint

# OccProphet-style dense refiner baseline (외부 비교용, 선택적 import)
try:
    from baseline_occprophet_refiner import OccProphetDenseRefiner2D
    HAS_OCCPROPHET_BASELINE = True
except ImportError:
    HAS_OCCPROPHET_BASELINE = False

try:
    import timm
    HAS_TIMM = True
except ImportError:
    HAS_TIMM = False

# ── 동적 객체 클래스 상수 (논문/실험 전체에서 동일하게 사용) ──────────
# UGAFR의 핵심 타깃: nuScenes에서 상대적으로 충분한 동적 객체 계열
DYN_OBJ_CLASSES   = [3, 4, 10]          # bus, car, truck
TARGET_DYN_CLASSES = DYN_OBJ_CLASSES    # 명시적 alias: UGAFR target classes

# 참고 지표용 VRU 계열
VRU_CLASSES       = [2, 7, 6]           # bicycle, pedestrian, motorcycle

# 일반 movable/dynamic 계열. train/val 로그의 dyn_mIoU 등에서 사용 가능.
MOVABLE_CLASSES   = [2, 3, 4, 5, 6, 7, 8, 9, 10]
DYN_CLASSES       = MOVABLE_CLASSES


# ──────────────────────────────────────────────────────────────────────
#  Tools
# ──────────────────────────────────────────────────────────────────────

def gen_dx_bx(xbound, ybound, zbound):
    dx = torch.tensor([row[2] for row in [xbound, ybound, zbound]])
    bx = torch.tensor([row[0] + row[2] / 2.0 for row in [xbound, ybound, zbound]])
    nx = torch.LongTensor(
        [round((row[1] - row[0]) / row[2]) for row in [xbound, ybound, zbound]]
    )
    return dx, bx, nx


def cumsum_trick(x, geom_feats, ranks):
    x = x.cumsum(0)
    kept = torch.ones(x.shape[0], device=x.device, dtype=torch.bool)
    kept[:-1] = ranks[1:] != ranks[:-1]
    x, geom_feats = x[kept], geom_feats[kept]
    x = torch.cat((x[:1], x[1:] - x[:-1]))
    return x, geom_feats


def _make_norm3d(norm, c, gn_groups=32):
    norm = (norm or "group").lower()
    if norm in ("group", "gn"):
        g = min(gn_groups, c)
        while c % g != 0 and g > 1: g -= 1
        return nn.GroupNorm(g, c)
    return nn.BatchNorm3d(c)


def _make_norm2d(norm, c, gn_groups=32):
    norm = (norm or "group").lower()
    if norm in ("group", "gn"):
        g = min(gn_groups, c)
        while c % g != 0 and g > 1: g -= 1
        return nn.GroupNorm(g, c)
    return nn.BatchNorm2d(c)


def _freeze_bn(m):
    for mod in m.modules():
        if isinstance(mod, (nn.BatchNorm1d, nn.BatchNorm2d, nn.BatchNorm3d)):
            mod.eval()


# ──────────────────────────────────────────────────────────────────────
#  [NEW] GatePolicyRouter — Experiment 1, 2, 3 핵심 인프라
# ──────────────────────────────────────────────────────────────────────

class GatePolicyRouter:
    """
    Centralized gate policy dispatcher.

    Policy 설명:
      "coarse_only" — Fine Mamba 없음 (legacy alias)
      "no_refinement" — independently trainable no-fine-refinement baseline
      "all_fine"    — 모든 patch refine (upper bound)
      "random"      — 무작위 TopK
      "entropy"     — 예측 불확실성 기반 TopK
      "motion"      — 움직임 크기 기반 TopK
      "ugafr"       — CE+entropy+motion+small prior (UGAFR, 제안)

    Experiment 1: coarse_only vs random vs entropy vs motion vs ugafr
    Experiment 2: gate policy × HitRate/small_mIoU/FLOPs 비교 테이블
    Experiment 3: topk_ratio sweep × policy (10/25/50/100%)
    """

    VALID_POLICIES = ("coarse_only", "no_refinement", "all_fine", "random", "entropy", "motion", "ugafr",
                      "ugafr_quota", "occprophet_dense")

    @staticmethod
    @torch.no_grad()
    def entropy_patch_scores(
        patch_features: torch.Tensor,   # (B, T, C, Xp, Yp) — coarse patch features
        decoder_fn,                     # callable: patch_feat → logits (B, T, K, Xp, Yp)
    ) -> torch.Tensor:
        """
        Entropy-based patch importance score.

        coarse logit → softmax → Shannon entropy → max over T → (B, N)
        """
        with torch.no_grad():
            logits = decoder_fn(patch_features)      # (B, T, K, Xp, Yp)
            p      = torch.softmax(logits.float(), dim=2)
            ent    = -(p * torch.log(p.clamp(1e-8))).sum(2)   # (B, T, Xp, Yp)
            score  = ent.max(dim=1).values                     # (B, Xp, Yp)
        return score.reshape(patch_features.shape[0], -1)      # (B, N)

    @staticmethod
    @torch.no_grad()
    def motion_patch_scores(
        motion_patch: torch.Tensor,   # (B, T-1, Xp, Yp) or (B, T-1, Z, Xp, Yp)
        B: int, Xp: int, Yp: int,
        device: torch.device = None,
    ) -> torch.Tensor:
        """
        Motion-based patch importance score: (B, N)
        """
        if motion_patch is None:
            dev = device or torch.device("cpu")
            return torch.zeros(B, Xp * Yp, device=dev)
        mp = motion_patch
        if mp.ndim == 5:
            mp = mp.max(dim=2).values                    # (B, T-1, Xp, Yp)
        if mp.shape[2] != Xp or mp.shape[3] != Yp:
            BT = mp.shape[0] * mp.shape[1]
            mp = F.interpolate(
                mp.reshape(BT, 1, mp.shape[2], mp.shape[3]),
                size=(Xp, Yp), mode="bilinear", align_corners=False,
            ).reshape(mp.shape[0], -1, Xp, Yp)
        return mp.float().max(dim=1).values.reshape(B, Xp * Yp)

    @staticmethod
    @torch.no_grad()
    def compute_patch_hit_rate(
        selected_flat_idx: torch.Tensor,   # (B, N_sel)
        gt_cls:            torch.Tensor,   # (B, T, X, Y)
        patch_size:        int,
        Xp: int, Yp: int,
        target_classes:    list,
        ignore_index:      int = 255,
    ) -> dict:
        """
        Patch selection hit-rate for target classes.

        HitRate = |I_fine ∩ I_target| / |I_target|

        논문 Exp 2 핵심 metric.
        Random/entropy policy보다 UGAFR hit-rate가 높으면 contribution 성립.
        """
        if selected_flat_idx is None or selected_flat_idx.numel() == 0:
            return {"hit_rate": 0.0, "n_target": 0, "n_hit": 0}

        B, T, X, Y = gt_cls.shape
        dev = gt_cls.device; ps = patch_size

        # GT → patch level (any T frame에 target이 있으면 target patch)
        tm = torch.zeros(B, T, X, Y, dtype=torch.bool, device=dev)
        for c in target_classes:
            tm |= (gt_cls == c)
        tm_p = F.max_pool2d(
            tm.float().reshape(B * T, 1, X, Y), ps, stride=ps
        ).reshape(B, T, Xp, Yp)
        tm_any = (tm_p.max(dim=1).values > 0).reshape(B, -1)   # (B, N)

        total_hit = 0; total_tgt = 0
        N_sel = selected_flat_idx.shape[1]
        for b in range(B):
            tgt_set = set(tm_any[b].nonzero(as_tuple=True)[0].cpu().tolist())
            sel_set = set(selected_flat_idx[b, :N_sel].cpu().tolist())
            total_tgt += len(tgt_set)
            total_hit += len(tgt_set & sel_set)

        return {
            "hit_rate": total_hit / max(total_tgt, 1),
            "n_target": total_tgt,
            "n_hit":    total_hit,
        }

    @staticmethod
    def select_patches(
        policy:          str,
        K:               int,
        B:               int,
        N:               int,
        device:          torch.device,
        gate_score_flat: torch.Tensor = None,   # (B, N) — UGAFR learned gate
        entropy_scores:  torch.Tensor = None,   # (B, N)
        motion_scores:   torch.Tensor = None,   # (B, N)
        tc_idx_sets:     list         = None,   # list[B] of 1D tensors
    ) -> torch.Tensor:
        """
        Policy별 patch 선택 → (B, N_sel) flat indices.

        Note: ugafr는 gate_score를 사용하므로, entropy/motion을 추가로
        gate score에 blend하는 것은 호출 측에서 처리.
        """
        assert policy in GatePolicyRouter.VALID_POLICIES

        if policy in ("coarse_only", "no_refinement"):
            return torch.empty(B, 0, device=device, dtype=torch.long)

        if policy == "all_fine":
            return torch.arange(N, device=device).unsqueeze(0).expand(B, -1).contiguous()

        K = max(1, min(K, N))

        if policy == "random":
            return torch.stack(
                [torch.randperm(N, device=device)[:K] for _ in range(B)], dim=0
            )

        if policy == "entropy":
            sc = entropy_scores if entropy_scores is not None else \
                 torch.rand(B, N, device=device)
            _, idx = torch.topk(sc.to(device), K, dim=1, largest=True, sorted=False)
            return idx

        if policy == "motion":
            sc = motion_scores if motion_scores is not None else \
                 torch.rand(B, N, device=device)
            _, idx = torch.topk(sc.to(device), K, dim=1, largest=True, sorted=False)
            return idx

        # ugafr: gate TopK ∪ TC-STOP guaranteed
        if gate_score_flat is None:
            gate_score_flat = torch.rand(B, N, device=device)
        _, setA = torch.topk(gate_score_flat, K, dim=1, largest=True, sorted=False)

        if tc_idx_sets is None:
            return setA

        idx_list = []
        for b in range(B):
            tc = tc_idx_sets[b]
            if tc is None or len(tc) == 0:
                idx_list.append(setA[b])
            else:
                merged = torch.unique(torch.cat([setA[b], tc.long()], dim=0))
                idx_list.append(merged)

        max_n = max(int(i.shape[0]) for i in idx_list)
        final = torch.zeros(B, max_n, device=device, dtype=torch.long)
        for b in range(B):
            n = int(idx_list[b].shape[0])
            final[b, :n] = idx_list[b]
            if n < max_n:
                final[b, n:] = idx_list[b][0] if n > 0 else 0
        return final


# ──────────────────────────────────────────────────────────────────────
#  Camera Encoder
# ──────────────────────────────────────────────────────────────────────

class SwinTCamEncode(nn.Module):
    def __init__(self, D, C, downsample, *, pretrained=True,
                 norm="group", gn_groups=32, img_size=(224, 224)):
        super().__init__()
        self.D = D; self.C = C; self.downsample = downsample
        assert HAS_TIMM, "pip install timm"
        self.backbone = timm.create_model(
            "swin_tiny_patch4_window7_224", pretrained=pretrained,
            features_only=True, out_indices=[2, 3], img_size=img_size,
        )
        _freeze_bn(self.backbone)
        self.up   = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=True)
        self.neck = nn.Sequential(
            nn.Conv2d(384+768, 512, 3, padding=1, bias=False),
            _make_norm2d(norm,512,gn_groups), nn.ReLU(inplace=False),
            nn.Conv2d(512, 512, 3, padding=1, bias=False),
            _make_norm2d(norm,512,gn_groups), nn.ReLU(inplace=False),
        )
        self.depthnet = nn.Conv2d(512, D + C, kernel_size=1)

    def _to_nchw(self, f):
        return f.permute(0,3,1,2).contiguous() if f.ndim==4 and f.shape[1]<32 and f.shape[-1]>=64 else f

    def forward(self, x):
        with autocast(device_type="cuda", enabled=False):
            feats = self.backbone(x.float())
        fl = self._to_nchw(feats[0]); fh = self._to_nchw(feats[1])
        fu = self.up(fh)
        if fu.shape[2:] != fl.shape[2:]:
            fu = F.interpolate(fu, size=fl.shape[2:], mode="bilinear", align_corners=True)
        out = self.depthnet(self.neck(torch.cat([fl, fu], dim=1)))
        with autocast(device_type="cuda", enabled=False):
            depth = torch.softmax(
                torch.nan_to_num(out[:,:self.D].float(), nan=0.0, posinf=50.0, neginf=-50.0).clamp(-50,50),
                dim=1,
            )
        feat = out[:, self.D:self.D+self.C].float()
        return depth.unsqueeze(1) * feat.unsqueeze(2)   # (B, C, D, fH, fW)


# ──────────────────────────────────────────────────────────────────────
#  3D Backbone / Warper
# ──────────────────────────────────────────────────────────────────────

class ResBlock3D(nn.Module):
    def __init__(self, C, norm="group", gn_groups=32):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv3d(C,C,3,padding=1,bias=False), _make_norm3d(norm,C,gn_groups), nn.ReLU(inplace=False),
            nn.Conv3d(C,C,3,padding=1,bias=False), _make_norm3d(norm,C,gn_groups),
        )
        self.act = nn.ReLU(inplace=False)
    def forward(self, x): return self.act(x + self.net(x))


class VoxelSpatialBackbone(nn.Module):
    def __init__(self, C, n=3, norm="group", gn_groups=32, ckpt=True):
        super().__init__()
        self.blocks = nn.Sequential(*[ResBlock3D(C, norm, gn_groups) for _ in range(n)])
        self.ckpt = ckpt
    def forward(self, x):
        return grad_checkpoint(self.blocks, x, use_reentrant=False) if self.ckpt and self.training else self.blocks(x)


class EgoMotionWarper(nn.Module):
    def __init__(self): super().__init__()
    @staticmethod
    @torch.no_grad()
    def _grid(Z, X, Y, dx, bx, dev):
        xs = torch.arange(X,device=dev,dtype=torch.float32)*dx[0]+bx[0]
        ys = torch.arange(Y,device=dev,dtype=torch.float32)*dx[1]+bx[1]
        zs = torch.arange(Z,device=dev,dtype=torch.float32)*dx[2]+bx[2]
        gz,gx,gy = torch.meshgrid(zs,xs,ys,indexing="ij")
        return torch.stack([gx,gy,gz],dim=-1)
    def forward(self, src, T, dx, bx, nx):
        B,C,Z,X,Y = src.shape; dev = src.device
        g = self._grid(Z,X,Y,dx,bx,dev)
        h = torch.cat([g, torch.ones(*g.shape[:-1],1,device=dev)],dim=-1)
        p = torch.bmm(h.reshape(1,-1,4).expand(B,-1,-1), T.transpose(1,2))
        xr=max(float((nx[0]-1)*dx[0]),1e-6); yr=max(float((nx[1]-1)*dx[1]),1e-6); zr=max(float((nx[2]-1)*dx[2]),1e-6)
        sg = torch.stack([
            2*(p[...,0]-bx[0])/xr-1, 2*(p[...,1]-bx[1])/yr-1, 2*(p[...,2]-bx[2])/zr-1
        ], dim=-1).reshape(B,Z,X,Y,3)
        return F.grid_sample(src, sg, mode="bilinear", padding_mode="zeros", align_corners=True)


# ──────────────────────────────────────────────────────────────────────
#  ZPC-v2: Soft priority collapse + height-prior Lvox
# ──────────────────────────────────────────────────────────────────────

_VOXEL_PRIORITY = {
    4:0,7:1,2:2,6:3,8:4,3:5,10:6,9:7,5:8,1:9,0:10,
    13:11,14:12,12:13,15:14,16:15,11:16,17:99,
}
_HEIGHT_BOUNDS_M = [
    (-1.0,5.4),(0.0,1.5),(0.0,1.8),(0.0,3.5),(0.0,1.5),(0.0,4.0),
    (0.0,1.8),(0.0,1.8),(0.0,1.0),(0.0,3.5),(0.0,3.5),
    (-1.0,0.2),(-1.0,0.5),(-0.5,0.5),(-1.0,0.5),(0.0,5.4),(0.0,5.4),
]


class ZPriorityCollapse(nn.Module):
    """
    ZPC-v2: Soft temperature-weighted aggregation.

    Implementation detail — alignment analysis component.
    실험에서는 항상 켜진 상태로 고정. ablation은 gate_policy로만.

    v2 변경:
      - hard argmin → softmax(-τ × prio) weighted sum (τ 어닐링)
      - cls_head: 1×1×1 → 1×1×1 + 3×1×1 (Z-context)
      - height_mask: class별 물리적 높이 범위만 Lvox 감독
      - dynamic-selective gate: FG 확률 기반 ZPC/mag 혼합
    """
    FG_CLASS_IDS = DYN_OBJ_CLASSES           # bus, car, truck

    def __init__(self, C=64, num_classes=17, warm_steps=5000,
                 tau_start=0.5, tau_end=8.0, tau_anneal=80000,
                 dyn_thresh=0.25, z_start=-1.0, z_res=0.4, Z=16):
        super().__init__()
        self.num_classes = num_classes
        self.warm_steps  = warm_steps
        self.tau_start   = tau_start; self.tau_end = tau_end; self.tau_anneal = tau_anneal
        self.dyn_thresh  = dyn_thresh

        half = max(C // 2, 16); g = min(8, half)
        self.cls_head = nn.Sequential(
            nn.Conv3d(C, half, 1, bias=False), nn.GroupNorm(g, half), nn.ReLU(inplace=False),
            nn.Conv3d(half, half, (3,1,1), padding=(1,0,0), bias=False), nn.GroupNorm(g, half), nn.ReLU(inplace=False),
            nn.Conv3d(half, num_classes, 1, bias=True),
        )
        prio = torch.full((num_classes+1,), 999.0)
        for k, p in _VOXEL_PRIORITY.items():
            if k <= num_classes: prio[k] = float(p)
        self.register_buffer("prio_lut", prio)

        hm = torch.zeros(num_classes, Z)
        for ci, (zlo, zhi) in enumerate(_HEIGHT_BOUNDS_M[:num_classes]):
            blo = max(0, int(math.floor((zlo-z_start)/z_res)))
            bhi = min(Z-1, int(math.ceil((zhi-z_start)/z_res)))
            hm[ci, blo:bhi+1] = 1.0
        self.register_buffer("height_mask", hm)
        self.register_buffer("_step", torch.zeros((), dtype=torch.long))

        with torch.no_grad():
            bc = self.cls_head[-1].bias
            bc[11] -= 3.0
            for c in [4,2,6,8]:
                if c < num_classes: bc[c] += 2.0
            if 7 < num_classes: bc[7] += 4.0
            for c in [1,3,5,9,10]:
                if c < num_classes: bc[c] += 1.0

    def set_step(self, s): self._step.fill_(int(s))

    def _tau(self, s):
        return self.tau_end if s >= self.tau_anneal else \
               self.tau_start + (self.tau_end-self.tau_start)*s/max(self.tau_anneal,1)

    def forward(self, vox):
        B,C,Z,X,Y = vox.shape; s = int(self._step.item())
        cl  = self.cls_head(vox)
        pm  = self.prio_lut[cl.argmax(dim=1).clamp(0,self.num_classes)]
        tau = self._tau(s)
        sw  = torch.softmax(-tau * pm, dim=1)
        bev_pri = (vox * sw.unsqueeze(1)).sum(dim=2)
        with torch.no_grad():
            fp  = torch.softmax(cl.detach().float(), dim=1)
            fi  = torch.tensor(self.FG_CLASS_IDS, device=vox.device)
            dg  = torch.sigmoid((fp[:,fi].max(1).values.max(1).values - self.dyn_thresh) * 10.0)
        bev_max = vox.max(dim=2).values
        bev_zpc = dg.unsqueeze(1) * bev_pri + (1-dg.unsqueeze(1)) * bev_max
        if s < self.warm_steps:
            alpha = min(1.0, s / max(self.warm_steps, 1))
            return alpha * bev_zpc + (1-alpha) * bev_max
        return bev_zpc

    def get_cls_logits(self, vox): return self.cls_head(vox)

    def compute_height_prior_loss(self, vox, gt_bev_cls, ignore_index=255, w=0.1):
        """Height-prior Lvox: class별 물리 높이 범위 내 Z bin만 감독."""
        B,_,Z,X,Y = vox.shape; C_cls = self.num_classes
        logits = self.cls_head(vox)
        gt_2d  = (gt_bev_cls[:,0] if gt_bev_cls.dim()==4 else gt_bev_cls).long()
        valid  = gt_2d != ignore_index
        if not valid.any(): return logits.sum() * 0.0
        gt_cl  = gt_2d.clamp(0, C_cls-1)
        hv     = self.height_mask[gt_cl].permute(0,3,1,2)           # (B,Z,X,Y)
        gt_3d  = gt_2d.unsqueeze(1).expand(-1,Z,-1,-1)
        v3d    = valid.unsqueeze(1).expand(-1,Z,-1,-1) & hv.bool()
        logf   = logits.permute(0,2,3,4,1).reshape(-1,C_cls).float()
        msk    = v3d.reshape(-1)
        if not msk.any(): return logits.sum() * 0.0
        with autocast(device_type="cuda", enabled=False):
            L = F.cross_entropy(logf[msk], gt_3d.reshape(-1)[msk], reduction="mean")
        return torch.nan_to_num(L, nan=0.0, posinf=1.0) * w


# ──────────────────────────────────────────────────────────────────────
#  Mamba / SSM
# ──────────────────────────────────────────────────────────────────────

def _make_mamba(d):
    try:
        from mamba_ssm import Mamba; return Mamba(d_model=d)
    except Exception:
        from mamba_ssm.modules.mamba_simple import Mamba; return Mamba(d_model=d)


class MambaResidualBlock(nn.Module):
    def __init__(self, d, dropout=0.0):
        super().__init__()
        self.norm = nn.LayerNorm(d); self.mamba = _make_mamba(d); self.drop = nn.Dropout(dropout)
    def forward(self, x): return x + self.drop(self.mamba(self.norm(x)))


class BEVPatchEmbed2D(nn.Module):
    def __init__(self, Cin, Cout, ps=8, norm="group", gn_groups=32):
        super().__init__()
        self.proj = nn.Conv2d(Cin, Cout, ps, stride=ps, bias=False)
        self.norm = _make_norm2d(norm, Cout, gn_groups); self.act = nn.ReLU(inplace=False)
    def forward(self, bev):
        B,T,C,X,Y = bev.shape
        x = self.act(self.norm(self.proj(bev.reshape(B*T,C,X,Y))))
        _,Co,Xp,Yp = x.shape; return x.reshape(B,T,Co,Xp,Yp)


def _xy2h(n, x, y):
    d=0; s=n//2
    while s>0:
        rx=1 if x&s else 0; ry=1 if y&s else 0
        d+=s*s*((3*rx)^ry)
        if not ry:
            if rx: x,y=s-1-x,s-1-y
            x,y=y,x
        s//=2
    return d


def build_hilbert_lut(H, W):
    n=1
    while n<max(H,W): n*=2
    pos = sorted((_xy2h(n,c,r), r*W+c) for r in range(H) for c in range(W))
    return torch.tensor([p[1] for p in pos], dtype=torch.long)


class SpatialMamba2DLayer(nn.Module):
    def __init__(self, C, Xp, Yp, n=2, dropout=0.0, scan_mode="hilbert"):
        super().__init__()
        self.Xp=Xp; self.Yp=Yp; self.N=Xp*Yp; self.scan_mode=scan_mode
        self.blocks=nn.ModuleList([MambaResidualBlock(C,dropout) for _ in range(n)])
        self.norm=nn.LayerNorm(C); self._cache={}
        if scan_mode=="hilbert": self._hlut=build_hilbert_lut(Xp,Yp)

    @torch.no_grad()
    def _orders(self, dev):
        k=f"{self.scan_mode}_{dev}"
        if k in self._cache: return self._cache[k]
        N=self.N
        def inv(o):
            i=torch.empty_like(o); i[o]=torch.arange(N,device=dev); return i
        if self.scan_mode=="ss2d":
            b=torch.arange(N,device=dev).reshape(self.Xp,self.Yp)
            ords=[(b.reshape(-1),inv(b.reshape(-1))),(b.flip(1).reshape(-1),inv(b.flip(1).reshape(-1))),
                  (b.T.reshape(-1),inv(b.T.reshape(-1))),(b.T.flip(1).reshape(-1),inv(b.T.flip(1).reshape(-1)))]
        else:
            ho=self._hlut.to(dev); hr=ho.flip(0)
            ords=[(ho,inv(ho)),(hr,inv(hr))]
        self._cache[k]=ords; return ords

    def _scan(self, h, o, io):
        y=h[:,o,:]
        for b in self.blocks: y=b(y)
        return self.norm(y)[:,io,:]

    def forward(self, x):
        B,T,C,Xp,Yp=x.shape
        h=x.permute(0,1,3,4,2).reshape(B*T,self.N,C)
        out=torch.stack([self._scan(h,o,io) for o,io in self._orders(x.device)]).mean(0)
        return out.reshape(B,T,Xp,Yp,C).permute(0,1,4,2,3).contiguous()


class TemporalMamba2DLayer(nn.Module):
    def __init__(self, C, n=2, dropout=0.0):
        super().__init__()
        self.blocks=nn.ModuleList([MambaResidualBlock(C,dropout) for _ in range(n)])
        self.norm=nn.LayerNorm(C)
    def forward(self, x):
        B,T,C,Xp,Yp=x.shape
        h=x.permute(0,3,4,1,2).reshape(B*Xp*Yp,T,C)
        for b in self.blocks: h=b(h)
        return self.norm(h).reshape(B,Xp,Yp,T,C).permute(0,3,4,1,2).contiguous()


class CoarseSpaTempMamba2D(nn.Module):
    def __init__(self, C, T_past, T_future, Xp, Yp,
                 n_stages=2, n_sp=2, n_tp=2, dropout=0.0,
                 scan_mode="hilbert", use_ckpt=True):
        super().__init__()
        self.T_past=T_past; self.T_future=T_future; self.use_ckpt=use_ckpt
        T=T_past+T_future
        self.te=nn.Embedding(T,C); self.xe=nn.Embedding(Xp,C); self.ye=nn.Embedding(Yp,C)
        self.stages=nn.ModuleList()
        for _ in range(n_stages):
            self.stages.append(SpatialMamba2DLayer(C,Xp,Yp,n_sp,dropout,scan_mode))
            self.stages.append(TemporalMamba2DLayer(C,n_tp,dropout))

    def forward(self, pp, pf):
        B,_,C,Xp,Yp=pp.shape; T=self.T_past+self.T_future; dev=pp.device
        tok=torch.cat([pp,pf],dim=1)
        tok=tok+self.te(torch.arange(T,device=dev)).reshape(1,T,C,1,1)
        tok=tok+self.xe(torch.arange(Xp,device=dev)).reshape(1,1,C,Xp,1)
        tok=tok+self.ye(torch.arange(Yp,device=dev)).reshape(1,1,C,1,Yp)
        for s in self.stages:
            tok = grad_checkpoint(s,tok,use_reentrant=False) if self.use_ckpt and self.training else s(tok)
        return tok


class CoarseGateHead2D(nn.Module):
    """[recall] 미래 프레임별로 dynamic 존재를 예측하고 max로 union을 만든다.

    변경 이유:
      기존 구현은 시간 축 평균(x.mean(dim=1)) 후 conv를 태웠다. 그러나 차량은
      5초에 수십 미터를 이동하므로, 16프레임 평균에서는 각 위치의 객체 신호가
      1/T로 희석된다 — 정확히 우리가 잡아야 할 '움직이는 객체'가 가장 심하게
      뭉개진다. 게다가 Hit_dyn의 정의는 '어느 미래 프레임에든 dynamic이면
      positive'인 union이므로, 평균 feature 하나로 궤적 전체를 맞히라는 것은
      목표와 표현이 어긋난다.

      per-frame 예측 후 max 합성은 (i) 희석을 제거하고 (ii) union 정의와 정확히
      일치하며 (iii) 프레임당 dynamic 비율(2.1%)이 union(5.4%)보다 쉬운 문제라
      학습 난이도도 낮다.
    """
    def __init__(self, C, norm="group", gn_groups=32, dropout=0.2):
        super().__init__()
        h=max(C//2,16)
        self.net=nn.Sequential(
            nn.Conv2d(C,h,3,padding=1,bias=False), _make_norm2d(norm,h,gn_groups), nn.ReLU(inplace=False),
            nn.Dropout2d(dropout), nn.Conv2d(h,1,1,bias=True),
        )

    def forward(self, x, t_past=0, return_per_frame=False):
        """x: (B, T_total, C, Xp, Yp)
           반환: union logit (B, Xp*Yp)  [, per-frame logit (B, Tf, Xp, Yp)]"""
        B,T,C,Xp,Yp = x.shape
        fut = x[:, t_past:] if t_past else x
        Tf = fut.shape[1]
        lg = self.net(fut.reshape(B*Tf, C, Xp, Yp)).squeeze(1).reshape(B, Tf, Xp, Yp)
        union = lg.max(dim=1).values.reshape(B, -1)     # union = max over future
        return (union, lg) if return_per_frame else union


class BEVForecastHead2D(nn.Module):
    """
    소형 객체 복원을 위한 경량 FPN 스타일 decoder.

    문제: 기존 single-scale conv는 patch embedding에서 소실된
          소형 객체 정보를 복원할 수 없음.
    해결:
      - 2× 중간 scale 피처 생성 후 concat → 고해상도 정보 보완
      - depthwise separable conv로 파라미터 절약
    """
    def __init__(self, C, K, norm="group", gn_groups=32):
        super().__init__()
        h  = max(C, 32)          # 채널 수 확보 (C=32면 32, C=64면 64)
        h2 = max(h // 2, 16)

        # ── detail branch: 전체 해상도에서 세밀한 패턴 포착 ──────────
        self.detail = nn.Sequential(
            nn.Conv2d(C, h, 3, padding=1, bias=False, groups=min(C, h)),  # depthwise
            nn.Conv2d(h, h, 1, bias=False),                               # pointwise
            _make_norm2d(norm, h, gn_groups),
            nn.ReLU(inplace=False),
        )
        # ── semantic branch: 넓은 receptive field ────────────────────
        self.semantic = nn.Sequential(
            nn.Conv2d(C, h, 5, padding=2, bias=False),
            _make_norm2d(norm, h, gn_groups),
            nn.ReLU(inplace=False),
        )
        # ── fusion + head ─────────────────────────────────────────────
        self.fuse = nn.Sequential(
            nn.Conv2d(h * 2, h, 1, bias=False),
            _make_norm2d(norm, h, gn_groups),
            nn.ReLU(inplace=False),
            nn.Conv2d(h, h2, 3, padding=1, bias=False),
            _make_norm2d(norm, h2, gn_groups),
            nn.ReLU(inplace=False),
            nn.Conv2d(h2, K, 1, bias=True),
        )
        # 소형 객체 클래스 bias를 약간 올려 초기 예측 bias 완화
        nn.init.zeros_(self.fuse[-1].bias)
        for c in [3, 4, 10]:   # bus, car, truck
            if c < K:
                self.fuse[-1].bias.data[c] = 0.1

    def forward(self, bev):
        B, T, C, X, Y = bev.shape
        x = bev.reshape(B * T, C, X, Y)
        det = self.detail(x)
        sem = self.semantic(x)
        out = self.fuse(torch.cat([det, sem], dim=1))
        return out.reshape(B, T, -1, X, Y)


# ──────────────────────────────────────────────────────────────────────
#  TC-STOP (implementation detail)
# ──────────────────────────────────────────────────────────────────────

class MotionEncoder2D(nn.Module):
    def __init__(self, C, norm="group", gn=32):
        super().__init__()
        self.net=nn.Sequential(
            nn.Conv2d(2*C,C,3,padding=1,bias=False), _make_norm2d(norm,C,gn), nn.ReLU(inplace=False),
            nn.Conv2d(C,1,1,bias=True),
        )
    def forward(self, a, b): return torch.sigmoid(self.net(torch.cat([a,b],dim=1))).squeeze(1)


class SoftTrackletExtractor2D(nn.Module):
    def __init__(self, K=32, nms=7): super().__init__(); self.K=K; self.nms=nms
    def forward(self, feat, pm):
        B,C,X,Y=feat.shape; K=min(self.K,X*Y); dev=feat.device
        with torch.no_grad():
            pad=self.nms//2
            pool=F.max_pool2d(pm.unsqueeze(1),self.nms,stride=1,padding=pad).squeeze(1)
            pk=(pm>=pool-1e-6).float()*pm
            _,ti=torch.topk(pk.reshape(B,-1),K,dim=1,largest=True,sorted=False)
        sc=torch.gather(pm.reshape(B,-1),1,ti)
        xi=ti//Y; yi=ti%Y; c=torch.stack([xi,yi],dim=-1).float()
        xn=2*xi.float()/max(X-1,1)-1; yn=2*yi.float()/max(Y-1,1)-1
        g=torch.stack([yn,xn],dim=-1).reshape(B,1,K,2)
        s=F.grid_sample(feat,g,mode="bilinear",padding_mode="zeros",align_corners=True)
        return c, s.reshape(B,C,K).permute(0,2,1).contiguous(), sc


class TrackletMamba(nn.Module):
    def __init__(self, C, n=3, dropout=0.0):
        super().__init__()
        self.blocks=nn.ModuleList([MambaResidualBlock(C,dropout) for _ in range(n)])
        self.norm=nn.LayerNorm(C)
    def forward(self, x):
        B,K,T,C=x.shape; h=x.reshape(B*K,T,C)
        for b in self.blocks: h=b(h)
        h=self.norm(h).reshape(B,K,T,C)
        return h, h[:,:,-1,:]


class CrossFrameAssociator2D(nn.Module):
    def __init__(self, C, nh=4, dropout=0.0):
        super().__init__()
        self.pe=nn.Linear(2,C,bias=False)
        self.attn=nn.MultiheadAttention(C,nh,dropout=dropout,batch_first=True)
        self.n1=nn.LayerNorm(C)
        self.ffn=nn.Sequential(nn.Linear(C,C*2),nn.GELU(),nn.Dropout(dropout),nn.Linear(C*2,C))
        self.n2=nn.LayerNorm(C)
    def forward(self, cf, cc, pf, pc):
        q=cf+self.pe(cc); k=pf+self.pe(pc)
        ctx,_=self.attn(q,k,pf)
        o=self.n1(cf+ctx); return self.n2(o+self.ffn(o))


class GaussianBEV2DSplatter(nn.Module):
    def __init__(self, C, radius=5, max_sig=8.0):
        super().__init__()
        self.r=radius; self.ms=max_sig; self.fp=nn.Linear(C,C,bias=False); self._oc={}
    def _offs(self, dev):
        k=str(dev)
        if k not in self._oc:
            ox=torch.arange(-self.r,self.r+1,device=dev,dtype=torch.long)
            oy=torch.arange(-self.r,self.r+1,device=dev,dtype=torch.long)
            gx,gy=torch.meshgrid(ox,oy,indexing="ij")
            self._oc[k]=torch.stack([gx.reshape(-1),gy.reshape(-1)],dim=1)
        return self._oc[k]
    def forward(self, cen, feat, vel, lsig, Tf, X, Y):
        B,K,C=feat.shape; dev=feat.device; dtype=feat.dtype
        proj=self.fp(feat); sb=torch.exp(lsig.float()).clamp(0.5,self.ms)
        offs=self._offs(dev); P=offs.shape[0]; dl=[]
        for h in range(Tf):
            tf=float(h+1); pc=cen+vel*tf; sh=sb*math.sqrt(tf)
            ci=pc.round().long(); nb=ci.unsqueeze(2)+offs.reshape(1,1,P,2)
            valid=((nb[...,0]>=0)&(nb[...,0]<X)&(nb[...,1]>=0)&(nb[...,1]<Y))
            diff=nb.float()-pc.unsqueeze(2); se=sh.unsqueeze(2).clamp(min=1e-2)
            g=torch.exp(-0.5*((diff/se)**2).sum(-1))*valid.float()
            gn=torch.nan_to_num(g/g.sum(dim=2,keepdim=True).clamp(min=1e-6),nan=0.0)
            wf=gn.unsqueeze(-1)*proj.unsqueeze(2)
            lin=(nb[...,0]*Y+nb[...,1]).long()
            dhl=[]
            for b in range(B):
                vm=valid[b].reshape(-1); lb=lin[b].reshape(-1)[vm]; wfb=wf[b].reshape(-1,C)[vm]
                buf=torch.zeros(C,X*Y,device=dev,dtype=dtype)
                if lb.shape[0]>0: buf=buf.index_add(1,lb,wfb.t().to(dtype))
                dhl.append(buf)
            dl.append(torch.stack(dhl).reshape(B,C,X,Y))
        return torch.stack(dl,dim=1)


class TCSTOP2DModule(nn.Module):
    def __init__(self, C, T_past, T_future, X, Y,
                 K=32, nms=7, ah=4, ml=3, sr=5, dropout=0.0, trw=0.1, norm="group", gn=32):
        super().__init__()
        self.Tf=T_future; self.X=X; self.Y=Y; self.trw=trw
        self.me=MotionEncoder2D(C,norm,gn); self.ex=SoftTrackletExtractor2D(K,nms)
        self.as_=CrossFrameAssociator2D(C,ah,dropout); self.tm=TrackletMamba(C,ml,dropout)
        half=max(C//2,16)
        self.vel=nn.Sequential(nn.Linear(C,half),nn.ReLU(inplace=False),nn.Linear(half,2))
        self.sig=nn.Sequential(nn.Linear(C,half),nn.ReLU(inplace=False),nn.Linear(half,2))
        self.sp=GaussianBEV2DSplatter(C,sr); self.te=nn.Embedding(T_past,C)
    def forward(self, bp):
        B,T,C,X,Y=bp.shape; dev=bp.device; cl,fl,sl=[],[],[]
        for t in range(T):
            mm=self.me(bp[:,t],bp[:,max(t-1,0)]); c,f,s=self.ex(bp[:,t],mm)
            cl.append(c); fl.append(f); sl.append(s)
        as_=[fl[0]]
        for t in range(1,T): as_.append(self.as_(fl[t],cl[t],as_[t-1],cl[t-1]))
        seq=torch.stack(as_,dim=2)+self.te(torch.arange(T,device=dev)).reshape(1,1,T,C)
        _,h=self.tm(seq); vel=self.vel(h); lsig=self.sig(h)
        fd=self.sp(cl[-1],h,vel,lsig,self.Tf,X,Y)
        if T>=2:
            with torch.no_grad(): ad=bp[:,-1]-bp[:,-2]
            L=F.l1_loss(fd[:,0],ad)*self.trw
        else: L=fd.sum()*0.0
        return fd, L, {"tracklet_centers_last":cl[-1].detach(),"tracklet_vel":vel.detach(),
                       "tracklet_scores":sl[-1].detach(),"raw_score_mse":L.item()}



# ──────────────────────────────────────────────────────────────────────
#  [MotionHead] Learned motion score head
# ──────────────────────────────────────────────────────────────────────

class MotionScoreHead2D(nn.Module):
    """학습형 motion score head.

    coarse patch feature의 시간 차분 |f_t - f_{t-1}| 에서 per-patch motion
    logit을 예측한다. 전처리된 GT inter-frame change map(motion_patch)은
    이 head의 '학습 타깃'으로만 사용되며(supervision_loss), 추론 경로에는
    절대 들어가지 않는다 — gate supervisor와 동일한 GT 지위(target 전용).

    3x3 conv로 이웃 patch 문맥을 함께 보므로, ego-motion에 의한 전역 시차
    패턴과 실제 객체 이동을 구분할 여지가 있다 (gate head와 동일 설계 철학).
    """

    def __init__(self, C, p_drop=0.1):
        super().__init__()
        c_mid = max(C // 2, 16)
        self.net = nn.Sequential(
            nn.Conv2d(C, c_mid, 3, padding=1),
            nn.GELU(),
            nn.Dropout2d(p_drop),
            nn.Conv2d(c_mid, 1, 1),
        )

    def forward(self, pa, t_past):
        """pa: (B, T_total, C, Xp, Yp) → motion logits (B, Tf, Xp, Yp).

        t=0 항은 '마지막 관측 → 첫 미래' 차분(무감독, 추론 score에는 포함),
        t>=1 항은 미래-미래 차분(GT change map으로 감독).
        """
        fut  = pa[:, t_past:]                                     # (B,Tf,C,Xp,Yp)
        prev = torch.cat([pa[:, t_past - 1:t_past], fut[:, :-1]], dim=1)
        d = (fut - prev).abs()
        B, Tf, C, Xp, Yp = d.shape
        logit = self.net(d.reshape(B * Tf, C, Xp, Yp))
        return logit.reshape(B, Tf, Xp, Yp)

    def supervision_loss(self, mot_logit, motion_patch):
        """GT change map은 여기'에서만' 사용된다 (학습 타깃, distillation).

        mot_logit    : (B, Tf, Xp, Yp)   head 출력
        motion_patch : (B, Tf-1, Z, Xp, Yp) 또는 (B, Tf-1, Xp, Yp)
        head 출력의 [1:] 구간(미래-미래 쌍)만 감독한다.
        per-sample min-max로 [0,1] soft target을 만들어 BCE.
        """
        with torch.no_grad():
            tgt = motion_patch
            if tgt.ndim == 5:
                tgt = tgt.max(dim=2).values                       # (B,Tf-1,Xp,Yp)
            if tgt.shape[-2:] != mot_logit.shape[-2:]:
                B0, T0 = tgt.shape[0], tgt.shape[1]
                tgt = F.interpolate(
                    tgt.reshape(B0 * T0, 1, *tgt.shape[-2:]).float(),
                    size=mot_logit.shape[-2:], mode="bilinear",
                    align_corners=False,
                ).reshape(B0, T0, *mot_logit.shape[-2:])
            B = tgt.shape[0]
            flat = tgt.float().reshape(B, -1)
            lo = flat.min(dim=1, keepdim=True).values
            hi = flat.max(dim=1, keepdim=True).values
            tgt = ((flat - lo) / (hi - lo + 1e-6)).reshape_as(tgt)
        n_sup = min(mot_logit.shape[1] - 1, tgt.shape[1])
        return F.binary_cross_entropy_with_logits(
            mot_logit[:, 1:1 + n_sup].float(), tgt[:, :n_sup])
# ──────────────────────────────────────────────────────────────────────
#  UGAFR Gate Supervisor
# ──────────────────────────────────────────────────────────────────────

class UGAFR2DGateSupervisor(nn.Module):
    """
    UGAFR: Uncertainty-Guided Adaptive Fine Refinement.

    Training target 구성:
      score = eCE + λ_ent × entropy + μ_mot × motion
              + b_small × small_prior + b_ped × ped_prior + b_tc × TC_prior

    논문 Exp 2 gate policy 비교:
      CE only / CE+entropy / CE+entropy+motion / Full UGAFR
      → inference gate가 training target에서 잘 학습되었음을 보임

    Reviewer 질문 대응:
      Q: "GT-dependent term을 inference에서 어떻게 쓰나?"
      A: training target으로만 사용. inference gate는 coarse feature + motion + TC signal.
    """
    SMALL_CLS = DYN_OBJ_CLASSES

    def __init__(self, ps=8, fn_w=2.0, fp_w=1.0,
                 lam=0.1, mu=0.3, sb=2.0, tcb=3.0, ign=255,
                 target_mode="teacher", rank_w=1.0, rank_margin=1.0,
                 pos_weight_cap=50.0):
        super().__init__()
        self.ps=ps; self.fn_w=fn_w; self.fp_w=fp_w
        self.lam=lam; self.mu=mu; self.sb=sb; self.tcb=tcb; self.ign=ign
        # [recall] target_mode:
        #   "teacher" : 기존 — coarse CE 오차+entropy+motion+dyn boost의 top-K
        #   "dynamic" : 신규 — "이 패치가 어느 미래 프레임에든 target dynamic을
        #               포함하는가"라는 이진 라벨. Hit_dyn의 정의 그 자체이므로
        #               학습 목표와 평가 지표가 일치한다.
        self.target_mode=target_mode
        self.rank_w=rank_w; self.rank_margin=rank_margin
        self.pos_weight_cap=pos_weight_cap

    # ── [recall] 프레임별 dynamic 패치 라벨 (B, Tf, Xp, Yp) ──
    def _dyn_patch_target(self, gt, mask, ps, Xp, Yp):
        B,Tf,X,Y = gt.shape
        d = torch.zeros_like(gt, dtype=torch.bool)
        for c in self.SMALL_CLS: d |= (gt == c)
        d &= mask
        return F.max_pool2d(d.float().reshape(B*Tf,1,X,Y), ps, stride=ps) \
                .reshape(B,Tf,Xp,Yp)

    def _bce_pos_weighted(self, logit, target):
        """클래스 불균형(positive 약 2~5%) 보정. 배치에서 비율을 재서
           pos_weight를 동적으로 정하되 상한을 둬 폭주를 막는다."""
        pos = target.sum(); tot = target.numel()
        pw = ((tot - pos) / pos.clamp(min=1.0)).clamp(max=self.pos_weight_cap)
        return F.binary_cross_entropy_with_logits(
            logit.float(), target.float(), pos_weight=pw)

    def _topk_ranking_loss(self, score, target, K):
        """[recall] top-K recall을 직접 최적화.

        실제 지표는 '분류가 맞았는가'가 아니라 'top-K 안에 들어왔는가'라는
        순위 문제다. 각 샘플에서 negative 중 K번째로 높은 점수를 문턱으로 잡고,
        그보다 margin 이상 높지 않은 positive에 벌점을 준다.
        """
        B,N = score.shape
        loss = score.new_zeros(())
        cnt = 0
        for b in range(B):
            pos = score[b][target[b] > 0.5]
            neg = score[b][target[b] <= 0.5]
            if pos.numel() == 0 or neg.numel() == 0: continue
            k = int(min(K, neg.numel()))
            thr = torch.topk(neg, k, largest=True).values[-1]   # K번째 negative
            loss = loss + F.relu(self.rank_margin + thr - pos).mean()
            cnt += 1
        return loss / max(cnt, 1)

    def _abce(self, logit, target):
        return (self.fn_w*target*F.softplus(-logit) + self.fp_w*(1-target)*F.softplus(logit)).mean()

    def forward(self, coarse_logits, gate_logit, gt_cls, gt_mask,
                motion_patch, tc_aux, patch_size=None, K_topk=256,
                gate_logit_per_frame=None):
        ps=patch_size or self.ps; dev=coarse_logits.device
        B,Tf,ncls,X,Y=coarse_logits.shape; Xp,Yp=X//ps,Y//ps; N=Xp*Yp
        gt=gt_cls.long(); mask=gt_mask.bool()&(gt!=self.ign)

        # ══ [recall] dynamic 이진 목표 모드 ══════════════════════════
        #   학습 목표를 평가 지표(Hit_dyn)와 일치시킨다. teacher score의
        #   top-K를 맞히는 대신, 'dynamic을 포함하는 패치'를 직접 맞힌다.
        if self.target_mode == "dynamic":
            tgt_pf = self._dyn_patch_target(gt, mask, ps, Xp, Yp)   # (B,Tf,Xp,Yp)
            tgt_un = tgt_pf.max(dim=1).values.reshape(B, N)          # union
            Kc = max(1, min(K_topk, N))

            # (a) 프레임별 감독 — dense gradient. per-frame이 union보다 쉬운 문제.
            if gate_logit_per_frame is not None:
                L_bce = self._bce_pos_weighted(gate_logit_per_frame, tgt_pf)
            else:
                L_bce = self._bce_pos_weighted(gate_logit.reshape(B, N), tgt_un)

            # (b) union 점수에 top-K 랭킹 손실 — 지표를 직접 최적화
            L_rank = self._topk_ranking_loss(gate_logit.reshape(B, N).float(),
                                             tgt_un, Kc) if self.rank_w > 0 else \
                     gate_logit.new_zeros(())
            L = L_bce + self.rank_w * L_rank
            with torch.no_grad():
                sc = gate_logit.reshape(B, N).float()
                sel = torch.topk(sc, Kc, dim=1).indices
                hit = torch.zeros_like(tgt_un).scatter_(1, sel, 1.0)
                nd = tgt_un.sum().clamp(min=1)
                hit_rate = float((hit * tgt_un).sum() / nd)
            return L, {"gate_target": tgt_un.detach(), "K_target": Kc,
                       "L_bce": float(L_bce.detach()),
                       "L_rank": float(L_rank.detach()) if torch.is_tensor(L_rank) else 0.0,
                       "pos_ratio": float(tgt_un.mean()),
                       "train_hit_dyn": hit_rate}
        # ══ 기존 teacher 모드 ═══════════════════════════════════════

        with autocast(device_type="cuda", enabled=False):
            logf=coarse_logits.permute(0,1,3,4,2).reshape(-1,ncls).float()
            ce=F.cross_entropy(logf,gt.reshape(-1),reduction="none",ignore_index=self.ign)
        ce=(ce.reshape(B,Tf,X,Y)*mask.float())
        ce_p=F.max_pool2d(ce.reshape(B*Tf,X,Y).unsqueeze(1),ps,stride=ps).squeeze(1).reshape(B,Tf,Xp,Yp).max(1).values

        with autocast(device_type="cuda", enabled=False):
            p=torch.softmax(coarse_logits.float(),dim=2)
            ent=-(p*torch.log(p.clamp(1e-8))).sum(2)
        ent_p=F.avg_pool2d(ent.reshape(B*Tf,X,Y).unsqueeze(1),ps,stride=ps).squeeze(1).reshape(B,Tf,Xp,Yp).max(1).values

        if motion_patch is not None:
            mp=motion_patch
            if mp.ndim==5: mp=mp.max(dim=2).values
            if mp.shape[2]!=Xp or mp.shape[3]!=Yp:
                mp=F.interpolate(mp.reshape(mp.shape[0]*mp.shape[1],1,mp.shape[2],mp.shape[3]),
                                 size=(Xp,Yp),mode="bilinear",align_corners=False).reshape(B,-1,Xp,Yp)
            mp_agg=mp.float().max(1).values
        else: mp_agg=torch.zeros_like(ce_p)

        # S(p): DYN_OBJ_CLASSES (bus/car/truck) 포함 patch upweight
        sm=torch.zeros_like(gt,dtype=torch.bool)
        for c in self.SMALL_CLS: sm|=(gt==c)
        sm&=mask
        sm_p=F.max_pool2d(sm.float().reshape(B*Tf,X,Y).unsqueeze(1),ps,stride=ps).squeeze(1).reshape(B,Tf,Xp,Yp).max(1).values
        # car(4) 에 추가 boost: 가장 많고 명확한 동적 클래스
        car_p=F.max_pool2d(((gt==4)&mask).float().reshape(B*Tf,X,Y).unsqueeze(1),ps,stride=ps).squeeze(1).reshape(B,Tf,Xp,Yp).max(1).values
        sm_agg=sm_p*self.sb + car_p*self.sb*2.0

        tc_map=torch.zeros(B,Xp,Yp,device=dev)
        if tc_aux and "tracklet_centers_last" in tc_aux:
            tcs=tc_aux["tracklet_centers_last"]
            tsc=tc_aux.get("tracklet_scores",torch.ones(tcs.shape[:2],device=dev))
            tp=(tcs/ps).long().clamp(0)
            tp[...,0]=tp[...,0].clamp(0,Xp-1); tp[...,1]=tp[...,1].clamp(0,Yp-1)
            for b in range(B):
                for k in range(tp.shape[1]):
                    xi,yi,sc=int(tp[b,k,0]),int(tp[b,k,1]),float(tsc[b,k])
                    for dx in [-1,0,1]:
                        for dy in [-1,0,1]:
                            nx_,ny_=max(0,min(Xp-1,xi+dx)),max(0,min(Yp-1,yi+dy))
                            tc_map[b,nx_,ny_]=max(tc_map[b,nx_,ny_].item(),self.tcb*sc)

        score=ce_p+self.lam*ent_p+self.mu*mp_agg+sm_agg+tc_map
        flat=score.reshape(B,N); Kc=max(1,min(K_topk,N))
        _,idx=torch.topk(flat.detach(),Kc,dim=1,largest=True,sorted=False)
        tgt=torch.zeros(B,N,device=dev); tgt.scatter_(1,idx,1.0)
        L=self._abce(gate_logit.float(),tgt)
        return L, {"gate_target":tgt.detach(),"K_target":Kc,
                   "ce_mean":ce_p.mean().item(),"ent_mean":ent_p.mean().item()}


# ──────────────────────────────────────────────────────────────────────
#  Main Model: LiftSplatShoot4DBEV (ver.2)
# ──────────────────────────────────────────────────────────────────────

class LiftSplatShoot4DBEV(nn.Module):
    """
    4D BEV Forecasting Model (ver.2, experiment-ready).

    Main contributions:
      1. Coarse-to-Fine Mamba
      2. UGAFR adaptive refinement

    gate_policy: ablation experiment 직접 지원
      "coarse_only" | "no_refinement" | "random" | "entropy" | "motion" | "ugafr" | "all_fine"
    """

    def __init__(
        self, grid_conf, data_aug_conf,
        voxel_featC=64, num_classes=17, T_past=6, T_future=10, patch_size=8,
        backbone="swin_t", backbone_pretrained=True,
        use_spatial_backbone=True, spatial_backbone_blocks=3,
        backbone_checkpoint=True, use_ego_warping=True,
        tc_enabled=True, tc_K=32, tc_nms_kernel=7, tc_assoc_heads=4,
        tc_mamba_layers=3, tc_splat_radius=5, tc_dropout=0.0,
        tc_track_reg_w=0.1, tc_loss_weight=1.0,
        mamba_n_stages=2, mamba_spatial_layers=2, mamba_temporal_layers=2,
        mamba_dropout=0.0, scan_mode="hilbert",
        ugafr_enabled=True, ugafr_fn_weight=2.0, ugafr_fp_weight=1.0,
        ugafr_lambda_ent=0.1, ugafr_mu_motion=0.3,
        ugafr_small_boost=2.0, ugafr_tc_boost=3.0, ugafr_loss_weight=0.5,
        gate_target_mode="dynamic", gate_rank_w=1.0, gate_rank_margin=1.0,
        gate_pos_weight_cap=50.0,
        motion_head_loss_w=0.1, motion_source="feat_future",
        # [핵심] 2-Pass Adaptive Re-selection
        two_pass_enabled=True, two_pass_ratio=0.25,
        # [v2] Ablation gate policy
        gate_policy: str = "ugafr",
        gate_topk=256, fine_disable_steps=20000,
        random_patch_steps=10000, ramp_steps=30000,
        # [SampledK/Q1] adaptive-budget refiner 학습: 학습 forward마다
        #   K ~ U[min_frac·N, max_frac·N] 균등 샘플. 평가 시엔 gate_topk 고정.
        sampled_k_enabled=False, sampled_k_min_frac=0.05, sampled_k_max_frac=0.20,
        norm="group", gn_groups=32, ego_decay=0.15,
        zpc_tau_start=0.5, zpc_tau_end=8.0,
        zpc_tau_anneal_steps=80000, zpc_dyn_gate_thresh=0.25,
        # [CARLA] LiDAR BEV fusion (camera+lidar). lidar_bev (B,lidar_in_ch,X,Y)
        # is encoded and added to the current-frame BEV feature before decoding.
        lidar_fusion=False, lidar_in_ch=5,
    ):
        super().__init__()
        assert gate_policy in GatePolicyRouter.VALID_POLICIES, \
            f"gate_policy must be one of {GatePolicyRouter.VALID_POLICIES}"

        dx, bx, nx = gen_dx_bx(grid_conf["xbound"], grid_conf["ybound"], grid_conf["zbound"])
        self.dx=nn.Parameter(dx,requires_grad=False)
        self.bx=nn.Parameter(bx,requires_grad=False)
        self.nx=nn.Parameter(nx.float(),requires_grad=False)
        self.X=int(nx[0]); self.Y=int(nx[1]); self.Z=int(nx[2])
        self.downsample=16; self.camC=64
        self.frustum=self._create_frustum(grid_conf, data_aug_conf)
        self.D=self.frustum.shape[0]

        self.T_past=T_past; self.T_future=T_future; self.T_total=T_past+T_future
        self.C=voxel_featC; self.num_classes=num_classes
        # [CARLA] LiDAR BEV encoder (built only when fusion enabled)
        self.lidar_fusion=bool(lidar_fusion)
        if self.lidar_fusion:
            _lc = max(32, self.C)
            self.lidar_encoder=nn.Sequential(
                nn.Conv2d(lidar_in_ch,_lc,3,padding=1,bias=False), nn.GroupNorm(min(gn_groups,_lc),_lc), nn.ReLU(inplace=False),
                nn.Conv2d(_lc,_lc,3,padding=1,bias=False), nn.GroupNorm(min(gn_groups,_lc),_lc), nn.ReLU(inplace=False),
                nn.Conv2d(_lc,self.C,1,bias=True))
        else:
            self.lidar_encoder=None
        self.patch_size=patch_size
        self.Xp=self.X//patch_size; self.Yp=self.Y//patch_size
        assert self.X%patch_size==0 and self.Y%patch_size==0

        self.ego_decay=ego_decay; self.tc_loss_weight=tc_loss_weight
        self.ugafr_loss_weight=ugafr_loss_weight
        self.tc_enabled=tc_enabled; self.ugafr_enabled=ugafr_enabled
        self.use_ego_warping=use_ego_warping; self.use_spatial_backbone=use_spatial_backbone
        self.backbone_checkpoint=backbone_checkpoint
        self.gate_policy=gate_policy; self.gate_topk=gate_topk
        self.fine_disable_steps=fine_disable_steps
        self.random_patch_steps=random_patch_steps; self.ramp_steps=ramp_steps

        # [SampledK/Q1] budget-transfer 발견(refiner는 학습 예산 밖에서 성능 저하)에
        #   대응해, adaptive-K inference를 위한 refiner는 K를 범위로 샘플하며 학습.
        #   _sampled_K는 forward 시작 시 1회 샘플되어, 같은 forward 안의
        #   gate supervision(K_topk)과 patch selection이 동일한 K를 공유한다.
        assert 0.0 < float(sampled_k_min_frac) <= float(sampled_k_max_frac) <= 1.0, \
            f"sampled_k fracs invalid: [{sampled_k_min_frac}, {sampled_k_max_frac}]"
        self.sampled_k_enabled=bool(sampled_k_enabled)
        self.sampled_k_min_frac=float(sampled_k_min_frac)
        self.sampled_k_max_frac=float(sampled_k_max_frac)
        self._sampled_K=None

        img_size=tuple(data_aug_conf["final_dim"])
        self.camencode=SwinTCamEncode(self.D,self.camC,self.downsample,
                                      pretrained=backbone_pretrained,norm=norm,
                                      gn_groups=gn_groups,img_size=img_size)
        self.voxel_proj=nn.Sequential(
            nn.Conv3d(self.camC,self.C,1,bias=False), _make_norm3d(norm,self.C,gn_groups), nn.ReLU(inplace=False))
        self.height_encoder=nn.Sequential(
            nn.Linear(4,self.C,bias=True),nn.ReLU(inplace=False),nn.Linear(self.C,self.C,bias=False))

        if use_spatial_backbone:
            self.spatial_backbone=VoxelSpatialBackbone(self.C,spatial_backbone_blocks,norm,gn_groups,backbone_checkpoint)
        if use_ego_warping:
            self.ego_warper=EgoMotionWarper()
        if tc_enabled:
            self.tcstop=TCSTOP2DModule(C=self.C,T_past=T_past,T_future=T_future,
                X=self.X,Y=self.Y,K=tc_K,nms=tc_nms_kernel,ah=tc_assoc_heads,
                ml=tc_mamba_layers,sr=tc_splat_radius,dropout=tc_dropout,
                trw=tc_track_reg_w,norm=norm,gn=gn_groups)

        z_start=float(grid_conf["zbound"][0]); z_res=float(grid_conf["zbound"][2])
        self.z_collapse=ZPriorityCollapse(C=self.C,num_classes=num_classes,warm_steps=5000,
            tau_start=zpc_tau_start,tau_end=zpc_tau_end,tau_anneal=zpc_tau_anneal_steps,
            dyn_thresh=zpc_dyn_gate_thresh,z_start=z_start,z_res=z_res,Z=self.Z)

        self.patch_embed=BEVPatchEmbed2D(self.C,self.C,patch_size,norm,gn_groups)
        self.coarse_mamba=CoarseSpaTempMamba2D(C=self.C,T_past=T_past,T_future=T_future,
            Xp=self.Xp,Yp=self.Yp,n_stages=mamba_n_stages,n_sp=mamba_spatial_layers,
            n_tp=mamba_temporal_layers,dropout=mamba_dropout,scan_mode=scan_mode)
        self.gate_head=CoarseGateHead2D(self.C,norm,gn_groups)

        # [MotionHead] 학습형 motion score — GT change map은 학습 타깃 전용
        self.motion_head=MotionScoreHead2D(self.C)
        self.motion_head_loss_w=motion_head_loss_w
        # [MotionSrc] 추론 motion 신호의 출처:
        #   feat_future: 예측된 미래 feature의 시간 차분 (기본, 논문 Eq.4 원형,
        #                파라미터 불필요 → 구 체크포인트에도 적용 가능)
        #   head:        학습형 MotionScoreHead2D
        #   feat_past:   관측(과거) feature의 시간 차분
        assert motion_source in ("feat_future","head","feat_past"), motion_source
        self.motion_source=motion_source

        _C = self.C
        # patch_size에 맞게 upsample 단계 수 자동 결정
        # patch_size=4 → 50×50 → 4× up (2단계), patch_size=8 → 25×25 → 8× up (3단계)
        import math as _math
        n_up = int(_math.log2(patch_size))   # 4→2, 8→3

        _up_layers = []
        for _ in range(n_up):
            _up_layers += [
                nn.ConvTranspose2d(_C, _C, 4, stride=2, padding=1, bias=False),
                _make_norm2d(norm, _C, gn_groups), nn.ReLU(inplace=False),
                nn.Conv2d(_C, _C, 3, padding=1, bias=False),
                _make_norm2d(norm, _C, gn_groups), nn.ReLU(inplace=False),
            ]
        self.coarse_upsample = nn.Sequential(*_up_layers)
        with torch.no_grad():
            for m in self.coarse_upsample.modules():
                if isinstance(m, nn.ConvTranspose2d):
                    nn.init.zeros_(m.weight)
                    Co = m.weight.shape[1]
                    for i in range(min(m.weight.shape[0], Co)):
                        m.weight.data[i, i % Co, 1, 1] = 1.0

        self.time_emb_full=nn.Embedding(self.T_total,self.C)
        self.fine_mamba=nn.Sequential(MambaResidualBlock(self.C,mamba_dropout),
                                       MambaResidualBlock(self.C,mamba_dropout))
        self.fine_norm=nn.LayerNorm(self.C)

        # ── [핵심] 2-Pass Adaptive Re-selection 설정 ────────────────────
        # 한 번 선택→정제로 끝내지 않고, 정제 후 남은 불확실성을 재측정하여
        # "여전히 어려운" patch를 재선택해 한 번 더 정제한다.
        # 전체 예산 K = K1(1-pass) + K2(2-pass)로 고정 → 공정 비교 유지.
        #   - dense refiner가 흉내낼 수 없는 iterative allocation
        #   - 논문 이름의 "Adaptive"를 실제로 구현
        self.two_pass_enabled = two_pass_enabled
        self.two_pass_ratio = two_pass_ratio      # K2 = round(ratio * K), K1 = K - K2
        # 두 pass가 fine refiner를 공유(파라미터 재사용)한다. 별도 모듈 불필요.

        # ── [1단계-B] Fine logit residual head ──────────────────────────
        # fine refiner 출력(refined patch feature)을 class logit delta로 직접 변환.
        # decoder를 거치며 fine update가 희석되는 문제를 막고,
        # dynamic class에 직접 gradient가 흐르도록 한다.
        #   final_logits = coarse_logits + beta · scatter(delta)
        # delta는 patch feature(C) → num_classes 선형 사상 (시점별 공유).
        self.fine_logit_head = nn.Sequential(
            nn.LayerNorm(self.C),
            nn.Linear(self.C, self.C), nn.GELU(),
            nn.Linear(self.C, num_classes),
        )
        # delta head 마지막 층을 0으로 초기화 → 학습 초기엔 coarse 그대로(안정).
        nn.init.zeros_(self.fine_logit_head[-1].weight)
        nn.init.zeros_(self.fine_logit_head[-1].bias)
        # logit residual 강도. learnable scalar (초기 작게).
        self.fine_logit_beta = nn.Parameter(torch.tensor(0.5))

        # ── [1단계-D(경량)] Residual update gate ────────────────────────
        # fine feature residual을 그대로 scatter하지 않고, patch별 gate로 조절.
        #   F' = F + alpha · ΔF,  alpha = sigmoid(MLP(fine_token))
        # easy region을 과도하게 흔드는 것을 막는다(선택적, 안전).
        self.fine_update_gate = nn.Sequential(
            nn.Linear(self.C, max(self.C // 2, 16)), nn.ReLU(inplace=False),
            nn.Linear(max(self.C // 2, 16), 1),
        )
        nn.init.zeros_(self.fine_update_gate[-1].bias)  # 초기 alpha≈0.5

        # ── [baseline] OccProphet-style dense refiner (외부 비교용) ──────
        # gate_policy="occprophet_dense"일 때 fine Mamba(selective) 대신 사용.
        # 실제 OccProphet Refiner(post_unet)처럼 [past⊕future] 전체를 받아
        # cross-frame refine → "selective vs dense" 비교.
        self.occprophet_refiner = None
        if HAS_OCCPROPHET_BASELINE:
            self.occprophet_refiner = OccProphetDenseRefiner2D(
                self.C, T_total=self.T_total, num_heads_base=8,
                window_size=7, downsample_layers=2, residual=True)

        if ugafr_enabled:
            self.gate_supervisor=UGAFR2DGateSupervisor(
                target_mode=gate_target_mode, rank_w=gate_rank_w,
                rank_margin=gate_rank_margin, pos_weight_cap=gate_pos_weight_cap,
                ps=patch_size,fn_w=ugafr_fn_weight,
                fp_w=ugafr_fp_weight,lam=ugafr_lambda_ent,mu=ugafr_mu_motion,
                sb=ugafr_small_boost,tcb=ugafr_tc_boost)

        self.decoder_v2=BEVForecastHead2D(self.C,num_classes,norm,gn_groups)
        self.register_buffer("_step",torch.zeros((),dtype=torch.long),persistent=False)
        self._last_aux={}

    # ── geometry ────────────────────────────────────────────────────

    def _create_frustum(self, gc, da):
        H,W=da["final_dim"]; fH,fW=H//self.downsample,W//self.downsample
        ds=torch.arange(*gc["dbound"],dtype=torch.float).reshape(-1,1,1).expand(-1,fH,fW)
        D=ds.shape[0]
        xs=torch.linspace(0,W-1,fW).reshape(1,1,fW).expand(D,fH,fW)
        ys=torch.linspace(0,H-1,fH).reshape(1,fH,1).expand(D,fH,fW)
        return nn.Parameter(torch.stack((xs,ys,ds),-1),requires_grad=False)

    def get_geometry(self, rots, trans, intrins, pr, pt):
        B,N,_=trans.shape
        pts=self.frustum[None,None].expand(B,N,-1,-1,-1,-1).clone()
        pts=pts-pt[:,:,None,None,None,:]
        pts=(torch.linalg.inv(pr)[:,:,None,None,None]@pts.unsqueeze(-1)).squeeze(-1)
        xy=pts[...,0:2]*pts[...,2:3]
        pts=torch.cat([xy,pts[...,2:3]],dim=-1)
        pts=(rots@torch.linalg.inv(intrins))[:,:,None,None,None]@pts.unsqueeze(-1)
        return pts.squeeze(-1)+trans[:,:,None,None,None,:]

    def get_cam_feats(self, x):
        B,N,C,H,W=x.shape; ff=self.camencode(x.reshape(B*N,C,H,W))
        return ff.reshape(B,N,self.camC,self.D,H//self.downsample,W//self.downsample).permute(0,1,3,4,5,2).contiguous()

    def voxel_pooling_3d(self, geom, x):
        B,N,D,H,W,C=x.shape; Np=B*N*D*H*W
        x=x.reshape(Np,C)
        gf=((geom-(self.bx-self.dx/2))/self.dx).long().reshape(Np,3)
        bi=torch.cat([torch.full([Np//B,1],i,device=x.device,dtype=torch.long) for i in range(B)])
        gf=torch.cat((gf,bi),1)
        kp=((gf[:,0]>=0)&(gf[:,0]<int(self.nx[0]))&(gf[:,1]>=0)&(gf[:,1]<int(self.nx[1]))&
            (gf[:,2]>=0)&(gf[:,2]<int(self.nx[2])))
        x,gf=x[kp],gf[kp]
        rk=gf[:,0]*(int(self.nx[1])*int(self.nx[2])*B)+gf[:,1]*(int(self.nx[2])*B)+gf[:,2]*B+gf[:,3]
        s=rk.argsort(); x,gf,rk=x[s],gf[s],rk[s]; x,gf=cumsum_trick(x,gf,rk)
        fn=torch.zeros((B,C,int(self.nx[2]),int(self.nx[0]),int(self.nx[1])),device=x.device,dtype=x.dtype)
        # 방어적 clamp: 부동소수점 경계 케이스 방지
        b_idx = gf[:,3].clamp(0, B-1)
        z_idx = gf[:,2].clamp(0, int(self.nx[2])-1)
        x_idx = gf[:,0].clamp(0, int(self.nx[0])-1)
        y_idx = gf[:,1].clamp(0, int(self.nx[1])-1)
        fn[b_idx,:,z_idx,x_idx,y_idx]=x; return fn

    def get_voxel_feat_one_t(self, x, rots, trans, intrins, pr, pt):
        geom=self.get_geometry(rots,trans,intrins,pr,pt)
        cf=self.get_cam_feats(x); vox=self.voxel_pooling_3d(geom,cf); vox=self.voxel_proj(vox)
        Z=vox.shape[2]; dev=vox.device
        zr=self.bx[2]+torch.arange(Z,device=dev,dtype=torch.float32)*self.dx[2]
        hf=torch.stack([torch.sin(zr*math.pi/2),torch.cos(zr*math.pi/2),
                        torch.sin(zr*math.pi/0.5),torch.cos(zr*math.pi/0.5)],dim=-1)
        return vox+self.height_encoder(hf).to(vox.dtype).reshape(1,-1,Z,1,1)

    @torch.no_grad()
    def _ego_extrap(self, bp, e2g):
        B,T,C,X,Y=bp.shape; dev=bp.device; bl=bp[:,-1]
        if e2g is None or e2g.shape[1]<2:
            return torch.stack([bl*math.exp(-self.ego_decay*h) for h in range(1,self.T_future+1)],dim=1)
        Ri=torch.linalg.inv(e2g[:,-1,:3,:3])
        vl=torch.bmm(Ri,(e2g[:,-1,:3,3]-e2g[:,-2,:3,3]).unsqueeze(-1)).squeeze(-1)
        vx,vy=vl[:,0],vl[:,1]
        xr=max(float((self.nx[0]-1)*self.dx[0]),1e-6); yr=max(float((self.nx[1]-1)*self.dx[1]),1e-6)
        el=[]
        for h in range(self.T_future):
            tf=float(h+1); sx=vx*tf/xr*2; sy=vy*tf/yr*2
            th=torch.zeros(B,2,3,device=dev); th[:,0,0]=1;th[:,1,1]=1;th[:,0,2]=sx;th[:,1,2]=sy
            g=F.affine_grid(th,(B,C,X,Y),align_corners=True)
            el.append(F.grid_sample(bl,g,mode="bilinear",padding_mode="zeros",align_corners=True)*math.exp(-self.ego_decay*h))
        return torch.stack(el,dim=1)

    def _upsample(self, p):
        """
        coarse_upsample을 프레임별로 순차 처리.

        기존: (B*T, C, 25, 25) 전체를 한 번에 → GroupNorm에서 OOM
             (B=1, T=16, C=64) 기준 working memory ~314MB 필요
        수정: T 루프로 (B, C, 25, 25) 1프레임씩 처리 → peak memory ~1/T로 절감
        training 중: grad_checkpoint 추가 적용 (activation 절감)
        """
        B, T, C, Xp, Yp = p.shape
        frames = []
        for t in range(T):
            frame = p[:, t]                       # (B, C, Xp, Yp)
            if self.backbone_checkpoint and self.training:
                out = grad_checkpoint(
                    self.coarse_upsample, frame, use_reentrant=False
                )
            else:
                out = self.coarse_upsample(frame)  # (B, C, X, Y)
            frames.append(out)
        return torch.stack(frames, dim=1)          # (B, T, C, X, Y)

    def _build_bev(self, bp, fi):
        te=self.time_emb_full(torch.arange(self.T_total,device=bp.device))
        return torch.cat([bp,fi],dim=1)+te.reshape(1,self.T_total,self.C,1,1)

    @torch.no_grad()
    def set_step(self, s): self._step.fill_(int(s))

    def _phase(self, s):
        if s<self.fine_disable_steps: return "coarse_only"
        s2=s-self.fine_disable_steps
        if s2<self.random_patch_steps: return "random"
        return "ramp" if s2-self.random_patch_steps<self.ramp_steps else "topk"

    def _cur_topk(self, s):
        ph=self._phase(s); N=self.Xp*self.Yp
        if ph=="coarse_only": return 0
        # [SampledK/Q1] 학습 중 sampled-K 모드: 커리큘럼 ramp 대신 forward에서
        #   샘플된 K를 사용. (같은 forward 안의 두 호출 — gate supervision과
        #   _select_patches — 이 동일한 K를 보도록 forward 시작 시 1회 샘플됨.)
        #   평가 시(self.training=False)에는 아래 기존 로직으로 gate_topk 고정.
        if self.training and self.sampled_k_enabled and self._sampled_K is not None:
            return min(int(self._sampled_K), N)
        if ph=="random": return min(64,N)
        if ph=="ramp":
            frac=(s-self.fine_disable_steps-self.random_patch_steps)/max(1,self.ramp_steps)
            return max(64,min(int(64+frac*(self.gate_topk-64)),self.gate_topk))
        return min(self.gate_topk,N)

    def _beta_ramp(self, s):
        """fine logit residual 강도 ramp factor (0→1).
        fine_disable_steps 이전: 0 (fine 자체가 꺼져 있음).
        이후 ramp_steps 동안 0→1 선형 증가, 그 다음 1.0 유지.
        처음부터 학습 시 logit head가 너무 일찍 강하게 개입하는 것을 막아
        coarse/decoder가 먼저 안정화되도록 한다."""
        if s < self.fine_disable_steps:
            return 0.0
        s2 = s - self.fine_disable_steps
        if s2 >= self.ramp_steps:
            return 1.0
        return float(s2) / max(1, self.ramp_steps)

    # ── [v2] Policy-aware patch selection
    #      Dynamic-Calibrated Budget Reservation UGAFR
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    @torch.no_grad()
    def _norm01(x: torch.Tensor):
        """Per-sample min-max normalization → [0, 1]."""
        if x is None:
            return None
        mn = x.min(dim=1, keepdim=True).values
        mx = x.max(dim=1, keepdim=True).values
        return (x - mn) / (mx - mn + 1e-6)

    @torch.no_grad()
    def _motion_head_scores(self, patch_all):
        """[MotionHead] 학습형 head 기반 per-patch motion score (B, N).
        GT motion_patch를 사용하지 않는다 (leakage 제거)."""
        logit = self.motion_head(patch_all, self.T_past)      # (B,Tf,Xp,Yp)
        return torch.sigmoid(logit.float()).amax(dim=1).reshape(
            patch_all.shape[0], -1)

    @torch.no_grad()
    def _motion_feat_scores(self, patch_all, past=False):
        """[MotionSrc] feature 시간 차분 기반 per-patch motion score (B, N).
        past=False: 예측된 미래 feature 차분 (마지막 관측→t+1 포함, Tf개)
        past=True : 관측 프레임 feature 차분 (T_past-1개)
        채널 평균 L1 사용 — 이후 _norm01(min-max)이 적용되므로 합과 등가."""
        if past:
            seg = patch_all[:, :self.T_past]
            d = (seg[:, 1:] - seg[:, :-1]).abs().mean(dim=2)   # (B,Tp-1,Xp,Yp)
        else:
            fut  = patch_all[:, self.T_past:]
            prev = torch.cat([patch_all[:, self.T_past-1:self.T_past],
                              fut[:, :-1]], dim=1)
            d = (fut - prev).abs().mean(dim=2)                 # (B,Tf,Xp,Yp)
        return d.float().amax(dim=1).reshape(patch_all.shape[0], -1)

    @torch.no_grad()
    def _motion_scores(self, patch_all):
        """[MotionSrc] motion_source 설정에 따른 통합 진입점."""
        if patch_all is None:
            return None
        if self.motion_source == "head":
            return self._motion_head_scores(patch_all)
        return self._motion_feat_scores(
            patch_all, past=(self.motion_source == "feat_past"))

    @torch.no_grad()
    def _compute_dynamic_prior_simple(self, patch_all):
        """
        GT-free dynamic prior for inference-time UGAFR routing.

        Returns:
          ent_sc: (B, N) entropy score at patch level.
          dyn_sc: (B, N) dynamic candidate score at patch level.

        Design kept intentionally simple for paper clarity:
          s_dyn(p) = max_{t,u∈p} sqrt(P_t(u ∈ {bus, car, truck}))

        This directly corresponds to the GT dynamic prior used by the
        Gate Supervisor, but does not require GT at inference.
        """
        if patch_all is None:
            return None, None

        fut_patch = patch_all[:, self.T_past:]          # (B, T_fut, C, Xp, Yp)
        B_, T_, C_, Xp_, Yp_ = fut_patch.shape

        # Coarse full-resolution logits from future patch features.
        full = self._upsample(fut_patch)                # (B, T_fut, C, X, Y)
        logits_full = self.decoder_v2(full)             # (B, T_fut, K, X, Y)
        p = torch.softmax(logits_full.float(), dim=2)   # (B, T_fut, K, X, Y)
        ps = self.patch_size

        def _patch_max(m):
            # m: (B, T_fut, X, Y) → (B, Xp*Yp), max over patch and time
            mp = F.max_pool2d(
                m.reshape(B_ * T_, self.X, self.Y).unsqueeze(1),
                ps, stride=ps,
            ).squeeze(1).reshape(B_, T_, Xp_, Yp_)
            return mp.max(dim=1).values.reshape(B_, -1)

        def _patch_avg_then_maxT(m):
            # entropy is smoother: average inside a patch, max over time
            mp = F.avg_pool2d(
                m.reshape(B_ * T_, self.X, self.Y).unsqueeze(1),
                ps, stride=ps,
            ).squeeze(1).reshape(B_, T_, Xp_, Yp_)
            return mp.max(dim=1).values.reshape(B_, -1)

        # Uncertainty score.
        ent = -(p * torch.log(p.clamp(1e-8))).sum(dim=2)       # (B, T_fut, X, Y)
        ent_sc = _patch_avg_then_maxT(ent)

        # Dynamic candidate score: bus/car/truck probability.
        dyn_ids = torch.tensor(DYN_OBJ_CLASSES, device=p.device, dtype=torch.long)
        p_dyn = p.index_select(2, dyn_ids).sum(dim=2).clamp(0.0, 1.0)

        # Concave calibration keeps weak dynamic candidates from vanishing,
        # while still remaining a single simple dynamic prior.
        dyn_sc = _patch_max(p_dyn.pow(0.5))

        return ent_sc, dyn_sc

    @torch.no_grad()
    def _build_tc_sets(self, tc_aux, device):
        """TC-STOP guaranteed patch set, list[B] of 1D tensors."""
        if not (tc_aux and "tracklet_centers_last" in tc_aux):
            return None

        tcs = tc_aux["tracklet_centers_last"]
        B = tcs.shape[0]
        tp = (tcs / self.patch_size).long()
        tp[..., 0] = tp[..., 0].clamp(0, self.Xp - 1)
        tp[..., 1] = tp[..., 1].clamp(0, self.Yp - 1)

        offs = torch.tensor(
            [(-1,-1),(-1,0),(-1,1),
             (0,-1), (0,0), (0,1),
             (1,-1), (1,0), (1,1)],
            device=device, dtype=torch.long,
        )

        tc_sets = []
        for b in range(B):
            nb = tp[b].unsqueeze(1) + offs.unsqueeze(0)
            nb[..., 0] = nb[..., 0].clamp(0, self.Xp - 1)
            nb[..., 1] = nb[..., 1].clamp(0, self.Yp - 1)
            tc_sets.append(torch.unique((nb[..., 0] * self.Yp + nb[..., 1]).reshape(-1)))
        return tc_sets

    @torch.no_grad()
    def _union_and_refill(self, parts, refill_score, K: int, N: int, tc_sets=None):
        """
        Merge reserved sets, then enforce an exact-K budget.

        Why this matters:
          - union can reduce the budget due to duplicates;
          - TC guaranteed patches can increase the budget;
          - exact-K keeps FLOPs and ablation comparisons fair.

        If merged candidates exceed K, keep the highest refill_score candidates.
        If fewer than K, refill by refill_score from unused patches.
        """
        B = refill_score.shape[0]
        dev = refill_score.device
        K = max(0, min(int(K), int(N)))
        if K == 0:
            return torch.empty(B, 0, device=dev, dtype=torch.long)

        out = torch.empty(B, K, device=dev, dtype=torch.long)
        for b in range(B):
            candidates = []
            for p in parts:
                if p is not None and p.numel() > 0:
                    candidates.append(p[b].reshape(-1).long())
            if tc_sets is not None and tc_sets[b] is not None and tc_sets[b].numel() > 0:
                candidates.append(tc_sets[b].reshape(-1).long())

            if candidates:
                merged = torch.unique(torch.cat(candidates, dim=0).clamp(0, N - 1))
                # If there are too many guaranteed/reserved patches, keep the best K
                # under the main routing score to preserve budget fairness.
                sc = refill_score[b, merged]
                merged = merged[torch.argsort(sc, descending=True)]
                selected = merged[:K]
            else:
                selected = torch.empty(0, device=dev, dtype=torch.long)

            used = torch.zeros(N, device=dev, dtype=torch.bool)
            if selected.numel() > 0:
                used[selected] = True

            if selected.numel() < K:
                need = K - selected.numel()
                score = refill_score[b].clone()
                score[used] = -1e9
                refill = torch.topk(score, need, largest=True, sorted=False).indices
                selected = torch.cat([selected, refill], dim=0)

            out[b] = selected[:K]
        return out

    @torch.no_grad()
    def _select_patches(self, gate_score, motion_patch, patch_all, tc_aux, step):
        """
        Gate-policy-aware patch selection.

        UGAFR uses a compact Dynamic-Calibrated Budget Reservation:
          1) main_score = 0.55·gate + 0.25·entropy + 0.10·motion + 0.10·dynamic
          2) reserve 15% of the fine budget for dynamic candidates.
          3) union with TC-STOP patches, then exact-K refill/truncate.

        This keeps the method simple enough for the main paper while addressing
        the train-inference mismatch from the GT dynamic prior in supervision.
        """
        B = gate_score.shape[0]
        N = self.Xp * self.Yp
        dev = gate_score.device
        policy = self.gate_policy

        # [Conformal/외부선택] 평가 전용 override: conformal adaptive-K 평가에서
        #   외부 점수(오차 예측 head)로 고른 패치 집합을 그대로 refine하기 위함.
        #   raw.external_patch_idx = LongTensor (B, K_j) 를 forward 직전에 설정.
        #   학습 중에는 무시된다 (학습 프로토콜 오염 방지).
        if (not self.training) and getattr(self, "external_patch_idx", None) is not None:
            idx = self.external_patch_idx
            assert idx.dim() == 2 and idx.shape[0] == B, \
                f"external_patch_idx shape {tuple(idx.shape)} != (B={B}, K)"
            return idx.to(device=dev, dtype=torch.long).contiguous()

        # Explicit policies should not be disabled by the UGAFR curriculum.
        # In particular, gate_policy="all_fine" is used for the same-architecture
        # dense-refinement baseline. If the generic fine_disable curriculum is
        # applied first, step 0 selects zero patches, the fine branch is unused,
        # and freeze-refiner training can produce loss.requires_grad=False.
        if policy in ("coarse_only", "no_refinement"):
            return torch.empty(B, 0, device=dev, dtype=torch.long)
        if policy == "all_fine":
            return torch.arange(N, device=dev).unsqueeze(0).expand(B, -1).contiguous()

        if self._phase(step) == "coarse_only":
            return torch.empty(B, 0, device=dev, dtype=torch.long)

        K = self._cur_topk(step)
        if K <= 0:
            return torch.empty(B, 0, device=dev, dtype=torch.long)

        # ── Non-UGAFR policies: keep router behavior for ablations ───
        #   ugafr, ugafr_quota는 아래 UGAFR 경로로 (quota는 그 안에서 분기)
        if policy not in ("ugafr", "ugafr_quota"):
            ent_sc = None
            mot_sc = None
            if policy == "entropy" and patch_all is not None:
                ent_sc, _ = self._compute_dynamic_prior_simple(patch_all)
            if policy == "motion" and patch_all is not None:
                # [MotionSrc] motion_source 설정 기반 model-internal 신호
                mot_sc = self._motion_scores(patch_all)
            raw_idx = GatePolicyRouter.select_patches(
                policy=policy, K=K, B=B, N=N, device=dev,
                gate_score_flat=gate_score,
                entropy_scores=ent_sc,
                motion_scores=mot_sc,
                tc_idx_sets=None,
            )
            return raw_idx.clamp(0, N - 1) if raw_idx.numel() > 0 else raw_idx

        # ── UGAFR: learned gate + uncertainty/motion + dynamic reservation ─
        ent_sc, dyn_sc = self._compute_dynamic_prior_simple(patch_all)

        # [MotionSrc] motion_source 설정 기반 model-internal 신호 (GT 미사용)
        mot_sc = self._motion_scores(patch_all)
        if mot_sc is not None:
            assert mot_sc.shape == (B, N), f"motion score shape {mot_sc.shape} != (B={B}, N={N})"

        g = self._norm01(gate_score)
        e = self._norm01(ent_sc) if ent_sc is not None else torch.zeros_like(g)
        m = self._norm01(mot_sc) if mot_sc is not None else torch.zeros_like(g)
        d = self._norm01(dyn_sc) if dyn_sc is not None else torch.zeros_like(g)

        main_score = 0.55 * g + 0.25 * e + 0.10 * m + 0.10 * d

        # ── Final UGAFR: blend-only TopK (rho/quota/union 없음) ──────────
        # 논문 main method. dynamic reservation, TC guaranteed set 등을
        # 제거해 "learned gate + 3 calibration cues의 단순 가중합 TopK"로 통일.
        #   S = TopK(main_score, K)
        # quota 버전이 필요하면 gate_policy="ugafr_quota"로 분리(아래).
        if self.gate_policy == "ugafr_quota":
            # (ablation 전용) dynamic reservation + TC union 버전
            rho_dyn = 0.15
            K_dyn = min(max(1, int(round(rho_dyn * K))), K)
            K_main = max(0, K - K_dyn)
            idx_parts = []
            if K_main > 0:
                idx_parts.append(torch.topk(main_score, min(K_main, N), dim=1,
                                            largest=True, sorted=False).indices)
            if K_dyn > 0:
                idx_parts.append(torch.topk(d, min(K_dyn, N), dim=1,
                                            largest=True, sorted=False).indices)
            tc_sets = self._build_tc_sets(tc_aux, dev)
            final_idx = self._union_and_refill(
                parts=idx_parts, refill_score=main_score, K=K, N=N, tc_sets=tc_sets)
            return final_idx.clamp(0, N - 1)

        # 기본 UGAFR (논문 main): blend-only TopK
        # [FIX] sorted=True: 2-pass의 [:K1] 슬라이스가 '상위 K1'이 되도록.
        #   (sorted=False면 top-K의 임의 부분집합이 1-pass에 들어가는 버그)
        final_idx = torch.topk(main_score, min(K, N), dim=1,
                               largest=True, sorted=True).indices
        return final_idx.clamp(0, N - 1)

    # ── Fine Mamba ──────────────────────────────────────────────────

    def _gather(self, pa, idx):
        B,T,C,Xp,Yp=pa.shape; N=Xp*Yp; Ns=idx.shape[1]
        # 방어적 clamp: 상위 로직의 인덱스 오류가 있어도 CUDA assertion 방지
        idx_safe = idx.clamp(0, N - 1)
        f = pa.reshape(B,T,C,N).permute(0,1,3,2)   # (B, T, N, C)
        return torch.gather(f, 2, idx_safe[:,None,:,None].expand(B,T,Ns,C))

    def _fine(self, sel):
        """
        Selected patch trajectory를 fine Mamba로 refine.
        반환:
          ref       : gated refined feature (B, T, Ns, C)  — scatter용
          logit_delta: class logit residual (B, T, Ns, num_classes) — 직접 보정용
        """
        B,T,Ns,C=sel.shape
        h=sel.permute(0,2,1,3).reshape(B*Ns,T,C)
        for b in self.fine_mamba: h=b(h)
        refined = self.fine_norm(h).reshape(B,Ns,T,C).permute(0,2,1,3)   # (B,T,Ns,C)

        # [D] Residual update gate: F' = F + alpha·ΔF
        #   여기서는 refined feature 자체를 alpha로 스케일해 과보정 억제.
        delta_feat = refined - sel                      # fine이 만든 feature 변화량
        alpha = torch.sigmoid(self.fine_update_gate(refined))   # (B,T,Ns,1)
        ref = sel + alpha * delta_feat                  # gated refined feature

        # [B] Logit residual: refined feature → class logit delta
        logit_delta = self.fine_logit_head(refined)     # (B,T,Ns,num_classes)
        return ref, logit_delta

    def _scatter(self, pa, ref, idx):
        B,T,C,Xp,Yp=pa.shape; N=Xp*Yp; Ns=idx.shape[1]
        # 방어적 clamp
        idx_safe = idx.clamp(0, N - 1)
        f = pa.reshape(B,T,C,N).permute(0,1,3,2).clone()  # (B, T, N, C)
        f.scatter_(2, idx_safe[:,None,:,None].expand(B,T,Ns,C), ref)
        return f.permute(0,1,3,2).reshape(B,T,C,Xp,Yp)

    def _scatter_logit_delta(self, logit_delta, idx, Tf):
        """
        Fine logit delta(B,T,Ns,ncls)를 full-res logit map(B,Tf,ncls,X,Y)로 scatter.
        선택된 patch 영역만 delta를 채우고 나머지는 0.
        patch(Xp,Yp) → pixel(X,Y)은 nearest 확장(patch 내부 동일 delta).
        """
        B, T, Ns, ncls = logit_delta.shape
        N = self.Xp * self.Yp; ps = self.patch_size
        dev = logit_delta.device
        idx_safe = idx.clamp(0, N - 1)
        # 미래 시점만 사용 (logit_delta는 T_total 길이 → 뒤 Tf개)
        ld = logit_delta[:, self.T_past:]               # (B, Tf, Ns, ncls)
        # patch-level delta map
        dmap = torch.zeros(B, Tf, N, ncls, device=dev, dtype=ld.dtype)
        dmap.scatter_(2, idx_safe[:, None, :, None].expand(B, Tf, Ns, ncls), ld)
        dmap = dmap.permute(0, 1, 3, 2).reshape(B, Tf, ncls, self.Xp, self.Yp)
        # patch → pixel (nearest 확장)
        dmap = dmap.reshape(B * Tf, ncls, self.Xp, self.Yp)
        dmap = F.interpolate(dmap, scale_factor=ps, mode="nearest")
        return dmap.reshape(B, Tf, ncls, self.X, self.Y)

    @torch.no_grad()
    def _residual_entropy(self, patch_feat):
        """
        정제된 patch feature의 잔여 불확실성(엔트로피)을 patch 단위로 측정.
          patch_feat (B,T,C,Xp,Yp) → decoder → softmax → entropy → max over T
        반환: (B, N)  — N=Xp*Yp
        미래 시점만 사용(과거는 정제 대상 아님).
        """
        fut = patch_feat[:, self.T_past:]                       # (B, Tf, C, Xp, Yp)
        B, Tf, C, Xp, Yp = fut.shape
        logits = self.decoder_v2(fut)                           # (B, Tf, ncls, X, Y)
        # pixel → patch 축소 (patch 내 평균)로 patch-level 엔트로피
        p = torch.softmax(logits.float(), dim=2)
        ent = -(p * torch.log(p.clamp(1e-8))).sum(2)            # (B, Tf, X, Y)
        ent = ent.max(dim=1).values.unsqueeze(1)               # (B,1,X,Y)
        ent_patch = F.avg_pool2d(ent, kernel_size=self.patch_size)  # (B,1,Xp,Yp)
        return ent_patch.reshape(B, -1)                        # (B, N)

    @torch.no_grad()
    def _select_residual(self, residual_score, already_idx, K2):
        """
        2-pass 재선택: 이미 선택된(already_idx) patch를 제외하고,
        잔여 불확실성(residual_score, B×N)이 높은 K2개를 새로 고른다.
        반환: (B, K2) 재선택 인덱스.
        """
        B, N = residual_score.shape
        dev = residual_score.device
        # 이미 뽑힌 patch는 -inf로 마스킹 (재선택 방지)
        masked = residual_score.clone()
        neg = torch.full((B, already_idx.shape[1]), float("-inf"), device=dev, dtype=masked.dtype)
        masked.scatter_(1, already_idx.clamp(0, N - 1), neg)
        K2 = min(K2, N)
        idx2 = torch.topk(masked, K2, dim=1, largest=True, sorted=False).indices
        return idx2.clamp(0, N - 1)

    def _compute_fine_selected_loss(self, future_logits, gt_bev_cls, gt_bev_mask,
                                    final_idx, dyn_weight=3.0, ignore_index=255):
        """
        [1단계-A] Fine selected loss.

        선택된 patch가 덮는 pixel 영역에서만 CE를 계산하되,
        dynamic class(bus/car/truck)에 가중치를 줘서
        fine branch가 동적 객체를 실제로 고치도록 학습 신호를 준다.

        future_logits: (B, Tf, ncls, X, Y)
        final_idx:     (B, K) selected patch flat indices
        """
        B, Tf, ncls, X, Y = future_logits.shape
        N = self.Xp * self.Yp; ps = self.patch_size
        dev = future_logits.device

        # selected patch → pixel mask (B, X, Y)
        sel_patch = torch.zeros(B, N, device=dev)
        sel_patch.scatter_(1, final_idx.clamp(0, N - 1), 1.0)
        sel_patch = sel_patch.reshape(B, 1, self.Xp, self.Yp)
        sel_pix = F.interpolate(sel_patch, scale_factor=ps, mode="nearest")
        sel_pix = sel_pix.reshape(B, 1, X, Y).bool()                 # (B,1,X,Y)
        sel_pix = sel_pix.expand(B, Tf, X, Y)                        # (B,Tf,X,Y)

        gt = gt_bev_cls.long()
        mask = gt_bev_mask.bool() & (gt != ignore_index) & sel_pix
        if not mask.any():
            return future_logits.sum() * 0.0

        # dynamic class 가중치
        w = torch.ones(ncls, device=dev)
        for c in DYN_OBJ_CLASSES:
            if c < ncls: w[c] = dyn_weight

        with autocast(device_type="cuda", enabled=False):
            logf = future_logits.float().permute(0,1,3,4,2).reshape(-1, ncls)
            gtf = gt.reshape(-1)
            mf = mask.reshape(-1)
            ce = F.cross_entropy(logf[mf], gtf[mf], weight=w,
                                 ignore_index=ignore_index, reduction="mean")
        return torch.nan_to_num(ce, nan=0.0, posinf=5.0, neginf=0.0)

    # ── Forward ─────────────────────────────────────────────────────

    def forward(
        self, x, rots, trans, intrins, post_rots, post_trans,
        *, ego2globals=None, gt_bev_cls=None, gt_bev_mask=None,
        motion_patch=None, lidar_bev=None, return_aux=False,
    ):
        step=int(self._step.item()); B,T=x.shape[:2]; dev=x.device
        assert T==self.T_past

        # [SampledK/Q1] forward당 1회 K 샘플. DDP에서는 rank마다 독립적으로
        #   샘플되므로 스텝당 여러 K가 동시에 학습됨(예산 다양성 관점에서 의도된 동작).
        self._sampled_K=None
        if self.training and self.sampled_k_enabled:
            _Np=self.Xp*self.Yp
            _k_lo=max(1,int(round(self.sampled_k_min_frac*_Np)))
            _k_hi=max(_k_lo,int(round(self.sampled_k_max_frac*_Np)))
            self._sampled_K=int(torch.randint(_k_lo,_k_hi+1,(1,)).item())

        # 1. Voxels
        vl=[self.get_voxel_feat_one_t(x[:,t],rots[:,t],trans[:,t],intrins[:,t],post_rots[:,t],post_trans[:,t]) for t in range(T)]

        # 2. 3D Backbone
        if self.use_spatial_backbone: vl=[self.spatial_backbone(v) for v in vl]

        # 3. Ego Warping
        if self.use_ego_warping and ego2globals is not None and ego2globals.shape[1]>=T:
            for t in range(T-1):
                Tw=torch.linalg.inv(ego2globals[:,t])@ego2globals[:,T-1]
                vl[t]=self.ego_warper(vl[t],Tw,self.dx,self.bx,self.nx)

        # 4. ZPC → 2D BEV
        self.z_collapse.set_step(step)
        bev_past=torch.stack([self.z_collapse(v) for v in vl],dim=1)

        # ZPC Height-prior Lvox (training only)
        vox_height_loss=torch.tensor(0.0,device=dev)
        if self.training and gt_bev_cls is not None:
            vox_height_loss=self.z_collapse.compute_height_prior_loss(vl[-1],gt_bev_cls)

        # 5. TC-STOP
        track_loss=torch.tensor(0.0,device=dev); tc_aux={}
        fut_delta=torch.zeros(B,self.T_future,self.C,self.X,self.Y,device=dev,dtype=bev_past.dtype)
        if self.tc_enabled:
            fut_delta,track_loss,tc_aux=self.tcstop(bev_past)
            track_loss=track_loss*self.tc_loss_weight

        # 6. Future init
        fut_init=self._ego_extrap(bev_past,ego2globals)+fut_delta

        # 7. Coarse Mamba
        pp=self.patch_embed(bev_past); pf=self.patch_embed(fut_init)
        pa=self.coarse_mamba(pp,pf)

        # 8. Gate — [recall] 미래 프레임별 예측 후 max로 union
        gate_logit,gate_logit_pf=self.gate_head(pa,self.T_past,return_per_frame=True)
        gate_score=torch.sigmoid(gate_logit.float())

        # 8b. [MotionSrc] head 모드에서만 학습형 logit/loss 계산.
        #   feat 모드에서는 head loss가 pa를 통해 coarse에 gradient를 흘려
        #   구 main과 학습 레시피가 달라지는 것을 방지하기 위해 완전 비활성.
        motion_logit=None
        motion_head_loss=None
        if self.motion_source=="head":
            motion_logit=self.motion_head(pa,self.T_past)      # (B,Tf,Xp,Yp)
            if self.training and motion_patch is not None:
                motion_head_loss=self.motion_head.supervision_loss(
                    motion_logit,motion_patch)*self.motion_head_loss_w

        # 9. Coarse skip + full BEV
        bev_base=self._build_bev(bev_past,fut_init)
        # [CARLA] fuse current-frame LiDAR BEV into the future(=current) feature so
        # it reaches coarse, fine, and final decoders alike.
        if self.lidar_fusion and self.lidar_encoder is not None and lidar_bev is not None:
            lf=self.lidar_encoder(lidar_bev.to(bev_base.dtype))          # (B,C,X,Y)
            bev_base=bev_base.clone()
            bev_base[:,self.T_past:]=bev_base[:,self.T_past:]+lf.unsqueeze(1)
        bev_all=bev_base+self._upsample(pa)

        # 10. UGAFR Gate Supervision (training only)
        #   occprophet_dense baseline은 gate를 쓰지 않으므로 스킵.
        gate_loss=None; gate_info={}
        if (self.gate_policy not in ("occprophet_dense", "no_refinement", "coarse_only")
                and self.ugafr_enabled and gt_bev_cls is not None and gt_bev_mask is not None):
            with torch.no_grad():
                cl_sup=self.decoder_v2(bev_all[:,self.T_past:].detach())
            Kn=self._cur_topk(step)
            gate_loss,gate_info=self.gate_supervisor(
                cl_sup,gate_logit.detach(),gt_bev_cls,gt_bev_mask,
                motion_patch,tc_aux,patch_size=self.patch_size,K_topk=Kn,
                gate_logit_per_frame=gate_logit_pf.detach())
            gl_g,_=self.gate_supervisor(
                cl_sup,gate_logit,gt_bev_cls,gt_bev_mask,
                motion_patch,tc_aux,patch_size=self.patch_size,K_topk=Kn,
                gate_logit_per_frame=gate_logit_pf)
            gate_loss=gl_g*self.ugafr_loss_weight

        # 11. [v2] Policy-aware patch selection
        #   occprophet_dense는 전체 refine이라 selection 불필요 → 스킵
        if self.gate_policy == "occprophet_dense":
            final_idx = None
        else:
            with torch.no_grad():
                final_idx=self._select_patches(gate_score,motion_patch,pa,tc_aux,step)

        # ── [baseline] OccProphet-style dense refine ───────────────────
        # gate_policy="occprophet_dense"이면 selective fine 대신
        # 전체 future patch를 dense refiner로 refine (외부 비교 baseline).
        if self.gate_policy == "occprophet_dense":
            if self.occprophet_refiner is None:
                raise RuntimeError(
                    "gate_policy='occprophet_dense'인데 occprophet_refiner가 None입니다. "
                    "baseline_occprophet_refiner.py가 models_bev.py와 같은 폴더에 있는지 "
                    "확인하세요 (import 실패 시 silent fallback 방지).")
            # [진단] occprophet 분기 진입 확인 (첫 step에만)
            if self.training and int(self._step.item()) <= 1:
                print(f"[occprophet] dense refine 분기 진입 OK "
                      f"(refiner trainable={any(p.requires_grad for p in self.occprophet_refiner.parameters())})")
            # 실제 OccProphet Refiner: [past⊕future] 전체를 함께 refine 후 future만 사용
            pa_ref = self.occprophet_refiner(pa)                  # (B,T_total,C,Xp,Yp)
            bev_all = bev_base + self._upsample(pa_ref)
            future_logits = self.decoder_v2(bev_all[:, self.T_past:])
            fine_selected_loss = torch.tensor(0.0, device=dev)
            # hit-rate 등 공통 aux는 아래 14에서 final_idx로 계산되지만
            # occprophet_dense는 전체 refine이므로 hit 개념이 다름 → n_sel=N으로 표기
            phr_target=0.0; phr_movable=0.0; n_target=0
            if gt_bev_cls is not None:
                all_idx = torch.arange(self.Xp*self.Yp, device=dev).unsqueeze(0).expand(B,-1)
                hr_t=GatePolicyRouter.compute_patch_hit_rate(
                    all_idx,gt_bev_cls,self.patch_size,self.Xp,self.Yp,DYN_OBJ_CLASSES)
                phr_target=hr_t["hit_rate"]; n_target=hr_t["n_target"]
            coarse_bev=bev_base+self._upsample(pa)
            coarse_logits_det=self.decoder_v2(coarse_bev[:,self.T_past:]).detach()
            self._last_aux={
                "step":step,"routing_phase":"occprophet_dense","gate_policy":self.gate_policy,
                "gate_loss":gate_loss,"track_loss":track_loss,
                "motion_head_loss":motion_head_loss,
                "fine_selected_loss":fine_selected_loss,
                "gate_info":gate_info,"tc_aux":tc_aux,
                "n_sel_patches":self.Xp*self.Yp,
                "gate_score_mean":gate_score.mean().item(),
                "vox_height_loss":vox_height_loss,
                "patch_hit_rate_small":phr_target,"patch_hit_rate_dyn":phr_target,
                "patch_hit_rate_target":phr_target,"patch_hit_rate_movable":phr_target,
                "n_small_patches":n_target,"n_dyn_patches":n_target,
                "gate_score_flat":gate_score.detach(),
                "selected_patch_idx":all_idx.detach() if gt_bev_cls is not None
                                     else torch.empty(B,0,device=dev,dtype=torch.long),
                "coarse_logits":coarse_logits_det,
            }
            return (future_logits, self._last_aux) if return_aux else future_logits

        # 12. Fine Mamba — 2-Pass Adaptive Re-selection
        #   Pass 1: UGAFR가 고른 patch를 정제
        #   Pass 2: 정제 후에도 불확실한 patch를 재선택해 한 번 더 정제
        #           (전체 예산 K = K1 + K2로 고정 → 공정 비교)
        fine_logit_delta_full = None   # (B, Tf, ncls, X, Y) or None
        pr_ = pa                       # 정제 누적본 (초기엔 coarse)
        bev_all = bev_base + self._upsample(pa)   # 기본: coarse (fine 없을 때)
        if final_idx.shape[1] > 0:
            do_two_pass = (self.two_pass_enabled
                           and self.gate_policy in ("ugafr", "ugafr_quota")
                           and self._phase(step) == "topk")   # 안정화 후에만 2-pass

            if do_two_pass:
                K_total = final_idx.shape[1]
                K2 = max(1, int(round(self.two_pass_ratio * K_total)))
                K1 = max(1, K_total - K2)
                idx1 = final_idx[:, :K1]                     # 1-pass: 상위 K1
            else:
                idx1 = final_idx                             # 단일 pass (기존 동작)

            # ── Pass 1 ──────────────────────────────────────────────
            sel1 = self._gather(pr_, idx1)
            ref1, ld1 = self._fine(sel1)
            pr_ = self._scatter(pr_, ref1, idx1)
            delta1 = self._scatter_logit_delta(ld1, idx1, self.T_future)
            fine_logit_delta_full = delta1
            used_idx = idx1

            # ── Pass 2 (adaptive re-selection) ─────────────────────
            if do_two_pass:
                # 정제된 pr_로 잔여 불확실성 재측정 → 안 뽑힌 곳 중 top K2
                res_score = self._residual_entropy(pr_)          # (B, N)
                idx2 = self._select_residual(res_score, idx1, K2)
                sel2 = self._gather(pr_, idx2)
                ref2, ld2 = self._fine(sel2)                     # 같은 refiner 재사용
                pr_ = self._scatter(pr_, ref2, idx2)
                delta2 = self._scatter_logit_delta(ld2, idx2, self.T_future)
                fine_logit_delta_full = delta1 + delta2          # 두 pass의 보정 합산
                # hit-rate/loss는 전체 선택(1+2 pass)을 기준으로
                used_idx = torch.cat([idx1, idx2], dim=1)

            bev_all = bev_base + self._upsample(pr_)
            final_idx = used_idx        # 이후 loss/hit-rate가 전체 선택을 보도록 갱신

        # 13. Decode
        future_logits=self.decoder_v2(bev_all[:,self.T_past:])
        # [B] coarse logits + beta · fine logit delta (직접 보정, decoder 희석 방지)
        #   beta ramp: fine_disable_steps 이전엔 0, 이후 ramp_steps 동안 0→1로 증가.
        if fine_logit_delta_full is not None:
            beta_ramp = self._beta_ramp(step)
            future_logits = future_logits + beta_ramp * self.fine_logit_beta * fine_logit_delta_full

        # [A] Fine selected loss (training only)
        #   선택된 patch 영역에서만 dynamic 가중 CE를 부과 →
        #   fine branch가 "고른 곳을 실제로 고치도록" 직접 신호를 받음.
        fine_selected_loss = torch.tensor(0.0, device=dev)
        if (self.training and gt_bev_cls is not None and gt_bev_mask is not None
                and final_idx.shape[1] > 0):
            fine_selected_loss = self._compute_fine_selected_loss(
                future_logits, gt_bev_cls, gt_bev_mask, final_idx)


        # 14. [v2] Patch hit-rate
        #   target/dyn: UGAFR 핵심 타깃인 bus/car/truck 기준
        #   movable   : 일반 movable 계열 참고 지표
        phr_target=0.0; phr_movable=0.0; n_target=0
        if gt_bev_cls is not None and final_idx.shape[1]>0:
            hr_t=GatePolicyRouter.compute_patch_hit_rate(
                final_idx,gt_bev_cls,self.patch_size,self.Xp,self.Yp,DYN_OBJ_CLASSES)
            hr_m=GatePolicyRouter.compute_patch_hit_rate(
                final_idx,gt_bev_cls,self.patch_size,self.Xp,self.Yp,MOVABLE_CLASSES)
            phr_target=hr_t["hit_rate"]; phr_movable=hr_m["hit_rate"]; n_target=hr_t["n_target"]

        # coarse_logits: Fine Mamba 적용 전 예측 (Panel C Coarse vs UGAFR 비교용)
        # gate_score_flat: UGAFR gate score map (Panel A 시각화용)
        # selected_patch_idx: Fine Mamba 수행된 patch 인덱스 (Panel B 시각화용)
        coarse_bev  = bev_base + self._upsample(pa)
        coarse_logits_det = self.decoder_v2(coarse_bev[:, self.T_past:]).detach()

        self._last_aux={
            "step":step, "routing_phase":self._phase(step),
            "gate_policy":self.gate_policy,
            "gate_loss":gate_loss, "track_loss":track_loss,
            "motion_head_loss":motion_head_loss,
            "fine_selected_loss":fine_selected_loss,
            "gate_info":gate_info, "tc_aux":tc_aux,
            "n_sel_patches":int(final_idx.shape[1]) if final_idx.numel() else 0,
            "gate_score_mean":gate_score.mean().item(),
            "vox_height_loss":vox_height_loss,
            # [v2] UGAFR selection quality
            # 하위 호환을 위해 small/dyn 키는 target bus/car/truck 기준으로 유지
            "patch_hit_rate_small": phr_target,
            "patch_hit_rate_dyn":   phr_target,
            "patch_hit_rate_target": phr_target,
            "patch_hit_rate_movable": phr_movable,
            "n_small_patches":      n_target,
            "n_dyn_patches":        n_target,
            # [viz] Panel A: UGAFR gate map
            "gate_score_flat":     gate_score.detach(),         # (B, Xp*Yp)
            # [viz] Panel B: selected patch indices
            "selected_patch_idx":  final_idx.detach(),          # (B, K)
            # [viz] Panel C: coarse vs UGAFR 비교
            "coarse_logits":       coarse_logits_det,           # (B, T_fut, K, X, Y)
        }
        return (future_logits, self._last_aux) if return_aux else future_logits


# ──────────────────────────────────────────────────────────────────────
#  Compile helper
# ──────────────────────────────────────────────────────────────────────

def compile_model_bev(grid_conf, data_aug_conf, **kwargs):
    return LiftSplatShoot4DBEV(grid_conf=grid_conf, data_aug_conf=data_aug_conf, **kwargs)