from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import subprocess
from typing import Any


_OCR_ENVIRONMENT = {
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
}
_OCR_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "from studydy_local_ai.ocr_process import main;raise SystemExit(main())"
)


class LocalAIError(RuntimeError):
    """只保存固定 reason code，不保存 child diagnostics。"""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LocalAIError("CHILD_RESPONSE_INVALID")
        value[key] = item
    return value


def _reject_constant(_: str) -> None:
    raise LocalAIError("CHILD_RESPONSE_INVALID")


def _read_response(stream: Any, max_bytes: int) -> dict[str, Any]:
    encoded = stream.readline(max_bytes + 2)
    if not encoded or len(encoded) > max_bytes + 1 or not encoded.endswith(b"\n"):
        raise LocalAIError("CHILD_RESPONSE_INVALID")
    try:
        response = json.loads(
            encoded[:-1].decode("utf-8"),
            object_pairs_hook=_without_duplicates,
            parse_constant=_reject_constant,
        )
    except LocalAIError:
        raise
    except (RecursionError, UnicodeDecodeError, ValueError) as error:
        raise LocalAIError("CHILD_RESPONSE_INVALID") from error
    if not isinstance(response, dict):
        raise LocalAIError("CHILD_RESPONSE_INVALID")
    return response


class LocalAIProcess:
    """管理單一 child 的 request、timeout 與終止。"""

    def __init__(self, command: list[str], *, request_limit: int, response_limit: int) -> None:
        self._request_limit = request_limit
        self._response_limit = response_limit
        self._is_closed = False
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_OCR_ENVIRONMENT,
            )
        except OSError as error:
            raise LocalAIError("CHILD_EXITED") from error

    def _exchange(self, encoded: bytes) -> dict[str, Any]:
        assert self._process.stdin is not None and self._process.stdout is not None
        try:
            self._process.stdin.write(encoded + b"\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise LocalAIError("CHILD_EXITED") from error
        return _read_response(self._process.stdout, self._response_limit)

    def _close_streams(self) -> None:
        for stream in (self._process.stdin, self._process.stdout):
            try:
                if stream is not None and not stream.closed:
                    stream.close()
            except OSError:
                pass

    def request(
        self, request: dict[str, Any], timeout_seconds: float | None
    ) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                request,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (RecursionError, TypeError, ValueError) as error:
            raise LocalAIError("INTERNAL_FAILURE") from error
        if len(encoded) > self._request_limit:
            raise LocalAIError("PROTOCOL_LIMIT_EXCEEDED")
        if self._process.poll() is not None:
            raise LocalAIError("CHILD_EXITED")
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._exchange, encoded)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeout as error:
            self.abort()
            raise LocalAIError("CHILD_TIMEOUT") from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def read_startup_response(self, timeout_seconds: float) -> dict[str, Any]:
        """等待 child readiness；timeout 時終止尚未就緒的 process。"""

        assert self._process.stdout is not None
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            _read_response, self._process.stdout, self._response_limit
        )
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeout as error:
            self.abort()
            raise LocalAIError("CHILD_TIMEOUT") from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def close(self) -> None:
        if self._is_closed:
            return
        self._is_closed = True
        try:
            if self._process.stdin is not None and not self._process.stdin.closed:
                self._process.stdin.close()
            return_code = self._process.wait()
            if return_code != 0:
                raise LocalAIError("CHILD_EXITED")
            assert self._process.stdout is not None
            if self._process.stdout.read(1):
                raise LocalAIError("CHILD_RESPONSE_INVALID")
        finally:
            self._close_streams()

    def abort(self) -> None:
        if self._is_closed:
            return
        self._is_closed = True
        try:
            try:
                if self._process.poll() is None:
                    self._process.kill()
                self._process.wait()
            except OSError:
                pass
        finally:
            self._close_streams()


def start_ocr_process(settings: dict[str, Any]) -> LocalAIProcess:
    return LocalAIProcess(
        [
            settings["python_executable"],
            "-I",
            "-c",
            _OCR_BOOTSTRAP,
            settings["site_packages"],
            settings["ocr_model_root"],
        ],
        request_limit=96 * 1024 * 1024,
        response_limit=4 * 1024 * 1024,
    )
