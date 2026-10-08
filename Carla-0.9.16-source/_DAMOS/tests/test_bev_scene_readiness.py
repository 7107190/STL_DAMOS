from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scenic_custom_walker_injector as injector


class Clock:
    value = 0.
    def now(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


class SceneReadinessTests(unittest.TestCase):
    def scene(self, lines):
        clock = Clock()
        frames = iter(range(100, 200))
        world = SimpleNamespace(get_snapshot=lambda: SimpleNamespace(frame=next(frames)))
        ego = SimpleNamespace(id=24)
        process = SimpleNamespace(poll=lambda: None)
        with patch.object(injector, "safe_get_world", return_value=world), \
             patch.object(injector, "select_latest_ego_actor", return_value=ego), \
             patch.object(injector, "collect_scenic_anchor_candidates", return_value=[
                 {"actor_id":25,"scenario_instance":"S8.1"}]) as collect, \
             patch.object(injector.time, "monotonic", side_effect=clock.now), \
             patch.object(injector.time, "sleep", side_effect=clock.sleep):
            try:
                return injector.wait_for_live_scene(None, process, 2., 1, output_lines=lines)
            finally:
                if not injector.extract_scenario_labels(lines):
                    collect.assert_not_called()

    def test_partial_spawns_without_setup_marker_never_ready(self):
        with self.assertRaises(RuntimeError):
            self.scene(["Beginning simulation of BaseSetup()..."])

    def test_finished_setup_and_stable_actors_ready(self):
        world, ego = self.scene(["S8#1: crowd 21"])
        self.assertEqual(ego.id,24)
        self.assertIsNotNone(world)

    def test_live_output_drain_keeps_single_copy(self):
        lines = []
        with patch.object(injector, "scenic_root_for", return_value=Path.cwd()), \
             patch.object(injector, "selected_abnormal_scenario_summary", return_value="test"):
            process = injector.launch_scenic_process(SimpleNamespace(verbose=False),
                [sys.executable,"-c","print('S8#1: crowd 21', flush=True)"],
                logger=lambda _:None, capture_lines=lines)
        process.wait(timeout=5)
        injector.collect_process_output(process,lines)
        injector.collect_process_output(process,lines)
        self.assertEqual(lines,["S8#1: crowd 21"])


if __name__=="__main__":
    unittest.main()
