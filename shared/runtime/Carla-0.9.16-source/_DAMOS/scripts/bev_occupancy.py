"""Category-agnostic obstacle presence; unobserved is never free space."""
import numpy as np

UNKNOWN, GROUND, OBSTACLE = 0, 1, 2
OBSTACLE_CLASSES = (1,2,3,4,5,6,7,8,9,10,15,16)
GROUND_CLASSES = (11,12,13,14)


def obstacle_presence(probabilities, valid):
    probabilities = np.asarray(probabilities)
    valid = np.asarray(valid).astype(bool)
    if probabilities.shape != (17,200,200) or valid.shape != (200,200):
        raise ValueError("Invalid obstacle-presence grid.")
    labels = probabilities.argmax(0)
    occupancy = np.zeros(valid.shape,np.uint8)
    occupancy[valid&np.isin(labels,GROUND_CLASSES)] = GROUND
    occupancy[valid&np.isin(labels,OBSTACLE_CLASSES)] = OBSTACLE
    return {"occupancy":occupancy,
            "obstacle_probability":probabilities[list(OBSTACLE_CLASSES)].astype(np.float32).sum(0)}


def score_presence(prediction, truth_mask):
    occupancy = obstacle_presence(prediction["probabilities"],prediction["valid"])["occupancy"]
    total = int(truth_mask.sum())
    hits = int(((occupancy==OBSTACLE)&truth_mask).sum())
    grounds = int(((occupancy==GROUND)&truth_mask).sum())
    return {"gt_return_cells":total,"obstacle_cells":hits,"ground_cells":grounds,
            "unknown_cells":total-hits-grounds,"obstacle_recall":hits/total if total else None,
            "diagnostic_match":bool(total>=3 and hits/total>=.5)}


def score_scene_presence(prediction, ground_truth, gt_valid):
    occupancy = obstacle_presence(prediction["probabilities"],prediction["valid"])["occupancy"]
    obstacle = (ground_truth==OBSTACLE)&gt_valid
    ground = (ground_truth==GROUND)&gt_valid
    tp = int((obstacle&(occupancy==OBSTACLE)).sum())
    fp = int((ground&(occupancy==OBSTACLE)).sum())
    tn = int((ground&(occupancy==GROUND)).sum())
    return {"gt_obstacle_cells":int(obstacle.sum()),"gt_ground_cells":int(ground.sum()),
            "true_positive_cells":tp,"ground_false_positive_cells":fp,"true_ground_cells":tn,
            "unknown_on_ground_cells":int((ground&(occupancy==UNKNOWN)).sum()),
            "unknown_on_obstacle_cells":int((obstacle&(occupancy==UNKNOWN)).sum()),
            "obstacle_recall":tp/int(obstacle.sum()) if obstacle.any() else None,
            "obstacle_precision":tp/(tp+fp) if tp+fp else None,
            "ground_false_positive_rate_on_decided":fp/(fp+tn) if fp+tn else None,
            "scope":"Semantic-return GT cells only, not complete free-space or traversability GT"}


def interpolate_presence_for_display(prediction):
    """Close nearby gaps without changing any observed cell or inference output."""
    import cv2
    valid = prediction["valid"].astype(bool)
    occupancy = obstacle_presence(prediction["probabilities"],valid)["occupancy"]
    kernel = np.ones((3,3),np.uint8)
    proposals = []
    for cls in (GROUND,OBSTACLE):
        original = (occupancy==cls).astype(np.uint8)
        padded = np.pad(original,2)
        closed = cv2.morphologyEx(padded,cv2.MORPH_CLOSE,kernel)[2:-2,2:-2].astype(bool)
        distance = cv2.distanceTransform(1-original,cv2.DIST_L2,cv2.DIST_MASK_PRECISE)
        proposals.append(closed & ~valid & (distance<=np.sqrt(2)+1e-5))
    # Never resolve conflicting ground/obstacle proposals or grow from filled cells.
    conflict = proposals[0]&proposals[1]
    display = occupancy.copy()
    for cls,candidate in zip((GROUND,OBSTACLE),proposals):
        display[candidate&~conflict] = cls
    return {"occupancy":display,"interpolated":display!=occupancy,
            "metadata":{"display_only":True,"algorithm":"single 3x3 class-wise closing",
                "nearest_original_cell_max_m":float(.4*np.sqrt(2)),
                "filled_ground_cells":int(((display==GROUND)&(display!=occupancy)).sum()),
                "filled_obstacle_cells":int(((display==OBSTACLE)&(display!=occupancy)).sum()),
                "ambiguous_cells_left_unknown":int(conflict.sum()),
                "used_for_metrics_or_sharing":False}}


