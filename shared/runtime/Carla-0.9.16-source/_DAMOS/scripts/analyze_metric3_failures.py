"""Read-only forensic analysis of the submitted 100-case ZIP; never replace verdicts."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import zipfile


def longest_span(rows):
    start, previous, best = None, None, 0
    for row in rows:
        stamp = int(row["capture_sim_ns"])
        good = row["eligible"] is True and row["fused_detected"] is True
        consecutive = previous is not None and abs(stamp - previous - 200000000) <= 1000
        if not good:
            start, previous = None, None
            continue
        if start is None or not consecutive:
            start = stamp
        best = max(best, stamp - start)
        previous = stamp
    return best


def analyze(source):
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    with zipfile.ZipFile(source) as archive:
        summary_names = [n for n in archive.namelist() if n.endswith('/summary.json')
                         and '/results/' not in n and '/raw/' not in n]
        if len(summary_names) != 1:
            raise ValueError("Expected one top-level submitted summary")
        summary = json.loads(archive.read(summary_names[0]))
        failures = []
        for case in summary["cases"]:
            if case["successful"] is True:
                continue
            case_id = case["case_id"]
            names = sorted(n for n in archive.namelist() if f'/results/{case_id}/' in n)
            rows = [json.loads(archive.read(n)) for n in names if n.endswith('/evidence.json')]
            result = {"case_id": case_id, "scenario": case["scenario"], "status": case["status"],
                      "original_successful": False, "evidence_paths": names}
            if rows:
                result.update(category="fragmented_detection", processed_samples=len(rows),
                    eligible_samples=sum(r["eligible"] is True for r in rows),
                    gt_below_3_samples=sum(r["gt_return_cells"] < 3 for r in rows),
                    eligible_prediction_miss_samples=sum(r["eligible"] is True and r["fused_detected"] is not True for r in rows),
                    detected_samples=sum(r["fused_detected"] is True for r in rows),
                    longest_span_ns=str(longest_span(rows)),
                    direct_cause="GT eligibility gaps and connected-overlap misses break the 0.5s continuous witness",
                    deeper_cause="occlusion, scan geometry and model errors not causally separated by these metadata alone",
                    timeline=[{k:r[k] for k in ("round_index", "frame_id", "capture_sim_ns", "eligible",
                        "gt_return_cells", "ego_connected_overlap_cells", "fused_connected_overlap_cells", "fused_detected")} for r in rows])
                assert result["longest_span_ns"] == case["fused_longest_span_ns"]
            else:
                logs = '\n'.join(archive.read(n).decode(errors='replace') for n in names if n.endswith('/runtime.log'))
                error = case.get("error") or ""
                if "out of memory" in (logs + error).lower() or "outofmemoryerror" in (logs + error).lower():
                    result.update(category="cuda_oom", direct_cause="CUDA memory exhaustion in the original runtime log")
                elif "did not reach the preselected anchor" in logs:
                    result.update(category="ego_approach_timeout", direct_cause="Ego did not reach the preselected anchor within the fixed approach limit",
                                  deeper_cause="route blockage versus controller behavior requires a separate trace investigation")
                else:
                    result.update(category="unclassified_execution_error", direct_cause=error)
            failures.append(result)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == sha
    return {"source_zip": str(source), "source_zip_sha256": sha, "source_unchanged": True,
        "planned": summary["planned_cases"], "successful": summary["successful_cases"],
        "success_rate_percent": summary["success_rate_percent"], "failure_cases": failures,
        "categories": dict(Counter(r["category"] for r in failures)),
        "original_verdicts_preserved": True, "no_model_threshold_or_seed_changes": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_zip", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.source_zip)
    with args.output.open('x', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps({"categories": result["categories"], "source_unchanged": True}, indent=2))


if __name__ == "__main__":
    main()
