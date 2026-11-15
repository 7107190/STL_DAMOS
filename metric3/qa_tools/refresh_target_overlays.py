"""Add versioned display images; verify every original result file is untouched."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from m3_visuals import OVERLAY_IMAGE, OVERLAY_RECORD, render_target, target_match_cells


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run", action="append", required=True)
    args = parser.parse_args()
    records = []
    for run in args.run:
        if not run.startswith("live_metric3_") or Path(run).name != run:
            raise ValueError("Invalid live run")
        folder = args.root / "reports" / run
        summary = json.loads((folder / "summary.json").read_text())
        assert summary["all_cases_complete"]
        protected = {p: digest(p) for p in folder.rglob("*")
                     if p.is_file() and p.name not in (OVERLAY_IMAGE, OVERLAY_RECORD)}
        samples = matches = misses = 0
        for case in summary["cases"]:
            target = Path(case["output"])
            assert target.is_relative_to(folder / "cases")
            rows = [json.loads(line) for line in (target / "samples.jsonl").read_text().splitlines()]
            for index, row in enumerate(rows, 1):
                sample = target / f"round_{index:03d}"
                render_target(sample, row)
                record = json.loads((sample / OVERLAY_RECORD).read_text())
                assert record["target_match_cell_indices"] == target_match_cells(row).tolist()
                assert record["missed_target_cells_filled"] is False
                samples += 1
                matches += int(record["target_match_cells"] > 0)
                misses += int(record["target_match_cells"] == 0)
        assert all(digest(path) == expected for path, expected in protected.items())
        record = {"run": run, "samples_rendered": samples, "samples_with_target_matches": matches,
                  "samples_without_target_matches": misses, "original_files_hash_checked": len(protected),
                  "all_original_files_unchanged": True, "all_pass": True}
        records.append(record)
        print(json.dumps(record), flush=True)
    result = {"created_utc": datetime.now(timezone.utc).isoformat(), "display_only": True,
              "target_match_color_rgb": [237, 100, 166], "runs": records, "all_pass": True}
    output = args.root / "logs/display_update_20261109"
    output.mkdir(exist_ok=True)
    (output / "overlay_verification.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
