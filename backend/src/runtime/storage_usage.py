"""帳號空間：5 GiB 是「接受新增工作」的門檻。

已用＝該帳號全部 artifacts 的 size_bytes＋`analysis/<learner>/` 目錄＋`storage_reservations` 的保留額度，
artifacts 與保留在同一個 SQL 敘述（同一快照）加總。保留額度在接受工作時固定寫入：
- upload：分段上傳以第一段宣告的整檔大小、單次上傳以本次大小；
- podcast_audio／podcast_video：建立 Podcast（或重試／補建影片）當時每集的估值，取全站既有同類成品
  的平均大小，尚無成品時用下方預設值。之後全站平均變動不影響已保留的額度。
成品寫入的同一交易內刪除對應保留，改計實際大小；失敗、取消或刪除時釋放。

會新增檔案的入口在帳號層級 advisory lock 內，以短交易完成「再次辨識冪等 key＋核對額度＋建立保留」，
檔案落盤在鎖外。鎖等待逾時回可重試的 STORAGE_BUSY。同一交易若也鎖教材／Podcast，教材鎖在前。
已接受的工作產生的檔案（成品比預估大、normalized_pdf／mapping／分析檔、補充學習匯入）照常完成，
計入已用並擋住之後的新增，不會中途中斷。卡片匯出暫存與 Podcast 匯出不落地或由維護程序清除，不計入。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import os
import stat
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from .storage.artifacts import _root
from .storage.database import database_session
from .storage.tables import Artifact

# 每個帳號的總空間上限（5 GiB）。
ACCOUNT_STORAGE_LIMIT_BYTES = 5 * 1024 * 1024 * 1024
# 全站還沒有同類成品可平均時的每集預估。
DEFAULT_EPISODE_AUDIO_BYTES = 20 * 1024 * 1024
DEFAULT_EPISODE_VIDEO_BYTES = 64 * 1024 * 1024
# 單次上傳或中斷的分段上傳在這段時間後視為失效，由維護 worker 釋放保留。
UPLOAD_RESERVATION_TTL = timedelta(days=1)


class StorageQuotaError(RuntimeError):
    """新增檔案會超過帳號空間；只帶位元組數，不含路徑或內容。"""

    def __init__(self, used_bytes: int, limit_bytes: int) -> None:
        super().__init__("STORAGE_QUOTA_EXCEEDED")
        self.used_bytes = used_bytes
        self.limit_bytes = limit_bytes


class StorageBusyError(RuntimeError):
    """同帳號另一個新增請求仍在核對額度；可稍後重試。"""

    def __init__(self) -> None:
        super().__init__("STORAGE_BUSY")


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
    """帳號層級互斥，隨交易結束釋放；等待逾時回可重試的 STORAGE_BUSY。"""
    key = int.from_bytes(
        sha256(b"studydy-account-storage:" + owner.bytes).digest()[:8], "big", signed=True
    )
    try:
        with db.begin_nested():
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    except OperationalError as error:
        if getattr(error.orig, "sqlstate", None) in ("55P03", "57014"):
            raise StorageBusyError() from None
        raise


def upload_reservation_id(owner, material_id, key) -> UUID:
    """同一上傳意圖（帳號、教材、Idempotency-Key）固定的保留 ID。"""
    return UUID(bytes=sha256(f"upload:{owner}:{material_id}:{key}".encode()).digest()[:16])


def episode_estimates(db) -> dict:
    """新工作的每集起始估值：全站既有同類成品的平均大小。"""
    averages = dict(
        db.execute(
            select(Artifact.kind, func.avg(Artifact.size_bytes))
            .where(Artifact.kind.in_(("podcast_audio", "podcast_video")))
            .group_by(Artifact.kind)
        ).all()
    )
    return {
        "audio": max(1, int(averages.get("podcast_audio") or DEFAULT_EPISODE_AUDIO_BYTES)),
        "video": max(1, int(averages.get("podcast_video") or DEFAULT_EPISODE_VIDEO_BYTES)),
    }


def _usage(db, owner: UUID, *, exclude=None) -> dict:
    # artifacts 與保留在同一個敘述加總：背景同時提交「新增成品＋刪除保留」時不會兩邊都漏算。
    row = db.execute(
        text(
            """
            SELECT
              (SELECT coalesce(sum(size_bytes), 0) FROM artifacts WHERE learner_id = :owner) AS files,
              (SELECT coalesce(sum(r.bytes), 0) FROM storage_reservations r
                 LEFT JOIN podcasts p ON p.podcast_id = r.podcast_id
                 WHERE r.learner_id = :owner
                   AND (r.podcast_id IS NULL OR p.status <> 'deleted')
                   AND r.reservation_id IS DISTINCT FROM CAST(:exclude AS uuid)) AS reserved
            """
        ),
        {"owner": owner, "exclude": exclude},
    ).one()
    reserved = int(row.reserved)
    used = int(row.files) + reserved + _tree_bytes(_root() / "analysis" / owner.hex)
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


def require_room(db, owner: UUID, incoming_bytes: int, *, exclude=None) -> None:
    """呼叫端已取得帳號鎖：「已用（含保留）＋本次 > 上限」就拒絕；大小未知時至少算 1 byte。"""
    used = _usage(db, owner, exclude=exclude)["used_bytes"]
    if used + max(incoming_bytes, 1) > ACCOUNT_STORAGE_LIMIT_BYTES:
        raise StorageQuotaError(used, ACCOUNT_STORAGE_LIMIT_BYTES)


def reserve(db, owner, material_id, kind, size, *, reservation_id=None, podcast_id=None, episode_index=None):
    """建立保留；同一工作已有保留時沿用原值（不重新定價）。"""
    db.execute(
        text(
            """
            INSERT INTO storage_reservations
              (reservation_id, learner_id, material_id, kind, podcast_id, episode_index, bytes, created_at)
            VALUES (:id, :owner, :material, :kind, :podcast, :episode, :bytes, :now)
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "id": reservation_id or uuid4(),
            "owner": owner,
            "material": material_id,
            "kind": kind,
            "podcast": podcast_id,
            "episode": episode_index,
            "bytes": max(1, int(size)),
            "now": datetime.now(UTC),
        },
    )


