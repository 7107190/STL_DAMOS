"""Read-only, allowlisted references to the actual metric calculation sources."""
import ast
import hashlib
import json
from pathlib import Path
import re

from report_data import relocated_path
from damos_paths import project_root, runtime_root


def catalog(root, metric):
    root = Path(root).resolve()
    if metric == 2:
        return [
            dict(id="timer_start", label="계측 시작", path=root / "app/core_overlay/bev_mesh.py", function="BEVMesh.exchange"),
            dict(id="timer_end", label="계측 종료·시간 차이", path=root / "app/core_overlay/bev_mesh.py", function="_worker",
                 start="ended = time.perf_counter_ns()", end='row["pipeline_budget_pass"] ='),
            dict(id="scope", label="135회 시험 범위", path=root / "app/report_data.py", function="is_full"),
            dict(id="aggregate", label="최대값·전체 판정", path=root / "app/report_data.py", function="Reports.snapshot",
                 start="valid = [r[", end="'max_seconds': max(valid)"),
        ]
    if metric == 3:
        scripts = runtime_root(root) / "Carla-0.9.16-source/_DAMOS/scripts"
        return [
            dict(id="frame_detection", label="시점별 탐지", path=scripts / "bev_anchor_evaluation.py", function="region_presence"),
            dict(id="target_reference", label="목표 위치·예측 대조", path=scripts / "bev_accuracy_batch.py", function="score_round"),
            dict(id="duration", label="연속 탐지시간", path=root / "app/m3_rules.py", function="duration"),
            dict(id="anchor_success", label="앵커별 성공 판정", path=root / "app/m3_stream.py", function="install.run",
                 start="span = duration(rows)", end='"producer_ended_wall_ns": capture.get('),
            dict(id="aggregate", label="성공률·전체 판정", path=root / "app/m3_rules.py", function="rollup"),
        ]
    raise ValueError("Unsupported metric")


def snippet(text, spec):
    node = ast.parse(text)
    for name in spec["function"].split("."):
        matches = [child for child in node.body if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                   and child.name == name]
        if len(matches) != 1:
            raise ValueError("Source function is missing or ambiguous")
        node = matches[0]
    lines = text.splitlines()
    first, last = node.lineno, node.end_lineno
    for key in ("start", "end"):
        if key not in spec:
            continue
        found = [i + 1 for i in range(node.lineno - 1, node.end_lineno) if spec[key] in lines[i]]
        if len(found) != 1:
            raise ValueError("Source excerpt marker is missing or ambiguous")
        if key == "start":
            first = found[0]
        else:
            last = found[0]
    if first > last:
        raise ValueError("Invalid source excerpt")
    return first, last, "\n".join(lines[first - 1:last])


def recorded_digest(root, metric, run, path):
    if run is None:
        return None
    if not isinstance(run, str) or not re.fullmatch(rf"live_metric{metric}_[0-9TZ_]+[a-f0-9]{{8}}", run):
        raise ValueError("Invalid trial reference")
    report = Path(root) / "reports" / run
    name, key = ("environment.json", "measured_code_sha256") if metric == 2 else ("protocol.json", "source_hashes")
    try:
        recorded = json.loads((report / name).read_text()).get(key, {})
    except FileNotFoundError:
        return None
    resolved = path.resolve()
    candidates = {digest for filename, digest in recorded.items()
                  if relocated_path(filename, project_root(root)).resolve() == resolved}
    if len(candidates) > 1:
        raise ValueError("Conflicting recorded source hashes")
    return next(iter(candidates), None)


def reference(root, metric, run=None, section=None):
    entries = catalog(root, metric)
    section = section or entries[0]["id"]
    matches = [entry for entry in entries if entry["id"] == section]
    if len(matches) != 1:
        raise ValueError("Unknown calculation source")
    spec = matches[0]
    raw = spec["path"].read_bytes()
    first, last, code = snippet(raw.decode("utf-8"), spec)
    current = hashlib.sha256(raw).hexdigest()
    expected = recorded_digest(root, metric, run, spec["path"])
    return {"metric": metric, "run": run, "section": section, "label": spec["label"],
            "sections": [{"id": row["id"], "label": row["label"]} for row in entries],
            "file": str(spec["path"]), "function": spec["function"], "start_line": first, "end_line": last,
            "code": code, "sha256": current, "recorded_sha256": expected,
            "matches_selected_run": current == expected if expected is not None else None,
            "source": "current_server_file", "read_only": True}
