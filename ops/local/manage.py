"""管理既有本機產品服務；資料庫只啟動，資料與 Pod 不隨 stop 刪除。"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time


REPO = Path(__file__).resolve().parents[2]
STATE = REPO.parent / ".studydy-product"
LOGS = STATE / "logs"
RUN = STATE / "run"
PORTS = {"model-bridge": 18000, "backend": 8001, "frontend": 4173}
MARKERS = {"model-bridge": "model_bridge.py", "backend": "runtime.local_app", "frontend": "preview"}


def process_id(name):
    """核對 PID 與程式名稱，避免處理被其他程式重用的 PID。"""
    try:
        pid = int((RUN / f"{name}.pid").read_text())
        command = Path(f"/proc/{pid}/cmdline").read_bytes().decode()
        if MARKERS[name] in command and os.getpgid(pid) == pid:
            return pid
    except (OSError, ValueError, UnicodeError):
        pass
    return None


def listening_ports():
    """只讀本機 socket 狀態，不發 HTTP 或模型 probe。"""
    ports = set()
    for protocol in ("tcp", "tcp6"):
        for line in Path(f"/proc/net/{protocol}").read_text().splitlines()[1:]:
            fields = line.split()
            address, port = fields[1].split(":")
            # Product services bind 127.0.0.1. Other loopback addresses are independent.
            # Wildcard (including dual-stack / mapped IPv4) listeners still conflict.
            product_addresses = {
                "0100007F", "00000000", "0" * 32,
                "0000000000000000FFFF00000100007F",
                "0000000000000000FFFF000000000000",
            }
            if fields[3] == "0A" and address.upper() in product_addresses:
                ports.add(int(port, 16))
    return ports


def database():
    config = json.loads((STATE / "private-config.json").read_text())
    inspected = subprocess.run(["docker", "inspect", config["container"]], capture_output=True, text=True, check=True)
    container = json.loads(inspected.stdout)[0]
    return config, container


def status():
    config, container = database()
    ports = listening_ports()
    return {
        "url": "http://127.0.0.1:4173",
        "checkout": str(REPO),
        "state_directory": str(STATE),
        "database": {
            "container": config["container"],
            "state": container["State"]["Status"],
            "persistent_volume": config["volume"],
            "auto_remove": container["HostConfig"]["AutoRemove"],
            "tmpfs": bool(container["HostConfig"].get("Tmpfs")),
        },
        "services": {name: {"pid": process_id(name), "listening": port in ports} for name, port in PORTS.items()},
    }


def launch(name, command):
    if process_id(name):
        if PORTS[name] not in listening_ports():
            raise RuntimeError(f"{name}: already starting; inspect its local log before continuing")
        return
    if PORTS[name] in listening_ports():
        raise RuntimeError(f"{name}: port already occupied; no second service started")
    with os.fdopen(os.open(LOGS / f"{name}.log", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600), "ab") as log:
        process = subprocess.Popen(command, cwd=REPO, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    (RUN / f"{name}.pid").write_text(str(process.pid))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name}: exited; inspect its local log")
        if PORTS[name] in listening_ports():
            return
        time.sleep(0.25)
    raise RuntimeError(f"{name}: still starting; inspect status/logs before running start again")


def start():
    LOGS.mkdir(mode=0o700, exist_ok=True)
    RUN.mkdir(mode=0o700, exist_ok=True)
    config, container = database()
    if container["HostConfig"]["AutoRemove"] or container["HostConfig"].get("Tmpfs"):
        raise RuntimeError("Refusing a disposable database for product startup")
    if not any(m.get("Type") == "volume" and m.get("Name") == config["volume"] for m in container["Mounts"]):
        raise RuntimeError("Product database volume does not match its local configuration")
    if container["State"]["Status"] != "running":
        subprocess.run(["docker", "start", config["container"]], check=True, stdout=subprocess.DEVNULL)
    launch("model-bridge", ["python3", str(REPO / "ops/local/model_bridge.py")])
    launch("backend", [str(REPO / "backend/.venv/bin/python"), str(REPO / "ops/local/run_backend.py")])
    if not process_id("frontend"):
        with (LOGS / "frontend-build.log").open("ab") as log:
            subprocess.run(["npm", "--prefix", "frontend", "run", "build"], cwd=REPO, check=True, stdout=log, stderr=log)
    launch("frontend", ["npm", "--prefix", "frontend", "run", "preview", "--", "--host", "127.0.0.1", "--port", "4173", "--strictPort"])


def stop():
    for name in ("frontend", "backend", "model-bridge"):
        pid = process_id(name)
        if pid:
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while process_id(name) == pid and time.monotonic() < deadline:
                time.sleep(0.25)
            if process_id(name) == pid:
                raise RuntimeError(f"{name}: still stopping; no forced termination sent")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "start", "stop"))
    command = parser.parse_args().command
    try:
        if command == "start":
            start()
        elif command == "stop":
            stop()
        print(json.dumps(status(), indent=2, ensure_ascii=False))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, RuntimeError) as error:
        # 子程序與 config 的原始例外可能包含私密連線資訊。
        print(str(error) if type(error) is RuntimeError else "LOCAL_ENVIRONMENT_OPERATION_FAILED")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
