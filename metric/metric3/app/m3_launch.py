"""Physical-PC entry point for a new live 100-anchor trial."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen

URL="http://127.0.0.1:8763"


def healthy(root):
    try:
        with urlopen(URL+"/health",timeout=1) as response:
            data=json.load(response)
        return data.get("app")=="DAMOS-LIVE-METRIC3" and data.get("root")==str(root) and data.get("offscreen") is False
    except (OSError,ValueError):
        return False


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",required=True,type=Path)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument("--full",action="store_true")
    mode.add_argument("--five",action="store_true",help="Start one cumulative five-anchor trial")
    mode.add_argument("--three",action="store_true",help="Start one cumulative three-anchor trial")
    mode.add_argument("--scenario",choices=[f"S{i}" for i in range(1,10)])
    args=parser.parse_args()
    root=args.root.resolve()
    ready=root/"runtime_ready.json"
    if not ready.exists() or not json.loads(ready.read_text()).get("verified"):
        raise SystemExit("3번 실구동 시험 환경 검증 미완료. 아직 현장 시험 시작 불가.")
    def open_trial():
        url=URL
        if args.full or args.five or args.three or args.scenario:
            with urlopen(URL+"/api/config",timeout=5) as response:
                config=json.load(response)
            request=Request(URL+"/api/start",data=json.dumps({"mode":"full" if args.full else "five" if args.five else "three" if args.three else "single","scenario":args.scenario or "S1"}).encode(),
                            headers={"Origin":URL,"Content-Type":"application/json","X-CSRF-Token":config["token"]})
            with urlopen(request,timeout=30) as response:
                run=json.load(response)["run"]
            print("새 CARLA 탐지정확도 시험 시작: "+run,flush=True)
            url+="/?run="+run
        subprocess.run(["xdg-open",url],check=True)
    with (root/".viewer_start.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if healthy(root):
            open_trial()
            return 0
        process=subprocess.Popen([sys.executable,"-u",str(root/"app/m3_server.py"),"--root",str(root)])
        try:
            for _ in range(100):
                if healthy(root):
                    break
                if process.poll() is not None:
                    raise RuntimeError("3번 시험 화면 서버 시작 실패")
                time.sleep(.1)
            else:
                raise TimeoutError("시험 화면 응답 시간 초과")
            open_trial()
            fcntl.flock(lock,fcntl.LOCK_UN)
            print("실제 CARLA와 탐지정확도 화면 실행 중. 이 터미널 유지.",flush=True)
            return process.wait()
        except KeyboardInterrupt:
            return 130
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=150)


if __name__=="__main__":
    raise SystemExit(main())
