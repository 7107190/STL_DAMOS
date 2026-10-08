"""Planar pose alignment and confidence-weighted fusion of observed BEV cells."""
import numpy as np
from bev_occupancy import obstacle_presence, OBSTACLE_CLASSES, GROUND_CLASSES

FUSION_MODES = ("weighted","ego_observed","binary_weighted")


def warp_to_ego(source, ego_pose):
    axis = -40 + (np.arange(200) + .5)*.4
    x, y = np.meshgrid(axis, axis, indexing="ij")
    query = np.stack([x, y, np.zeros_like(x), np.ones_like(x)]).reshape(4, -1)
    # Inverse lookup avoids forward-splat holes. Local BEV ground is z=0.
    points = np.linalg.inv(source["pose"]) @ ego_pose @ query
    ix = np.floor((points[0] + 40)/.4).astype(np.int64)
    iy = np.floor((points[1] + 40)/.4).astype(np.int64)
    in_grid = (ix>=0)&(ix<200)&(iy>=0)&(iy<200)
    cx, cy = np.clip(ix,0,199), np.clip(iy,0,199)
    mask = (in_grid & source["valid"][cx,cy].astype(bool)).reshape(200,200)
    probs = source["probabilities"][:,cx,cy].reshape(17,200,200).astype(np.float32)
    return probs, mask


def fuse_bevs(ego, peers, *, mode="weighted"):
    if mode not in FUSION_MODES:
        raise ValueError("Unknown BEV fusion mode.")
    ego_pose = ego["pose"]
    total = np.zeros((17,200,200), np.float32)
    weight = np.zeros((200,200), np.float32)
    contributor_count = np.zeros((200,200), np.uint8)
    provenance = np.full((200,200), 255, np.uint8)
    best = np.zeros((200,200), np.float32)
    for i, source in enumerate([ego, *peers]):
        p, valid = warp_to_ego(source, ego_pose)
        if mode=="binary_weighted":
            obstacle = p[list(OBSTACLE_CLASSES)].max(0)
            ground = p[list(GROUND_CLASSES)].max(0)
            certainty = np.abs(obstacle-ground)/np.maximum(obstacle+ground,1e-7)
            confidence = np.maximum(certainty,.05)*valid
        else:
            confidence = p.max(0) * valid
        total += p * confidence[None]
        weight += confidence
        contributor_count += valid
        better = valid & (confidence > best)
        provenance[better], best[better] = i, confidence[better]
    valid = weight > 0
    total[:,valid] /= weight[valid]
    if mode == "ego_observed":
        # Experimental conservative baseline: peers fill blind cells, not ego evidence.
        observed = ego["valid"].astype(bool)
        total[:,observed] = ego["probabilities"][:,observed]
        selected_source = provenance.copy()
        selected_source[observed] = 0
    # Unknown cells carry no confident prediction and are rendered dark.
    total[:,~valid] = 1/17
    total = total.astype(np.float16)
    presence = obstacle_presence(total,valid)
    result = {"probabilities": total, "valid": valid.astype(np.uint8), **presence,
            "pose": ego_pose.copy(), "contributors": contributor_count,
            "strongest_source": provenance,
            "peer_added_cells": (valid & ~ego["valid"].astype(bool)).astype(np.uint8)}
    if mode == "ego_observed":
        result["selected_source"] = selected_source
    return result
