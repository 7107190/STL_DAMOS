"""
data_bev_carla.py — multi-agent CARLA dataset, drop-in for BEVForecastDataset.

Reinterprets the standard constructor:
    nusc_root  -> image root  (localized: /mnt/Data/carla_columbus/<uid>/)
    bev_root   -> GT root     (/mnt/Data/carla_columbus_bev/<uid>/npz/<token>.npz)
Task = CURRENT-frame reconstruction: input past_len frames ending at t0,
target = t0's BEV (npz stores T=1). future_len is ignored (kept for signature).

train/val split: per-agent temporal — first (1-val_frac) of each agent's
windows = train, last val_frac = val. Ensures both splits span all conditions.
"""
import os, json, glob
from pathlib import Path
import numpy as np, torch
from torch.utils.data import Dataset
from nuscenes.nuscenes import NuScenes

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src_2"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_bev import (_sample_aug, _get_cam_data, _get_ego2global, CAMS_6)
import carla_gt_core as G

VAL_FRAC = 0.1
# LiDAR BEV fusion input (option 3): rasterize current-frame LIDAR_TOP into the
# 200x200 grid, 6 channels [occupied, log_count, max_z, min_z, z_range, max_intensity].
USE_LIDAR = os.environ.get("CARLA_LIDAR", "") == "1"
LIDAR_CH = 6
_XB, _YB = G.XB, G.YB
_NX, _NY = G.NX, G.NY

def _lidar_bev_from_points(pts_ego, intensity):
    """pts_ego (M,3) already Y-flipped ego frame; intensity (M,). -> (6,NX,NY)."""
    out = np.zeros((LIDAR_CH, _NX, _NY), np.float32)
    if len(pts_ego) == 0: return out
    xi = np.floor((pts_ego[:,0]-_XB[0])/_XB[2]).astype(np.int64)
    yi = np.floor((pts_ego[:,1]-_YB[0])/_YB[2]).astype(np.int64)
    ok = (xi>=0)&(xi<_NX)&(yi>=0)&(yi<_NY)
    xi, yi = xi[ok], yi[ok]; z = pts_ego[ok,2]; inten = intensity[ok]
    if len(xi) == 0: return out
    flat = xi*_NY + yi
    n = _NX*_NY
    cnt = np.bincount(flat, minlength=n).astype(np.float32)
    zmax = np.full(n, -1e4, np.float32); np.maximum.at(zmax, flat, z)
    zmin = np.full(n,  1e4, np.float32); np.minimum.at(zmin, flat, z)
    imax = np.zeros(n, np.float32);      np.maximum.at(imax, flat, inten)
    occ = cnt > 0
    zmax[~occ] = 0; zmin[~occ] = 0
    out[0] = occ.reshape(_NX,_NY)
    out[1] = np.log1p(cnt).reshape(_NX,_NY)
    out[2] = zmax.reshape(_NX,_NY)
    out[3] = zmin.reshape(_NX,_NY)
    out[4] = (zmax - zmin).reshape(_NX,_NY)
    out[5] = imax.reshape(_NX,_NY)
    return out