def _presence_colors(occupancy, valid, interpolated=None):
    image = np.full((200,200,3),28,np.uint8)
    image[valid] = (125,80,125)
    image[occupancy==GROUND] = (185,185,185)
    image[occupancy==OBSTACLE] = (0,180,255)
    if interpolated is not None:
        image[interpolated&(occupancy==GROUND)] = (190,165,135)
        image[interpolated&(occupancy==OBSTACLE)] = (125,205,255)
    return image


def render_presence(prediction, size=600):
    import cv2
    valid = prediction["valid"].astype(bool)
    occupancy = obstacle_presence(prediction["probabilities"],valid)["occupancy"]
    image = _presence_colors(occupancy,valid)
    return cv2.resize(np.ascontiguousarray(image[::-1,::-1]),(size,size),interpolation=cv2.INTER_NEAREST)


def render_presence_context(prediction, size=600):
    """One obstacle colour, including predicted buildings and vegetation."""
    return render_presence(prediction, size)


def _ego_in_grid(ego_pose, grid_pose):
    poses = [np.asarray(p, np.float64) for p in (ego_pose, grid_pose)]
    if any(p.shape != (4, 4) or not np.isfinite(p).all()
           or not np.allclose(p[3], [0, 0, 0, 1]) for p in poses):
        raise ValueError("Invalid ego/grid pose for display.")
    relative = np.linalg.solve(poses[1], poses[0])
    forward = relative[:2, 0]
    length = np.linalg.norm(forward)
    if length < 1e-6:
        raise ValueError("Ego forward axis has no planar direction.")
    return (relative[:2, 3] + 40) / .4, forward / length


def _display_crop(crop):
    x, y, width = crop
    if (width <= 0 or min(x, y) < 0 or max(x, y) + width > 200
            or any(int(value) != value for value in crop)):
        raise ValueError("Invalid BEV display crop.")
    return np.array([x, y], np.float64), int(width)


def ego_visible_crop(crop, ego_pose, grid_pose, margin_cells=8):
    """Expand the view to include ego; never discard the original crop."""
    start, width = _display_crop(crop)
    point, _ = _ego_in_grid(ego_pose, grid_pose)
    if margin_cells < 1 or not ((point >= 0) & (point < 200)).all():
        raise ValueError("Ego is outside the available BEV grid.")
    low = np.minimum(start, np.maximum(0, np.floor(point - margin_cells + 1e-8)))
    high = np.maximum(start + width, np.minimum(200, np.ceil(point + margin_cells - 1e-8)))
    expanded = int((high - low).max())
    origin = np.clip(np.floor((low + high - expanded) / 2), 0, 200 - expanded).astype(int)
    return int(origin[0]), int(origin[1]), expanded


def project_ego_pose(ego_pose, grid_pose, crop, size=640):
    """Project recorded ego pose, accounting for both flipped display axes."""
    start, width = _display_crop(crop)
    if size <= 0:
        raise ValueError("Invalid BEV image size.")
    point, forward = _ego_in_grid(ego_pose, grid_pose)
    pixel = ((start + width - point) / width * size - .5)[::-1]
    direction = -forward[::-1]
    return {"pixel_xy": pixel.tolist(), "forward_pixel_unit": direction.tolist(),
            "in_frame": bool(((pixel >= 0) & (pixel < size)).all()),
            "local_xy_m": (point * .4 - 40).tolist(),
            "source": "recorded ego pose; display only, not a model detection"}


