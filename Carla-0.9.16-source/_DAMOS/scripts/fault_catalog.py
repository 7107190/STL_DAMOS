"""Canonical fault types shared by capture reports and CARLA pipeline trials."""
from dataclasses import asdict, dataclass
import json


@dataclass(frozen=True)
class Fault:
    fault_id: str
    key: str
    label: str
    report_key: str | None = None
    aliases: tuple[str, ...] = ()
    input_fault: str | None = None
    wire_fault: str | None = None
    expected_state: str | None = None
    compound: bool = False


FAULTS = (
    Fault("F01", "front_blackout", "Camera blackout", report_key="camera_blackout",
          input_fault="front_blackout", expected_state="DEGRADED_COOPERATIVE"),
    Fault("F02", "camera_degradation", "Camera degradation", report_key="camera_degradation",
          input_fault="camera_degradation", expected_state="DEGRADED_COOPERATIVE"),
    Fault("F03", "camera_misalignment", "Camera misalignment", report_key="camera_misalignment",
          input_fault="camera_misalignment", expected_state="DEGRADED_COOPERATIVE"),
    Fault("F04", "lidar_noise", "LiDAR noise", report_key="lidar_noise",
          input_fault="lidar_noise", expected_state="DEGRADED_COOPERATIVE"),
    Fault("F05", "lidar_dropout", "LiDAR partial dropout", report_key="lidar_dropout",
          input_fault="lidar_dropout", expected_state="DEGRADED_COOPERATIVE"),
    Fault("F06", "lidar_outlier", "LiDAR outliers", report_key="lidar_outlier",
          input_fault="lidar_outlier", expected_state="DEGRADED_COOPERATIVE"),
    Fault("F07", "lidar_missing", "LiDAR complete loss", input_fault="lidar_missing",
          expected_state="DEGRADED_COOPERATIVE"),
    Fault("F08", "sensor_delay", "Sensor input delay", report_key="sensor_delay",
          input_fault="sensor_delay", expected_state="DEGRADED_COOPERATIVE"),
    Fault("F09", "output_freeze", "Held / stale output", report_key="output_freeze",
          aliases=("module_stop", "stale_perception_output"), input_fault="output_freeze",
          expected_state="DEGRADED_COOPERATIVE"),
    Fault("F10", "link_drop", "Observer link loss", wire_fault="drop", expected_state="SAFE_STOP"),
    Fault("F11", "packet_tamper", "Packet body tampering", wire_fault="tamper", expected_state="SAFE_STOP"),
    Fault("F12", "packet_replay", "Old packet replay", wire_fault="replay", expected_state="SAFE_STOP"),
    Fault("F13", "invalid_proof", "Invalid ZKP proof", wire_fault="bad_proof", expected_state="SAFE_STOP"),
    Fault("F14", "rgb_lidar_failure", "RGB and LiDAR loss", input_fault="rgb_lidar_failure",
          expected_state="DEGRADED_COOPERATIVE", compound=True),
    Fault("F15", "compound_link_sensor", "RGB / LiDAR / link loss", input_fault="rgb_lidar_failure",
          wire_fault="drop", expected_state="SAFE_STOP", compound=True),
)

REPORT_FAULTS = tuple(f for f in FAULTS if f.report_key is not None)
PIPELINE_CASE_NAMES = tuple(f.key for f in FAULTS if f.expected_state is not None)


def resolve_fault(name):
    if not isinstance(name, str):
        raise KeyError(name)
    for fault in FAULTS:
        if name in (fault.fault_id, fault.key, fault.report_key, *fault.aliases):
            return fault
    raise KeyError(name)


def pipeline_cases():
    cases = {}
    for fault in FAULTS:
        if fault.expected_state is None:
            continue
        injection = {}
        if fault.input_fault:
            injection["ego"] = {"input": fault.input_fault}
        if fault.wire_fault:
            injection["observer"] = {"wire": fault.wire_fault}
        cases[fault.key] = (injection, fault.expected_state)
    return cases


def catalog_counts():
    return {"total": len(FAULTS), "single": sum(not f.compound for f in FAULTS),
            "compound": sum(f.compound for f in FAULTS), "pipeline_cases": len(PIPELINE_CASE_NAMES),
            "capture_only": sum(f.expected_state is None for f in FAULTS),
            "report_faults": len(REPORT_FAULTS)}


if __name__ == "__main__":
    print(json.dumps({"schema": "DAMOS_FAULT_CATALOG_V1", "counts": catalog_counts(),
                      "faults": [asdict(f) for f in FAULTS]}, indent=2))
