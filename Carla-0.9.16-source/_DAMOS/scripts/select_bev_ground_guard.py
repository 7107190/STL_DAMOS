"""Freeze a ground guard on development scenes before opening confirmation data."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = json.loads(args.results.read_text())
    configs = {r["name"]: r for r in results["protocol"]["configs"]}
    baseline = {r["scenario"]: r for r in results["rows"] if r["candidate"] == "baseline"}
    comparisons = []
    for name, config in configs.items():
        rows = [r for r in results["rows"] if r["candidate"] == name]
        total, base = Counter(), Counter()
        errors = []
        for row in rows:
            original = baseline[row["scenario"]]
            total.update({k: v for k, v in row.items() if isinstance(v, int)})
            base.update({k: v for k, v in original.items() if isinstance(v, int)})
            for prefix in ("full", "crop"):
                for suffix in ("detected_frames", "region_detected_frames"):
                    if row[prefix + "_" + suffix] < original[prefix + "_" + suffix]:
                        errors.append(row["scenario"] + ":" + prefix + "_" + suffix)
                if row[prefix + "_target_obstacle"] < .99 * original[prefix + "_target_obstacle"]:
                    errors.append(row["scenario"] + ":" + prefix + "_target_cell_loss")
                if row[prefix + "_obstacle_obstacle"] < .98 * original[prefix + "_obstacle_obstacle"]:
                    errors.append(row["scenario"] + ":" + prefix + "_scene_obstacle_cell_loss")
                if row[prefix + "_road_unknown"] > original[prefix + "_road_unknown"]:
                    errors.append(row["scenario"] + ":" + prefix + "_road_unknown_increase")
        reduction = 1 - total["full_road_obstacle"] / base["full_road_obstacle"]
        strict_errors = errors + (["full_road_FP_reduction_below_80_percent"] if reduction < .8 else [])
        if reduction < .5:
            errors.append("full_road_FP_reduction_below_50_percent")
        comparisons.append({"name": name, "config": config, "eligible": not errors,
            "rejections": errors, "strict_80_percent_goal_passed": not strict_errors,
            "full_road_fp_reduction": reduction,
            "full_road_fp_cells": total["full_road_obstacle"],
            "crop_road_fp_cells": total["crop_road_obstacle"],
            "changed_cells": total["changed_cells"],
            "target_hit_cells": total["crop_target_obstacle"],
            "target_detected_frames": total["crop_detected_frames"],
            "region_detected_frames": total["crop_region_detected_frames"]})
    eligible = [r for r in comparisons if r["eligible"] and r["config"]["kind"] == "guard"]
    strict = [r for r in eligible if r["strict_80_percent_goal_passed"]]
    eligible = strict or eligible
    selected = min(eligible, key=lambda r: (r["full_road_fp_cells"], r["changed_cells"])) if eligible else None
    report = {"source_sha256": hashlib.sha256(args.results.read_bytes()).hexdigest(),
              "selected": selected, "comparisons": comparisons,
              "confirmation_opened_for_selection": False}
    (args.output / "selection.json").write_text(json.dumps(report, indent=2))
    if selected:
        (args.output / "confirmation_configs.json").write_text(json.dumps([
            configs["baseline"], selected["config"]], indent=2))
        profile = {k: v for k, v in selected["config"].items() if k not in ("name", "kind")}
        (args.output / "ground_guard.json").write_text(json.dumps(profile, indent=2))
    print(json.dumps(selected, indent=2))


if __name__ == "__main__":
    main()
