from __future__ import annotations

from typing import Callable, Dict, Tuple


def parse_options_header(value: str | bytes | None) -> tuple[bytes, dict[bytes, bytes]]:
    """
    Minimal `parse_options_header` implementation compatible with Starlette.

    Returns:
    - main value (bytes)
    - params dict with *bytes* keys/values (e.g. {b"boundary": b"..."}).
    """
    if value is None:
        return b"", {}

    if isinstance(value, bytes):
        raw = value.decode("latin-1")
    else:
        raw = value

    parts = [p.strip() for p in raw.split(";") if p.strip()]
    if not parts:
        return b"", {}

    main = parts[0].encode("latin-1")
    params: dict[bytes, bytes] = {}
    for item in parts[1:]:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        key = k.strip().lower().encode("latin-1")
        val = v.strip()
        if len(val) >= 2 and val[0] == val[-1] == '"':
            val = val[1:-1]
        params[key] = val.encode("latin-1")
    return main, params


QuerystringCallbacks = Dict[str, Callable[..., None]]
MultipartCallbacks = Dict[str, Callable[..., None]]


class QuerystringParser:
    """
    Minimal x-www-form-urlencoded parser with the same callback surface as python-multipart.

    Starlette's `FormParser` only requires that callbacks are called before the request
    stream ends (it handles percent-decoding itself).
    """

    def __init__(self, callbacks: QuerystringCallbacks) -> None:
        self._callbacks = callbacks
        self._buffer = bytearray()

    def write(self, data: bytes) -> None:
        if data:
            self._buffer.extend(data)

    def finalize(self) -> None:
        data = bytes(self._buffer)
        self._buffer.clear()

        if not data:
            self._callbacks["on_end"]()
            return

        for pair in data.split(b"&"):
            if not pair:
                continue
            name, _, value = pair.partition(b"=")
            self._callbacks["on_field_start"]()
            self._callbacks["on_field_name"](name, 0, len(name))
            self._callbacks["on_field_data"](value, 0, len(value))
            self._callbacks["on_field_end"]()
        self._callbacks["on_end"]()


class MultipartParser:
    """
    Minimal multipart/form-data parser compatible with Starlette's callback interface.

    This implementation is intentionally small and is meant as a fallback for
    environments where `python-multipart` cannot be installed (e.g. offline sandboxes).
    It is not a complete RFC-compliant streaming parser, but it is sufficient for:
    - typical browser FormData uploads
    - httpx/requests multipart encoding
    """

    def __init__(self, boundary: bytes | str, callbacks: MultipartCallbacks) -> None:
        if isinstance(boundary, str):
            boundary_bytes = boundary.encode("latin-1")
        else:
            boundary_bytes = boundary

        self._callbacks = callbacks
        self._boundary = boundary_bytes
        self._delim = b"--" + boundary_bytes
        self._marker = b"\r\n" + self._delim
        self._buffer = bytearray()
        self._state: str = "START"
        self._finished = False

    def write(self, data: bytes) -> None:
        if self._finished:
            return
        if data:
            self._buffer.extend(data)
        self._drain()

    def finalize(self) -> None:
        if self._finished:
            return
        # Try to consume any remaining buffered data.
        self._drain(final=True)
        if not self._finished:
            # Best-effort termination. Starlette will handle malformed bodies as errors upstream.
            self._callbacks["on_end"]()
            self._finished = True

    def _drain(self, *, final: bool = False) -> None:
        while True:
            if self._state == "START":
                idx = self._buffer.find(self._delim)
                if idx == -1:
                    # Keep a small tail so a boundary split across chunks can still be detected.
                    keep = max(len(self._delim) + 4, 64)
                    if len(self._buffer) > keep:
                        del self._buffer[:-keep]
                    return

                # Drop preamble.
                if idx:
                    del self._buffer[:idx]

                # Need `--boundary` + either `\r\n` (next part) or `--` (end).
                need = len(self._delim) + 2
                if len(self._buffer) < need:
                    return

                if self._buffer.startswith(self._delim + b"--"):
                    del self._buffer[: len(self._delim) + 2]
                    if self._buffer.startswith(b"\r\n"):
                        del self._buffer[:2]
                    self._callbacks["on_end"]()
                    self._finished = True
                    self._state = "END"
                    return

                if not self._buffer.startswith(self._delim + b"\r\n"):
                    # Wait for more data.
                    return

                del self._buffer[: len(self._delim) + 2]
                self._callbacks["on_part_begin"]()
                self._state = "HEADERS"
                continue

            if self._state == "HEADERS":
                sep = b"\r\n\r\n"
                idx = self._buffer.find(sep)
                if idx == -1:
                    return
                header_block = bytes(self._buffer[:idx])
                del self._buffer[: idx + len(sep)]
                self._process_headers(header_block)
                self._callbacks["on_headers_finished"]()
                self._state = "DATA"
                continue

            if self._state == "DATA":
                idx = self._buffer.find(self._marker)
                if idx == -1:
                    # Emit data but keep a tail that might contain a boundary marker.
                    keep = len(self._marker) + 8
                    if len(self._buffer) > keep:
                        body = bytes(self._buffer[:-keep])
                        del self._buffer[:-keep]
                        if body:
                            self._callbacks["on_part_data"](body, 0, len(body))
                    return

                body = bytes(self._buffer[:idx])
                del self._buffer[: idx + len(self._marker)]
                if body:
                    self._callbacks["on_part_data"](body, 0, len(body))
                self._callbacks["on_part_end"]()

                # After `\r\n--boundary`, there is either `--` (end) or `\r\n` (next part).
                if len(self._buffer) < 2:
                    if final:
                        self._callbacks["on_end"]()
                        self._finished = True
                        self._state = "END"
                    return

                if self._buffer.startswith(b"--"):
                    del self._buffer[:2]
                    if self._buffer.startswith(b"\r\n"):
                        del self._buffer[:2]
                    self._callbacks["on_end"]()
                    self._finished = True
                    self._state = "END"
                    return

                if self._buffer.startswith(b"\r\n"):
                    del self._buffer[:2]
                    self._callbacks["on_part_begin"]()
                    self._state = "HEADERS"
                    continue

                # Unexpected; wait for more data.
                return

            return

    def _process_headers(self, header_block: bytes) -> None:
        if not header_block:
            return
        for line in header_block.split(b"\r\n"):
            if not line:
                continue
            name, sep, value = line.partition(b":")
            if not sep:
                continue
            field = name.strip()
            val = value.strip()
            if field:
                self._callbacks["on_header_field"](field, 0, len(field))
            if val:
                self._callbacks["on_header_value"](val, 0, len(val))
            self._callbacks["on_header_end"]()

