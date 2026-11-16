"""New CARLA world -> current sensors -> CUDA/Groth16/TCP -> timed Ego fusion."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import platform
import subprocess
import sys
import time

from report_data import atomic_json, read
from gpu_resources import binding, worker_slot
from damos_paths import runtime_root, shared_root, worker_lock_root


def utc():
    return datetime.now(timezone.utc).isoformat()


def terminate(signum, frame):
    raise KeyboardInterrupt()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    job = root / ".metric2_ui_jobs" / args.run
    launch = read(job / "launch.json")
    if not launch or not launch["protocol"].get("source") == "live_carla":
        raise ValueError("A live-only launch manifest is required")
    output = root / "reports" / args.run
    if output.resolve().parent != (root / "reports").resolve():
        raise ValueError("Invalid output ID")
    output.mkdir(exist_ok=False)
    proto = launch["protocol"]
    state = {"pid": os.getpid(), "status": "running", "started_utc": utc(), "source": "live_carla"}
    atomic_json(job / "state.json", state)
    signal.signal(signal.SIGTERM, terminate)
    source = runtime_root(root) / "Carla-0.9.16-source"
    sys.path[:0] = [str(Path(__file__).parent / "core_overlay"), str(source / "_DAMOS/scripts")]
    checkpoint = Path(os.environ["DAMOS_BEV_CHECKPOINT"])
    checkpoint_hash = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    proto.update(checkpoint_sha256=checkpoint_hash, gpu_binding=binding(),
                 scope="current CARLA raw sensors ready before IPC -> Ego obstacle-presence BEV fusion complete",
                 excluded=["sensor history wait", "model/key initialization and warmup", "diagnostic hashing", "report/image writes", "post-run proof audit", "path planning"],
                 render_load="CARLA renders concurrently on the same GPU throughout measurement; not subtracted",
                 input_mode="current_carla_sensor_callbacks", observer_policy="one nearest enrolled custom observer within 60m",
                 scenes="one new Scenic scene per scenario and session; every timed round acquires a fresh disjoint four-frame window",
                 accuracy_metric3="not evaluated", native_carla_window=not args.offscreen)
    atomic_json(output / "protocol.json", proto)
    def command(args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    code_files = [*Path(__file__).parent.glob("*.py"), *Path(__file__).parent.glob("core_overlay/*.py")]
    code_files += [shared_root(root) / name for name in ('damos_paths.py', 'env.sh')]
    code_files += [source / "_DAMOS/scripts" / name for name in
                   ("bev_sensor_rig.py", "bev_model_runtime.py", "bev_fusion.py", "bev_protocol.py", "scenic_custom_walker_injector.py")]
    atomic_json(output / "environment.json", {"hostname": platform.node(), "python": sys.version,
                "gpu": command(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv"]),
                "gpu_processes": command(["nvidia-smi", "--query-compute-apps=pid,process_name,used_gpu_memory", "--format=csv"]),
                "measured_code_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_files if p.is_file()}})
    expected = proto["sessions"] * proto["repeats_per_input_per_session"] * len(proto["scenarios"])
    context = {"root": root, "job": job, "run": args.run, "output": output, "rows": [],
               "expected": expected, "checkpoint_hash": checkpoint_hash, "tm_port": proto["carla_port"] + 6000}
    failures, cancelled, code = [], False, 1
    with worker_slot(worker_lock_root(root)):
        try:
            atomic_json(job / "live.json", {"phase": "carla_starting", "current": None, "source": "live_carla"})
            for session in range(1, proto["sessions"] + 1):
                for scenario in proto["scenarios"]:
                    context.update(session=session, scenario=scenario, scene=f"session{session}:{scenario}")
                    atomic_json(job / "live.json", {"phase": "scene_starting", "source": "live_carla",
                                "current": {"session": session, "scenario": scenario}})
                    atomic_json(job / "scene_live.json", {"source": "live_carla", "phase": "scene_starting", "scenario": scenario})
                    for old in job.glob("scene_live_F*.jpg"):
                        old.unlink(missing_ok=True)
                    scene_command = [sys.executable, '-u', str(Path(__file__).with_name('run_scene.py')),
                                     '--root', str(root), '--run', args.run, '--session', str(session), '--scenario', scenario]
                    if args.offscreen:
                        scene_command.append('--offscreen')
                    process = subprocess.Popen(scene_command, start_new_session=True)
                    try:
                        result_code = process.wait()
                    finally:
                        if process.poll() is None:
                            process.send_signal(signal.SIGINT)
                            process.wait(timeout=90)
                        from process_control import cleanup_scene
                        cleanup_scene(job / f'scene_{session:02d}_{scenario}')
                    if result_code != 0:
                        scene_result = read(job / f'scene_{session:02d}_{scenario}' / 'result.json', {})
                        raise RuntimeError(scene_result.get('error') or f'Scene process exited with code {result_code}')
                    context['rows'] = [json.loads(line) for line in (output / 'rounds.jsonl').read_text().splitlines()]
                    for row in context['rows']:
                        row['audit'] = read(Path(row['output']) / 'audit.json', {})
            all_pass = (len(context["rows"]) == expected and all(
                row["speed_pass"] and row.get("audit", {}).get("proofs_reverified") == 2 for row in context["rows"]))
            code = 0 if all_pass else 1
        except KeyboardInterrupt:
            cancelled, code = True, 130
        except BaseException as exc:
            failures.append({"scene": context.get("scene"), "error": type(exc).__name__ + ": " + str(exc)})
            print("[LIVE M2 ERROR] " + failures[-1]["error"], flush=True)
        finally:
            if (output / 'rounds.jsonl').exists():
                context['rows'] = [json.loads(line) for line in (output / 'rounds.jsonl').read_text().splitlines()]
                for row in context['rows']:
                    row['audit'] = read(Path(row['output']) / 'audit.json', {})
            rows = context["rows"]
            good = [row["elapsed_ms"] for row in rows if row["functional_pass"]]
            summary = {"source": "live_carla", "protocol": proto, "rounds_expected": expected,
                       "rounds_completed": len(rows), "functional_successes": sum(r["functional_pass"] for r in rows),
                       "within_18_seconds": sum(r["speed_pass"] for r in rows),
                       "all_requested_processing_checks_pass": code == 0 and not failures,
                       "formal_metric2_pass": None, "failures": failures,
                       "proofs_reverified": sum(r.get("audit", {}).get("proofs_reverified", 0) for r in rows),
                       "statistics": {"max_seconds": max(good) / 1000, "mean_seconds": sum(good) / len(good) / 1000} if good else None,
                       "completed_utc": utc()}
            summary['post_scene_diagnostics'] = [read(p) for p in sorted(job.glob('scene_*/result.json'))
                                                 if read(p).get('post_scene_diagnostic')]
            atomic_json(output / "summary.json", summary)
            state.update(status="cancelled" if cancelled else ("complete" if code == 0 else "failed"),
                         ended_utc=utc(), returncode=code, error=failures[-1]["error"] if failures else None)
            atomic_json(job / "state.json", state)
            atomic_json(job / "live.json", {"phase": state["status"], "current": None, "source": "live_carla"})
            print(json.dumps({"source": "live_carla", "run": args.run, "rounds": len(rows), "expected": expected,
                              "statistics": summary["statistics"], "complete": code == 0}, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
