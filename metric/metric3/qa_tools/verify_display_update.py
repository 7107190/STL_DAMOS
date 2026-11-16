"""Audit a display-only revision without replacing the original trial records."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import unittest

from m3_verify import verify
from m3_visuals import OVERLAY_IMAGE, OVERLAY_RECORD, target_match_cells
from report_data import atomic_json


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--fresh-run", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / "logs/display_update_20261109"
    baseline = read(root / "runtime_ready.json")["full_live_run"]
    report = root / "reports" / baseline
    protocol = read(report / "protocol.json")
    changed = {path: digest(Path(path)) for path, expected in protocol["source_hashes"].items()
               if digest(Path(path)) != expected}
    assert set(changed) == {str(root / "app" / name) for name in ("m3_visuals.py", "m3_data.py")}, changed
    assert digest(Path(os.environ["DAMOS_BEV_CHECKPOINT"])) == protocol["checkpoint_sha256"]
    original = read(report / "verification.json")
    baseline_recheck = verify(root, baseline, require_full=True)
    assert baseline_recheck == original
    atomic_json(output / "baseline_recalculation.json", baseline_recheck)
    fresh = verify(root, args.fresh_run)
    assert fresh["all_pass"] and fresh["actual_live_anchor_cases"] == fresh["successful"] == 1
    for case in read(root / "reports" / args.fresh_run / "summary.json")["cases"]:
        target = Path(case["output"])
        rows = [json.loads(line) for line in (target / "samples.jsonl").read_text().splitlines()]
        for index, row in enumerate(rows, 1):
            sample = target / f"round_{index:03d}"
            assert (sample / OVERLAY_IMAGE).is_file()
            overlay = read(sample / OVERLAY_RECORD)
            assert overlay["target_match_cell_indices"] == target_match_cells(row).tolist()
            assert overlay["frame_id"] == row["frame_id"]
            assert overlay["missed_target_cells_filled"] is False
    atomic_json(output / "fresh_trial_verification.json", fresh)
    tests = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover(str(root / "app"), pattern="test_m3_*.py"))
    assert tests.wasSuccessful() and tests.testsRun >= 30
    overlay = read(output / "overlay_verification.json")
    browser = read(output / "browser_verification.json")
    assert overlay["all_pass"] and browser["all_pass"]
    result = {"created_utc": datetime.now(timezone.utc).isoformat(), "display_only": True,
              "changed_original_python_files": changed, "inference_scoring_and_sharing_sources_unchanged": True,
              "checkpoint_unchanged": True, "baseline_trial_unchanged": True,
              "baseline_full_trial": baseline, "baseline_successful": original["successful"],
              "fresh_trial": args.fresh_run, "fresh_successful": fresh["successful"],
              "unit_tests_passed": tests.testsRun, "browser_checks_passed": True,
              "versioned_overlays_keep_original_images": True, "all_pass": True}
    atomic_json(output / "verification.json", result)
    ready = read(root / "runtime_ready.json")
    ready["display_update"] = {"verified": True, "verified_utc": result["created_utc"],
                               "verification": str((output / "verification.json").relative_to(root)),
                               "fresh_trial": args.fresh_run, "unit_tests_passed": tests.testsRun,
                               "scoring_unchanged": True}
    atomic_json(root / "runtime_ready.json", ready)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
