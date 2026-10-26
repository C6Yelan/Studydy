"""Provider 的工作取消與子程序生命週期；不接資料庫、不記錄教材內容。"""

from contextlib import contextmanager
import os
import signal
import subprocess
from threading import Event, Lock, Thread
import time
from uuid import UUID


_jobs = {}
_jobs_lock = Lock()


def _key(body):
    try:
        return str(UUID(body["job_id"])), str(UUID(body["job_token"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError("REQUEST_INVALID") from None


def cancel(body):
    key = _key(body)
    with _jobs_lock:
        _prune()
        # 取消可能早於生成 HTTP 請求到達；保留本次 lease 的取消訊號。
        event, _, _ = _jobs.setdefault(key, (Event(), time.monotonic(), 0))
        event.set()
    return {"cancelled": True}


def _prune():
    for key, (_, created, active) in list(_jobs.items()):
        if not active and time.monotonic() - created > 10800:
            _jobs.pop(key, None)


@contextmanager
def job(body, *, required=False):
    if not required and "job_id" not in body and "job_token" not in body:
        # Voice 等非 Podcast 呼叫仍使用原有協定。
        yield None
        return
    key = _key(body)
    with _jobs_lock:
        _prune()
        event, created, active = _jobs.setdefault(key, (Event(), time.monotonic(), 0))
        _jobs[key] = (event, created, active + 1)
    try:
        check(event)
        yield event
    finally:
        with _jobs_lock:
            # 多階段工作共用 lease；已取消的 lease 不得在後續階段重新啟動。
            _, created, active = _jobs[key]
            if active > 1:
                _jobs[key] = (event, created, active - 1)
            elif event.is_set():
                _jobs[key] = (event, time.monotonic(), 0)
            else:
                _jobs.pop(key, None)


def check(cancelled, code="PODCAST_CANCELLED"):
    if cancelled is not None and cancelled.is_set():
        raise ValueError(code)


@contextmanager
def locked(lock, cancelled, *, timeout=None):
    deadline = time.monotonic() + timeout if timeout is not None else None
    acquired = False
    try:
        while not acquired:
            check(cancelled)
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise RuntimeError("PODCAST_PROVIDER_UNAVAILABLE")
            acquired = lock.acquire(
                timeout=0.2 if remaining is None else min(0.2, remaining)
            )
        check(cancelled)
        yield
    finally:
        if acquired:
            lock.release()


def _stop(process):
    # 終止整個 process group，包含 TTS／CLI 啟動的後代程序。
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def run(
    args,
    *,
    input=None,
    env=None,
    timeout,
    cancelled=None,
    cancel_code="PODCAST_CANCELLED",
    started=None,
):
    check(cancelled, cancel_code)
    with subprocess.Popen(
        args,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=not isinstance(input, bytes),
        env=env,
        start_new_session=True,
    ) as process:
        try:
            if started is not None:
                started()
        except BaseException:
            _stop(process)
            raise
        # communicate 只執行一次。Python 3.12 在短 timeout 後以 input=None
        # 重入時，不會繼續寫完尚未送出的 stdin，會讓子程序永遠等不到 EOF。
        finished = Event()
        failures = []

        def communicate():
            try:
                process.communicate(input=input)
            except BaseException as error:
                failures.append(error)
            finally:
                finished.set()

        io_thread = Thread(target=communicate, name="provider-process-io", daemon=True)
        io_thread.start()
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            while True:
                check(cancelled, cancel_code)
                remaining = (
                    deadline - time.monotonic() if deadline is not None else None
                )
                if remaining is not None and remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                if finished.wait(0.2 if remaining is None else min(0.2, remaining)):
                    check(cancelled, cancel_code)
                    if failures:
                        raise failures[0]
                    return subprocess.CompletedProcess(args, process.returncode)
        except BaseException:
            _stop(process)
            raise
        finally:
            io_thread.join()