def draw_ego_pose(image, ego_pose, grid_pose, crop):
    """Red location dot and forward arrow; no fabricated edge-clamped pose."""
    import cv2
    if image.ndim != 3 or image.shape[0] != image.shape[1] or image.shape[2] != 3:
        raise ValueError("Ego marker requires a square BGR image.")
    record = project_ego_pose(ego_pose, grid_pose, crop, image.shape[0])
    image = image.copy()
    if not record["in_frame"]:
        return image, record
    centre = np.asarray(record["pixel_xy"])
    direction = np.asarray(record["forward_pixel_unit"])
    origin = tuple(np.rint(centre).astype(int))
    tip = tuple(np.rint(centre + direction * 32).astype(int))
    red = (35, 55, 240)
    for color, thickness in (((15, 15, 15), 8), ((255, 255, 255), 6), (red, 3)):
        cv2.arrowedLine(image, origin, tip, color, thickness, cv2.LINE_AA, tipLength=.3)
    cv2.circle(image, origin, 11, (15, 15, 15), -1, cv2.LINE_AA)
    cv2.circle(image, origin, 9, (255, 255, 255), -1, cv2.LINE_AA)
    cv2.circle(image, origin, 6, red, -1, cv2.LINE_AA)
    text = (int(np.clip(origin[0] + 15, 4, image.shape[1] - 60)),
            int(np.clip(origin[1] + 24, 22, image.shape[0] - 8)))
    cv2.putText(image, "EGO", text, cv2.FONT_HERSHEY_SIMPLEX, .65, (15, 15, 15), 5, cv2.LINE_AA)
    cv2.putText(image, "EGO", text, cv2.FONT_HERSHEY_SIMPLEX, .65, (255, 255, 255), 2, cv2.LINE_AA)
    return image, record


