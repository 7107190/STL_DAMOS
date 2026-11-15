"""Exercise actual live single-scenario trials through the same UI start API."""
import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8762")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenarios", nargs="+", default=[f"S{i}" for i in range(1, 10)])
    args = parser.parse_args()
    def get(path):
        with urllib.request.urlopen(args.url + path, timeout=30) as response:
            return json.load(response)
    def start(scenario):
        config = get("/api/config")
        request = urllib.request.Request(args.url + "/api/start",
            data=json.dumps({"mode": "single", "scenario": scenario}).encode(),
            headers={"Content-Type": "application/json", "Origin": args.url, "X-CSRF-Token": config["token"]})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)["run"]
    results = []
    for scenario in args.scenarios:
        run = start(scenario)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            snap = get("/api/state?run=" + run)
            if snap["status"] in ("failed", "cancelled", "complete"):
                break
            time.sleep(2)
        else:
            raise TimeoutError("Live trial did not finish: " + run)
        row = {"scenario": scenario, "run": run, "status": snap["status"],
               "normal": snap["normal"], "max_seconds": snap["max_seconds"],
               "audited": snap["audited"], "clock_coverage": snap["clock_coverage"],
               "source": snap["source"], "full_scope": snap["full_scope"], "error": snap.get("job_error")}
        row["passed"] = (snap["status"] == "complete" and snap["normal"] == 1 and snap["within_limit"] == 1
                         and snap["audited"] == 1 and snap["clock_coverage"] == 1
                         and snap["source"] == "live_carla" and not snap["full_scope"])
        results.append(row)
        args.output.write_text(json.dumps({"actual_live_trials": True, "results": results,
                                           "all_pass": all(r["passed"] for r in results)}, indent=2))
        print(json.dumps(row), flush=True)
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
