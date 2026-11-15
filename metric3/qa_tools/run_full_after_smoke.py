"""One-shot validation: finish nine fresh scenes, verify them, then test all 100."""
import argparse
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen

BASE="http://127.0.0.1:8763"


def get(path):
    with urlopen(BASE+path,timeout=30) as response:
        return json.load(response)


def wait(run,limit):
    deadline=time.monotonic()+limit
    previous=None
    while time.monotonic()<deadline:
        state=get("/api/state?run="+run)
        signature=state["status"],state["evaluated"],state["live"].get("case_id")
        if signature!=previous:
            print(json.dumps({"run":run,**{k:state[k] for k in ("status","evaluated","planned","successful","detection_failures","execution_errors","error")},"current":state["live"]},ensure_ascii=False),flush=True)
            previous=signature
        if state["status"]=="complete":
            return state
        if state["status"] in ("failed","cancelled"):
            raise RuntimeError("Live trial stopped: "+json.dumps(state.get("error"),ensure_ascii=False))
        time.sleep(5)
    raise TimeoutError("Live trial did not finish within the validation window")


def verify(root,run,full=False):
    args=[sys.executable,str(root/"app/m3_verify.py"),"--root",str(root),"--run",run]
    if full:
        args.append("--require-full")
    subprocess.run(args,check=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",required=True,type=Path)
    parser.add_argument("--smoke",required=True)
    args=parser.parse_args()
    root=args.root.resolve()
    smoke=wait(args.smoke,3600)
    assert smoke["evaluated"]==9 and smoke["execution_errors"]==0
    verify(root,args.smoke)
    config=get("/api/config")
    request=Request(BASE+"/api/start",data=b'{"mode":"full","scenario":"S1"}',
                    headers={"Origin":BASE,"Content-Type":"application/json","X-CSRF-Token":config["token"]})
    with urlopen(request,timeout=30) as response:
        run=json.load(response)["run"]
    (root/"logs/full_validation_run.json").write_text(json.dumps({"run":run,"smoke_run":args.smoke},indent=2))
    final=wait(run,18000)
    assert final["evaluated"]==100 and final["execution_errors"]==0
    verify(root,run,full=True)
    folder=root/"reports"/run
    for kind,filename in (("json","screen_results.json"),("csv","screen_results.csv")):
        with urlopen(BASE+"/api/download?run="+run+"&format="+kind,timeout=60) as response:
            data=response.read()
        if kind=="csv":
            rows=list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
            assert len(rows)==100
            assert sum(r["status"]=="success" for r in rows)==final["successful"]
            assert sum(r["status"]=="failure" for r in rows)==final["detection_failures"]
        (folder/filename).write_bytes(data)
    print(json.dumps({"completed_full_run":run,"successful":final["successful"],"success_rate_percent":final["success_rate_percent"],
                      "criterion_pass":final["criterion_pass"],"independent_verification":True},ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