def render_presence_focus(prediction, target_mask, crop, size=640, *, ego_pose=None):
    """Context plus explicitly GT-centred zoom; predictions are never masked."""
    import cv2
    x, y, width = crop
    if (width <= 0 or min(x, y) < 0 or max(x, y) + width > 200
            or np.shape(target_mask) != (200, 200) or size < 320):
        raise ValueError("Invalid focused BEV display.")
    full = render_presence_context(prediction, 200)
    image = cv2.resize(full[200-x-width:200-x, 200-y-width:200-y],
                       (size, size), interpolation=cv2.INTER_NEAREST)
    mask = cv2.resize(target_mask[x:x+width, y:y+width][::-1, ::-1].astype(np.uint8),
                      (size, size), interpolation=cv2.INTER_NEAREST)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(image, contours, -1, (255, 255, 255), 2)
    if not target_mask.any():
        return draw_ego_pose(image, ego_pose, prediction["pose"], crop)[0] if ego_pose is not None else image
    centre = np.argwhere(target_mask).mean(0)
    u, v = (y + width - centre[1] - .5) / width * size, (x + width - centre[0] - .5) / width * size
    if 0 <= u < size and 0 <= v < size:
        cv2.circle(image, (round(u), round(v)), 17, (255, 255, 255), 1)
    zx, zy = np.clip(np.floor(centre).astype(int) - 5, 0, 190)
    tile = cv2.resize(full[190-zx:200-zx, 190-zy:200-zy], (160, 160), interpolation=cv2.INTER_NEAREST)
    zoom_mask = cv2.resize(target_mask[zx:zx+10, zy:zy+10][::-1, ::-1].astype(np.uint8),
                          (160, 160), interpolation=cv2.INTER_NEAREST)
    outlines, _ = cv2.findContours(zoom_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(tile, outlines, -1, (255, 255, 255), 2)
    inset = np.full((190, 172, 3), 20, np.uint8)
    cv2.putText(inset, "GT-centred 4m zoom", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, .43, (240, 240, 240), 1)
    inset[25:185, 6:166] = tile
    cv2.rectangle(inset, (0, 0), (171, 189), (180, 180, 180), 1)
    corners = ((size - 180, 8), (8, 8), (size - 180, size - 198), (8, size - 198))
    marker = project_ego_pose(ego_pose, prediction["pose"], crop, size) if ego_pose is not None else None
    for left, top in corners:
        if marker is not None and marker["in_frame"]:
            u, v = marker["pixel_xy"]
            if left - 70 <= u <= left + 242 and top - 45 <= v <= top + 235:
                continue
        image[top:top+190, left:left+172] = inset
        break
    if ego_pose is not None:
        image, _ = draw_ego_pose(image, ego_pose, prediction["pose"], crop)
    return image


def render_presence_comparison(prediction, *, title="EGO FUSED", frame_id=None,
                               crop=None, target_mask=None):
    """Raw and display-only interpolation; GT mask is an optional outline only."""
    import cv2
    valid = prediction["valid"].astype(bool)
    occupancy = obstacle_presence(prediction["probabilities"],valid)["occupancy"]
    filled = interpolate_presence_for_display(prediction)
    images = [_presence_colors(occupancy,valid),
              _presence_colors(filled["occupancy"],valid,filled["interpolated"])]
    x0,y0,width = (0,0,200) if crop is None else crop
    if width<=0 or min(x0,y0)<0 or max(x0,y0)+width>200:
        raise ValueError("Invalid display crop.")
    panels = []
    for name,image in zip(("RAW OBSERVED CELLS","SMALL-GAP FILL: DISPLAY ONLY"),images):
        image = np.ascontiguousarray(image[x0:x0+width,y0:y0+width][::-1,::-1])
        image = cv2.resize(image,(600,600),interpolation=cv2.INTER_NEAREST)
        if target_mask is not None:
            outline = target_mask[x0:x0+width,y0:y0+width][::-1,::-1].astype(np.uint8)
            contours,_ = cv2.findContours(cv2.resize(outline,(600,600),interpolation=cv2.INTER_NEAREST),
                                         cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(image,contours,-1,(255,255,255),1)
        header = np.full((84,600,3),20,np.uint8)
        detail = title + ("" if frame_id is None else f" | F{frame_id}")
        lines = [name,detail,f"Same {width*.4:g}m crop | x forward up, y left left"]
        for index,line in enumerate(lines):
            cv2.putText(header,line,(12,23+25*index),cv2.FONT_HERSHEY_SIMPLEX,.58,(240,240,240),1)
        panels.append(np.vstack([header,image]))
    legend = np.full((90,1200,3),20,np.uint8)
    entries = [("Observed obstacle",(0,180,255)),("Observed ground",(185,185,185)),
               ("Filled obstacle",(125,205,255)),("Filled ground",(190,165,135))]
    for index,(name,color) in enumerate(entries):
        x = 12+300*index
        cv2.rectangle(legend,(x,10),(x+20,30),color,-1)
        cv2.putText(legend,name,(x+30,27),cv2.FONT_HERSHEY_SIMPLEX,.53,(240,240,240),1)
    notes = ["Dark=unobserved, not free; purple=uncertain. Filled cells do NOT count as detection evidence.",
             "Single 3x3 closing; nearest same-group cell <=0.57m. Observed cells and BEV data unchanged."]
    if target_mask is not None:
        notes[0] = "White outline=target GT only. Dark=unobserved; purple=uncertain. Filled cells are NOT detections."
    for index,line in enumerate(notes):
        cv2.putText(legend,line,(12,53+25*index),cv2.FONT_HERSHEY_SIMPLEX,.51,(225,225,225),1)
    return np.vstack([np.hstack(panels),legend]),filled["metadata"]
