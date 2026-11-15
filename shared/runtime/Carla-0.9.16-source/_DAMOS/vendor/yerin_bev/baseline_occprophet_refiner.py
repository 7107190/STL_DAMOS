"""
baseline_occprophet_refiner.py
================================================================
OccProphet Forecaster-Refiner — 외부 비교 baseline (2D BEV 이식)

[원 논문/코드] OccProphet (ICLR 2025, arXiv:2502.15180)
  github.com/JLChen-C/OccProphet
  실제 구현: projects/occ_plugin/occupancy/necks/unet_trans.py

[실제 코드 구조 — 논문 추상명 ↔ 코드 클래스]
  Observer  → UNet (pre_unet)        과거 feature 인코딩
  Forecaster→ ConditionalGenerator   scene-condition dynamic conv로 미래 예측
  Refiner   → UNet (post_unet)       [past⊕future] cross-frame refine
  TAF       → HybridFusion           scene/bev/height 3-branch + temporal attn
  E4A       → UNet                   downsample→HybridFusion→upsample+skip

[실제 코드의 핵심 디테일 — 본 재구현이 충실히 반영]
  1. HybridFusion(TAF): scene(GAP→Conv→TempAttn) + bev(W-MSA+FFN→TempAttn)
     + height(3D 전용) → bev += scene[bc]; out = bev + height[bc] (+residual)
  2. TemporalAttention: learnable temporal PE + causal mask(tril) + MHA+FFN
  3. ConditionalGenerator(Forecaster): scene로 dynamic conv kernel 생성→미래
  4. UNetPredictor: pre_unet → pred_module → post_unet([past⊕future] refine 후
     future만 반환). Refiner가 past+future를 함께 보는 게 cross-frame 핵심.

[2D BEV 이식]
  BEACON은 2D BEV patch(Z collapse). height branch 제거, Conv3d→Conv2d.
  mmcv 의존(ShiftWindowMSA/FFN/ConvModule)은 표준 torch로 대체.
  나머지(W-MSA, causal temporal attn, dynamic-conv forecaster,
  past⊕future concat refine)는 실제 코드 그대로 유지.

[공정성]
  BEACON과 backbone/coarse/decoder 공유, refine 전략만
  "dense(전체) vs UGAFR selective" 비교. 3D 원본과 직접 숫자 비교는
  output representation이 달라 부적절.
"""

import math
import torch
from torch import nn
import torch.nn.functional as F


# ── TemporalAttention (OccProphet 실제 코드 충실 이식) ─────────────────
class TemporalAttention(nn.Module):
    def __init__(self, embed_dims, num_heads, timesteps, with_pe=True):
        super().__init__()
        self.with_pe = with_pe
        if with_pe:
            self.temporal_pe = nn.Parameter(torch.randn(timesteps, embed_dims))
        self.pre_norm  = nn.LayerNorm(embed_dims)
        self.attn = nn.MultiheadAttention(embed_dims, num_heads, batch_first=True)
        self.post_norm = nn.LayerNorm(embed_dims)
        self.ffn = nn.Sequential(
            nn.Linear(embed_dims, 4 * embed_dims), nn.GELU(),
            nn.Linear(4 * embed_dims, embed_dims))
        self.out_norm = nn.LayerNorm(embed_dims)

    @staticmethod
    def causal_mask(T, device):
        m = torch.ones((T, T), dtype=torch.bool, device=device).tril()
        return ~m   # True = attend 금지 → 미래가 과거만 본다

    def forward(self, x):
        ndim = x.ndim
        if ndim == 4:
            B, T, N, C = x.shape
            x = x.transpose(1, 2).reshape(B * N, T, C)
        else:
            B, T, C = x.shape
        mask = self.causal_mask(T, x.device)
        if self.with_pe:
            x = x + self.temporal_pe[None, :, :]
        xn = self.pre_norm(x)
        x = x + self.attn(xn, xn, xn, attn_mask=mask, need_weights=False)[0]
        x = x + self.ffn(self.post_norm(x))
        x = self.out_norm(x)
        if ndim == 4:
            x = x.reshape(B, N, T, C).transpose(1, 2).contiguous()
        return x


