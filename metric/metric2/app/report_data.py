"""Read benchmark artifacts; never infer missing timestamps or stage values."""
from datetime import datetime, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import statistics
import uuid
from zoneinfo import ZoneInfo

from damos_paths import project_root

STAGES = [
    ('preprocess', '입력 전처리'), ('inference', '인지·BEV 생성'),
    ('protect', '증명·패킷 생성'), ('send', 'TCP 송신'),
    ('receive_wait', '수신 대기'), ('verify', '수신 검증·복원'),
    ('fusion', 'BEV 정렬·병합'),
]
KST = ZoneInfo('Asia/Seoul')


def relocated_path(value, project=None):
    """Resolve old report paths without rewriting signed evaluation records."""
    path = Path(value)
    project = Path(project) if project is not None else project_root(Path(__file__).resolve().parent.parent)
    metric_prefixes = [
        ('/home/vv/DAMOS_live/metric2_20261106', 'metric/metric2'),
        ('/home/vv/DAMOS_live/metric3_20261106', 'metric/metric3'),
        ('/home/vv/vv/DAMOS/live/metric2_20261106', 'metric/metric2'),
        ('/home/vv/vv/DAMOS/live/metric3_20261106', 'metric/metric3'),
        (str(project / 'metric2'), 'metric/metric2'),
        (str(project / 'metric3'), 'metric/metric3'),
        (str(project / 'metric/metric2'), 'metric/metric2'),
        (str(project / 'metric/metric3'), 'metric/metric3'),
        ('/home/vv/DAMOS', 'metric/metric2'),
    ]
    prefixes = list(metric_prefixes)
    for prefix, _ in metric_prefixes:
        prefixes.extend([(prefix + '/runtime', 'shared/runtime'),
                         (prefix + '/.venv', 'shared/venvs/simulation')])
    prefixes.extend([
        ('/home/vv/DAMOS_bench/metric23_replay_20261105/.venv', 'shared/venvs/base'),
        ('/home/vv/vv/DAMOS/bench/metric23_replay_20261105/.venv', 'shared/venvs/base'),
        (str(project / 'shared/.venv'), 'shared/venvs/base'),
        ('/home/vv/DAMOS_bench/metric23_replay_20261105', 'shared'),
        ('/home/vv/vv/DAMOS/bench/metric23_replay_20261105', 'shared'),
    ])
    for prefix, target in sorted(prefixes, key=lambda item: len(item[0]), reverse=True):
        if path.is_relative_to(prefix):
            return project / target / path.relative_to(prefix)
    return path


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


@lru_cache(maxsize=4096)
def cached_json(name, mtime, size):
    return json.loads(Path(name).read_text())


