"""Shared weather selection and read-only CARLA weather/renderer verification."""
from __future__ import annotations

import json
import math
from pathlib import Path

WEATHER_CATALOG_PATH = Path(__file__).resolve().parents[1] / "_scenarios/weather_presets.json"
BASE_WEATHERS = json.loads(WEATHER_CATALOG_PATH.read_text())
BASE_WEATHER_NAMES = tuple(BASE_WEATHERS)
LEGACY_WEATHER_NAMES = ("ClearNoon", "HardRainNoon", "ClearNight")
WEATHER_FIELDS = ("cloudiness", "precipitation", "precipitation_deposits", "wind_intensity",
                  "sun_azimuth_angle", "sun_altitude_angle", "fog_density", "fog_distance",
                  "fog_falloff", "wetness", "scattering_intensity", "mie_scattering_scale",
                  "rayleigh_scattering_scale", "dust_storm")
WEATHER_TOLERANCE = .001


def scenic_weather_parameter(name):
    if name in BASE_WEATHER_NAMES:
        return "DAMOS_WEATHER_INDEX", str(BASE_WEATHER_NAMES.index(name))
    return "weather", name


def weather_values(weather):
    return {field: float(getattr(weather, field)) for field in WEATHER_FIELDS if hasattr(weather, field)}


def resolve_weather(name):
    import carla
    if name in BASE_WEATHERS:
        requested = dict(BASE_WEATHERS[name])
        parameters = carla.WeatherParameters(**requested)
        family = "scenic_base"
    elif name in LEGACY_WEATHER_NAMES:
        requested = name
        parameters = getattr(carla.WeatherParameters, name)
        family = "legacy_carla_preset"
    else:
        raise ValueError("Unknown resilience weather")
    return {"name": name, "family": family, "requested": requested,
            "expected_full": weather_values(parameters), "absolute_tolerance": WEATHER_TOLERANCE}


def weather_mismatches(actual, expected):
    bad = []
    for field, value in expected.items():
        measured = actual.get(field)
        if (measured is None or not math.isfinite(measured) or not math.isfinite(value)
                or abs(measured - value) > WEATHER_TOLERANCE):
            bad.append(field)
    return bad


def observe_weather(world, expected):
    observed = weather_values(world.get_weather())
    mismatches = weather_mismatches(observed, expected) if expected is not None else []
    return {"snapshot_frame": world.get_snapshot().frame, "actual": observed,
            "expected": expected, "mismatches": mismatches,
            "matches_plan": not mismatches if expected is not None else None,
            "read_only_no_tick": True}


def observe_renderer(port, expected_quality, proc_root=Path("/proc")):
    matches = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = [part.decode() for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
        except (OSError, UnicodeDecodeError):
            continue
        if (f"-carla-rpc-port={port}" in argv and
                any(Path(arg).name == "UE4Editor" for arg in argv)):
            flags = [arg.split("=", 1)[1] for arg in argv if arg.startswith("-quality-level=")]
            matches.append({"pid": int(entry.name), "argv": argv,
                            "quality": flags[0] if len(flags) == 1 else None})
    if len(matches) != 1:
        raise RuntimeError("Cannot uniquely verify local CARLA renderer process")
    return dict(matches[0], expected=expected_quality,
                matches_plan=matches[0]["quality"] == expected_quality,
                read_only_no_tick=True)
