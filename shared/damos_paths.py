"""Common resource locations, independent of either evaluation's directory."""
from pathlib import Path


def project_root(metric_root):
    parent = Path(metric_root).resolve().parent
    return parent.parent if parent.name == 'metric' else parent


def shared_root(metric_root):
    return project_root(metric_root) / 'shared'


def runtime_root(metric_root):
    return shared_root(metric_root) / 'runtime'


def python_env(metric_root):
    return shared_root(metric_root) / 'venvs/simulation'


def python_executable(metric_root):
    return python_env(metric_root) / 'bin/python'


def worker_lock_root(metric_root):
    folder = shared_root(metric_root) / 'locks'
    folder.mkdir(parents=True, exist_ok=True)
    return folder
