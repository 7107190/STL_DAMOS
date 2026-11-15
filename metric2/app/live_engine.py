"""Start and stop only the CARLA instance owned by the live metric2 trial."""
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

from report_data import atomic_json
from process_control import identity
from gpu_resources import renderer_args, verify_engine_gpu
from damos_paths import runtime_root


@contextmanager
def simulation(root, job, port, offscreen=False):
    import carla
    runtime = runtime_root(root)
    engine = runtime / "UnrealEngine_4.26/Engine/Binaries/Linux/UE4Editor"
    project = runtime / "Carla-0.9.16-source/Unreal/CarlaUE4/CarlaUE4.uproject"
    for path in (engine, project):
        if not path.is_file():
            raise FileNotFoundError("CARLA source runtime is not ready: " + str(path))
    if not offscreen and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise RuntimeError("Goosebumps PC의 그래픽 데스크톱 터미널에서 실행해야 합니다. 자동 검증 시에만 --offscreen 사용.")
    for candidate in (port, port + 1, port + 2, port + 6000):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError as exc:
                raise RuntimeError(f"전용 포트 {candidate} 사용 중. 기존 작업은 종료하지 않습니다.") from exc
    command = [str(engine), str(project), "/Game/Carla/Maps/Town10HD_Opt", "-game", "-nosound",
               "-quality-level=Low", f"-carla-rpc-port={port}", f"-carla-streaming-port={port + 1}",
               "-windowed", "-ResX=1280", "-ResY=720", "-NoSplash", "-unattended"]
    command += renderer_args()
    if offscreen:
        command.append("-RenderOffScreen")
    with (job / "carla.log").open("x") as log:
        process = subprocess.Popen(command, cwd=project.parent, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, stdin=subprocess.DEVNULL)
    atomic_json(job / "carla_process.json", {**identity(process.pid, command), "port": port,
                "offscreen": offscreen, "owner": "live_metric2_trial"})
    try:
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("CARLA exited before RPC readiness; inspect " + str(job / "carla.log"))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.5):
                    pass
                client = carla.Client("127.0.0.1", port)
                cache = root.parent / "shared/cache/carla"
                cache.mkdir(parents=True, exist_ok=True)
                client.set_files_base_folder(str(cache))
                client.set_timeout(5)
                version = client.get_server_version()
                world = client.get_world()
                if world.get_map().name.split("/")[-1] != "Town10HD_Opt":
                    raise RuntimeError("Live CARLA loaded the wrong map")
                expected = {"walker.pedestrian.damos_humanoid", "walker.pedestrian.damos_deliverybot",
                            "vehicle.ford.mustang", "sensor.camera.rgb", "sensor.lidar.ray_cast"}
                available = {bp.id for bp in world.get_blueprint_library()}
                if not expected <= available:
                    raise RuntimeError("CARLA runtime lacks DAMOS blueprints: " + ", ".join(sorted(expected - available)))
                atomic_json(job / "carla_ready.json", {"server_version": version, "client_version": client.get_client_version(),
                            "map": world.get_map().name, "blueprints_verified": sorted(expected),
                            "gpu_binding": verify_engine_gpu(process.pid)})
                break
            except (ConnectionError, TimeoutError, OSError):
                time.sleep(.5)
            except RuntimeError as exc:
                if "wrong map" in str(exc) or "lacks DAMOS" in str(exc) or "GPU" in str(exc):
                    raise
                time.sleep(.5)
        else:
            raise TimeoutError("CARLA RPC readiness exceeded 1800s")
        yield client
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            except ProcessLookupError:
                pass

