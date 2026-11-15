"""Loopback-only live CARLA metric3 controller and evidence viewer."""
import argparse
import csv
from datetime import datetime, timezone
import fcntl
import hmac
from http.server import ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import platform
import secrets
import shlex
import signal
import subprocess
import threading
import uuid
from urllib.parse import parse_qs, urlparse

from m3_data import Reports
from m3_rules import safe_run
from report_data import atomic_json, read
from source_reference import reference
from server import Handler as BaseHandler
from evaluation_report import render_report, summary_csv
from damos_paths import python_executable

HERE = Path(__file__).resolve().parent


def alive(state):
    try:
        return any(p.endswith(b"/m3_run.py") for p in Path(f"/proc/{state['pid']}/cmdline").read_bytes().split(b"\0"))
    except (OSError, KeyError):
        return False


class Console:
    def __init__(self, root, offscreen):
        self.data, self.offscreen = Reports(root), offscreen
        self.token, self.launched = secrets.token_urlsafe(32), {}

    def start(self, mode="full", scenario="S1", resume=None):
        if mode not in ("full", "single", "sweep", "five", "three") or scenario not in {f"S{i}" for i in range(1,10)}:
            raise ValueError("Unsupported live test selection")
        with (self.data.jobs / ".launch.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for path in self.data.jobs.glob("live_metric3_*"):
                state = read(path / "state.json", {})
                if state.get("status") in ("running", "stopping") and alive(state):
                    raise RuntimeError("A live metric3 test is already running")
            if resume:
                name = safe_run(resume)
                state = read(self.data.jobs / name / "state.json", {})
                if state.get("status") not in ("failed", "cancelled"):
                    raise ValueError("Only an interrupted/error trial can resume")
                self.data.protocol(name)
                job = self.data.jobs / name
            else:
                name = "live_metric3_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
                job = self.data.jobs / name
                job.mkdir()
                atomic_json(job / "launch.json", {"mode": mode, "scenario": scenario, "source": "live_carla_sensor_stream"})
            root = self.data.root
            command = "source " + shlex.quote(str(root / "env.sh")) + "; exec " + shlex.join([
                str(python_executable(root)), "-u", str(HERE / "m3_run.py"), "--root", str(root), "--run", name]
                + (["--offscreen"] if self.offscreen else []))
            with (job / "stdout.log").open("a") as log:
                process = subprocess.Popen(["bash", "-c", command], cwd=root, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            atomic_json(job / "state.json", {"status": "running", "pid": process.pid})
            self.launched[name] = process
            def observe():
                code = process.wait()
                state = read(job / "state.json", {})
                if state.get("status") not in ("complete", "failed", "cancelled"):
                    state.update(status="failed", error=f"Controller exited {code}; see {job / 'stdout.log'}")
                    atomic_json(job / "state.json", state)
            threading.Thread(target=observe, daemon=True).start()
            return {"run": name, "pid": process.pid}

    def stop(self, name):
        safe_run(name)
        state = read(self.data.jobs / name / "state.json", {})
        if not alive(state):
            raise ValueError("Selected live test is not running")
        if name.encode() not in Path(f"/proc/{state['pid']}/cmdline").read_bytes().split(b"\0"):
            raise ValueError("Controller PID belongs to a different trial")
        os.kill(state["pid"], signal.SIGINT)
        return {"run": name, "stopping": True}

    def close_trials(self):
        for name, process in self.launched.items():
            if process.poll() is None:
                self.stop(name)
                process.wait(timeout=120)


class Handler(BaseHandler):
    def do_GET(self):
        if not self.safe_host():
            return self.send({"error": "Invalid host"}, 403)
        request = urlparse(self.path)
        query = parse_qs(request.query)
        one = lambda key, default=None: query.get(key, [default])[0]
        try:
            if request.path == "/health":
                return self.send({"app": "DAMOS-LIVE-METRIC3", "root": str(self.app.data.root), "offscreen": self.app.offscreen})
            if request.path == "/api/source":
                run = one("run")
                if run:
                    self.app.data.folder(run)
                return self.send(reference(self.app.data.root, 3, run, one("section")))
            if request.path == "/api/config":
                history = self.app.data.history()
                return self.send({"token": self.app.token, "hostname": platform.node(), "history": history,
                                  "default_run": history[0]["id"] if history else None})
            if request.path == "/api/history":
                return self.send(self.app.data.history())
            if request.path == "/api/state":
                return self.send(self.app.data.snapshot(one("run")))
            if request.path == "/report":
                snap = self.app.data.snapshot(one("run"))
                page = render_report(3, snap, self.app.data.root, one("view", "summary"))
                return self.send(page.encode(), content_type="text/html; charset=utf-8")
            if request.path == "/api/summary-csv":
                snap = self.app.data.snapshot(one("run"))
                return self.send(summary_csv(3, snap), content_type="text/csv; charset=utf-8", filename=snap["id"] + "_summary.csv")
            if request.path == "/api/detail":
                index = one("index")
                return self.send(self.app.data.detail(one("run"), one("case"), int(index) if index is not None else None))
            if request.path == "/api/asset":
                path = self.app.data.asset(one("run"), one("case"), int(one("index")), one("kind"))
                return self.send(path.read_bytes(), content_type="image/png")
            if request.path in ("/api/live", "/api/live-image"):
                run = safe_run(one("run"))
                job = self.app.data.jobs / run
                meta = read(job / "scene_live.json", {})
                state = read(job / "state.json", {})
                if request.path == "/api/live":
                    return self.send({**meta, "active": state.get("status") == "running" and alive(state)})
                if meta.get("source") != "live_carla_rgb_sensor":
                    raise FileNotFoundError("Current CARLA camera not ready")
                frame = int(one("frame"))
                if frame < 0:
                    raise ValueError("Invalid frame")
                return self.send((job / f"scene_live_F{frame:010d}.jpg").read_bytes(), content_type="image/jpeg")
            if request.path == "/api/download":
                state = self.app.data.snapshot(one("run"))
                if one("format") == "json":
                    return self.send(json.dumps(state, ensure_ascii=False, indent=2).encode(), content_type="application/octet-stream", filename=state["id"]+".json")
                fields = ["evaluation_number", "case_id", "scenario", "scene_seed", "weather", "status", "successful", "anchor_index",
                          "samples", "fused_longest_span_seconds", "ego_longest_span_seconds", "proofs_reverified", "attempt", "error"]
                stream = io.StringIO()
                writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(state["cases"])
                return self.send(stream.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8", filename=state["id"]+".csv")
            relative = "index.html" if request.path == "/" else request.path.removeprefix("/")
            web = HERE / "web"
            if relative in ("common.css", "lucide.min.js", "source-reference.js", "source-reference.css"):
                base = HERE.parent.parent / "metric2/app/web"
                path = base / {"common.css": "style.css", "lucide.min.js": "vendor/lucide.min.js",
                               "source-reference.js": "source-reference.js", "source-reference.css": "source-reference.css"}[relative]
            else:
                path = (web / relative).resolve()
                if not path.is_relative_to(web) or not path.is_file():
                    raise FileNotFoundError("Not found")
            return self.send(path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
        except FileNotFoundError as exc:
            self.send({"error": str(exc)}, 404)
        except (ValueError, KeyError, TypeError) as exc:
            self.send({"error": str(exc)}, 400)
        except Exception as exc:
            self.send({"error": type(exc).__name__ + ": " + str(exc)}, 500)

    def do_POST(self):
        expected = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if not self.safe_host() or self.headers.get("Origin") not in expected or not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), self.app.token):
            return self.send({"error": "Invalid origin or token"}, 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(size))
            if self.path == "/api/start":
                return self.send(self.app.start(body.get("mode", "full"), body.get("scenario", "S1")), 201)
            if self.path == "/api/resume":
                return self.send(self.app.start(resume=body["run"]), 201)
            if self.path == "/api/stop":
                return self.send(self.app.stop(body["run"]))
            raise FileNotFoundError("Unknown endpoint")
        except RuntimeError as exc:
            self.send({"error": str(exc)}, 409)
        except (ValueError, KeyError) as exc:
            self.send({"error": str(exc)}, 400)
        except Exception as exc:
            self.send({"error": str(exc)}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--offscreen", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 8763), Handler)
    server.console = Console(args.root.resolve(), args.offscreen)
    def interrupt(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)
    print(json.dumps({"pid": os.getpid(), "url": "http://127.0.0.1:8763/"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.console.close_trials()


if __name__ == "__main__":
    main()