# Small/vulnerable classes dilated by DILATE cells (3x3 when 1) so tiny 1-2 cell
# targets are learnable and a 1-cell offset is tolerated in both train & eval.
# Dilate lowest-priority first so higher-priority (pedestrian) wins overlaps;
# only expand into background / ignore, never over other foreground vehicles.
DILATE = int(os.environ.get("CARLA_DILATE", "0"))
# per-class dilation radius (option 1): pedestrians (moving, night, tiny) get a
# wider tolerance than the static object/prop class which already works at r=1.
_DILATE_ORDER  = [7, 8, 2, 6]                      # pedestrian first = claims cells before lower-priority
_DILATE_RADIUS = {7: 3, 8: 1, 2: 2, 6: 2}         # ped 3, object 1, bicycle/moto 2
_DILATE_INTO   = {255, 0, 1, 11, 12, 13, 14, 15, 16}
# option 2: restrict to daytime sessions when CARLA_DAYTIME=1
DAYTIME_ONLY = os.environ.get("CARLA_DAYTIME", "") == "1"
def _is_daytime(uid): return ("Noon" in uid) or ("Sunset" in uid)
def _bin_dilate(m, r):
    """Square dilation of a boolean mask by radius r — pure numpy (no cv2/threads,
    safe inside forked DataLoader workers)."""
    out = m.astype(bool); acc = out.copy(); H, W = out.shape
    for dy in range(-r, r+1):
        for dx in range(-r, r+1):
            if dy == 0 and dx == 0: continue
            ys0, ys1 = max(0, dy), min(H, H+dy); yt0, yt1 = max(0, -dy), min(H, H-dy)
            xs0, xs1 = max(0, dx), min(W, W+dx); xt0, xt1 = max(0, -dx), min(W, W-dx)
            acc[yt0:yt1, xt0:xt1] |= out[ys0:ys1, xs0:xs1]
    return acc

def _dilate_small(bev, mask, r):
    allowed = np.isin(bev, list(_DILATE_INTO))
    for c in _DILATE_ORDER:
        m = (bev == c)
        if not m.any(): continue
        rc = _DILATE_RADIUS.get(c, r)
        grow = _bin_dilate(m, rc) & allowed
        bev[grow] = c; mask[grow] = 1
        allowed &= ~grow                            # higher-priority (earlier) class keeps its fill
    return bev, mask

