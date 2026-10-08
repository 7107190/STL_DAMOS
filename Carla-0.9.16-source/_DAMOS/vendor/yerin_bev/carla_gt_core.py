"""
carla_gt_core.py — Hybrid 2D BEV semantic GT for CARLA Columbus (nuScenes format).

GT sources (per keyframe, ego(t0) frame):
  - Foreground (people / vehicles / boxed objects): rasterize 3D box annotations.
  - Background + props (road/sidewalk/building/vegetation/trash-can): semantic LiDAR
    (LIDAR_SEGMENTATION), Y-flipped to match the coordinate-fixed nuScenes convention.

Output taxonomy = existing 17-class Occ3D-nuScenes scheme (model unchanged):
  0 others 1 barrier 2 bicycle 3 bus 4 car 5 construction 6 motorcycle 7 pedestrian
  8 object(cone/prop/trash/robot) 9 trailer 10 truck 11 drive_surf 12 other_flat
  13 sidewalk 14 terrain 15 manmade 16 vegetation ; 255 = ignore/unobserved.
"""
import numpy as np

# ---- grid (matches project grid_conf) ----
XB=(-40.0,40.0,0.4); YB=(-40.0,40.0,0.4); ZB=(-1.0,5.4,0.4)
NX=int(round((XB[1]-XB[0])/XB[2]))  # 200
NY=int(round((YB[1]-YB[0])/YB[2]))  # 200
NZ=int(round((ZB[1]-ZB[0])/ZB[2]))  # 16
IGNORE=255

# ---- CARLA semantic-lidar tag -> 17-class (background/props) ----
CARLA2CLS={
 1:11, 24:11,           # Roads, RoadLine -> drive_surf
 2:13,                  # SideWalks -> sidewalk
 25:12, 23:12, 27:12,   # Ground, Water, RailTrack -> other_flat
 10:14,                 # Terrain -> terrain
 9:16,                  # Vegetation -> vegetation
 3:15, 4:15, 6:15, 7:15, 8:15, 26:15,  # Building/Wall/Pole/TL/Sign/Bridge -> manmade
 5:1, 28:1,             # Fence, GuardRail -> barrier
 20:8, 21:8,            # Static, Dynamic (props incl. trash can) -> object
 22:0, 0:0,             # Other, Unlabeled -> others
 # dynamic classes from lidar (boxes usually override, kept as fallback)
 14:4, 16:3, 15:10, 12:7, 13:7, 18:6, 19:2, 17:3,
}
# ---- CARLA box category name -> 17-class (foreground) ----
def box_cat2cls(name):
    if name.startswith("human.pedestrian"): return 7      # pedestrian
    if name=="vehicle.car": return 4
    if name=="vehicle.truck": return 10
    if name=="vehicle.emergency.ambulance": return 4      # treat as car (자동차)
    if name=="vehicle.bicycle": return 2
    if name=="vehicle.robot.delivery": return 8           # object/prop
    if name.startswith("vehicle"): return 4
    return 0

# ---- priority for z-collapse & foreground-over-background (lower = wins) ----
# people > objects > two-wheel > cars/trucks/bus > background
PRIORITY={7:0, 8:1, 2:2, 6:3, 4:4, 10:5, 3:6, 9:7, 1:8, 5:9, 0:10,
          13:11, 14:12, 12:13, 15:14, 16:15, 11:16}
CLASS_NAMES=["others","barrier","bicycle","bus","car","construction","motorcycle",
 "pedestrian","object","trailer","truck","drive_surf","other_flat","sidewalk",
 "terrain","manmade","vegetation"]
# BGR colors for viz
CLASS_COLORS=[(128,128,128),(255,20,147),(0,255,0),(255,69,0),(0,165,255),(160,32,240),
 (0,200,255),(255,0,0),(0,255,255),(139,90,43),(71,99,255),(255,255,0),(180,215,0),
 (128,0,128),(34,139,34),(180,130,70),(0,100,0)]

def quat2rot(q):
    w,x,y,z=q
    return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
      [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
      [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]],dtype=np.float64)

def yaw_of(q):
    return np.arctan2(2*(q[0]*q[3]+q[1]*q[2]),1-2*(q[2]**2+q[3]**2))

def voxel_idx(pts):
    xi=np.floor((pts[:,0]-XB[0])/XB[2]).astype(np.int64)
    yi=np.floor((pts[:,1]-YB[0])/YB[2]).astype(np.int64)
    zi=np.floor((pts[:,2]-ZB[0])/ZB[2]).astype(np.int64)
    ok=(xi>=0)&(xi<NX)&(yi>=0)&(yi<NY)&(zi>=0)&(zi<NZ)
    return xi,yi,zi,ok

