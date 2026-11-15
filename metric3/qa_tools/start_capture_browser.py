"""An isolated Goosebumps Chrome for screenshots, never a user's browser profile."""
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2] / "metric2/logs/exam_handoff_20261109"
ROOT.mkdir(exist_ok=True)
try:
    urlopen("http://127.0.0.1:9229/json/version", timeout=1)
except OSError:
    pass
else:
    raise SystemExit("Capture browser port is already in use")
profile = ROOT / "isolated_chrome_profile"
profile.mkdir(mode=0o700, exist_ok=True)
command = ["/usr/bin/google-chrome", "--headless=new", "--remote-debugging-address=127.0.0.1",
           "--remote-debugging-port=9229", "--user-data-dir=" + str(profile), "--no-first-run",
           "--no-default-browser-check", "--disable-dev-shm-usage", "--password-store=basic", "about:blank"]
with (ROOT / "chrome.log").open("w") as log:
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True)
for _ in range(100):
    if process.poll() is not None:
        raise RuntimeError((ROOT / "chrome.log").read_text())
    try:
        with urlopen("http://127.0.0.1:9229/json/version", timeout=1) as response:
            version = json.load(response)
        record = {"pid": process.pid, "profile": str(profile), "version": version, "isolated": True}
        (ROOT / "browser.json").write_text(json.dumps(record, indent=2))
        print(json.dumps(record), flush=True)
        break
    except OSError:
        time.sleep(.1)
else:
    process.terminate()
    process.wait(timeout=10)
    raise TimeoutError("Capture browser did not start")
