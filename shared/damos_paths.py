"""Common resource locations, independent of either evaluation's directory."""
from pathlib import Path


def shared_root(metric_root):
    return Path(metric_root).resolve().parent / 'shared'


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
