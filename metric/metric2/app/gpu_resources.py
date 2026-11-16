"""Explicit GPU binding and compatible per-GPU ownership locks."""
from contextlib import contextmanager, ExitStack
import fcntl
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET


def binding():
    gpu = os.environ.get("DAMOS_GPU_UUID")
    adapter = os.environ.get("DAMOS_RENDER_ADAPTER")
    if gpu is None and adapter is None:
        return None
    if not isinstance(gpu, str) or not re.fullmatch(r"GPU-[a-fA-F0-9-]{36}", gpu):
        raise ValueError("DAMOS_GPU_UUID must identify one physical NVIDIA GPU")
    if adapter not in ("0", "1") or os.environ.get("CUDA_VISIBLE_DEVICES") != gpu:
        raise ValueError("Explicit renderer adapter and matching CUDA GPU UUID required")
    return {"gpu_uuid": gpu, "renderer_adapter": int(adapter),
            "cuda_visible_devices": gpu, "cpu_affinity": sorted(os.sched_getaffinity(0))}


def renderer_args():
    current = binding()
    return [f"-graphicsadapter={current['renderer_adapter']}"] if current else []


def check_observations(records, expected):
    # Vulkan enumeration can leave a small graphics-only context on another card.
    active = {r["gpu_uuid"] for r in records
              if "C" in r["type"] or float(r["memory"].split()[0]) > 32}
    if active and active != {expected}:
        raise RuntimeError(f"GPU work assignment mismatch: expected {expected}, observed {sorted(active)}")
    return {"active_gpu_uuids": sorted(active),
            "secondary_graphics_contexts": [r for r in records if r["gpu_uuid"] != expected]}


@contextmanager
def worker_slot(root):
    root = Path(root)
    current = binding()
    with ExitStack() as stack:
        legacy = stack.enter_context((root / ".live_metric2_worker.lock").open("a"))
        fcntl.flock(legacy, (fcntl.LOCK_SH if current else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        if current:
            local = stack.enter_context((root / (".live_gpu_" + current["gpu_uuid"] + ".lock")).open("a"))
            fcntl.flock(local, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield current


def verify_engine_gpu(pid):
    current = binding()
    if current is None:
        return None
    xml = subprocess.check_output(["nvidia-smi", "-q", "-x"], text=True, timeout=20)
    records = [{"gpu_uuid": gpu.findtext("uuid"), "pid": pid, "type": p.findtext("type"),
                "memory": p.findtext("used_memory")}
               for gpu in ET.fromstring(xml).findall("gpu") for p in gpu.findall("processes/process_info")
               if p.findtext("pid") == str(pid)]
    checked = check_observations(records, current["gpu_uuid"])
    if checked["active_gpu_uuids"] != [current["gpu_uuid"]]:
        raise RuntimeError("CARLA renderer GPU work not observable")
    return {**current, "engine_pid": pid, "physical_gpu_verified": True, "observed_devices": records, **checked}
