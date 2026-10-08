"""Bind the selected sensor-only postprocessor to a new immutable checkpoint hash."""
import argparse
import hashlib
import json
from pathlib import Path

import torch

from bev_ground_guard import validate_ground_guard


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Never replace a historical checkpoint.")
    profile = json.loads(args.profile.read_text())
    validate_ground_guard(profile)
    state = torch.load(args.parent, map_location="cpu", weights_only=True)
    if state.get("ground_guard") is not None:
        raise ValueError("Expected an unguarded parent checkpoint.")
    parent_hash = hashlib.sha256(args.parent.read_bytes()).hexdigest()
    state["ground_guard"] = profile
    state["ground_guard_profile_sha256"] = hashlib.sha256(args.profile.read_bytes()).hexdigest()
    state["ground_guard_parent_sha256"] = parent_hash
    args.output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(state, args.output)
    loaded = torch.load(args.output, map_location="cpu", weights_only=True)
    if set(loaded["model"]) != set(state["model"]) or any(
            not torch.equal(loaded["model"][k], state["model"][k]) for k in state["model"]):
        raise AssertionError("Checkpoint model weights changed during packaging.")
    if hashlib.sha256(args.parent.read_bytes()).hexdigest() != parent_hash:
        raise AssertionError("Parent checkpoint was modified.")
    report = {"parent_sha256": parent_hash, "checkpoint_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "all_model_weights_unchanged": True, "ground_guard": profile,
        "profile_bound_via_checkpoint_sha256": True, "retraining_performed": False}
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
