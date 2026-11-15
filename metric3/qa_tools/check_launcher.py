"""Cold-start the exact operator command without opening a user's browser profile."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from urllib.parse import parse_qs,urlsplit
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
BASE="http://127.0.0.1:8763"


def get(path):
    with urlopen(BASE+path,timeout=10) as response:
        return json.load(response)


def main():
    output=ROOT/"logs"/("launcher_qa_"+time.strftime("%Y%m%dT%H%M%S"))
    output.mkdir()
    browser_log=output/"browser.json"
    env=dict(os.environ,PATH=str(ROOT/"qa_tools")+":"+os.environ["PATH"],DAMOS_QA_BROWSER_LOG=str(browser_log))
    try:
        get("/health")
    except OSError:
        pass
    else:
        raise RuntimeError("Cold-start test requires port 8763 to be free")
    result=None
    with (output/"console.log").open("w") as log:
        process=subprocess.Popen(["bash","start_metric3.sh","--scenario","S3"],cwd="/home/vv/DAMOS",env=env,
                                 stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError("Operator launcher exited prematurely")
                if browser_log.exists():
                    url=json.loads(browser_log.read_text())["url"]
                    run=parse_qs(urlsplit(url).query)["run"][0]
                    state=get("/api/state?run="+run)
                    if state["status"] in ("failed","cancelled"):
                        raise RuntimeError(str(state["error"]))
                    if state["status"]=="complete":
                        assert state["planned"]==state["evaluated"]==1 and state["execution_errors"]==0
                        assert state["source"]=="live_carla_sensor_stream" and state["criterion_pass"] is False
                        result={"operator_command":"cd /home/vv/DAMOS; bash start_metric3.sh --scenario S3",
                                "run":run,"new_live_trial":True,"evaluated":state["evaluated"],"successful":state["successful"],
                                "browser_url":url,"native_carla":True,
                                "browser_check":"xdg-open URL intercepted; UI separately verified using isolated headless Chrome"}
                        break
                time.sleep(2)
            else:
                raise TimeoutError("Operator launcher trial did not complete")
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=180)
    try:
        get("/health")
    except OSError:
        result.update(owned_server_closed=True,all_pass=True)
    else:
        raise RuntimeError("Launcher left its own server alive after Ctrl+C")
    (output/"verification.json").write_text(json.dumps(result,indent=2))
    print(json.dumps({"output":str(output),**result},indent=2))


if __name__=="__main__":
    main()
