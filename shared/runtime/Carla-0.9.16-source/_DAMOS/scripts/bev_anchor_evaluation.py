"""Evaluation-only situation regions, not object or scenario-class classification."""
import cv2
import numpy as np

from bev_occupancy import OBSTACLE,obstacle_presence


def region_presence(prediction, mask):
    occupancy = obstacle_presence(prediction["probabilities"],prediction["valid"])["occupancy"]
    overlap = ((occupancy==OBSTACLE)&mask).astype(np.uint8)
    _,_,stats,_ = cv2.connectedComponentsWithStats(overlap,connectivity=8)
    largest = int(stats[1:,cv2.CC_STAT_AREA].max()) if len(stats)>1 else 0
    assessable = int(mask.sum())>=3
    return {"gt_return_cells":int(mask.sum()),"obstacle_overlap_cells":int(overlap.sum()),
            "largest_connected_overlap_cells":largest,"assessable":assessable,
            "detected":bool(largest>=3) if assessable else None}


def evaluate_anchors(objects, masks, own, fused):
    groups = {}
    for obj in objects:
        key = (obj["scenario"],obj["anchor_index"])
        groups.setdefault(key,[]).append(obj)
    anchors = []
    for (scenario,index),members in sorted(groups.items()):
        mask = np.zeros((200,200),bool)
        hidden = np.zeros_like(mask)
        for member in members:
            mask |= masks[member["actor_id"]]
            if member["occlusion_verified"]:
                hidden |= masks[member["actor_id"]]
        ego_score,fused_score = region_presence(own,mask),region_presence(fused,mask)
        hidden_ego,hidden_fused = region_presence(own,hidden),region_presence(fused,hidden)
        anchors.append({"scenario":scenario,"anchor_index":index,
            "member_actor_ids":[m["actor_id"] for m in members],
            "in_ego_roi":any(m["in_ego_roi"] for m in members),
            "members_in_roi":sum(m["in_ego_roi"] for m in members),
            "members_total":len(members),"assessable":ego_score["assessable"],
            "ego":ego_score,"fused":fused_score,
            "new_situation_presence":ego_score["detected"] is False and fused_score["detected"] is True,
            "hidden_part_recovered":hidden_ego["detected"] is False and hidden_fused["detected"] is True,
            "fully_occluded_member_count":sum(m["occlusion_verified"] for m in members),
            "hidden_ego":hidden_ego,"hidden_fused":hidden_fused})
    return {"anchors":anchors,"anchor_total":len(anchors),
        "anchor_assessable":sum(a["assessable"] for a in anchors),
        "anchor_ego_detected":sum(a["ego"]["detected"] is True for a in anchors),
        "anchor_fused_detected":sum(a["fused"]["detected"] is True for a in anchors),
        "anchor_new_detections":sum(a["new_situation_presence"] for a in anchors),
        "anchor_hidden_parts_recovered":sum(a["hidden_part_recovered"] for a in anchors),
        "anchor_criterion":"diagnostic: >=3 connected raw observed obstacle cells overlapping exact target-return GT in an anchor; no interpolation",
        "anchor_limit":"Presence at a situation region only, not S1-S9 classification or behavioral abnormality detection. Partial ROI/coverage is reported; GT used only to match evaluation."}
