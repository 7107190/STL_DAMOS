#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_REPORT_DIR = SOURCE_ROOT / "_DAMOS" / "reports"
EXPECTED_CAMERA_NAMES = (
    "cam_front",
    "cam_front_left",
    "cam_front_right",
    "cam_back",
    "cam_back_left",
    "cam_back_right",
)
EXPECTED_ROLES = {"humanoid", "deliverybot"}


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Explicit Scenic observer JSON report path.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="Directory containing Scenic observer JSON reports.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Select the latest JSON report for this CARLA RPC port.",
    )
    parser.add_argument(
        "--expected-cameras",
        type=int,
        default=6,
        help="Expected number of cameras attached to each observer.",
    )
    parser.add_argument(
        "--max-movement",
        type=float,
        default=0.25,
        help="Maximum expected movement in observer mode.",
    )
    parser.add_argument(
        "--no-camera-check",
        action="store_true",
        help="Skip observer camera attachment checks.",
    )


def find_latest_report(report_dir: Path, port: int | None) -> Path:
    pattern = "*.json" if port is None else f"*port{port}_*.json"
    reports = sorted(report_dir.glob(pattern), key=lambda path: path.stat().st_mtime)
    if not reports:
        detail = f" for port {port}" if port is not None else ""
        raise FileNotFoundError(f"No JSON reports found in {report_dir}{detail}.")
    return reports[-1]


def load_report(args: argparse.Namespace) -> tuple[Path, dict]:
    report_path = args.report or find_latest_report(args.report_dir, args.port)
    with report_path.open(encoding="utf-8") as report_file:
        return report_path, json.load(report_file)