class CarlaBEVDataset(Dataset):
    def __init__(self, bev_root, split, nusc_version, nusc_root,
                 data_aug_conf, grid_conf, past_len=6, future_len=1,
                 is_train=True, cams=None, token_list_file=None):
        self.data_aug_conf = data_aug_conf; self.grid_conf = grid_conf
        self.past_len = past_len; self.is_train = is_train; self.split = split
        self.cams = cams or data_aug_conf.get("cams", CAMS_6)
        self.gt_root = bev_root; self.img_root = nusc_root
        self.version = nusc_version

        uids = sorted(d for d in os.listdir(self.gt_root)
                      if os.path.isdir(os.path.join(self.gt_root, d)) and not d.startswith("_"))
        _filt = os.environ.get("CARLA_AGENTS", "").strip()
        if _filt:
            keep = set(_filt.split(","))
            uids = [u for u in uids if u in keep]
        if DAYTIME_ONLY:
            uids = [u for u in uids if _is_daytime(u)]
        self.nuscs = []           # per-agent NuScenes
        self.npz_dirs = []        # per-agent npz dir
        self.ixes = []            # (agent_idx, [past sample recs], t0_token)
        n_train = n_val = 0
        for uid in uids:
            npz_dir = os.path.join(self.gt_root, uid, "npz")
            img_ds  = os.path.join(self.img_root, uid)
            if not os.path.isdir(img_ds):     # images not localized yet -> skip
                continue
            valid = {Path(p).stem for p in glob.glob(os.path.join(npz_dir, "*.npz"))}
            if not valid: continue
            nusc = NuScenes(version=self.version, dataroot=img_ds, verbose=False)
            ai = len(self.nuscs); self.nuscs.append(nusc); self.npz_dirs.append(npz_dir)
            # build per-scene ordered windows
            agent_items = []
            for scene in nusc.scene:
                samples, tok = [], scene["first_sample_token"]
                while tok:
                    rec = nusc.get("sample", tok); samples.append(rec); tok = rec["next"]
                for i in range(self.past_len - 1, len(samples)):
                    t0 = samples[i]
                    if t0["token"] not in valid: continue
                    past = samples[i - (self.past_len - 1): i + 1]
                    agent_items.append((ai, past, t0["token"]))
            # temporal split
            ntot = len(agent_items); ncut = int(round(ntot * (1 - VAL_FRAC)))
            chosen = agent_items[:ncut] if is_train else agent_items[ncut:]
            self.ixes.extend(chosen)
            n_train += ncut; n_val += ntot - ncut
        print(f"[CarlaBEVDataset/{split}] agents={len(self.nuscs)} items={len(self.ixes)} "
              f"(train_pool={n_train} val_pool={n_val}) cams={len(self.cams)}", flush=True)

    def __len__(self): return len(self.ixes)

    def _lidar_bev(self, nusc, rec):
        try:
            sd = nusc.get("sample_data", rec["data"]["LIDAR_TOP"])
            cs = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])
            path = os.path.join(nusc.dataroot, sd["filename"])
            raw = np.fromfile(path, dtype=np.float32)
            k = (raw.size // 5) * 5
            p = raw[:k].reshape(-1, 5)
            R = G.quat2rot(cs["rotation"]); t = np.array(cs["translation"])
            pe = p[:, :3].astype(np.float64) @ R.T + t
            pe[:, 1] *= -1.0                          # Y-flip to match GT/cameras
            return _lidar_bev_from_points(pe, p[:, 3])
        except Exception:
            return np.zeros((LIDAR_CH, _NX, _NY), np.float32)

    def __getitem__(self, index):
        ai, past, token = self.ixes[index]
        nusc = self.nuscs[ai]
        aug = _sample_aug(self.data_aug_conf, self.is_train)
        imgs_l, rots_l, trans_l, intr_l, pr_l, pt_l = [], [], [], [], [], []
        for rec in past:
            i_,r_,t_,k_,pr_,pt_ = [],[],[],[],[],[]
            for cam in self.cams:
                img_t, rot, tran, intrin, pr, pt = _get_cam_data(nusc, rec, cam, aug)
                i_.append(img_t); r_.append(torch.from_numpy(rot)); t_.append(torch.from_numpy(tran))
                k_.append(torch.from_numpy(intrin)); pr_.append(torch.from_numpy(pr)); pt_.append(torch.from_numpy(pt))
            imgs_l.append(torch.stack(i_)); rots_l.append(torch.stack(r_)); trans_l.append(torch.stack(t_))
            intr_l.append(torch.stack(k_)); pr_l.append(torch.stack(pr_)); pt_l.append(torch.stack(pt_))
        imgs=torch.stack(imgs_l); rots=torch.stack(rots_l); trans=torch.stack(trans_l)
        intrins=torch.stack(intr_l); post_rots=torch.stack(pr_l); post_trans=torch.stack(pt_l)
        ego2globals=torch.from_numpy(np.stack([_get_ego2global(nusc, rec) for rec in past],0))

        data=np.load(os.path.join(self.npz_dirs[ai], token+".npz"))
        bc=data["bev_cls_future"].astype(np.int64)      # (1,X,Y)
        bm=data["bev_mask_future"].astype(np.uint8)     # (1,X,Y)
        if DILATE>0:
            b0,m0=_dilate_small(bc[0].copy(), bm[0].copy(), DILATE)
            bc=b0[None]; bm=m0[None]
        bev_cls=torch.from_numpy(bc)
        bev_mask=torch.from_numpy(bm)
        mp=torch.from_numpy(data["motion_patch"].astype(np.float32))          # (0,Z,Xp,Yp)
        out={"imgs":imgs,"rots":rots,"trans":trans,"intrins":intrins,
                "post_rots":post_rots,"post_trans":post_trans,
                "bev_cls_future":bev_cls,"bev_mask_future":bev_mask,
                "motion_patch":mp,"ego2globals":ego2globals,"t0_token":token}
        if USE_LIDAR:
            out["lidar_bev"]=torch.from_numpy(self._lidar_bev(nusc, past[-1]))  # (6,X,Y) current frame
        return out

    def __repr__(self):
        return f"CarlaBEVDataset(split={self.split}, n={len(self)}, agents={len(self.nuscs)})"
