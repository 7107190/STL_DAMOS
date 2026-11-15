"""Experimental source-evidence correction; no target IDs, labels, or evaluation inputs."""

import numpy as np
from scipy.ndimage import convolve, maximum_filter

from bev_fusion import warp_to_ego
from bev_occupancy import GROUND_CLASSES, OBSTACLE_CLASSES, obstacle_presence

PARAMETERS = {
    "height_m": .20,
    "span_m": .20,
    "local_obstacle_ratio": .25,
    "neighbor_radius_cells": 1,
    "allow_single_return": True,
    "single_return_structural_neighbor": True,
    "peer_probability": .60,
    "peer_obstacle_ground_ratio": .90,
    "peer_classes": [2, 7],
    "minimum_peer_obstacle_neighbors": 1,
}


def _occupied(p, valid):
    return obstacle_presence(p, valid)["occupancy"] == 2


def correct_evidence(baseline, peers, lidar, geometry, *, parameters=PARAMETERS,
                     use_sparse=True, use_peer=True):
    raster = np.asarray(lidar)
    if raster.shape == (1, 6, 200, 200):
        raster = raster[0]
    if raster.shape != (6, 200, 200) or np.shape(geometry) != (5, 200, 200):
        raise ValueError("Invalid ordinary-LiDAR geometry")
    p = baseline["probabilities"].astype(np.float32).copy()
    valid = baseline["valid"].astype(bool)
    original_obstacles = _occupied(p, valid)
    neighbor = maximum_filter(original_obstacles.astype(np.uint8),
                              2 * parameters["neighbor_radius_cells"] + 1, mode="constant") > 0
    obs = p[list(OBSTACLE_CLASSES)].max(0)
    ground = p[list(GROUND_CLASSES)].max(0)
    count = np.rint(np.expm1(raster[1])).astype(np.int32)
    # A single return has zero measured span regardless of its height above the road.
    span_ok = raster[4] > parameters["span_m"]
    if use_sparse and parameters["allow_single_return"]:
        single = count == 1
        if parameters["single_return_structural_neighbor"]:
            structure = (original_obstacles & (raster[0] > 0)
                         & (geometry[0] > parameters["height_m"])
                         & (raster[4] > parameters["span_m"]))
            single &= maximum_filter(structure.astype(np.uint8), 3, mode="constant") > 0
        span_ok |= single
    local_mask = (valid & (raster[0] > 0) & neighbor & ~original_obstacles
                  & (geometry[0] > parameters["height_m"]) & span_ok
                  & (obs / np.maximum(obs + ground, 1e-7) >= parameters["local_obstacle_ratio"]))
    if local_mask.any():
        choices = np.array(OBSTACLE_CLASSES)[p[list(OBSTACLE_CLASSES)].argmax(0)]
        x, y = np.where(local_mask)
        p[choices[x, y], x, y] = np.maximum(p[:, x, y].max(0) * 1.05, .01)
        p[:, x, y] /= p[:, x, y].sum(0)
    peer_mask = np.zeros((200, 200), bool)
    selected_peer = np.full((200, 200), -1, np.int16)
    best = np.zeros((200, 200), np.float32)
    if use_peer:
        for index, peer in enumerate(peers):
            aligned, peer_valid = warp_to_ego(peer, baseline["pose"])
            labels = aligned.argmax(0)
            occupied = _occupied(aligned, peer_valid)
            other_neighbors = convolve(occupied.astype(np.int16), np.ones((3, 3), np.int16),
                                       mode="constant") - occupied
            peer_obs = aligned[list(OBSTACLE_CLASSES)].max(0)
            peer_ground = aligned[list(GROUND_CLASSES)].max(0)
            ratio = peer_obs / np.maximum(peer_obs + peer_ground, 1e-7)
            classes = parameters["peer_classes"] or OBSTACLE_CLASSES
            credible = (peer_valid & occupied & np.isin(labels, classes)
                        & (peer_obs >= parameters["peer_probability"])
                        & (ratio >= parameters["peer_obstacle_ground_ratio"])
                        & (other_neighbors >= parameters["minimum_peer_obstacle_neighbors"]))
            # Preserve local positive detections, not every local ground label.
            adopt = credible & ~original_obstacles & ~local_mask & (peer_obs > best)
            p[:, adopt] = aligned[:, adopt]
            valid[adopt] = True
            best[adopt] = peer_obs[adopt]
            peer_mask[adopt] = True
            selected_peer[adopt] = index
    p = p.astype(np.float16)
    result = dict(baseline, probabilities=p, valid=valid.astype(np.uint8),
                  **obstacle_presence(p, valid))
    if "selected_source" in result:
        result["selected_source"] = result["selected_source"].copy()
        result["selected_source"][peer_mask] = selected_peer[peer_mask] + 1
    return result, {"local_promoted": local_mask, "peer_promoted": peer_mask,
                    "selected_peer": selected_peer}