def status_text(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def print_table(headers, rows) -> None:
    widths = [len(str(header)) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))

    line = " | ".join(str(header).ljust(widths[index]) for index, header in enumerate(headers))
    sep = "-+-".join("-" * width for width in widths)
    print(line)
    print(sep)
    for row in rows:
        print(" | ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row)))


def index_anchor_assignments(assignments):
    anchors = defaultdict(list)
    for assignment in assignments:
        anchors[assignment.get("anchor_index", "?")].append(assignment)
    return anchors


def index_camera_attachments(attachments):
    cameras_by_track = defaultdict(list)
    for attachment in attachments:
        cameras_by_track[attachment.get("track_label", "")].append(attachment)
    return cameras_by_track


def report_png_paths(report_path: Path) -> tuple[Path, Path]:
    stem = report_path.with_suffix("")
    return stem.with_suffix(".png"), stem.parent / f"{stem.name}_focus.png"


def validate_report(data: dict, args: argparse.Namespace) -> tuple[bool, list[str]]:
    failures = []
    mode = data.get("custom_walker_mode")
    if mode != "observer":
        failures.append(f"custom_walker_mode is {mode!r}, expected 'observer'.")

    max_distance = float(
        data.get("observer_requirements", {}).get("max_observer_anchor_distance", 22.0)
    )
    max_yaw = float(
        data.get("observer_requirements", {}).get("max_observer_facing_error_degrees", 35.0)
    )

    anchors = index_anchor_assignments(data.get("anchor_assignments", []))
    if not anchors:
        failures.append("No anchor assignments were recorded.")
    for anchor_index, assignments in sorted(anchors.items(), key=lambda item: str(item[0])):
        roles = {assignment.get("observer_role") for assignment in assignments}
        missing = EXPECTED_ROLES - roles
        if missing:
            failures.append(
                f"anchor {anchor_index} is missing observer roles: {', '.join(sorted(missing))}."
            )

    camera_specs = data.get("observer_camera_specs", [])
    spec_names = {spec.get("name") for spec in camera_specs}
    if not args.no_camera_check:
        missing_specs = set(EXPECTED_CAMERA_NAMES) - spec_names
        if missing_specs:
            failures.append(
                "Missing camera specs: " + ", ".join(sorted(missing_specs)) + "."
            )

    cameras_by_track = index_camera_attachments(data.get("observer_camera_attachments", []))
    movements = data.get("walker_movements", {})
    for metric in data.get("observer_metrics", []):
        label = metric.get("track_label", "")
        role = metric.get("observer_role", "<unknown>")
        if metric.get("status") != "ok":
            failures.append(f"{role} observer {label} status is {metric.get('status')!r}.")
        distance = float(metric.get("observer_to_anchor_distance", float("inf")))
        if distance > max_distance:
            failures.append(f"{role} observer {label} is too far from anchor: {distance:.3f}m.")
        yaw_error = float(metric.get("facing_error_degrees", float("inf")))
        if yaw_error > max_yaw:
            failures.append(f"{role} observer {label} yaw error is too high: {yaw_error:.3f}deg.")
        movement = float(movements.get(label, 0.0))
        if movement > args.max_movement:
            failures.append(f"{role} observer {label} moved {movement:.3f}m.")
        if not args.no_camera_check:
            attached_metric = int(metric.get("attached_sensor_count", -1))
            attached_count = len(cameras_by_track.get(label, []))
            if attached_metric != args.expected_cameras or attached_count != args.expected_cameras:
                failures.append(
                    f"{role} observer {label} camera count mismatch: "
                    f"metric={attached_metric}, attachments={attached_count}."
                )

    if not data.get("observer_metrics"):
        failures.append("No observer metrics were recorded.")

    return not failures, failures


def print_summary(report_path: Path, data: dict, args: argparse.Namespace) -> None:
    ok, failures = validate_report(data, args)
    png_path, focus_png_path = report_png_paths(report_path)
    camera_attachments = data.get("observer_camera_attachments", [])
    cameras_by_track = index_camera_attachments(camera_attachments)
    movements = data.get("walker_movements", {})

    print(f"Report: {report_path}")
    print(f"Mode: {data.get('custom_walker_mode')}")
    print(f"Map: {data.get('map')}")
    print(f"Scenario labels: {', '.join(data.get('scenario_labels', [])) or '<none>'}")
    print(f"Overall: {status_text(ok)}")
    print()

    anchor_rows = []
    for anchor_index, assignments in sorted(
        index_anchor_assignments(data.get("anchor_assignments", [])).items(),
        key=lambda item: str(item[0]),
    ):
        first = assignments[0]
        roles = ", ".join(sorted(str(item.get("observer_role")) for item in assignments))
        anchor_rows.append(
            (
                anchor_index,
                first.get("anchor_actor_id"),
                first.get("anchor_type_id"),
                first.get("anchor_label"),
                roles,
                status_text(EXPECTED_ROLES <= {item.get("observer_role") for item in assignments}),
            )
        )
    print("Anchor coverage")
    print_table(
        ("anchor", "actor_id", "type", "label", "roles", "status"),
        anchor_rows or [("-", "-", "-", "-", "-", "FAIL")],
    )
    print()

    observer_rows = []
    for metric in sorted(
        data.get("observer_metrics", []),
        key=lambda item: (str(item.get("anchor_index")), str(item.get("observer_role"))),
    ):
        label = metric.get("track_label", "")
        observer_ok = (
            metric.get("status") == "ok"
            and int(metric.get("attached_sensor_count", -1)) == args.expected_cameras
            and len(cameras_by_track.get(label, [])) == args.expected_cameras
            and float(movements.get(label, 0.0)) <= args.max_movement
        )
        observer_rows.append(
            (
                metric.get("anchor_index"),
                metric.get("observer_role"),
                metric.get("walker_actor_id"),
                metric.get("anchor_actor_id"),
                metric.get("observer_to_anchor_distance"),
                metric.get("facing_error_degrees"),
                movements.get(label, 0.0),
                len(cameras_by_track.get(label, [])),
                status_text(observer_ok),
            )
        )
    print("Observer checks")
    print_table(
        (
            "anchor",
            "role",
            "actor_id",
            "anchor_id",
            "dist_m",
            "yaw_deg",
            "moved_m",
            "cams",
            "status",
        ),
        observer_rows or [("-", "-", "-", "-", "-", "-", "-", "-", "FAIL")],
    )
    print()

    camera_names = [spec.get("name") for spec in data.get("observer_camera_specs", [])]
    print("Camera layout")
    print(f"Specs: {len(camera_names)} ({', '.join(camera_names) or '<none>'})")
    print(f"Attachments: {len(camera_attachments)}")
    print()

    print("Artifacts")
    artifact_rows = (
        ("trajectory_png", png_path, status_text(png_path.exists())),
        ("focus_png", focus_png_path, status_text(focus_png_path.exists())),
        ("json", report_path, status_text(report_path.exists())),
    )
    print_table(("artifact", "path", "status"), artifact_rows)

    if failures:
        print()
        print("Failures")
        for failure in failures:
            print(f"- {failure}")

    sys.exit(0 if ok else 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    add_args(parser)
    args = parser.parse_args()
    try:
        report_path, data = load_report(args)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Failed to load report: {exc}", file=sys.stderr)
        sys.exit(2)
    print_summary(report_path, data, args)


if __name__ == "__main__":
    main()
