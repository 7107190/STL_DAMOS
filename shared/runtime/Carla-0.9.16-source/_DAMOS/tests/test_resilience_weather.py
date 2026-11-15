"""Shared-weather fixtures; actual rendering and model results require CARLA trials."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from resilience_weather import (BASE_WEATHERS, BASE_WEATHER_NAMES, WEATHER_CATALOG_PATH,
    observe_weather, observe_renderer, resolve_weather, scenic_weather_parameter, weather_mismatches)


class WeatherTests(unittest.TestCase):
    def test_catalog_preserves_all_four_original_definitions(self):
        expected = {
            "low_sun": {"cloudiness": 30, "sun_azimuth_angle": 270, "sun_altitude_angle": 5,
                        "scattering_intensity": 60, "wetness": 20},
            "heavy_rain": {"cloudiness": 95, "precipitation": 95, "precipitation_deposits": 80,
                           "wind_intensity": 30, "wetness": 90, "fog_density": 20, "sun_altitude_angle": 10},
            "dense_fog": {"cloudiness": 80, "fog_density": 100, "fog_distance": 10,
                          "fog_falloff": 1.0, "wetness": 10, "sun_altitude_angle": 15},
            "wet_night": {"cloudiness": 60, "sun_altitude_angle": -20, "wetness": 95,
                          "fog_density": 10, "scattering_intensity": 40}}
        self.assertEqual(BASE_WEATHERS, expected)
        self.assertEqual(BASE_WEATHER_NAMES, tuple(expected))

    def test_shared_file_is_used_by_scenic(self):
        source = (WEATHER_CATALOG_PATH.parent / "S_.scenic").read_text()
        self.assertIn("localPath('weather_presets.json')", source)
        self.assertIn("Uniform(*DAMOS_WEATHER_PRESETS.values())", source)
        self.assertEqual(json.loads(WEATHER_CATALOG_PATH.read_text()), BASE_WEATHERS)

    def test_each_base_cli_name_selects_its_original_index(self):
        for index, name in enumerate(BASE_WEATHER_NAMES):
            self.assertEqual(scenic_weather_parameter(name), ("DAMOS_WEATHER_INDEX", str(index)))

    def test_legacy_weather_name_is_not_reinterpreted(self):
        self.assertEqual(scenic_weather_parameter("ClearNight"), ("weather", "ClearNight"))

    def test_integration_cli_routes_base_names_to_scenic_index(self):
        import argparse
        from scenic_custom_walker_injector import (add_integration_args, build_scenic_command,
                                                   config_from_args, scenic_param_values)
        parser = argparse.ArgumentParser()
        add_integration_args(parser)
        for index, name in enumerate(BASE_WEATHER_NAMES):
            config = config_from_args(parser.parse_args(["--weather", name]))
            params = scenic_param_values(build_scenic_command(config))
            self.assertEqual(params["DAMOS_WEATHER_INDEX"], str(index))
            self.assertNotIn("weather", params)

    def test_float_serialization_tolerance_and_mismatch(self):
        self.assertEqual(weather_mismatches({"wetness": 95.0001}, {"wetness": 95}), [])
        self.assertEqual(weather_mismatches({"wetness": 0}, {"wetness": 95}), ["wetness"])

    def test_missing_or_nonfinite_weather_fails_closed(self):
        for actual in ({}, {"fog_density": float("nan")}, {"fog_density": float("inf")}):
            self.assertEqual(weather_mismatches(actual, {"fog_density": 100}), ["fog_density"])

    def test_weather_observation_never_sets_weather_or_ticks(self):
        world = SimpleNamespace(get_weather=lambda: SimpleNamespace(fog_density=100),
                                get_snapshot=lambda: SimpleNamespace(frame=88),
                                tick=Mock(), set_weather=Mock())
        result = observe_weather(world, {"fog_density": 100})
        self.assertTrue(result["matches_plan"])
        self.assertEqual(result["snapshot_frame"], 88)
        world.tick.assert_not_called()
        world.set_weather.assert_not_called()

    def test_defaults_are_recorded_without_overwriting_explicit_parameters(self):
        def parameters(**kwargs):
            return SimpleNamespace(**dict({"wetness": 0, "fog_density": 60}, **kwargs))
        with patch.dict(sys.modules, {"carla": SimpleNamespace(WeatherParameters=parameters)}):
            profile = resolve_weather("low_sun")
        self.assertEqual(profile["family"], "scenic_base")
        self.assertEqual(profile["expected_full"]["fog_density"], 60)
        self.assertEqual(profile["expected_full"]["wetness"], 20)

    def test_unknown_profile_rejected(self):
        with patch.dict(sys.modules, {"carla": SimpleNamespace()}):
            with self.assertRaises(ValueError):
                resolve_weather("unknown")

    def test_renderer_quality_is_read_from_actual_process_argv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = root / '123'
            entry.mkdir()
            raw = b'/engine/UE4Editor\0project\0-carla-rpc-port=2690\0-quality-level=Epic\0'
            (entry / 'cmdline').write_bytes(raw)
            result = observe_renderer(2690, 'Epic', root)
            self.assertTrue(result['matches_plan'])
            self.assertEqual(result['quality'], 'Epic')
            self.assertEqual((entry / 'cmdline').read_bytes(), raw)
            self.assertFalse(observe_renderer(2690, 'Low', root)['matches_plan'])

    def test_missing_or_ambiguous_renderer_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(RuntimeError):
                observe_renderer(2690, 'Epic', root)
            for name in ('1', '2'):
                (root / name).mkdir()
                (root / name / 'cmdline').write_bytes(
                    b'/engine/UE4Editor\0-carla-rpc-port=2690\0-quality-level=Epic\0')
            with self.assertRaises(RuntimeError):
                observe_renderer(2690, 'Epic', root)

    def test_unspecified_renderer_quality_is_not_assumed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '1').mkdir()
            (root / '1' / 'cmdline').write_bytes(b'/engine/UE4Editor\0-carla-rpc-port=2690\0')
            self.assertFalse(observe_renderer(2690, 'Epic', root)['matches_plan'])

    def test_shell_wrapper_syntax(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/run_scenic_custom_walkers_town10hd.sh'
        self.assertEqual(subprocess.run(['bash', '-n', str(script)], capture_output=True).returncode, 0)

    def test_invalid_quality_rejected_before_server_start(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/run_scenic_custom_walkers_town10hd.sh'
        result = subprocess.run(['bash', str(script), '--quality-level', 'invalid'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--quality-level must be Low or Epic', result.stderr)

    def test_resilience_uses_epic_while_general_wrapper_keeps_low(self):
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        self.assertIn('choices=("Low", "Epic"), default="Epic"',
                      (scripts / 'run_carla_resilience.py').read_text())
        self.assertIn('QUALITY_LEVEL="Low"', (scripts / 'run_scenic_custom_walkers_town10hd.sh').read_text())


if __name__ == "__main__":
    unittest.main()
