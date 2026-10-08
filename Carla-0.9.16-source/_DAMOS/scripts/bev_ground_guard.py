"""Conservative occupancy correction using ordinary LiDAR, never semantic GT."""
import numpy as np

from bev_lidar_calibration import geometry_features
from bev_occupancy import OBSTACLE_CLASSES, GROUND_CLASSES

VERSION = "ordinary-lidar-ground-guard-v1"


def validate_ground_guard(config):
    if config.get("version") != VERSION:
        raise ValueError("Unsupported ground guard.")
    for key in ("height_m", "span_m"):
        value = float(config[key])
        if not np.isfinite(value) or not 0 < value <= .3:
            raise ValueError("Invalid ground guard threshold.")
    below = float(config.get("below_plane_m", config["height_m"]))
    if not np.isfinite(below) or not 0 < below <= .3:
        raise ValueError("Invalid lower ground band.")
    radius = config.get("protect_radius_cells", 0)
    if type(radius) is not int or not 0 <= radius <= 2:
        raise ValueError("Invalid structural protection radius.")
    if config.get("protect_mode", "structure") not in ("structure", "foreground", "vulnerable"):
        raise ValueError("Invalid structural protection mode.")


def ground_surface(lidar, *, geometry=None):
    raster = np.asarray(lidar, np.float32)
    if raster.shape == (1, 6, 200, 200):
        raster = raster[0]
    if raster.shape != (6, 200, 200) or not np.isfinite(raster).all():
        raise ValueError("Invalid ordinary LiDAR raster.")
    if geometry is None:
        geometry = geometry_features(raster)
    return raster, geometry


def ground_candidates(lidar, config, *, geometry=None, prediction=None):
    validate_ground_guard(config)
    raster, geometry = ground_surface(lidar, geometry=geometry)
    height = float(config["height_m"])
    # Both ends must lie close to the fitted plane; flat elevated tops are kept.
    below = float(config.get("below_plane_m", height))
    near = ((geometry[0] <= height) & (geometry[1] >= -below)
            & (raster[4] <= float(config["span_m"])) & (raster[0] > 0))
    support = np.argwhere(near)
    if len(support) < 256 or np.min(np.ptp(support, axis=0)) * .4 < 12:
        return np.zeros((200, 200), bool)
    radius = config.get("protect_radius_cells", 0)
    if radius:
        from scipy.ndimage import maximum_filter
        structure = ((geometry[0] > .25) | (raster[4] > .2)) & (raster[0] > 0)
        mode = config.get("protect_mode", "structure")
        if mode != "structure":
            if prediction is None:
                raise ValueError("Model evidence required for foreground protection.")
            classes = tuple(range(1, 11)) if mode == "foreground" else (2, 6, 7)
            structure &= np.isin(prediction["probabilities"].argmax(0), classes)
        protected = maximum_filter(structure, 2 * radius + 1, mode="constant", cval=0)
        near &= ~protected
    return near


def lower_obstacle_evidence(prediction, mask):
    """Only lower conflicting obstacle evidence; never invent observed cells."""
    p = np.asarray(prediction["probabilities"], np.float32).copy()
    if p.shape != (17, 200, 200) or np.shape(mask) != (200, 200):
        raise ValueError("Invalid ground guard prediction.")
    labels = p.argmax(0)
    changed = (np.asarray(mask, bool) & prediction["valid"].astype(bool)
               & np.isin(labels, OBSTACLE_CLASSES))
    obstacle = p[list(OBSTACLE_CLASSES)].max(0)
    ground = p[list(GROUND_CLASSES)].max(0)
    scale = np.ones((200, 200), np.float32)
    scale[changed] = np.minimum(1., .95 * ground[changed] / np.maximum(obstacle[changed], 1e-7))
    p[list(OBSTACLE_CLASSES)] *= scale
    p[:, changed] /= np.maximum(p[:, changed].sum(0), 1e-7)
    # Others may beat ground after correction. Keep uncertainty instead of
    # forcing a road label or increasing ground evidence without model support.
    p[:, ~changed] = prediction["probabilities"][:, ~changed]
    return dict(prediction, probabilities=p.astype(np.float16)), changed


def apply_ground_guard(prediction, lidar, config, *, geometry=None):
    candidates = ground_candidates(lidar, config, geometry=geometry, prediction=prediction)
    return lower_obstacle_evidence(prediction, candidates)