def release(db, *, reservation_id=None, podcast_id=None, kind=None, episode_index=None):
    if reservation_id is not None:
        db.execute(
            text("DELETE FROM storage_reservations WHERE reservation_id = :id"),
            {"id": reservation_id},
        )
        return
    db.execute(
        text(
            "DELETE FROM storage_reservations WHERE podcast_id = :podcast"
            + (" AND kind = :kind" if kind else "")
            + (" AND episode_index = :episode" if episode_index is not None else "")
        ),
        {"podcast": podcast_id, "kind": kind, "episode": episode_index},
    )


def reservation_exists(db, reservation_id) -> bool:
    return bool(
        db.scalar(
            text("SELECT 1 FROM storage_reservations WHERE reservation_id = :id"),
            {"id": reservation_id},
        )
    )


def reserve_videos(db, podcast, indexes) -> None:
    """重試、補建或手動生成影片：呼叫端已鎖 Podcast；在帳號鎖內為尚無保留的分集核對並保留影片額度。"""
    if not indexes:
        return
    lock_account(db, podcast.learner_id)
    reserved = set(
        db.scalars(
            text(
                "SELECT episode_index FROM storage_reservations "
                "WHERE podcast_id = :podcast AND kind = 'podcast_video'"
            ),
            {"podcast": podcast.podcast_id},
        ).all()
    )
    missing = [index for index in indexes if index not in reserved]
    if not missing:
        return
    estimate = episode_estimates(db)["video"]
    require_room(db, podcast.learner_id, len(missing) * estimate)
    for index in missing:
        reserve(
            db,
            podcast.learner_id,
            podcast.material_id,
            "podcast_video",
            estimate,
            podcast_id=podcast.podcast_id,
            episode_index=index,
        )


def release_upload(reservation_id, *, dsn=None) -> None:
    with database_session(dsn) as db:
        release(db, reservation_id=reservation_id)


def release_stale_upload_reservations(*, dsn=None, now=None) -> None:
    """程序中斷留下的上傳保留：超過一天的釋放（與分段上傳暫存的清除期限相同）。"""
    with database_session(dsn) as db:
        db.execute(
            text("DELETE FROM storage_reservations WHERE kind = 'upload' AND created_at < :limit"),
            {"limit": (now or datetime.now(UTC)) - UPLOAD_RESERVATION_TTL},
        )
