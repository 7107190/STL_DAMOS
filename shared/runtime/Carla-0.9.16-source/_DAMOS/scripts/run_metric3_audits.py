"""One-time read-only audits with persistent logs, independent of SSH lifetime."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from bev_accuracy_batch import sha256, write_json
from run_metric3_100 import verify_freeze


def atomic_json(path, record):
    temporary = path.with_suffix(".tmp")
    write_json(temporary, record)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trial", type=Path, required=True)
    args = parser.parse_args()
    trial = args.trial.resolve()
    protocol = verify_freeze(trial)
    scripts = Path(__file__).parent
    operations = trial / "results/_operations"
    state_path = operations / "detached_audits.json"
    if state_path.exists():
        raise FileExistsError("Previous detached audit state exists; inspect before resuming")
    preserved = {str((trial / "results" / c["case_id"] / "result.json").relative_to(trial)):
        sha256(trial / "results" / c["case_id"] / "result.json") for c in protocol["cases"]}
    record = {"started_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
        "status": "running", "no_scenario_reexecuted": True,
        "previous_connection_interruption": "Three SSH audit connections exited 255 with connection reset; no completed audit artifacts existed",
        "preserved_result_sha256": preserved, "source_sha256": sha256(Path(__file__)),
        "source": Path(__file__).read_text(encoding="utf-8"), "stages": []}
    atomic_json(state_path, record)
    stages = [
        ("cell_duration", [sys.executable, "-X", "utf8", str(scripts / "verify_metric3_100.py"), str(trial)]),
        ("raw_arrays", [sys.executable, "-u", str(scripts / "audit_metric3_raw.py"), "--trial", str(trial),
            "--output", str(operations / "raw_arrays_audit.json")]),
        ("actual_groth16", [sys.executable, "-u", str(scripts / "audit_metric3_zkp.py"), "--trial", str(trial),
            "--output", str(trial / "zkp_spotcheck.json")]),
    ]
    for name, command in stages:
        log = operations / ("audit_" + name + ".log")
        if log.exists():
            raise FileExistsError("Audit log already exists")
        entry = {"name": name, "command": command, "started_utc": datetime.now(timezone.utc).isoformat()}
        print("[AUDIT START] " + name, flush=True)
        with log.open("x") as output:
            code = subprocess.call(command, stdout=output, stderr=subprocess.STDOUT,
                                   env=dict(os.environ, PYTHONUTF8="1"))
        entry.update(returncode=code, log_sha256=sha256(log),
                     finished_utc=datetime.now(timezone.utc).isoformat())
        if code == 0 and name == "cell_duration":
            entry["actual_result"] = json.loads(log.read_text(encoding="utf-8"))
        record["stages"].append(entry)
        record["status"] = "running" if code == 0 else "failed"
        atomic_json(state_path, record)
        if code:
            raise RuntimeError("Audit failed; original verdicts retained: " + name)
        for relative, original in preserved.items():
            if sha256(trial / relative) != original:
                raise ValueError("Original case result changed during audit")
        print("[AUDIT PASS] " + name, flush=True)
    verify_freeze(trial)
    record.update(status="complete", finished_utc=datetime.now(timezone.utc).isoformat())
    atomic_json(state_path, record)
    print("[AUDITS COMPLETE] Original 100 case results unchanged", flush=True)


if __name__ == "__main__":
    main()
