"""Require fresh full-trial, UI, unit, and frozen-source evidence before launch."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    run = read(ROOT / "logs/full_validation_run.json")["run"]
    report = ROOT / "reports" / run
    verification = read(report / "verification.json")
    assert verification["all_pass"] and verification["criterion_pass"]
    assert verification["actual_live_anchor_cases"] == 100
    assert verification["execution_errors"] == 0
    assert read(ROOT / "jobs" / run / "state.json")["status"] == "complete"
    unit = read(ROOT / "logs/unit_test_verification.json")
    ui = read(ROOT / "logs/qa_final.json")
    assert unit["all_pass"] and unit["tests_run"] == 23
    assert ui["all_pass"] and ui["run"] == run
    protocol = read(report / "protocol.json")
    assert digest(report / "protocol.json") == read(report / "protocol_lock.json")["sha256"]
    for path, expected in protocol["source_hashes"].items():
        assert digest(Path(path)) == expected, path
    assert digest(Path(os.environ["DAMOS_BEV_CHECKPOINT"])) == protocol["checkpoint_sha256"]
    integrity = {
        "run": run,
        "source_files_checked": len(protocol["source_hashes"]),
        "protocol_lock_unchanged": True,
        "checkpoint_unchanged": True,
        "all_pass": True,
    }
    (report / "source_integrity_verification.json").write_text(json.dumps(integrity, indent=2))
    ready = {
        "verified": True,
        "verified_utc": datetime.now(timezone.utc).isoformat(),
        "hostname": platform.node(),
        "full_live_run": run,
        "actual_live_anchor_cases": 100,
        "successful": verification["successful"],
        "success_rate_percent": verification["success_rate_percent"],
        "criterion_pass": verification["criterion_pass"],
        "full_verification": str((report / "verification.json").relative_to(ROOT)),
        "source_integrity_verification": str((report / "source_integrity_verification.json").relative_to(ROOT)),
        "browser_verification": "logs/qa_final.json",
        "unit_tests_passed": unit["tests_run"],
        "native_carla_window_verified": True,
        "automated_native_window_display": "isolated Xvfb :92",
        "physical_monitor_layout_manually_checked": False,
        "physical_launcher_display": "inherit vv desktop DISPLAY",
        "desktop_entrypoint_verified": False,
        "shared_runtime_readiness": str(ROOT.parent / "metric2/runtime_ready.json"),
        "shared_runtime_readiness_sha256": digest(ROOT.parent / "metric2/runtime_ready.json"),
    }
    launch_checks = sorted((ROOT / "logs").glob("launcher_qa_*/verification.json"))
    if launch_checks:
        latest = launch_checks[-1]
        launch = read(latest)
        assert launch["all_pass"] and launch["new_live_trial"] and launch["owned_server_closed"]
        ready.update(desktop_entrypoint_verified=True, launcher_verification=str(latest.relative_to(ROOT)))
    (ROOT / "runtime_ready.json").write_text(json.dumps(ready, indent=2))
    print(json.dumps({"readiness": ready, "integrity": integrity}, indent=2))


if __name__ == "__main__":
    main()
