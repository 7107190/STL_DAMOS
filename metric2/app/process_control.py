"""Identity-checked cleanup restricted to subprocesses recorded by this trial."""
import os
from pathlib import Path
import signal
import time

from report_data import read


def identity(pid, command=None):
    stat = Path(f"/proc/{pid}/stat").read_text()
    fields = stat.rsplit(")", 1)[1].split()
    return {"pid": pid, "start_ticks": fields[19], "state": fields[0],
            "command": command or [p.decode() for p in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if p]}


def terminate_record(record):
    if not record or not isinstance(record.get("pid"), int) or not record.get("start_ticks"):
        return
    pid = record["pid"]
    try:
        current = identity(pid)
        if current["start_ticks"] != record["start_ticks"]:
            return
        if current['state'] == 'Z':
            return
        owns_group = os.getpgid(pid) == pid
        send = (lambda sig: os.killpg(pid, sig)) if owns_group else (lambda sig: os.kill(pid, sig))
        send(signal.SIGTERM)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                status = identity(pid)
                if status["start_ticks"] != record["start_ticks"] or status['state'] == 'Z':
                    return
            except FileNotFoundError:
                return
            time.sleep(.1)
        send(signal.SIGKILL)
    except (ProcessLookupError, FileNotFoundError):
        return


def cleanup_scene(folder):
    for name in ("scenic_process.json", "carla_process.json"):
        record = read(folder / name)
        if record:
            terminate_record(record)
