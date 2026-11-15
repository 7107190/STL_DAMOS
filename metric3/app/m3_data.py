"""Read only current-run anchor outcomes; never import old metadata results."""
import json
from pathlib import Path

from m3_rules import duration, rollup, safe_run
from report_data import read


class Reports:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.reports, self.jobs = self.root / "reports", self.root / "jobs"

    def folder(self, run):
        return self.reports / safe_run(run)

    def protocol(self, run):
        data = read(self.folder(run) / "protocol.json")
        if not data or data.get("source") != "live_carla_sensor_stream":
            raise ValueError("New live CARLA protocol not yet ready")
        return data

    def history(self):
        result = []
        for job in sorted(self.jobs.glob("live_metric3_*"), reverse=True):
            protocol = read(self.folder(job.name) / "protocol.json", {})
            state = read(job / "state.json", {})
            result.append({"id": job.name, "created_utc": protocol.get("created_utc"),
                           "status": state.get("status"), "planned": len(protocol.get("cases", [])),
                           "mode": read(job / "launch.json", {}).get("mode")})
        return result

    def snapshot(self, run):
        folder = self.folder(run)
        protocol = read(folder / "protocol.json", {})
        if protocol and protocol.get("source") != "live_carla_sensor_stream":
            raise ValueError("Stored replay results cannot enter the live metric3 viewer")
        state = read(self.jobs / run / "state.json", {})
        results = {c["case_id"]: read(folder / "cases" / c["case_id"] / "result.json", {}) for c in protocol.get("cases", [])}
        data = rollup(protocol.get("cases", []), {k:v for k,v in results.items() if v}) if protocol else {
            "planned": 0, "evaluated": 0, "successful": 0, "detection_failures": 0, "execution_errors": 0,
            "success_rate_percent": 0, "all_cases_complete": False, "criterion_pass": False, "cases": []}
        live = read(self.jobs / run / "live.json", {})
        capture = read(self.jobs / run / "capture_progress.json", {})
        spans = [r["fused_longest_span_seconds"] for r in data["cases"]
                 if r["status"] in ("success", "failure")]
        data["mean_continuous_detection_seconds"] = sum(spans) / len(spans) if spans else None
        return {**data, "id": run, "source": "live_carla_sensor_stream", "status": state.get("status", "preparing"),
                "error": state.get("error"), "live": live,
                "capture": capture if capture.get("case_id") == live.get("case_id") else {},
                "protocol": protocol, "report_dir": str(folder)}

    def case_folder(self, run, case_id):
        if case_id not in {c["case_id"] for c in self.protocol(run)["cases"]}:
            raise ValueError("Unplanned anchor case")
        base = self.folder(run) / "cases" / case_id
        attempts = sorted(base.glob("attempt_*"))
        if not attempts:
            raise FileNotFoundError("Anchor not started")
        return attempts[-1]

    def samples(self, run, case_id):
        target = self.case_folder(run, case_id)
        path = target / "samples.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith("\n")] if path.exists() else []

    def detail(self, run, case_id, index=None):
        target = self.case_folder(run, case_id)
        rows = self.samples(run, case_id)
        if index is None:
            index = len(rows) - 1
        if not 0 <= index < len(rows):
            return {"case_id": case_id, "samples": [], "result": read(target / "result.json"), "manifest": read(target / "manifest.json")}
        row = rows[index]
        sample = target / f"round_{index+1:03d}"
        peers = read(sample / "peers.json")
        observer = next(n for n in peers if n != "ego")
        fields = ("round_index", "frame_id", "capture_sim_ns", "eligible", "fused_detected", "ego_detected",
                  "gt_return_cells", "fused_connected_overlap_cells", "ego_connected_overlap_cells", "ego_processing_ms")
        timeline = [{**{k:r.get(k) for k in fields}, "longest_seconds": duration(rows[:i+1])/1e9} for i,r in enumerate(rows)]
        return {"case_id": case_id, "index": index, "samples": timeline, "selected": timeline[index],
                "longest_seconds": duration(rows)/1e9, "ego_longest_seconds": duration(rows,"ego")/1e9,
                "result": read(target / "result.json"), "manifest": read(target / "manifest.json"),
                "proofs_reverified": sum(read(p / "audit.json", {}).get("proofs_reverified", 0) for p in target.glob("round_*")),
                "clock": read(sample / "stream_clock.json"), "stage_times": {"ego": peers["ego"]["stages_ms"], "observer": peers[observer]["stages_ms"]}}

    def asset(self, run, case_id, index, kind):
        samples = self.samples(run, case_id)
        if not 0 <= index < len(samples):
            raise ValueError("Unknown current-run sample")
        folder = self.case_folder(run, case_id) / f"round_{index+1:03d}"
        if kind == "observer_camera":
            matches = list(folder.glob("observer_*_front.png"))
            if len(matches) != 1:
                raise ValueError("Observer camera identity ambiguous")
            return matches[0]
        name = {"scene": "scene_rgb.png", "ego_camera": "ego_front.png", "bev": "ego_obstacle_presence.png"}.get(kind)
        if name is None:
            raise ValueError("Unsupported image")
        if kind == "bev":
            for overlay in ("evaluation_bev_target_v2.png", "evaluation_bev.png"):
                if (folder / overlay).exists():
                    return folder / overlay
        return folder / name
