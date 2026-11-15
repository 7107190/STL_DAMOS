"""Relative-confidence extension of the frozen v2 evidence correction."""

import numpy as np

from bev_evidence_correction import PARAMETERS as V2_PARAMETERS, correct_evidence
from bev_occupancy import GROUND_CLASSES, OBSTACLE_CLASSES, obstacle_presence

PARAMETERS = {"weak_peer_minimum": .50, "require_peer_over_ego_ground": True,
              "require_ego_observed_ground": True, "v2": V2_PARAMETERS}


def relative_correction(baseline, ego, peers, lidar, geometry):
    if not np.allclose(ego["pose"], baseline["pose"], rtol=0, atol=1e-4):
        raise ValueError("Ego and fused BEV poses differ")
    strict, strict_flags = correct_evidence(baseline, peers, lidar, geometry)
    proposal, proposal_flags = correct_evidence(baseline, peers, lidar, geometry,
        parameters=dict(V2_PARAMETERS, peer_probability=PARAMETERS["weak_peer_minimum"]))
    peer_score = proposal["probabilities"][list(OBSTACLE_CLASSES)].max(0).astype(np.float32)
    ego_ground = ego["probabilities"][list(GROUND_CLASSES)].max(0).astype(np.float32)
    ego_presence = obstacle_presence(ego["probabilities"], ego["valid"])["occupancy"]
    # Lower-confidence peer evidence must defeat an observed, uncertain Ego ground label.
    adopt = (proposal_flags["peer_promoted"] & (peer_score < V2_PARAMETERS["peer_probability"])
             & (strict["occupancy"] != 2) & (ego_presence == 1)
             & (peer_score > ego_ground))
    probabilities = strict["probabilities"].copy()
    probabilities[:, adopt] = proposal["probabilities"][:, adopt]
    valid = strict["valid"].copy()
    valid[adopt] = 1
    result = dict(strict, probabilities=probabilities, valid=valid,
                  **obstacle_presence(probabilities, valid))
    if "selected_source" in result:
        result["selected_source"] = strict["selected_source"].copy()
        result["selected_source"][adopt] = proposal["selected_source"][adopt]
    return result, {"relative_promoted": adopt, "strict_prediction": strict,
                    "relaxed_prediction": proposal, "strict_flags": strict_flags}
