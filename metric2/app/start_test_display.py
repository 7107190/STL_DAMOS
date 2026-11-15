"""Isolated native-window validation display; never connects to the physical desktop."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from damos_paths import shared_root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    base = shared_root(root) / "tools/xvfb"
    binary = base / "root/usr/bin/Xvfb"
    if Path("/tmp/.X92-lock").exists():
        raise SystemExit("Display :92 is already in use")
    auth = base / "test.Xauthority"
    auth.touch(mode=0o600)
    subprocess.run(["xauth", "-f", str(auth), "add", ":92", ".", secrets.token_hex(16)],
                   check=True, stdout=subprocess.DEVNULL)
    with (base / "server.log").open("w") as log:
        process = subprocess.Popen([str(binary), ":92", "-auth", str(auth), "-screen", "0", "1600x900x24",
                                    "-nolisten", "tcp", "-noreset"],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (base / "process.json").write_text(json.dumps({"pid": process.pid, "display": ":92", "authority": str(auth)}))
    for _ in range(50):
        if process.poll() is not None:
            raise SystemExit((base / "server.log").read_text())
        if Path("/tmp/.X11-unix/X92").exists():
            print(json.dumps({"pid": process.pid, "display": ":92", "authority": str(auth)}))
            return
        time.sleep(.1)
    process.terminate()
    process.wait(timeout=10)
    raise TimeoutError("Xvfb validation display did not start")


if __name__ == "__main__":
    main()
