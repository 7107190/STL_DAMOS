import ast
from pathlib import Path
import unittest
from unittest.mock import Mock


HERE = Path(__file__).resolve().parent


class ScopedTrafficManagers(unittest.TestCase):
    def test_all_live_plugin_autopilot_calls_explicit_port(self):
        tree = ast.parse((HERE / "live_plugin.py").read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "set_autopilot"]
        self.assertTrue(calls)
        self.assertTrue(all(len(c.args) == 2 for c in calls))

    def test_static_freeze_uses_supplied_manager(self):
        path = HERE.parent.parent / "shared/runtime/Carla-0.9.16-source/_DAMOS/scripts/scenic_custom_walker_injector.py"
        tree = ast.parse(path.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "freeze_static_ego_actor")
        namespace = {"carla": Mock()}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
        for port in (8820, 8830):
            ego = Mock()
            namespace["freeze_static_ego_actor"](ego, port)
            ego.set_autopilot.assert_called_once_with(False, port)

    def test_static_freeze_callers_scope_to_config_or_argument(self):
        path = HERE.parent.parent / "shared/runtime/Carla-0.9.16-source/_DAMOS/scripts/scenic_custom_walker_injector.py"
        tree = ast.parse(path.read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "freeze_static_ego_actor"]
        self.assertTrue(calls)
        self.assertTrue(all(len(c.args) == 2 for c in calls))


if __name__ == "__main__":
    unittest.main()
