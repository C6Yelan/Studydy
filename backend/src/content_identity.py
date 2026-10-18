"""內容身分的唯一序列化規則；保留已保存來源、Revision 與請求的 bytes。"""

import hashlib
import json
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
        # PDF 字型擷取可能含孤立 surrogate；以 JSON escape 無損保存，既有 UTF-8 hash 不變。
    ).encode("utf-8", errors="backslashreplace")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
