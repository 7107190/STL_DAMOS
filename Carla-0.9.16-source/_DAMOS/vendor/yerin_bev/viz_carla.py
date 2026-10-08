#!/usr/bin/env python3
"""
viz_carla.py — qualitative + quantitative eval of a CARLA current-frame BEV model.
Renders predicted vs GT BEV side-by-side and computes per-class IoU (focus on
pedestrian/object/car). Reuses the training model build and CARLA dataset.
"""
import os, sys, argparse, json
import numpy as np, torch, cv2
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE,"..","src_2")); sys.path.insert(0, HERE)
from models_bev import compile_model_bev
from data_bev_carla import CarlaBEVDataset
from data_bev import IMG_MEAN, IMG_STD, CAMS_6
import carla_gt_core as G

def _cam_montage(imgs_cur, width):
    """imgs_cur: (N,3,H,W) normalized RGB tensor for the current frame.
    Returns a BGR montage (2 rows x 3 cols: FL F FR / BL B BR) resized to `width`."""
    mean = np.array(IMG_MEAN, np.float32); std = np.array(IMG_STD, np.float32)
    labels = CAMS_6  # [FL, F, FR, BL, B, BR]
    tiles = []
    for c in range(imgs_cur.shape[0]):
        a = imgs_cur[c].numpy().transpose(1, 2, 0)           # H,W,3 RGB (norm)
        a = ((a * std + mean).clip(0, 1) * 255).astype(np.uint8)
        bgr = np.ascontiguousarray(a[:, :, ::-1])            # RGB->BGR
        cv2.putText(bgr, labels[c], (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
        tiles.append(bgr)
    row0 = np.concatenate(tiles[0:3], axis=1)
    row1 = np.concatenate(tiles[3:6], axis=1)
    grid = np.concatenate([row0, row1], axis=0)
    h = int(grid.shape[0] * width / grid.shape[1])
    return cv2.resize(grid, (width, h), interpolation=cv2.INTER_AREA)

def load_cfg(p): 
    import json; return json.load(open(p))

def build_model(cfg, device, past_len=4):
    mc={k:v for k,v in cfg.get("model",{}).items() if not k.startswith("_")}
    grid={"xbound":[-40,40,0.4],"ybound":[-40,40,0.4],"zbound":[-1,5.4,0.4],"dbound":[4,45,1.0]}
    daug={"resize_lim":(0.42,0.5),"final_dim":(128,320),"rot_lim":(0,0),"H":450,"W":800,
          "rand_flip":False,"bot_pct_lim":(0.0,0.15),
          "cams":["CAM_FRONT_LEFT","CAM_FRONT","CAM_FRONT_RIGHT","CAM_BACK_LEFT","CAM_BACK","CAM_BACK_RIGHT"],"Ncams":6}
    kw=dict(voxel_featC=mc.get("voxel_featC",32),num_classes=17,T_past=past_len,T_future=1,
            patch_size=mc.get("patch_size",4),backbone=mc.get("backbone","swin_t"),
            backbone_pretrained=False,tc_enabled=False,ugafr_enabled=True,two_pass_enabled=False,
            motion_head_loss_w=0.0,gate_policy="ugafr")
    if os.environ.get("CARLA_LIDAR")=="1":
        kw["lidar_fusion"]=True; kw["lidar_in_ch"]=6
    for k in ["use_spatial_backbone","spatial_backbone_blocks","backbone_checkpoint","use_ego_warping",
              "mamba_n_stages","mamba_spatial_layers","mamba_temporal_layers","scan_mode",
              "gate_topk","zpc_tau_start","zpc_tau_end","zpc_tau_anneal_steps","zpc_dyn_gate_thresh"]:
        if k in mc: kw[k]=mc[k]
    model=compile_model_bev(grid,daug,**kw).to(device).eval()
    return model,daug,grid

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--ckpt",required=True)
    ap.add_argument("--config",default=os.path.join(HERE,"..","src_2","configs","prism_v2.json"))
    ap.add_argument("--bev_root",default="/mnt/Data/carla_columbus_bev")
    ap.add_argument("--nusc_root",default="/mnt/Data/carla_columbus")
    ap.add_argument("--past_len",type=int,default=4)
    ap.add_argument("--n",type=int,default=12)
    ap.add_argument("--agents",default="")
    ap.add_argument("--out",default="/tmp/claude-1004/-home-dltj0425/b6f3cfd2-68f8-477d-b547-9af4382c8727/scratchpad/viz")
    ap.add_argument("--gpuid",type=int,default=0)
    a=ap.parse_args()
    if a.agents: os.environ["CARLA_AGENTS"]=a.agents
    os.makedirs(a.out,exist_ok=True)
    device=torch.device(f"cuda:{a.gpuid}")
    cfg=load_cfg(a.config)
    model,daug,grid=build_model(cfg,device,a.past_len)
    sd=torch.load(a.ckpt,map_location="cpu")
    state=sd.get("model",sd.get("state_dict",sd))
    state={k.replace("module.",""):v for k,v in state.items()}
    missing,unexpected=model.load_state_dict(state,strict=False)
    print(f"[ckpt] loaded {a.ckpt}  missing={len(missing)} unexpected={len(unexpected)}")

    ds=CarlaBEVDataset(a.bev_root,"val","v1.0-mini",a.nusc_root,daug,grid,
                       past_len=a.past_len,future_len=1,is_train=False)
    idxs=np.linspace(0,len(ds)-1,min(a.n,len(ds))).astype(int)
    NC=17; conf=np.zeros((NC,NC),np.int64)
    for j,i in enumerate(idxs):
        b=ds[int(i)]
        imgs=b["imgs"][None].to(device); rots=b["rots"][None].to(device); trans=b["trans"][None].to(device)
        intr=b["intrins"][None].to(device); pr=b["post_rots"][None].to(device); pt=b["post_trans"][None].to(device)
        e2g=b["ego2globals"][None].to(device)
        lidb=b.get("lidar_bev"); lidb=lidb[None].to(device) if torch.is_tensor(lidb) else None
        with torch.no_grad(), torch.autocast("cuda"):
            logits,aux=model(imgs,rots,trans,intr,pr,pt,ego2globals=e2g,lidar_bev=lidb,return_aux=True)
        pred=logits[0,0].argmax(0).cpu().numpy().astype(np.uint8)   # (X,Y)
        gt=b["bev_cls_future"][0].numpy().astype(np.int64)
        mask=b["bev_mask_future"][0].numpy().astype(bool)
        v=mask&(gt>=0)&(gt<NC)
        conf+=np.bincount((gt[v]*NC+pred[v]),minlength=NC*NC).reshape(NC,NC)
        # render side by side (pred masked to valid for fair view)
        pshow=pred.copy(); pshow[~mask]=255
        bev=np.concatenate([G.render(gt,2), np.full((400,6,3),60,np.uint8), G.render(pshow,2)],axis=1)
        cv2.putText(bev,"GT",(5,20),cv2.FONT_HERSHEY_SIMPLEX,0.6,(255,255,255),2)
        cv2.putText(bev,"PRED",(410,20),cv2.FONT_HERSHEY_SIMPLEX,0.6,(255,255,255),2)
        # input cameras (current frame) on top
        cam=_cam_montage(b["imgs"][a.past_len-1], bev.shape[1])
        cv2.putText(cam,"INPUT CAMERAS (current frame)",(6,cam.shape[0]-8),
                    cv2.FONT_HERSHEY_SIMPLEX,0.5,(255,255,255),1)
        gap=np.full((6,bev.shape[1],3),60,np.uint8)
        img=np.concatenate([cam,gap,bev],axis=0)
        cv2.imwrite(os.path.join(a.out,f"cmp_{j:02d}.png"),img)
    # IoU
    inter=np.diag(conf).astype(np.float64)
    union=conf.sum(0)+conf.sum(1)-np.diag(conf)
    iou=np.where(union>0, inter/union, np.nan)
    print("\n=== per-class IoU (val, %d samples) ==="%len(idxs))
    for c in range(NC):
        if not np.isnan(iou[c]):
            print("  %2d %-12s IoU=%.3f  (gt px=%d)"%(c,G.CLASS_NAMES[c],iou[c],int(conf[c].sum())))
    valid=~np.isnan(iou); print("  mIoU=%.3f"%(np.nanmean(iou)))
    key=[7,8,4,2,6]  # ped,object,car,bike,moto
    kn=[G.CLASS_NAMES[c] for c in key]; kv=[iou[c] for c in key]
    print("  focus:", {n:(None if np.isnan(v) else round(float(v),3)) for n,v in zip(kn,kv)})

    # ---- coarse groups aligned to user goal: people / objects / vehicles / bg ----
    GROUPS={"people":[7],"objects":[8],"vehicle":[2,3,4,6,9,10],
            "drivable":[11],"sidewalk":[13],"ground":[12,14],"structure":[1,15],"vegetation":[16]}
    lut=np.zeros(NC,int); names=list(GROUPS);
    for gi,(g,cs) in enumerate(GROUPS.items()):
        for c in cs: lut[c]=gi
    NG=len(names)
    cg=np.zeros((NG,NG),np.int64)
    for gt_c in range(NC):
        for pr_c in range(NC):
            cg[lut[gt_c],lut[pr_c]]+=conf[gt_c,pr_c]
    gi_=np.diag(cg).astype(float); gu=cg.sum(0)+cg.sum(1)-np.diag(cg)
    giou=np.where(gu>0, gi_/gu, np.nan)
    print("\n=== COARSE-GROUP IoU (people/objects/vehicles/bg) ===")
    for gi,g in enumerate(names):
        if not np.isnan(giou[gi]): print("  %-10s IoU=%.3f (gt px=%d)"%(g,giou[gi],int(cg[gi].sum())))
    print("  group mIoU=%.3f"%np.nanmean(giou))
    print("[saved]",a.out)

if __name__=="__main__": main()
