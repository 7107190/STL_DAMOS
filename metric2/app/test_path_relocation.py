import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from report_data import Reports, relocated_path
from source_reference import recorded_digest


class RelocationTests(unittest.TestCase):
    def test_old_home_prefixes(self):
        for name in ('metric2', 'metric3'):
            old = f'/home/vv/DAMOS_live/{name}_20261106/reports/run/sample'
            self.assertEqual(relocated_path(old, '/project'), Path(f'/project/{name}/reports/run/sample'))

    def test_previous_nested_layout(self):
        old = '/home/vv/vv/DAMOS/live/metric2_20261106/reports/run'
        self.assertEqual(relocated_path(old, '/project'), Path('/project/metric2/reports/run'))

    def test_shared_resources(self):
        old = '/home/vv/DAMOS_bench/metric23_replay_20261105/models/weights.pt'
        self.assertEqual(relocated_path(old, '/project'), Path('/project/shared/models/weights.pt'))

    def test_shared_runtime_precedes_generic_metric_prefix(self):
        for prefix in ('/home/vv/DAMOS_live/metric2_20261106',
                       '/home/vv/DAMOS_live/metric3_20261106',
                       '/home/vv/vv/DAMOS/live/metric2_20261106',
                       '/project/metric2', '/project/metric3', '/home/vv/DAMOS'):
            old = prefix + '/runtime/Carla-0.9.16-source/script.py'
            self.assertEqual(relocated_path(old, '/project'),
                             Path('/project/shared/runtime/Carla-0.9.16-source/script.py'))

    def test_shared_python_environments(self):
        self.assertEqual(relocated_path('/project/metric3/.venv/bin/python', '/project'),
                         Path('/project/shared/venvs/simulation/bin/python'))
        self.assertEqual(relocated_path('/home/vv/DAMOS_bench/metric23_replay_20261105/.venv/bin/python', '/project'),
                         Path('/project/shared/venvs/base/bin/python'))

    def test_unrelated_and_current_paths_unchanged(self):
        for path in ('/home/vv/DAMOS_other/file', '/project/metric2/app/file', 'relative/file'):
            self.assertEqual(relocated_path(path, '/project'), Path(path))

    def test_report_target_stays_inside_selected_trial(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'metric2'
            folder = root / 'reports/run'
            target = folder / 'round_001'
            target.mkdir(parents=True)
            reports = Reports(root)
            old = '/home/vv/DAMOS_live/metric2_20261106/reports/run/round_001'
            self.assertEqual(reports.target(folder.resolve(), {'output': old}), target.resolve())
            escape = '/home/vv/DAMOS_live/metric2_20261106/reports/run/../../outside'
            with self.assertRaises(ValueError):
                reports.target(folder.resolve(), {'output': escape})

    def test_original_source_digest_lookup_without_record_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'metric2'
            source = root / 'app/calculation.py'
            source.parent.mkdir(parents=True)
            source.write_text('value = 1\n')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            run = 'live_metric2_20261111T101806_367bf489'
            report = root / 'reports' / run
            report.mkdir(parents=True)
            old = '/home/vv/DAMOS_live/metric2_20261106/app/calculation.py'
            metadata = json.dumps({'measured_code_sha256': {old: digest}})
            (report / 'environment.json').write_text(metadata)
            self.assertEqual(recorded_digest(root, 2, run, source), digest)
            self.assertEqual((report / 'environment.json').read_text(), metadata)


if __name__ == '__main__':
    unittest.main()
