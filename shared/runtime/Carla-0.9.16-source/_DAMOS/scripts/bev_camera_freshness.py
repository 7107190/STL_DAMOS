"""Camera content checks for controlled, moving-ego pass-by recordings."""
import argparse
import hashlib
import json
from pathlib import Path

CAMERAS = ("cam_front_left", "cam_front", "cam_front_right",
           "cam_back_left", "cam_back", "cam_back_right")


class CameraContentAudit:
    def __init__(self):
        self.frames = []
        self.stamps = []
        self.nodes = {}

    def observe(self, frame, stamp, images):
        if self.frames and (frame <= self.frames[-1] or stamp <= self.stamps[-1]):
            raise ValueError("Camera audit requires increasing frame IDs and timestamps.")
        if self.nodes and set(images) != set(self.nodes):
            raise ValueError("Camera audit actor set changed.")
        for node, cameras in images.items():
            if set(cameras) != set(CAMERAS):
                raise ValueError("All six camera contents are required for a pass-by audit.")
            records = self.nodes.setdefault(node, {})
            for name in CAMERAS:
                data = cameras[name]
                digest = hashlib.sha256(data if isinstance(data, bytes) else data.tobytes()).hexdigest()
                record = records.setdefault(name, {"unique": set(), "previous": None,
                    "changed_transitions": 0, "max_identical_run": 0, "identical_run": 0,
                    "first_sha256": digest})
                changed = record["previous"] is not None and record["previous"] != digest
                record["changed_transitions"] += int(changed)
                record["identical_run"] = 1 if record["previous"] != digest else record["identical_run"] + 1
                record["max_identical_run"] = max(record["max_identical_run"], record["identical_run"])
                record["unique"].add(digest)
                record["previous"] = digest
        self.frames.append(int(frame))
        self.stamps.append(int(stamp))

    def result(self):
        nodes = {}
        for node, cameras in self.nodes.items():
            records = {name: {"unique_images": len(r["unique"]),
                "changed_transitions": r["changed_transitions"],
                "max_identical_run": r["max_identical_run"],
                "first_sha256": r["first_sha256"], "last_sha256": r["previous"]}
                for name, r in cameras.items()}
            nodes[node] = {"cameras": records,
                "all_six_identical_for_entire_clip": all(r["unique_images"] <= 1 for r in records.values())}
        return {"frames_checked": len(self.frames), "frame_ids": self.frames,
            "capture_sim_ns": self.stamps, "nodes": nodes,
            "scope": "Content variation gate for controlled moving-ego videos, not a universal static-scene freeze detector"}

    def require_passby_content(self):
        result = self.result()
        if len(self.frames) < 10:
            raise ValueError("Camera content audit needs at least 10 pass-by samples.")
        static = [node for node, row in result["nodes"].items()
                  if row["all_six_identical_for_entire_clip"]]
        if static:
            raise ValueError("Pass-by camera content never refreshed in any of six directions: " + ", ".join(static))
        return result


def audit_saved_report(report):
    import cv2
    capture = json.loads((report / "capture.json").read_text())
    audit = CameraContentAudit()
    for folder in sorted(report.glob("round_*")):
        metadata = json.loads((folder / "video_frame.json").read_text())
        images = {}
        for node in capture["actors"]:
            images[node] = {}
            for name in CAMERAS:
                pixels = cv2.imread(str(folder / f"{node}_rgb_{name}.png"))
                if pixels is None:
                    raise ValueError(f"Missing original camera pixels: {folder}/{node}/{name}")
                images[node][name] = pixels
        audit.observe(metadata["frame_id"], metadata["capture_sim_ns"], images)
    result = audit.result()
    try:
        audit.require_passby_content()
        result.update(content_gate_passed=True, error=None)
    except ValueError as exc:
        result.update(content_gate_passed=False, error=str(exc))
    return {"scenario": capture["scenario"], "report": str(report), **result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Use a new content audit path.")
    rows = [audit_saved_report(report) for report in args.reports]
    args.output.write_text(json.dumps({"rows": rows}, indent=2))
    for row in rows:
        print(f"{row['scenario']}: content_gate_passed={row['content_gate_passed']} {row['error'] or ''}", flush=True)
    return 0 if all(row["content_gate_passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
