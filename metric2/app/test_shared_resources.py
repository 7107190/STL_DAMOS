from pathlib import Path
import tempfile
import unittest

from damos_paths import python_executable, runtime_root, worker_lock_root
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

    def test_metric3_calculation_reference_uses_shared_runtime(self):
        frame = catalog(Path('/project/metric3'), 3)[0]
        self.assertEqual(frame['path'], Path('/project/shared/runtime/Carla-0.9.16-source/_DAMOS/scripts/bev_anchor_evaluation.py'))
