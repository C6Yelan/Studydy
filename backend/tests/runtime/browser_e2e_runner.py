"""預覽正式前端建置，執行指定的 Playwright spec。"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
from urllib.request import urlopen
from uuid import uuid4

import uvicorn


ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "frontend"
VITE = FRONTEND / "node_modules/.bin/vite"
PLAYWRIGHT = FRONTEND / "node_modules/.bin/playwright"
PORT = int(os.environ.get("STUDYDY_E2E_FRONTEND_PORT", "4173"))
API_PORT = int(os.environ.get("STUDYDY_E2E_API_PORT", "8001"))


def _port_is_free() -> bool:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", PORT))
            return True
        except OSError:
            return False


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


@contextmanager
def local_api(app):
    """真 DB browser 測試共用專屬 API socket，不啟動模型 worker。"""
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", API_PORT))
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.05)
            assert server.started
            yield
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def main(spec: str = "e2e/product-cutover.spec.ts", *, timeout_seconds: int = 120) -> int:
    if not _port_is_free():
        print("BROWSER_E2E_PORT_OCCUPIED")
        return 1
    environment = os.environ.copy()
    environment["STUDYDY_E2E_BASE_URL"] = f"http://127.0.0.1:{PORT}"
    environment["STUDYDY_E2E_API_ORIGIN"] = f"http://127.0.0.1:{API_PORT}"
    environment["STUDYDY_E2E_HARNESS_ID"] = f"studydy-e2e-{uuid4().hex}"
    vite_command = [str(VITE), "preview"]
    if dist := environment.get("STUDYDY_E2E_FRONTEND_DIST"):
        vite_command.extend(("--outDir", dist))
    vite_command.extend(("--host", "127.0.0.1", "--port", str(PORT), "--strictPort"))
    vite = subprocess.Popen(
        vite_command,
        cwd=FRONTEND,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if vite.poll() is not None:
                return 1
            try:
                with urlopen(f"http://127.0.0.1:{PORT}", timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            return 1
        completed = subprocess.run(
            [str(PLAYWRIGHT), "test", spec],
            cwd=FRONTEND,
            env=environment,
            check=False,
            timeout=timeout_seconds,
        )
        return completed.returncode
    finally:
        _stop(vite)


if __name__ == "__main__":
    raise SystemExit(main())
