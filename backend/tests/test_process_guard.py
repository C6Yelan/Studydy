from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from pdf_evidence.process_guard import main


GUARD = Path(__file__).parents[1] / "src" / "pdf_evidence" / "process_guard.py"


def _is_running(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split()[2]
    except (OSError, IndexError):
        return False
    return state != "Z"


def test_guard_rejects_missing_command():
    assert main([]) == 2


def test_guard_terminates_execed_process_when_launcher_dies(tmp_path):
    child_code = "import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(60)"
    launcher_code = (
        "import os,subprocess,sys,time;"
        "subprocess.Popen([sys.executable,sys.argv[1],str(os.getpid()),sys.executable,'-c',sys.argv[2],sys.argv[3]],"
        "start_new_session=True);time.sleep(60)"
    )
    pid_path = tmp_path / "child.pid"
    launcher = subprocess.Popen(
        [sys.executable, "-c", launcher_code, str(GUARD), child_code, str(pid_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not pid_path.exists():
            if time.monotonic() >= deadline:
                raise AssertionError("guarded child did not start")
            time.sleep(0.01)
        child_pid = int(pid_path.read_text())
        assert _is_running(child_pid)
        os.kill(launcher.pid, signal.SIGKILL)
        launcher.wait(timeout=5)
        deadline = time.monotonic() + 5
        while _is_running(child_pid):
            if time.monotonic() >= deadline:
                raise AssertionError("guarded child survived parent death")
            time.sleep(0.01)
    finally:
        if launcher.poll() is None:
            launcher.kill()
            launcher.wait(timeout=5)
