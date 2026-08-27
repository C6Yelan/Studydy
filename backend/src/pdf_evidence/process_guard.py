from __future__ import annotations

import ctypes
import os
import signal
import sys


_PR_SET_PDEATHSIG = 1


def main(arguments: list[str] | None = None) -> int:
    """讓被 exec 的本機模型在 app parent 消失時由 kernel 終止。"""

    values = sys.argv[1:] if arguments is None else arguments
    if len(values) < 2 or sys.platform != "linux":
        return 2
    try:
        expected_parent_pid = int(values[0])
        command = values[1:]
        if expected_parent_pid < 2 or os.getppid() != expected_parent_pid:
            return 4
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
            return 3
        # parent 可能在 prctl 前已死亡；重新檢查可關閉這個競態。
        if os.getppid() != expected_parent_pid:
            return 4
        os.execve(command[0], command, os.environ.copy())
    except (OSError, ValueError):
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
