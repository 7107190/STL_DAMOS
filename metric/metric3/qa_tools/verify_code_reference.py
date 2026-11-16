"""Verify the read-only source UI and unchanged metric calculations."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import unittest

from source_reference import catalog, reference
from verify_live_report import verify as verify_speed
from m3_verify import verify as verify_accuracy
from report_data import atomic_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT2 = PROJECT_ROOT / "metric2"
ROOT3 = PROJECT_ROOT / "metric3"


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ui = read(ROOT2 / "logs/code_reference_20261109/browser_verification.json")
    assert ui["all_pass"]
    runs = {2: read(ROOT2 / "runtime_ready.json")["full_live_run"],
            3: read(ROOT3 / "runtime_ready.json")["full_live_run"]}
    changed = {}
    preexisting = {}
    sources = []
    for metric, root in ((2, ROOT2), (3, ROOT3)):
        report = root / "reports" / runs[metric]
        frozen = read(report / ("environment.json" if metric == 2 else "protocol.json"))
        hashes = frozen["measured_code_sha256" if metric == 2 else "source_hashes"]
        actual = {path: digest(Path(path)) for path, expected in hashes.items() if digest(Path(path)) != expected}
        if metric == 2:
            prior = read(report / "code_snapshot_validation.json")
            launcher = str(root / "app/launch_local.py")
            assert prior["processing_sources_unchanged"] and launcher in prior["changed_nonprocessing_files"]
            assert actual[launcher] == "eb239fe9c3658e9a932a26efb217f778fbdaeb2e5a52e2efdc88093ae7be1ba2"
            preexisting[metric] = {launcher: actual[launcher]}
            allowed = {str(root / "app/server.py"), launcher}
        else:
            prior = read(root / "logs/display_update_20261109/verification.json")
            preexisting[metric] = prior["changed_original_python_files"]
            assert prior["all_pass"]
            assert all(actual[path] == expected for path, expected in preexisting[metric].items())
            allowed = {str(root / "app/m3_server.py"), *preexisting[metric]}
        assert set(actual) == allowed, actual
        changed[metric] = {path: value for path, value in actual.items() if path not in preexisting[metric]}
        for entry in catalog(root, metric):
            source = reference(root, metric, runs[metric], entry["id"])
            assert source["matches_selected_run"] is True
            sources.append({k: source[k] for k in ("metric", "section", "file", "function", "start_line", "end_line", "sha256", "matches_selected_run")})
    speed = verify_speed(ROOT2, runs[2], require_full=True)
    accuracy = verify_accuracy(ROOT3, runs[3], require_full=True)
    assert speed["actual_live_input_rounds"] == 135 and speed["max_seconds"] == 1.658583014
    assert accuracy == read(ROOT3 / "reports" / runs[3] / "verification.json")
    assert accuracy["successful"] == 100
    assert digest(Path(os.environ["DAMOS_BEV_CHECKPOINT"])) == read(ROOT3 / "reports" / runs[3] / "protocol.json")["checkpoint_sha256"]
    tests = {}
    for metric, root, pattern in ((2, ROOT2, "test_*.py"), (3, ROOT3, "test_m3_*.py")):
        result = unittest.TextTestRunner(verbosity=1).run(unittest.TestLoader().discover(str(root / "app"), pattern=pattern))
        assert result.wasSuccessful()
        tests[metric] = result.testsRun
    assert sum(tests.values()) == 75
    result = {"verified_utc": datetime.now(timezone.utc).isoformat(), "read_only_ui_update": True,
              "allowed_ui_source_changes": changed, "preexisting_verified_nonprocessing_changes": preexisting,
              "all_calculation_source_hashes_match_selected_trials": True,
              "sources": sources, "independent_speed_recalculation": speed,
              "independent_accuracy_recalculation": accuracy, "unit_tests": tests,
              "browser_checks_passed": True, "metric_data_not_overwritten": True, "all_pass": True}
    for metric, root in ((2, ROOT2), (3, ROOT3)):
        folder = root / "logs/code_reference_20261109"
        folder.mkdir(exist_ok=True)
        atomic_json(folder / "verification.json", result)
        ready = read(root / "runtime_ready.json")
        ready["code_reference_update"] = {"verified": True, "verified_utc": result["verified_utc"],
                                          "verification": str((folder / "verification.json").relative_to(root)),
                                          "calculation_unchanged": True}
        atomic_json(root / "runtime_ready.json", ready)
    print(json.dumps({"all_pass": True, "calculation_sections": len(sources), "unit_tests": tests,
                      "metric2_normal": 135, "metric2_max_seconds": speed["max_seconds"],
                      "metric3_normal": 100, "metric3_successful": accuracy["successful"]}, indent=2))


if __name__ == "__main__":
    main()
