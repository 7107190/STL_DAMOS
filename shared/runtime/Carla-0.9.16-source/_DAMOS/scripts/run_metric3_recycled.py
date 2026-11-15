"""Operational recovery: fresh owned CARLA server for each remaining case."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

from bev_accuracy_batch import sha256, write_json
from run_metric3_100 import aggregate, verify_freeze


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=2680)
    args = parser.parse_args()
    trial = args.output.resolve()
    protocol = verify_freeze(trial)
    aggregate(trial, protocol)
    operations = trial / "results/_operations"
    operations.mkdir(parents=True, exist_ok=True)
    stop = trial / "operations_001.json"
    if stop.exists():
        shutil.copy2(stop, operations / "initial_stop.json")
    source = Path(__file__)
    runner = source.parent / "run_metric3_100.py"
    audit_path = operations / "recycle_driver.json"
    if audit_path.exists():
        raise FileExistsError("Operational audit already exists; inspect before resuming")
    completed = [p for p in (trial / "results").glob("*/result.json")]
    audit = {"started_utc": datetime.now(timezone.utc).isoformat(),
        "reason": "Actual CARLA VRAM accumulation and CUDA OOM in the continuous server session",
        "change": "fresh owned CARLA server per remaining case, using frozen CLI --limit 1",
        "no_changes_to_model_case_seeds_or_criterion": True,
        "no_completed_case_reexecuted_or_replaced": True,
        "preserved_completed_results": {str(p.relative_to(trial)): sha256(p) for p in completed},
        "driver_sha256": sha256(source), "driver_source": source.read_text(),
        "frozen_runner_sha256": sha256(runner), "runs": []}
    write_json(audit_path, audit)
    for case in protocol["cases"]:
        folder = trial / "results" / case["case_id"]
        if (folder / "result.json").exists():
            continue
        if folder.exists():
            raise RuntimeError("Incomplete existing case requires audit: " + str(folder))
        verify_freeze(trial)
        command = [sys.executable, "-u", str(runner), "run", "--output", str(trial),
                   "--port", str(args.port), "--limit", "1"]
        entry = {"case_id": case["case_id"], "command": command,
                 "started_utc": datetime.now(timezone.utc).isoformat()}
        print(f"[RECYCLE] Fresh server for {case['case_id']}", flush=True)
        code = subprocess.call(command)
        entry.update(returncode=code, finished_utc=datetime.now(timezone.utc).isoformat())
        audit["runs"].append(entry)
        write_json(audit_path, audit)
        if code != 0 or not (folder / "result.json").exists():
            raise RuntimeError("Operational execution interrupted; no case replacement")
        for relative, original in audit["preserved_completed_results"].items():
            if sha256(trial / relative) != original:
                raise ValueError("Previously completed result changed")
    audit["finished_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(audit_path, audit)
    summary = aggregate(trial, protocol)
    print("[RECYCLE COMPLETE] " + json.dumps({k: summary[k] for k in
          ("completed_cases", "successful_cases", "criterion_pass")}), flush=True)


if __name__ == "__main__":
    main()
