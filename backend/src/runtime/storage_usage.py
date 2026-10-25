"""帳號空間：已保存檔案加上已接受工作的保留額度，不得超過帳號上限。

已用＝該帳號全部 artifacts 的 size_bytes＋`analysis/<learner>/` 目錄＋保留額度：
- 尚未收齊的分段上傳：以宣告的整檔大小保留（不是已收到的位元組）；
- 尚未產生音訊的 Podcast 分集（未刪除的節目，含失敗／取消後可接續的）：每集預估音訊大小；
  建立時勾選影片的節目，這些分集另保留每集預估影片大小；
- 排隊或生成中的影片工作：每集預估影片大小。
每集預估取全站既有同類成品（podcast_audio／podcast_video）的平均大小，尚無成品時用下方預設值。
實際成品寫入後改計實際大小，保留額度同時消失；產檔時不再檢查。

會新增檔案的入口（上傳教材、分段上傳第一段、加入 YouTube、建立 Podcast、生成或重試影片）
在同一個帳號層級的 PostgreSQL advisory lock 內「查額度＋寫入或保留」，同帳號的並行請求依序判斷。
鎖的順序固定為帳號空間鎖在前、教材／Podcast 鎖在後。卡片匯出暫存與 Podcast 匯出不落地或由
維護程序清除，不計入。
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import os
import stat
from uuid import UUID

from sqlalchemy import func, select, text

from .storage.artifacts import _root
from .storage.database import database_session
from .storage.tables import Artifact

# 每個帳號的總空間上限（5 GiB）。
ACCOUNT_STORAGE_LIMIT_BYTES = 5 * 1024 * 1024 * 1024
# 全站還沒有同類成品可平均時的每集預估。
DEFAULT_EPISODE_AUDIO_BYTES = 20 * 1024 * 1024
DEFAULT_EPISODE_VIDEO_BYTES = 64 * 1024 * 1024


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


def lock_account(db, owner: UUID) -> None:
    """帳號層級互斥；隨交易提交或回滾釋放。須在教材／Podcast 鎖之前取得。"""
    key = int.from_bytes(
        sha256(b"studydy-account-storage:" + owner.bytes).digest()[:8], "big", signed=True
    )
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def episode_estimates(db) -> dict:
    """每集預估：全站既有同類成品的平均大小。"""
    averages = dict(
        db.execute(
            select(Artifact.kind, func.avg(Artifact.size_bytes))
            .where(Artifact.kind.in_(("podcast_audio", "podcast_video")))
            .group_by(Artifact.kind)
        ).all()
    )
    return {
        "audio": int(averages.get("podcast_audio") or DEFAULT_EPISODE_AUDIO_BYTES),
        "video": int(averages.get("podcast_video") or DEFAULT_EPISODE_VIDEO_BYTES),
    }


def _reserved_bytes(db, owner, estimates) -> int:
    pending = db.execute(
        text(
            """
            SELECT
              coalesce(sum(missing), 0) AS audio,
              coalesce(sum(missing) FILTER (WHERE video_enabled), 0) AS future_videos
            FROM (
              SELECT p.video_enabled,
                     (SELECT count(*) FROM jsonb_array_elements(p.episodes) AS e
                      WHERE jsonb_typeof(e->'audio') IS DISTINCT FROM 'object') AS missing
              FROM podcasts p
              WHERE p.learner_id = :owner AND p.status <> 'deleted'
            ) AS counts
            """
        ),
        {"owner": owner},
    ).one()
    videos = db.scalar(
        text(
            """
            SELECT count(*) FROM podcast_videos v JOIN podcasts p ON p.podcast_id = v.podcast_id
            WHERE p.learner_id = :owner AND p.status <> 'deleted'
              AND v.status IN ('pending', 'running')
            """
        ),
        {"owner": owner},
    )
    return int(pending.audio) * estimates["audio"] + (
        int(pending.future_videos) + int(videos)
    ) * estimates["video"]


def _usage(db, owner: UUID) -> dict:
    from .media_sources import pending_upload_bytes

    artifacts = db.scalar(
        select(func.coalesce(func.sum(Artifact.size_bytes), 0)).where(
            Artifact.learner_id == owner
        )
    )
    reserved = _reserved_bytes(db, owner, episode_estimates(db)) + pending_upload_bytes(owner)
    used = int(artifacts) + _tree_bytes(_root() / "analysis" / owner.hex) + reserved
    return {
        "schema": "account-storage/v1",
        "used_bytes": used,
        "reserved_bytes": reserved,
        "limit_bytes": ACCOUNT_STORAGE_LIMIT_BYTES,
        "remaining_bytes": max(0, ACCOUNT_STORAGE_LIMIT_BYTES - used),
    }


def account_usage(owner: UUID, *, dsn=None) -> dict:
    with database_session(dsn) as db:
        return _usage(db, owner)


def require_room(db, owner: UUID, incoming_bytes: int) -> None:
    """在呼叫端交易內取得帳號鎖後判斷「已用（含保留）＋本次預估 > 上限」；大小未知時至少算 1 byte。

    呼叫端須在同一交易內寫入成品或保留紀錄，提交時才釋放鎖。
    """
    lock_account(db, owner)
    used = _usage(db, owner)["used_bytes"]
    if used + max(incoming_bytes, 1) > ACCOUNT_STORAGE_LIMIT_BYTES:
        raise StorageQuotaError(used, ACCOUNT_STORAGE_LIMIT_BYTES)


@contextmanager
def reserved_room(owner: UUID, incoming_bytes: int, *, dsn=None):
    """在其他交易寫入的入口（上傳、YouTube、分段上傳第一段）：寫入期間持有帳號鎖。"""
    with database_session(dsn) as db:
        require_room(db, owner, incoming_bytes)
        yield
