"""Physical-PC entry point: start local viewer, open browser, retain visible log."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen, Request


def healthy(root):
    try:
        with urlopen("http://127.0.0.1:8762/health", timeout=1) as response:
            data = json.load(response)
        return data.get("app") == "DAMOS-LIVE-METRIC2" and data.get("root") == str(root) and data.get("offscreen") is False
    except (OSError, ValueError, URLError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true", help="Start a new actual 135-round CARLA trial immediately")
    mode.add_argument("--five", action="store_true", help="Start one cumulative five-round live trial on S1")
    mode.add_argument("--three", action="store_true", help="Start one cumulative three-round live trial on S1")
    mode.add_argument("--scenario", choices=[f"S{i}" for i in range(1, 10)], help="Start one actual live round for the selected scenario")
    args = parser.parse_args()
    root = args.root.resolve()
    ready = root / "runtime_ready.json"
    if not ready.exists() or not json.loads(ready.read_text()).get("verified"):
        raise SystemExit("CARLA 실구동 환경 설치·검증이 아직 완료되지 않았습니다. 시험 시작 불가.")
    def open_trial():
        url = "http://127.0.0.1:8762"
        if args.full or args.five or args.three or args.scenario:
            with urlopen(url + "/api/config", timeout=5) as response:
                config = json.load(response)
            request = Request(url + "/api/start", data=json.dumps({"mode": "full" if args.full else "five" if args.five else "three" if args.three else "single",
                "scenario": args.scenario or "S1"}).encode(),
                headers={"Origin": url, "Content-Type": "application/json", "X-CSRF-Token": config["token"]})
            with urlopen(request, timeout=30) as response:
                run = json.load(response)["run"]
            print("새 CARLA 실구동 시험 시작: " + run, flush=True)
            url += "/?run=" + run
        subprocess.run(["xdg-open", url], check=True)
    (root / "logs").mkdir(exist_ok=True)
    with (root / ".viewer_start.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if healthy(root):
            open_trial()
            return 0
        process = subprocess.Popen([sys.executable, "-u", str(root / "app/server.py"), "--root", str(root), "--port", "8762"])
        for _ in range(100):
            if healthy(root):
                break
            if process.poll() is not None:
                raise SystemExit("시험 화면 서버 시작 실패. 위 오류 확인.")
            time.sleep(.1)
        else:
            process.terminate()
            process.wait(timeout=10)
            raise SystemExit("시험 화면 서버 응답 시간 초과")
        try:
            open_trial()
            fcntl.flock(lock, fcntl.LOCK_UN)
            print("시험 화면: http://127.0.0.1:8762/", flush=True)
            print("새 시험 진행 중. 이 터미널 유지." if args.full or args.five or args.three or args.scenario else
                  "화면에서 전체 135회·누적 3회·누적 5회·대표 1회 선택 후 실행. 이 터미널 유지.", flush=True)
            return process.wait()
        except KeyboardInterrupt:
            return 130
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=90)


if __name__ == "__main__":
    raise SystemExit(main())
