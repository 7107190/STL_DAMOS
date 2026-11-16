"""Display-only target matches and current poses; prediction arrays stay unchanged."""
import cv2
import numpy as np

from report_data import atomic_json


TARGET_BGR = (166, 100, 237)
OVERLAY_IMAGE = "evaluation_bev_target_v2.png"
OVERLAY_RECORD = "evaluation_bev_target_v2.json"


def target_match_cells(row):
    gt = np.asarray(row["gt_target_cell_indices"], dtype=np.int64)
    predicted = np.asarray(row["fused_obstacle_cell_indices"], dtype=np.int64)
    if any(values.ndim != 1 or np.any((values < 0) | (values >= 40_000))
           for values in (gt, predicted)):
        raise ValueError("Invalid target/prediction cell indices")
    return np.intersect1d(gt, predicted)


def paint_target_matches(image, row):
    if image.ndim != 3 or image.shape[0] != image.shape[1] or image.shape[2] != 3:
        raise ValueError("Target overlay requires a square BGR image")
    cells = target_match_cells(row)
    mask = np.zeros((200, 200), dtype=np.uint8)
    mask.flat[cells] = 1
    display_mask = cv2.resize(np.ascontiguousarray(mask[::-1, ::-1]),
                              (image.shape[1], image.shape[0]), interpolation=cv2.INTER_NEAREST)
    result = image.copy()
    result[display_mask.astype(bool)] = TARGET_BGR
    return result, cells


def draw_target_zoom(image, source, row, rectangle, positions=()):
    gt = np.asarray(row["gt_target_cell_indices"], dtype=np.int64)
    if not gt.size:
        return image, None
    x, y = np.divmod(gt, 200)
    width = min(200, max(10, int(max(np.ptp(x), np.ptp(y))) + 5))
    x0 = int(np.clip((int(x.min()) + int(x.max()) + 1 - width) // 2, 0, 200 - width))
    y0 = int(np.clip((int(y.min()) + int(y.max()) + 1 - width) // 2, 0, 200 - width))
    size = image.shape[0]
    scale = size / 200
    a = (round((200 - y0 - width) * scale), round((200 - x0 - width) * scale))
    b = (round((200 - y0) * scale), round((200 - x0) * scale))
    side = min(160, size // 3)
    panel_w, panel_h = side + 12, side + 36
    avoid = [rectangle] if rectangle else []
    avoid += [[(px - 24, py - 24), (px + 24, py + 24)] for px, py in positions]
    for left, top in ((8, 8), (size - panel_w - 8, 8), (8, size - panel_h - 8),
                      (size - panel_w - 8, size - panel_h - 8)):
        if any(left < hi[0] and left + panel_w > lo[0] and top < hi[1] and top + panel_h > lo[1]
               for lo, hi in avoid):
            continue
        tile = cv2.resize(source[a[1]:b[1], a[0]:b[0]], (side, side), interpolation=cv2.INTER_NEAREST)
        result = image.copy()
        result[top:top + panel_h, left:left + panel_w] = (28, 28, 28)
        result[top + 29:top + 29 + side, left + 6:left + 6 + side] = tile
        cv2.rectangle(result, (left, top), (left + panel_w - 1, top + panel_h - 1), (255, 220, 45), 1)
        cv2.putText(result, f"TARGET | {width * .4:g}m", (left + 7, top + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 220, 45), 1, cv2.LINE_AA)
        return result, {"source_pixel_rectangle": [list(a), list(b)],
                        "inset_pixel_rectangle": [[left, top], [left + panel_w, top + panel_h]],
                        "width_m": width * .4, "interpolation": "nearest", "display_only": True}
    return image, None


def render_target(folder, row):
    image=cv2.imread(str(folder/"ego_obstacle_presence.png"))
    if image is None:
        raise ValueError("Original prediction image missing")
    image, matched = paint_target_matches(image, row)
    zoom_source = image.copy()
    size=image.shape[0]
    scale=size/200
    gt=np.asarray(row["gt_target_cell_indices"],dtype=np.int64)
    rectangle=None
    if gt.size:
        x,y=np.divmod(gt,200)
        u=(199-y+.5)*scale
        v=(199-x+.5)*scale
        a=(max(0,int(u.min())-8),max(0,int(v.min())-8))
        b=(min(size-1,int(u.max())+8),min(size-1,int(v.max())+8))
        cv2.rectangle(image,a,b,(255,220,45),2,cv2.LINE_AA)
        cv2.putText(image,"TARGET",(a[0],max(15,a[1]-5)),cv2.FONT_HERSHEY_SIMPLEX,.45,(255,220,45),1,cv2.LINE_AA)
        rectangle=[list(a),list(b)]
    with np.load(folder/"ego_own.npz",allow_pickle=False) as data:
        pose=data["pose"]
    received=next(folder.glob("ego_received_observer_*.npz"))
    with np.load(received,allow_pickle=False) as data:
        peer=data["pose"]
    local=np.linalg.solve(pose,peer[:,3])
    def point(x,y):
        return (int((199-(y+40)/.4+.5)*scale),int((199-(x+40)/.4+.5)*scale))
    ego=point(0,0)
    cv2.circle(image,ego,6,(238,147,57),-1,cv2.LINE_AA)
    cv2.arrowedLine(image,ego,(ego[0],ego[1]-22),(255,255,255),2,cv2.LINE_AA,tipLength=.35)
    cv2.putText(image,"EGO",(ego[0]+8,ego[1]+5),cv2.FONT_HERSHEY_SIMPLEX,.4,(245,245,245),1,cv2.LINE_AA)
    observer=point(local[0],local[1])
    if 0<=observer[0]<size and 0<=observer[1]<size:
        cv2.rectangle(image,(observer[0]-5,observer[1]-5),(observer[0]+5,observer[1]+5),(95,205,109),-1)
        cv2.putText(image,"OBS",(min(size-40,observer[0]+8),max(14,observer[1]-5)),cv2.FONT_HERSHEY_SIMPLEX,.4,(95,205,109),1,cv2.LINE_AA)
    image, zoom = draw_target_zoom(image, zoom_source, row, rectangle, (ego, observer))
    if not cv2.imwrite(str(folder/OVERLAY_IMAGE),image):
        raise OSError("Cannot save target overlay")
    atomic_json(folder/OVERLAY_RECORD,{"display_only":True,"prediction_arrays_modified":False,
        "frame_id":row["frame_id"],"target_outline_source":"evaluation-only exact target return cells", "target_pixel_rectangle":rectangle,
        "ego_pixel":list(ego),"observer_pixel":list(observer),"base":"ego_obstacle_presence.png",
        "version":2,"target_match_color_rgb":[237,100,166],
        "target_match_cell_indices":matched.tolist(),"target_match_cells":int(matched.size),
        "target_zoom":zoom,
        "color_source":"existing fused predicted obstacle cells intersected with exact target return cells",
        "missed_target_cells_filled":False,"used_for_detection_or_sharing":False})
