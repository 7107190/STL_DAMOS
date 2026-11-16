"""Anchor-level decisions; temporal continuity always uses sensor capture time."""
import re

MIN_SPAN_NS = 500_000_000
PERIOD_NS = 200_000_000


def select_cases(cases, mode, scenario):
    if mode == "single":
        return [next(c for c in cases if c["scenario"] == scenario)]
    if mode == "five":
        return cases[:5]
    if mode == "three":
        return cases[:3]
    if mode == "sweep":
        return cases[:9]
    if mode == "full":
        return cases
    raise ValueError("Unsupported live test selection")


def safe_run(value):
    if not isinstance(value, str) or not re.fullmatch(r"live_metric3_[0-9TZ_]+[a-f0-9]{8}", value):
        raise ValueError("Invalid live metric3 run")
    return value


def duration(rows, name="fused"):
    start = previous = None
    best = 0
    for row in rows:
        stamp = int(row["capture_sim_ns"])
        if previous and (stamp <= int(previous["capture_sim_ns"]) or row["frame_id"] <= previous["frame_id"]):
            raise ValueError("Duplicate/reversed sensor time")
        connected = previous is not None and (
            row["round_index"] == previous["round_index"] + 1
            and row["frame_id"] == previous["frame_id"] + 2
            and abs(stamp - int(previous["capture_sim_ns"]) - PERIOD_NS) <= 1000)
        if row["eligible"] is not True or row[name + "_detected"] is not True:
            start = None
        else:
            if start is None or not connected:
                start = stamp
            best = max(best, stamp - start)
        previous = row
    return best


def rollup(cases, results):
    rows = [{**case, **results.get(case["case_id"], {"status": "not_run", "successful": False}), "evaluation_number": number}
            for number, case in enumerate(cases, 1)]
    evaluated = sum(r["status"] in ("success", "failure") for r in rows)
    successful = sum(r["status"] == "success" for r in rows)
    errors = sum(r["status"] in ("execution_error", "not_evaluable") for r in rows)
    complete = evaluated == len(cases) and errors == 0
    return {"planned": len(cases), "evaluated": evaluated, "successful": successful,
            "detection_failures": sum(r["status"] == "failure" for r in rows), "execution_errors": errors,
            "success_rate_percent": successful / len(cases) * 100,
            "all_cases_complete": complete, "criterion_pass": complete and len(cases) == 100 and successful >= 70,
            "cases": rows}
