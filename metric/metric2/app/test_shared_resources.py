from pathlib import Path
import tempfile
import unittest
import subprocess

from damos_paths import project_root, python_executable, runtime_root, worker_lock_root
from source_reference import catalog


class SharedResources(unittest.TestCase):
    def test_both_metrics_use_one_runtime_and_python(self):
        for name in ('metric2', 'metric3'):
            root = Path('/project') / name
            self.assertEqual(runtime_root(root), Path('/project/shared/runtime'))
            self.assertEqual(python_executable(root), Path('/project/shared/venvs/simulation/bin/python'))

    def test_worker_locks_are_shared(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = worker_lock_root(root / 'metric2')
            second = worker_lock_root(root / 'metric3')
            self.assertEqual(first, second)
            self.assertTrue(first.is_dir())

    def test_nested_metrics_use_project_resources(self):
        for name in ('metric2', 'metric3'):
            root = Path('/project/metric') / name
            self.assertEqual(project_root(root), Path('/project'))
            self.assertEqual(runtime_root(root), Path('/project/shared/runtime'))
            self.assertEqual(python_executable(root), Path('/project/shared/venvs/simulation/bin/python'))

    def test_nested_worker_locks_are_shared_with_legacy_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(worker_lock_root(root / 'metric/metric2'),
                             worker_lock_root(root / 'metric/metric3'))
            self.assertEqual(worker_lock_root(root / 'metric/metric2'),
                             worker_lock_root(root / 'metric2'))

    def test_environment_uses_internal_cache_and_nested_metric_imports(self):
        root = project_root(Path(__file__).resolve().parent.parent)
        output = subprocess.check_output([
            'bash', '-c', 'source "$1/shared/env.sh"; printf "%s\\n%s\\n" "$CARLA_CACHE_DIR" "$PYTHONPATH"',
            'test', str(root)], text=True).splitlines()
        self.assertEqual(output[0], str(root / 'shared/cache/carla'))
        paths = output[1].split(':')
        self.assertIn(str(root / 'metric/metric2/app'), paths)
        self.assertNotIn(str(root / 'metric2/app'), paths)

    def test_metric3_calculation_reference_uses_shared_runtime(self):
        frame = catalog(Path('/project/metric3'), 3)[0]
        self.assertEqual(frame['path'], Path('/project/shared/runtime/Carla-0.9.16-source/_DAMOS/scripts/bev_anchor_evaluation.py'))

    def test_nested_metric3_calculation_reference(self):
        frame = catalog(Path('/project/metric/metric3'), 3)[0]
        self.assertEqual(frame['path'], Path('/project/shared/runtime/Carla-0.9.16-source/_DAMOS/scripts/bev_anchor_evaluation.py'))
