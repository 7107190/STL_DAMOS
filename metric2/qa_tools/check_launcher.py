"""Exercise the actual desktop entry point on an isolated X display."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen


def fetch(path):
    with urlopen("http://127.0.0.1:8762" + path, timeout=10) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / "logs" / ("launcher_qa_" + time.strftime("%Y%m%dT%H%M%S"))
    output.mkdir(parents=True)
    browser_log = output / "browser.json"
    env = dict(os.environ, PATH=str(root / "qa_tools") + ":" + os.environ["PATH"],
               DAMOS_QA_BROWSER_LOG=str(browser_log))
    try:
        fetch("/health")
    except OSError:
        pass
    else:
        raise RuntimeError("Cold-start QA requires an unused port 8762")
    result, run = None, None
    with (output / "console.log").open("w") as log:
        process = subprocess.Popen(["bash", "start_metric2.sh", "--scenario", "S3"],
                                   cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            deadline = time.monotonic() + 420
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Launcher exited before trial completion")
                if browser_log.exists():
                    url = json.loads(browser_log.read_text())["url"]
                    run = parse_qs(urlsplit(url).query)["run"][0]
                    state = fetch("/api/state?run=" + run)
                    if state["status"] == "failed":
                        raise RuntimeError(str(state["job_error"]))
                    if state["status"] == "complete":
                        assert state["source"] == "live_carla" and state["processed"] == 1
                        assert state["normal"] == state["audited"] == state["within_limit"] == 1
                        assert state["full_scope"] is False
                        result = {"command": "bash start_metric2.sh --scenario S3", "run": run,
                                  "max_seconds": state["max_seconds"], "new_live_trial": True,
                                  "browser_url": url, "native_carla": True,
                                  "browser_check": "xdg-open URL intercepted; page checked separately with isolated headless Chrome"}
                        break
                time.sleep(2)
            else:
                raise TimeoutError("Desktop launcher trial timed out")
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=120)
    try:
        fetch("/health")
    except OSError:
        result.update(owned_server_closed=True, all_pass=True)
    else:
        raise RuntimeError("Launcher left its server running after Ctrl+C")
    (output / "verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"output": str(output), **result}, indent=2))


if __name__ == "__main__":
    main()