# ── 간이 Window-MSA (mmcv ShiftWindowMSA 대체) ────────────────────────
class WindowMSA2D(nn.Module):
    def __init__(self, dim, num_heads, window_size=7):
        super().__init__()
        self.ws = window_size
        self.attn = nn.MultiheadAttention(dim, num_heads, batch_first=True)

    def forward(self, x, H, W):
        B, N, C = x.shape; ws = self.ws
        x = x.view(B, H, W, C)
        pad_h = (ws - H % ws) % ws; pad_w = (ws - W % ws) % ws
        x = F.pad(x, (0, 0, 0, pad_w, 0, pad_h))
        Hp, Wp = H + pad_h, W + pad_w
        x = x.view(B, Hp // ws, ws, Wp // ws, ws, C)
        x = x.permute(0, 1, 3, 2, 4, 5).reshape(-1, ws * ws, C)
        x, _ = self.attn(x, x, x, need_weights=False)
        x = x.view(B, Hp // ws, Wp // ws, ws, ws, C)
        x = x.permute(0, 1, 3, 2, 4, 5).reshape(B, Hp, Wp, C)
        return x[:, :H, :W, :].reshape(B, N, C)


# ── HybridFusion 2D (TAF, height branch 제거) ─────────────────────────
class HybridFusion2D(nn.Module):
    def __init__(self, embed_dims, num_heads, window_size, timesteps, residual=False):
        super().__init__()
        self.residual = residual
        self.scene_branch = nn.Sequential(
            nn.AdaptiveAvgPool2d(1), nn.Conv2d(embed_dims, embed_dims, 1),
            nn.GroupNorm(min(8, embed_dims), embed_dims))
        self.scene_temporal_attn = TemporalAttention(embed_dims, num_heads, timesteps)
        self.bev_pre_norm  = nn.LayerNorm(embed_dims)
        self.bev_msa = WindowMSA2D(embed_dims, num_heads, window_size)
        self.bev_post_norm = nn.LayerNorm(embed_dims)
        self.bev_ffn = nn.Sequential(
            nn.Linear(embed_dims, 4 * embed_dims), nn.GELU(),
            nn.Linear(4 * embed_dims, embed_dims))
        self.bev_out_norm = nn.LayerNorm(embed_dims)
        self.bev_temporal_attn = TemporalAttention(embed_dims, num_heads, timesteps)

    def forward(self, feats):
        # feats: (B, T, C, H, W)
        B, T, C, H, W = feats.shape
        scene = self.scene_branch(feats.reshape(B * T, C, H, W)).reshape(B, T, C)
        scene = self.scene_temporal_attn(scene)
        bev = feats.permute(0, 1, 3, 4, 2).reshape(B * T, H * W, C)
        bev = bev + self.bev_msa(self.bev_pre_norm(bev), H, W)
        bev = bev + self.bev_ffn(self.bev_post_norm(bev))
        bev = self.bev_out_norm(bev).reshape(B, T, H * W, C)
        bev = self.bev_temporal_attn(bev)
        bev = bev + scene[:, :, None, :]
        out = bev.transpose(-2, -1).reshape(B, T, C, H, W)
        if self.residual:
            out = out + feats
        return out


# ── UNet2D (E4A) ──────────────────────────────────────────────────────
class UNet2D(nn.Module):
    def __init__(self, in_channels, embed_dims, num_heads_base, timesteps,
                 window_size=7, downsample_layers=2, residual=False):
        super().__init__()
        self.n = downsample_layers
        self.in_proj = (nn.Conv2d(in_channels, embed_dims, 3, padding=1)
                        if in_channels != embed_dims else None)
        self.down = nn.ModuleList(); c = embed_dims
        for _ in range(downsample_layers):
            self.down.append(nn.Sequential(
                nn.Conv2d(c, 2 * c, 3, stride=2, padding=1),
                nn.GroupNorm(min(8, 2 * c), 2 * c), nn.GELU())); c *= 2
        self.up_plugin = nn.ModuleList(); self.up = nn.ModuleList()
        for _ in range(downsample_layers):
            nh = max(1, c // num_heads_base)
            self.up_plugin.append(HybridFusion2D(c, nh, window_size, timesteps, residual))
            self.up.append(nn.Sequential(
                nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False),
                nn.Conv2d(c, c // 2, 3, padding=1),
                nn.GroupNorm(min(8, c // 2), c // 2), nn.GELU())); c //= 2

    def forward(self, feats):
        B, T, C, H, W = feats.shape
        x = feats
        if self.in_proj is not None:
            x = self.in_proj(x.reshape(B * T, C, H, W)).reshape(B, T, -1, H, W)
        skips = [x]; cur = x
        for i in range(self.n):
            B_, T_, C_, Hc, Wc = cur.shape
            d = self.down[i](cur.reshape(B_ * T_, C_, Hc, Wc))
            _, Cd, Hd, Wd = d.shape
            cur = d.reshape(B_, T_, Cd, Hd, Wd)
            if i != self.n - 1:
                skips.append(cur)
        for i in range(self.n):
            cur = self.up_plugin[i](cur)
            B_, T_, C_, Hc, Wc = cur.shape
            u = self.up[i](cur.reshape(B_ * T_, C_, Hc, Wc))
            _, Cu, Hu, Wu = u.shape
            cur = u.reshape(B_, T_, Cu, Hu, Wu)
            skip = skips[-(i + 1)]
            if cur.shape[-2:] != skip.shape[-2:]:
                cur = F.interpolate(cur.reshape(B_ * T_, Cu, Hu, Wu),
                                    size=skip.shape[-2:], mode="bilinear",
                                    align_corners=False).reshape(B_, T_, Cu, *skip.shape[-2:])
            cur = cur + skip
        return cur


# ── ConditionalGenerator 2D (Forecaster, 참고용) ──────────────────────
class ConditionalGenerator2D(nn.Module):
    def __init__(self, in_timesteps, out_timesteps, channels, num_heads, norm_and_act=True):
        super().__init__()
        self.Ti = in_timesteps; self.To = out_timesteps; self.C = channels
        self.scene = nn.Sequential(
            nn.AdaptiveAvgPool2d(1), nn.Conv2d(channels, channels, 1),
            nn.GroupNorm(min(8, channels), channels))
        self.scene_attn = TemporalAttention(channels, num_heads, in_timesteps)
        self.pred_kernel = nn.Linear(in_timesteps * channels,
                                     out_timesteps * channels * in_timesteps * channels)
        self.norm_and_act = norm_and_act
        if norm_and_act:
            self.norm = nn.GroupNorm(min(8, out_timesteps * channels), out_timesteps * channels)
            self.act = nn.ReLU(True)

    def forward(self, feats):
        B, Ti, C, H, W = feats.shape; To = self.To
        scene = self.scene(feats.reshape(B * Ti, C, H, W)).reshape(B, Ti, C)
        scene = self.scene_attn(scene).reshape(B, -1)
        kernel = self.pred_kernel(scene).view(B * To * C, Ti * C, 1, 1)
        x = feats.reshape(1, B * Ti * C, H, W)
        pred = F.conv2d(x, kernel, groups=B).view(B, To * C, H, W)
        if self.norm_and_act:
            pred = self.act(self.norm(pred))
        return pred.view(B, To, C, H, W)


# ── OccProphetDenseRefiner2D — BEACON baseline 본체 ───────────────────
class OccProphetDenseRefiner2D(nn.Module):
    """
    OccProphet Refiner(post_unet)의 BEACON 2D BEV 이식.

    BEACON coarse forecaster가 future patch를 이미 만드므로,
    이 모듈은 [past ⊕ future] full patch를 받아 UNet(E4A)+HybridFusion으로
    cross-frame refine. ★ 전체 patch refine (UGAFR selective와 대비).

    입력/출력: (B, T_total, C, Xp, Yp)  [BEACON forward에서 future만 decode]
    residual gamma=0 초기화 → 학습 초기 입력 보존(공정한 출발).
    """
    def __init__(self, C, T_total, num_heads_base=8, window_size=7,
                 downsample_layers=2, residual=True):
        super().__init__()
        self.residual = residual
        self.refiner_unet = UNet2D(
            in_channels=C, embed_dims=C, num_heads_base=num_heads_base,
            timesteps=T_total, window_size=window_size,
            downsample_layers=downsample_layers, residual=True)
        self.out_proj = nn.Sequential(
            nn.Conv2d(C, C, 3, padding=1), nn.GroupNorm(min(8, C), C), nn.GELU(),
            nn.Conv2d(C, C, 1))
        if residual:
            # gamma=0이면 refiner 출력이 0배되어 gradient가 안 흐른다
            # (특히 freeze 모드에서 refiner만 학습할 때 치명적).
            # 0.1로 시작해 refiner gradient를 보장하면서도 초기 안정성 유지.
            self.gamma = nn.Parameter(torch.tensor(0.1))

    def forward(self, full_patch_feat):
        B, T, C, Xp, Yp = full_patch_feat.shape
        refined = self.refiner_unet(full_patch_feat)
        delta = self.out_proj(refined.reshape(B * T, C, Xp, Yp)).reshape(B, T, C, Xp, Yp)
        if self.residual:
            return full_patch_feat + self.gamma * delta
        return delta


# ── Self-test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    torch.manual_seed(0)
    B, T_total, T_past, C, Xp, Yp = 1, 16, 6, 32, 50, 50
    print("=== OccProphet Refiner 2D (실제 코드 기반) self-test ===")

    ta = TemporalAttention(C, 4, T_total)
    print(f"TemporalAttention 3D: {tuple(ta(torch.randn(B,T_total,C)).shape)}")
    print(f"TemporalAttention 4D: {tuple(ta(torch.randn(B,T_total,Xp*Yp,C)).shape)}")

    xf = torch.randn(B, T_total, C, Xp, Yp)
    hf = HybridFusion2D(C, 4, 7, T_total, residual=True)
    assert hf(xf).shape == xf.shape
    print(f"HybridFusion2D(TAF): {tuple(xf.shape)} → {tuple(hf(xf).shape)}")

    unet = UNet2D(C, C, num_heads_base=8, timesteps=T_total, downsample_layers=2)
    assert unet(xf).shape == xf.shape
    print(f"UNet2D(E4A): OK {tuple(unet(xf).shape)}")

    cg = ConditionalGenerator2D(T_past, 10, C, 4)
    past = torch.randn(B, T_past, C, Xp, Yp)
    assert cg(past).shape == (B, 10, C, Xp, Yp)
    print(f"ConditionalGenerator2D(Forecaster): OK {tuple(cg(past).shape)}")

    refiner = OccProphetDenseRefiner2D(C, T_total, residual=True)
    o = refiner(xf)
    assert o.shape == xf.shape
    diff = (o - xf).abs().max().item()
    print(f"DenseRefiner: {tuple(o.shape)}, init diff(gamma=0.1)={diff:.4f}")
    assert diff > 0, "gamma=0.1이면 refiner 출력이 입력과 달라야 (grad 흐름)"
    o.sum().backward()
    # refiner 내부 weight에 grad가 흐르는지 확인 (freeze 학습 핵심)
    g_internal = refiner.refiner_unet.down[0][0].weight.grad
    assert g_internal is not None and g_internal.abs().sum() > 0, \
        "refiner 내부 weight에 grad가 안 흐름!"
    print(f"backward OK, gamma.grad={refiner.gamma.grad.item():.2f}, "
          f"refiner 내부 grad 흐름 확인")
    print(f"refiner params: {sum(p.numel() for p in refiner.parameters()):,}")
    print("\n✓ 모든 self-test 통과 (실제 OccProphet 구조 충실 이식)")