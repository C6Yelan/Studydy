"""啟動 disposable Vite，執行 final API contract 的 browser fixture tests。"""

from __future__ import annotations

import os
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
PORT = 4173


def _port_is_free() -> bool:
    with socket.socket() as listener:
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


def main() -> int:
    if not _port_is_free():
        print("BROWSER_E2E_PORT_OCCUPIED")
        return 1
    environment = os.environ.copy()
    environment["STUDYDY_E2E_HARNESS_ID"] = f"studydy-e2e-{uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="studydy-browser-e2e-") as directory:
        log_path = Path(directory) / "vite.log"
        with log_path.open("wb") as log:
            vite = subprocess.Popen(
                [str(VITE), "--host", "127.0.0.1", "--port", str(PORT), "--strictPort"],
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
                    [str(PLAYWRIGHT), "test", "e2e/product-cutover.spec.ts"],
                    cwd=FRONTEND,
                    env=environment,
                    check=False,
                    timeout=120,
                )
                return completed.returncode
            finally:
                _stop(vite)


if __name__ == "__main__":
    raise SystemExit(main())
