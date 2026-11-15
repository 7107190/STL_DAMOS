"""Loopback-only metric2 viewer and constrained benchmark launcher."""
import argparse
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
import platform
import pwd
from pathlib import Path
import secrets
import shlex
import signal
import subprocess
import threading
import uuid
from urllib.parse import parse_qs, urlparse

from report_data import Reports, STAGES, atomic_json, artifact, read
from source_reference import reference
from evaluation_report import render_report, summary_csv
from damos_paths import python_executable

HERE = Path(__file__).resolve().parent


def now():
    return datetime.now(timezone.utc).isoformat()


def alive_job(job):
    pid = job.get('pid')
    if not isinstance(pid, int):
        return False
    try:
        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        return any(b'/run_live.py' in p for p in command)
    except FileNotFoundError:
        return False


class Console:
    def __init__(self, root, offscreen=False):
        self.data = Reports(root)
        self.offscreen = offscreen
        self.launched = {}
        self.data.jobs.mkdir(exist_ok=True)
        self.token = secrets.token_urlsafe(32)

    def observe(self, process, job):
        code = process.wait()
        state = read(job / 'state.json', {})
        if state.get('status') in ('complete', 'failed', 'cancelled'):
            return
        cancelled = (job / 'stop_requested.json').exists() or code in (130, -signal.SIGINT)
        summary = read(self.data.reports / job.name / 'summary.json', {})
        complete = code == 0 and summary.get('all_requested_processing_checks_pass')
        state.update(status='complete' if complete else ('cancelled' if cancelled else 'failed'),
                     returncode=code, ended_utc=now(),
                     error=None if complete else ('User requested stop' if cancelled else f'Worker exited with code {code}'))
        atomic_json(job / 'state.json', state)

    def start(self, mode, scenario):
        if mode not in ('full', 'single', 'five', 'three') or scenario not in {f'S{i}' for i in range(1, 10)}:
            raise ValueError('Unsupported trial selection')
        with (self.data.jobs / '.launch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for path in self.data.jobs.iterdir():
                state = read(path / 'state.json', {}) if path.is_dir() else {}
                if state.get('status') in ('running', 'stopping') and alive_job(state):
                    raise RuntimeError('A metric2 UI trial is already running')
            name = 'live_metric2_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8]
            job = self.data.job(name)
            job.mkdir()
            root = self.data.root
            protocol = {'created_utc': now(), 'ego_only': True, 'budget_seconds': 18.0,
                        'source': 'live_carla', 'carla_port': 2820,
                        'sessions': 3 if mode == 'full' else 1,
                        'repeats_per_input_per_session': 3 if mode == 'three' else 5 if mode in ('full', 'five') else 1,
                        'scenarios': [f'S{i}' for i in range(1, 10)] if mode == 'full' else [scenario],
                        'network': 'single host, independent processes, shared GPU, TCP loopback'}
            atomic_json(job / 'launch.json', {'protocol': protocol, 'mode': mode,
                        'wrapper_sha256': hashlib.sha256((HERE / 'run_live.py').read_bytes()).hexdigest(),
                        'started_from': 'live_metric2_console'})
            command = ('source ' + shlex.quote(str(root / 'env.sh')) + '; exec '
                        + shlex.join([str(python_executable(root)), '-u', str(HERE / 'run_live.py'),
                                      '--root', str(root), '--run', name] + (['--offscreen'] if self.offscreen else [])))
            with (job / 'stdout.log').open('x') as log:
                process = subprocess.Popen(['bash', '-c', command], cwd=root, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            atomic_json(job / 'state.json', {'status': 'running', 'pid': process.pid, 'started_utc': now()})
            self.launched[name] = process
            threading.Thread(target=self.observe, args=(process, job), daemon=True).start()
            return {'run': name, 'pid': process.pid}

    def close_trials(self):
        for name, process in self.launched.items():
            if process.poll() is None:
                try:
                    self.stop(name)
                except (OSError, ValueError):
                    pass
        for process in self.launched.values():
            if process.poll() is None:
                process.wait(timeout=60)

    def stop(self, name):
        self.data.folder(name)
        job = self.data.job(name)
        state = read(job / 'state.json', {})
        if state.get('status') not in ('running', 'stopping') or not alive_job(state):
            raise ValueError('No active UI-owned process for this trial')
        command = Path(f'/proc/{state["pid"]}/cmdline').read_bytes().split(b'\0')
        if name.encode() not in command:
            raise ValueError('Process does not belong to the selected trial')
        atomic_json(job / 'stop_requested.json', {'requested_utc': now()})
        try:
            os.kill(state['pid'], signal.SIGINT)
        except ProcessLookupError:
            return {'run': name, 'status': 'already_finished'}
        return {'run': name, 'status': 'stopping'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if self.command == 'POST' or (len(args) > 1 and str(args[1]).startswith(('4', '5'))):
            print(self.log_date_time_string() + ' ' + fmt % args, flush=True)

    @property
    def app(self):
        return self.server.console

    def safe_host(self):
        return self.headers.get('Host', '') in {f'127.0.0.1:{self.server.server_port}',
                                                 f'localhost:{self.server.server_port}'}

    def send(self, value, status=200, content_type='application/json; charset=utf-8', filename=None):
        body = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith('application/json') else value
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.safe_host():
            return self.send({'error': 'Invalid Host'}, 403)
        request = urlparse(self.path)
        query = parse_qs(request.query)
        one = lambda key, default=None: query.get(key, [default])[0]
        try:
            if request.path == '/api/config':
                return self.send({'token': self.app.token, 'default_run': self.app.data.default(),
                                  'history': self.app.data.history(), 'source': 'live_carla',
                                  'hostname': platform.node(), 'user': pwd.getpwuid(os.getuid()).pw_name})
            if request.path == '/health':
                return self.send({'app': 'DAMOS-LIVE-METRIC2', 'root': str(self.app.data.root),
                                  'source': 'live_carla', 'offscreen': self.app.offscreen})
            if request.path == '/api/source':
                run = one('run')
                if run:
                    self.app.data.folder(run)
                return self.send(reference(self.app.data.root, 2, run, one('section')))
            if request.path in ('/api/live', '/api/live-image'):
                name = one('run')
                self.app.data.folder(name)
                job = self.app.data.job(name)
                meta = artifact(job / 'scene_live.json', {})
                state = artifact(job / 'state.json', {})
                if request.path == '/api/live':
                    return self.send({**meta, 'run': name, 'active': state.get('status') == 'running' and alive_job(state),
                                      'error': artifact(job / 'scene_preview_error.json')})
                if meta.get('source') != 'live_carla_rgb_sensor':
                    raise FileNotFoundError('Live CARLA RGB has not arrived')
                frame = int(one('frame', str(meta['frame_id'])))
                if frame < 0:
                    raise ValueError('Invalid live frame')
                return self.send((job / f'scene_live_F{frame:010d}.jpg').read_bytes(), content_type='image/jpeg')
            if request.path == '/api/history':
                return self.send(self.app.data.history())
            if request.path == '/api/state':
                return self.send(self.app.data.snapshot(one('run') or self.app.data.default()))
            if request.path == '/report':
                snap = self.app.data.snapshot(one('run'))
                page = render_report(2, snap, self.app.data.root, one('view', 'summary'))
                return self.send(page.encode(), content_type='text/html; charset=utf-8')
            if request.path == '/api/summary-csv':
                snap = self.app.data.snapshot(one('run'))
                return self.send(summary_csv(2, snap), content_type='text/csv; charset=utf-8', filename=snap['id'] + '_summary.csv')
            if request.path == '/api/round':
                return self.send(self.app.data.detail(one('run'), int(one('index', '0'))))
            if request.path == '/api/asset':
                path = self.app.data.asset(one('run'), int(one('index', '0')), one('kind'))
                return self.send(path.read_bytes(), content_type='image/png')
            if request.path == '/api/download':
                snap = self.app.data.snapshot(one('run'))
                if one('format') == 'json':
                    return self.send(json.dumps(snap, ensure_ascii=False, indent=2).encode(),
                                     content_type='application/octet-stream', filename=snap['id'] + '.json')
                stream = io.StringIO()
                columns = ['evaluation_number', 'session', 'scenario', 'repeat', 'frame_id', 'input_id', 'start_kst', 'end_kst',
                           'start_utc_ns', 'end_utc_ns', 'clock_source',
                           'started_ns', 'ended_ns', 'elapsed_ms', 'elapsed_seconds', 'input_source', 'capture_sim_ns', 'status', 'audit_pass']
                columns += [role + '_' + key + '_ms' for role in ('ego', 'observer') for key, _ in STAGES]
                writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
                writer.writeheader()
                for row in snap['rows']:
                    result = dict(row)
                    detail = self.app.data.detail(snap['id'], row['index'])
                    for node in detail['nodes']:
                        for key, _ in STAGES:
                            result[node['role'] + '_' + key + '_ms'] = node['stages_ms'].get(key)
                    writer.writerow(result)
                return self.send(stream.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8',
                                 filename=snap['id'] + '.csv')
            relative = 'index.html' if request.path == '/' else request.path.removeprefix('/')
            path = (HERE / 'web' / relative).resolve()
            web = (HERE / 'web').resolve()
            if not path.is_relative_to(web) or not path.is_file():
                raise FileNotFoundError('Not found')
            return self.send(path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
        except FileNotFoundError as exc:
            self.send({'error': str(exc)}, 404)
        except (ValueError, KeyError, TypeError) as exc:
            self.send({'error': str(exc)}, 400)
        except Exception as exc:
            self.send({'error': type(exc).__name__ + ': ' + str(exc)}, 500)

    def do_POST(self):
        origin = self.headers.get('Origin')
        allowed = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        token = self.headers.get('X-CSRF-Token', '')
        if not self.safe_host() or origin not in allowed or not hmac.compare_digest(token, self.app.token):
            return self.send({'error': 'Invalid request origin or token'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError('Invalid body size')
            body = json.loads(self.rfile.read(length))
            if self.path == '/api/start':
                return self.send(self.app.start(body.get('mode'), body.get('scenario', 'S1')), 201)
            if self.path == '/api/stop':
                return self.send(self.app.stop(body.get('run')))
            return self.send({'error': 'Not found'}, 404)
        except RuntimeError as exc:
            self.send({'error': str(exc)}, 409)
        except (ValueError, TypeError, KeyError) as exc:
            self.send({'error': str(exc)}, 400)
        except Exception as exc:
            self.send({'error': type(exc).__name__ + ': ' + str(exc)}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8762)
    parser.add_argument('--offscreen', action='store_true', help='Automated validation only; physical-PC trial defaults to a CARLA window')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.console = Console(args.root.resolve(), args.offscreen)
    print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'pid': os.getpid()}), flush=True)
    def stop(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.console.close_trials()


if __name__ == '__main__':
    main()