def artifact(path, default=None):
    try:
        stat = path.stat()
        return cached_json(str(path), stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        return default


def instant(ns):
    if ns is None:
        return None
    value = int(ns)
    dt = datetime.fromtimestamp(value // 1_000_000_000, KST)
    return dt.strftime('%Y-%m-%d %H:%M:%S.') + f'{value % 1_000_000_000 // 1_000_000:03d}'


def expected_count(protocol):
    return protocol['sessions'] * protocol['repeats_per_input_per_session'] * len(protocol['scenarios'])


def is_full(protocol):
    return (protocol.get('sessions') == 3 and protocol.get('repeats_per_input_per_session') == 5
            and len(protocol.get('scenarios', [])) == 9
            and set(protocol['scenarios']) == {f'S{i}' for i in range(1, 10)})


class Reports:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.reports = self.root / 'reports'
        self.jobs = self.root / '.metric2_ui_jobs'

    def job(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', name):
            raise ValueError('Invalid report ID')
        return self.jobs / name

    def folder(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', name):
            raise ValueError('Invalid report ID')
        folder = (self.reports / name).resolve()
        if folder.parent != self.reports.resolve() or (folder.exists() and not folder.is_dir()):
            raise ValueError('Unknown report')
        protocol = artifact(folder / 'protocol.json')
        launch = artifact(self.job(name) / 'launch.json')
        if (protocol or (launch or {}).get('protocol', {})).get('source') != 'live_carla':
            raise ValueError('Not an actual live-CARLA run')
        if not launch and not (protocol and protocol.get('ego_only') is True
                               and protocol.get('budget_seconds') == 18.0):
            raise ValueError('Not a metric2 run')
        return folder

    def history(self):
        rows = []
        names = {p.name for p in self.reports.iterdir() if p.is_dir() and not p.is_symlink()}
        if self.jobs.exists():
            names.update(p.name for p in self.jobs.iterdir() if p.is_dir() and not p.is_symlink())
        for name in names:
            path = self.reports / name
            if path.is_symlink():
                continue
            protocol = artifact(path / 'protocol.json')
            launch = artifact(self.job(name) / 'launch.json')
            if not launch and not (protocol and protocol.get('ego_only') is True
                                   and protocol.get('budget_seconds') == 18.0):
                continue
            proto = protocol or launch['protocol']
            if proto.get('source') != 'live_carla':
                continue
            summary = artifact(path / 'summary.json')
            job = artifact(self.job(name) / 'state.json', {})
            rows.append({'id': path.name, 'created_utc': proto.get('created_utc', ''),
                         'expected': expected_count(proto), 'full_scope': is_full(proto),
                         'active': job.get('status') in ('running', 'stopping'),
                         'complete': bool(summary and summary.get('all_requested_processing_checks_pass'))})
        return sorted(rows, key=lambda r: r['created_utc'], reverse=True)

    def default(self):
        history = self.history()
        candidates = ([r for r in history if r['active']] or
                      [r for r in history if r['full_scope'] and r['complete']] or history)
        return candidates[0]['id'] if candidates else None

    def target(self, folder, row):
        path = relocated_path(row['output'], project_root(self.root)).resolve()
        if not path.is_relative_to(folder) or not path.is_dir():
            raise ValueError('Round artifact outside selected report')
        return path

    def snapshot(self, name):
        folder = self.folder(name)
        launch = artifact(self.job(name) / 'launch.json', {})
        protocol = artifact(folder / 'protocol.json') or launch.get('protocol', {})
        expected = expected_count(protocol)
        summary = artifact(folder / 'summary.json')
        state = artifact(self.job(name) / 'state.json', {})
        live = artifact(self.job(name) / 'live.json', {})
        raw = []
        path = folder / 'rounds.jsonl'
        if path.exists():
            content = path.read_text()
            for line in content.splitlines(keepends=True):
                if line.endswith('\n'):
                    raw.append(json.loads(line))
        rows, identities = [], set()
        for index, source in enumerate(raw):
            target = self.target(folder, source)
            audit = artifact(target / 'audit.json')
            clock = artifact(target / 'ui_clock.json', {})
            live_input = artifact(target / 'live_input.json', {})
            scene_capture = artifact(target / 'scene_capture.json', {})
            if (source.get('input_source') != 'live_carla' or live_input.get('frames', [None])[-1] != source['frame_id']
                    or scene_capture.get('frame_id') != source['frame_id']
                    or scene_capture.get('capture_sim_ns') != source.get('capture_sim_ns')):
                raise ValueError('Live input and scene evidence do not match measured frame')
            ego_clock = clock.get('peers', {}).get('ego', {})
            start, end = int(source['started_ns']), int(source['ended_ns'])
            if end <= start or abs((end - start) / 1e6 - source['elapsed_ms']) > 1e-6:
                raise ValueError('Invalid measured clock interval')
            key = (source['session'], source['scenario'], source['repeat'])
            if key in identities:
                raise ValueError('Duplicate benchmark round')
            identities.add(key)
            audited = bool(audit and audit.get('proofs_reverified') == 2)
            good = source['functional_pass'] and source['speed_pass'] and audited
            rows.append({'index': index, 'evaluation_number': index + 1, 'session': source['session'], 'scenario': source['scenario'],
                         'repeat': source['repeat'], 'frame_id': source['frame_id'],
                         'input_id': source['input_id'], 'started_ns': str(start), 'ended_ns': str(end),
                         'input_source': source.get('input_source'), 'capture_sim_ns': source.get('capture_sim_ns'),
                         'elapsed_ms': source['elapsed_ms'], 'elapsed_seconds': source['elapsed_ms'] / 1000,
                         'stages_ms': source.get('stages_ms', {}),
                         'start_kst': instant(ego_clock.get('started_utc_ns')),
                         'end_kst': instant(ego_clock.get('ended_utc_ns')),
                         'start_utc_ns': ego_clock.get('started_utc_ns'),
                         'end_utc_ns': ego_clock.get('ended_utc_ns'),
                         'clock_source': 'wall_clock_anchor' if ego_clock else 'not_recorded',
                         'functional': source['functional_pass'], 'within_limit': source['speed_pass'],
                         'audit_pass': audited, 'passed': good,
                         'status': 'passed' if good else ('auditing' if source['functional_pass'] and not audit else 'failed'),
                         'problems': source.get('problems', [])})
        valid = [r['elapsed_ms'] for r in rows if r['functional']]
        failures = (summary or {}).get('failures', [])
        complete = bool(summary and len(rows) == expected and not failures
                        and summary.get('all_requested_processing_checks_pass')
                        and all(r['passed'] for r in rows))
        stop_requested = artifact(self.job(name) / 'stop_requested.json')
        if state.get('status') == 'cancelled':
            status = state['status']
        elif complete:
            status = 'complete'
        elif state.get('status') == 'running' and stop_requested:
            status = 'stopping'
        elif summary is not None or state.get('status') == 'failed':
            status = 'failed'
        elif state.get('status') == 'running':
            try:
                os.kill(state['pid'], 0)
                status = 'stopping' if stop_requested else 'running'
            except (KeyError, ProcessLookupError):
                status = 'failed'
        else:
            status = 'preparing'
        return {'id': name, 'status': status, 'source': 'live_carla', 'full_scope': is_full(protocol),
                'criterion_pass': is_full(protocol) and complete,
                'expected': expected, 'processed': len(rows),
                'normal': sum(r['functional'] for r in rows),
                'within_limit': sum(r['within_limit'] for r in rows),
                'audited': sum(r['audit_pass'] for r in rows), 'budget_seconds': 18,
                'max_seconds': max(valid) / 1000 if valid else None,
                'mean_seconds': statistics.mean(valid) / 1000 if valid else None,
                'min_seconds': min(valid) / 1000 if valid else None,
                'worst_index': max(rows, key=lambda r: r['elapsed_ms'])['index'] if rows else None,
                'clock_coverage': sum(r['start_kst'] is not None for r in rows),
                'created_kst': (datetime.fromisoformat(protocol['created_utc']).astimezone(KST).isoformat()
                                if protocol.get('created_utc') else None),
                'phase': live.get('phase', 'preparing'), 'current': live.get('current'),
                'failures': failures, 'job_error': state.get('error'),
                'post_scene_diagnostics': (summary or {}).get('post_scene_diagnostics', []),
                'network': protocol.get('network', 'single host, independent processes, shared GPU, TCP loopback'),
                'rows': rows, 'protocol': {'sessions': protocol['sessions'],
                    'repeats': protocol['repeats_per_input_per_session'], 'scenarios': protocol['scenarios'],
                    'checkpoint_sha256': protocol.get('checkpoint_sha256'),
                    'excluded': protocol.get('excluded', []), 'counter': 'perf_counter_ns'},
                'server_root': str(folder)}

    def detail(self, name, index):
        snap = self.snapshot(name)
        if not 0 <= index < len(snap['rows']):
            raise ValueError('Round is not available')
        folder = self.folder(name)
        source = json.loads((folder / 'rounds.jsonl').read_text().splitlines()[index])
        target = self.target(folder, source)
        peers = artifact(target / 'peers.json')
        if peers is None:
            peers = {p.name.removesuffix('_timing.json'): artifact(p)
                     for p in target.glob('*_timing.json')}
        if 'ego' not in peers:
            raise ValueError('Round peer artifacts incomplete')
        nodes = []
        for node, row in sorted(peers.items(), key=lambda p: p[0] != 'ego'):
            stages = row.get('stages_ms', {})
            residual = row['elapsed_ms'] - sum(stages[k] for k, _ in STAGES if k in stages)
            proof = row.get('proof', {}).get('prove_total_ms')
            verification = [r['verification'].get('verify_total_ms')
                            for r in row.get('received_bevs', {}).values()]
            nodes.append({'node_id': node, 'role': 'ego' if node == 'ego' else 'observer',
                          'actor_id': row['actor_id'], 'type_id': row.get('input', {}).get('actor_type_id'),
                          'frame_id': row['frame_id'], 'stages_ms': stages,
                          'residual_ms': residual if residual >= -1e-5 else None,
                          'elapsed_ms': row['elapsed_ms'], 'proof_ms': proof,
                          'proof_verify_ms': sum(verification) if verification and all(v is not None for v in verification) else None,
                          'verified': len(row.get('verified_peer_ids', [])),
                          'expected_peers': len(row['peer_ids']),
                          'backend': row.get('proof', {}).get('result_class'),
                          'own_observed_cells': row.get('own_observed_cells'),
                          'fused_observed_cells': row.get('fused_observed_cells'),
                          'peer_added_cells': row.get('peer_added_cells'),
                          'camera_count': row.get('input', {}).get('camera_count'),
                          'lidar_count': row.get('input', {}).get('lidar_count')})
        return {'run': name, 'row': snap['rows'][index], 'nodes': nodes,
                'stages': [{'key': key, 'label': label} for key, label in STAGES]}

    def asset(self, name, index, kind):
        detail = self.detail(name, index)
        observer = next(n['node_id'] for n in detail['nodes'] if n['role'] == 'observer')
        names = {'ego_camera': 'ego_front.png', 'observer_camera': observer + '_front.png',
                 'ego_bev': 'ego_own.png', 'observer_bev': observer + '_own.png',
                 'merged_bev': 'ego_obstacle_presence.png', 'scene_input': 'scene_rgb.png'}
        if kind not in names:
            raise ValueError('Unknown image')
        folder = self.folder(name)
        row = json.loads((folder / 'rounds.jsonl').read_text().splitlines()[index])
        target = self.target(folder, row)
        path = (target / names[kind]).resolve()
        if path.parent != target or not path.is_file():
            raise FileNotFoundError('Image not yet saved')
        return path