def build_voxel_semantics(seg_pts, seg_tag, boxes):
    """
    seg_pts: (M,3) semantic-lidar xyz already in ego(t0) frame (Y-flip applied).
    seg_tag: (M,) CARLA int tags.
    boxes: list of (cls, cx,cy,cz, w,l,h, yaw) in ego frame.
    Returns sem (NX,NY,NZ) uint8 (0..16 or 255), obs (NX,NY,NZ) bool.
    """
    prio_lut=np.full(17,999,np.int32)
    for c,p in PRIORITY.items(): prio_lut[c]=p

    sem=np.full((NX,NY,NZ),IGNORE,np.uint8)
    best=np.full((NX,NY,NZ),999,np.int32)
    obs=np.zeros((NX,NY,NZ),bool)

    # ---- lidar (background/props) ----
    xi,yi,zi,ok=voxel_idx(seg_pts)
    cls=np.array([CARLA2CLS.get(int(t),0) for t in seg_tag],np.int32)
    xi,yi,zi,cls=xi[ok],yi[ok],zi[ok],cls[ok]
    obs[xi,yi,zi]=True
    pr=prio_lut[cls]
    # assign by priority (min) using np.minimum.at + resolve
    order=np.argsort(pr)  # low prio first so later writes are lower prio -> use reverse
    # simplest: iterate high->low prio, overwrite
    order=np.argsort(-pr)
    sem[xi[order],yi[order],zi[order]]=cls[order].astype(np.uint8)
    best[xi[order],yi[order],zi[order]]=pr[order]

    # ---- boxes (foreground) rasterize ----
    for (c,cx,cy,cz,w,l,h,yaw) in boxes:
        p=PRIORITY.get(c,999)
        # footprint bounding cells
        r=max(w,l)/2+0.2
        x0=int(np.floor((cx-r-XB[0])/XB[2])); x1=int(np.ceil((cx+r-XB[0])/XB[2]))
        y0=int(np.floor((cy-r-YB[0])/YB[2])); y1=int(np.ceil((cy+r-YB[0])/YB[2]))
        z0=int(np.floor((cz-h/2-ZB[0])/ZB[2])); z1=int(np.ceil((cz+h/2-ZB[0])/ZB[2]))
        x0=max(x0,0);x1=min(x1,NX);y0=max(y0,0);y1=min(y1,NY);z0=max(z0,0);z1=min(z1,NZ)
        if x0>=x1 or y0>=y1 or z0>=z1: continue
        gx=(np.arange(x0,x1)+0.5)*XB[2]+XB[0]
        gy=(np.arange(y0,y1)+0.5)*YB[2]+YB[0]
        GX,GY=np.meshgrid(gx,gy,indexing="ij")
        dx=GX-cx; dy=GY-cy
        ca,sa=np.cos(-yaw),np.sin(-yaw)
        lx=dx*ca-dy*sa; ly=dx*sa+dy*ca
        inside=(np.abs(lx)<=l/2)&(np.abs(ly)<=w/2)   # (nx,ny)
        if not inside.any(): continue
        ii,jj=np.where(inside)
        ii=ii+x0; jj=jj+y0
        for zk in range(z0,z1):
            m=best[ii,jj,zk]>p
            sem[ii[m],jj[m],zk]=c
            best[ii[m],jj[m],zk]=p
            obs[ii[m],jj[m],zk]=True
    return sem,obs

def collapse_bev(sem,obs):
    """(NX,NY,NZ)->(NX,NY): pick highest-priority class per pillar; 255 if none."""
    prio_lut=np.full(256,999,np.int32)
    for c,p in PRIORITY.items(): prio_lut[c]=p
    pr=prio_lut[sem.astype(np.int64)]
    pr[(sem==IGNORE)|(~obs)]=999
    best_z=pr.argmin(axis=2)
    bev=np.take_along_axis(sem,best_z[:,:,None],axis=2)[:,:,0]
    empty=pr.min(axis=2)>=999
    bev[empty]=IGNORE
    mask=(~empty).astype(np.uint8)
    return bev,mask

def render(bev,scale=2):
    import cv2
    img=np.zeros((NX,NY,3),np.uint8)
    for c,col in enumerate(CLASS_COLORS): img[bev==c]=col
    img[bev==IGNORE]=(30,30,30)
    img=np.flipud(img)
    if scale!=1: img=cv2.resize(img,(NY*scale,NX*scale),interpolation=cv2.INTER_NEAREST)
    return img


# ---- ground densification (fill lidar ring gaps on contiguous ground) ----
GROUND_CLASSES=[11,13,14,12]   # drive_surf, sidewalk, terrain, other_flat
def densify_ground(bev, mask, close_k=5):
    """
    Morphological close per ground class to fill small unobserved gaps
    (lidar ring gaps) inside contiguous ground regions. Dynamic/object
    classes are left untouched. Returns new bev, mask.
    """
    import cv2
    bev=bev.copy(); mask=mask.copy()
    k=np.ones((close_k,close_k),np.uint8)
    unobs=(mask==0)
    for c in GROUND_CLASSES:
        m=(bev==c).astype(np.uint8)
        if m.sum()==0: continue
        closed=cv2.morphologyEx(m,cv2.MORPH_CLOSE,k)
        fill=(closed>0)&unobs
        bev[fill]=c; mask[fill]=1; unobs=(mask==0)
    return bev,mask
