"""帳號空間：該帳號所有 artifacts、分析檔目錄與尚未收齊的分段上傳合計。

會新增檔案的入口（上傳教材、加入 YouTube、建立 Podcast、生成影片）在接受請求時呼叫
`require_room` 一次；背景工作與已接受工作的後續步驟不再檢查。卡片匯出暫存與 Podcast 匯出
不落地或由維護程序清除，不計入。
"""

from __future__ import annotations

import os
import stat
from uuid import UUID

from sqlalchemy import func, select

from .storage.artifacts import _root
from .storage.database import database_session
from .storage.tables import Artifact

# 每個帳號的總空間上限（5 GiB）。
ACCOUNT_STORAGE_LIMIT_BYTES = 5 * 1024 * 1024 * 1024


class StorageQuotaError(RuntimeError):
    """新增檔案會超過帳號空間；只帶位元組數，不含路徑或內容。"""

    def __init__(self, used_bytes: int, limit_bytes: int) -> None:
        super().__init__("STORAGE_QUOTA_EXCEEDED")
        self.used_bytes = used_bytes
        self.limit_bytes = limit_bytes


def _tree_bytes(path):
    total = 0
    try:
        entries = list(os.scandir(path))
    except FileNotFoundError:
        return 0
    for entry in entries:
        try:
            details = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(details.st_mode):
            total += _tree_bytes(entry.path)
        elif stat.S_ISREG(details.st_mode):
            total += details.st_size
    return total


def account_usage(owner: UUID, *, dsn=None) -> dict:
    from .media_sources import pending_upload_bytes

    with database_session(dsn) as db:
        artifacts = db.scalar(
            select(func.coalesce(func.sum(Artifact.size_bytes), 0)).where(
                Artifact.learner_id == owner
            )
        )
    used = (
        int(artifacts)
        + _tree_bytes(_root() / "analysis" / owner.hex)
        + pending_upload_bytes(owner)
    )
    return {
        "schema": "account-storage/v1",
        "used_bytes": used,
        "limit_bytes": ACCOUNT_STORAGE_LIMIT_BYTES,
        "remaining_bytes": max(0, ACCOUNT_STORAGE_LIMIT_BYTES - used),
    }


def require_room(owner: UUID, incoming_bytes: int = 0, *, dsn=None) -> None:
    """已用＋本次大小超過上限就拒絕；大小未知的動作至少算 1 byte，空間已滿即拒絕。"""
    used = account_usage(owner, dsn=dsn)["used_bytes"]
    if used + max(incoming_bytes, 1) > ACCOUNT_STORAGE_LIMIT_BYTES:
        raise StorageQuotaError(used, ACCOUNT_STORAGE_LIMIT_BYTES)
