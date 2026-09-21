"""啟動 disposable Vite，執行 final API contract 的 browser fixture tests。"""

from __future__ import annotations

from contextlib import contextmanager
import os
import threading

import uvicorn
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen
from uuid import uuid4


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
    """兩種真 DB browser fixture 共用專屬 API socket，不啟動模型 worker。"""
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


def main(spec: str = "e2e/product-cutover.spec.ts", *, production: bool = False, timeout_seconds: int = 120) -> int:
    if not _port_is_free():
        print("BROWSER_E2E_PORT_OCCUPIED")
        return 1
    environment = os.environ.copy()
    environment["STUDYDY_E2E_BASE_URL"] = f"http://127.0.0.1:{PORT}"
    environment["STUDYDY_E2E_API_ORIGIN"] = f"http://127.0.0.1:{API_PORT}"
    environment["STUDYDY_E2E_HARNESS_ID"] = f"studydy-e2e-{uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="studydy-browser-e2e-") as directory:
        log_path = Path(directory) / "vite.log"
        with log_path.open("wb") as log:
            vite = subprocess.Popen(
                [str(VITE), *(["preview"] if production else []),
                 *(["--outDir", os.environ["STUDYDY_E2E_FRONTEND_DIST"]] if production and os.environ.get("STUDYDY_E2E_FRONTEND_DIST") else []), "--host", "127.0.0.1", "--port", str(PORT), "--strictPort"],
                cwd=FRONTEND,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
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
