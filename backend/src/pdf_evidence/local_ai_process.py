"""以 bounded anonymous pipes 啟動固定本機 AI child。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import subprocess
from threading import Thread
from typing import Any


MAX_STDERR_BYTES = 1024 * 1024
_OCR_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "from studydy_local_ai.ocr_process import main;raise SystemExit(main())"
)
_CONCEPT_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "from studydy_local_ai.concept_process import main;raise SystemExit(main())"
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
    """管理單一 child 的 request、timeout、stderr discard 與終止。"""

    def __init__(self, command: list[str], *, request_limit: int, response_limit: int) -> None:
        self._request_limit = request_limit
        self._response_limit = response_limit
        self._stderr_bytes = 0
        self._is_closed = False
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise LocalAIError("CHILD_EXITED") from error
        self._stderr_thread = Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        assert self._process.stderr is not None
        while chunk := self._process.stderr.read(8192):
            self._stderr_bytes += len(chunk)
            if self._stderr_bytes > MAX_STDERR_BYTES:
                self._process.kill()
                return

    def request(self, request: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
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
        assert self._process.stdin is not None and self._process.stdout is not None
        try:
            self._process.stdin.write(encoded + b"\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise LocalAIError("CHILD_EXITED") from error
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_read_response, self._process.stdout, self._response_limit)
            try:
                return future.result(timeout=timeout_seconds)
            except FutureTimeout as error:
                self.abort()
                raise LocalAIError("CHILD_TIMEOUT") from error

    def close(self) -> None:
        if self._is_closed:
            return
        self._is_closed = True
        try:
            if self._process.stdin is not None and not self._process.stdin.closed:
                self._process.stdin.close()
            try:
                return_code = self._process.wait(timeout=30)
            except subprocess.TimeoutExpired as error:
                self._process.kill()
                self._process.wait()
                raise LocalAIError("CHILD_TIMEOUT") from error
            self._stderr_thread.join(timeout=5)
            if self._stderr_bytes:
                raise LocalAIError(
                    "PROTOCOL_LIMIT_EXCEEDED"
                    if self._stderr_bytes > MAX_STDERR_BYTES
                    else "CHILD_RESPONSE_INVALID"
                )
            if return_code != 0:
                raise LocalAIError("CHILD_EXITED")
            assert self._process.stdout is not None
            if self._process.stdout.read(1):
                raise LocalAIError("CHILD_RESPONSE_INVALID")
        finally:
            for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

    def abort(self) -> None:
        if self._is_closed:
            return
        self._is_closed = True
        try:
            if self._process.poll() is None:
                self._process.kill()
            self._process.wait()
            self._stderr_thread.join(timeout=5)
        finally:
            for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


def start_ocr_process(settings: dict[str, Any]) -> LocalAIProcess:
    return LocalAIProcess(
        [
            settings["python_executable"],
            "-c",
            _OCR_BOOTSTRAP,
            settings["site_packages"],
            settings["ocr_model_root"],
        ],
        request_limit=96 * 1024 * 1024,
        response_limit=4 * 1024 * 1024,
    )


def start_concept_process(settings: dict[str, Any]) -> LocalAIProcess:
    return LocalAIProcess(
        [
            settings["python_executable"],
            "-c",
            _CONCEPT_BOOTSTRAP,
            settings["site_packages"],
            settings["concept_model_root"],
        ],
        request_limit=512 * 1024,
        response_limit=65_536 + 1024,
    )
