"""帳號空間：5 GiB 是「接受新增工作」的門檻。

已用＝該帳號全部 artifacts 的 size_bytes＋`analysis/<learner>/` 目錄＋`storage_reservations` 的保留額度；
artifacts 與保留在同一個 SQL 敘述（同一快照）加總。已接受工作的實際成品（可能大於估值）、衍生檔與
補充學習匯入照常完成，計入已用並擋住之後的新增，不會中途中斷。

保留在接受工作時固定大小，生命週期（另見 docs/materials.md「帳號空間」）：
- upload：一個上傳意圖（入口＋Idempotency-Key）只有一個寫入者。寫入來源的同一交易刪除；寫入失敗時釋放；
  寫入者租約（單次上傳 10 分鐘、分段上傳每段延長 1 天）過期後由維護 worker 釋放或由同意圖請求接手。每次接手換 writer_id，發布／清理只認本次寫入者。
- podcast_audio：建立 Podcast 時每集一筆；該集音訊寫入的同一交易刪除。講稿或音訊失敗時保留到
  接續完成或刪除 Podcast，因為接續生成不再核對額度。
- podcast_video：勾選影片的 Podcast 建立時每集一筆，或重試／補建／手動生成真正排入佇列時建立；
  影片寫入、失敗、中斷或取消的同一交易刪除（之後要再生成須重新核對並保留），刪除 Podcast 時一併刪除。

同時需要帳號鎖與教材鎖的交易，一律先取帳號鎖、再鎖教材（含保留外鍵需要的教材 KEY SHARE）；
只需教材鎖的背景工作（成品寫入、刪除）不取帳號鎖，因此不會形成循環等待。帳號鎖等待逾時回可重試的
STORAGE_BUSY。卡片匯出暫存與 Podcast 匯出不落地或由維護程序清除，不計入。
"""

from __future__ import annotations

from dataclasses import dataclass
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
# 上傳寫入者租約：單次上傳在一個請求內落盤；分段上傳每收到一段就延長，與暫存清除期限相同。
SINGLE_UPLOAD_LEASE = timedelta(minutes=10)
CHUNKED_UPLOAD_LEASE = timedelta(days=1)


@dataclass(frozen=True)
class UploadLease:
    reservation_id: UUID
    writer_id: UUID


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


def upload_reservation_id(entry, owner, material_id, key) -> UUID:
    """同一上傳意圖（入口、帳號、教材、Idempotency-Key）固定的保留 ID；不同入口互不視為重送。"""
    return UUID(bytes=sha256(f"upload:{entry}:{owner}:{material_id}:{key}".encode()).digest()[:16])


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


def reserve(
    db,
    owner,
    material_id,
    kind,
    size,
    *,
    reservation_id=None,
    podcast_id=None,
    episode_index=None,
    intent=None,
    lease=None,
    writer_id=None,
):
    """建立保留；同一工作已有保留時沿用原值（不重新定價）。呼叫端已持有帳號鎖。"""
    now = datetime.now(UTC)
    db.execute(
        text(
            """
            INSERT INTO storage_reservations
              (reservation_id, learner_id, material_id, kind, podcast_id, episode_index,
               intent_sha256, expires_at, writer_id, bytes, created_at)
            VALUES (:id, :owner, :material, :kind, :podcast, :episode, :intent, :expires, :writer, :bytes, :now)
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
            "intent": intent,
            "writer": writer_id,
            "expires": now + lease if lease else None,
            "bytes": max(1, int(size)),
            "now": now,
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


def upload_reservation(db, reservation_id):
    """回傳 (intent_sha256, expires_at) 並鎖住該列；沒有時為 None。"""
    row = db.execute(
        text(
            "SELECT intent_sha256, expires_at FROM storage_reservations "
            "WHERE reservation_id = :id FOR UPDATE"
        ),
        {"id": reservation_id},
    ).first()
    return (bytes(row.intent_sha256), row.expires_at) if row else None


def require_upload_lease(db, lease):
    """發布前鎖住保留列；接手或維護清除後，舊寫入者不能再發布。發布先鎖教材；續期只鎖保留。"""
    row = db.execute(
        text("SELECT writer_id, expires_at FROM storage_reservations "
             "WHERE reservation_id = :id FOR UPDATE"),
        {"id": lease.reservation_id},
    ).first()
    if row is None or row.writer_id != lease.writer_id or row.expires_at <= datetime.now(UTC):
        raise StorageBusyError()


def settle_upload(db, lease):
    """只釋放本次寫入者的保留；遲到的失敗清理不影響接手者。"""
    db.execute(
        text("DELETE FROM storage_reservations WHERE reservation_id = :id AND writer_id = :writer"),
        {"id": lease.reservation_id, "writer": lease.writer_id},
    )


def extend_upload(db, lease, duration) -> None:
    require_upload_lease(db, lease)
    db.execute(
        text("UPDATE storage_reservations SET expires_at = :expires WHERE reservation_id = :id"),
        {"id": lease.reservation_id, "expires": datetime.now(UTC) + duration},
    )


def reserve_videos(db, podcast, indexes) -> None:
    """只為真正要排入佇列的影片工作核對並保留額度。呼叫端已依序取得帳號鎖與 Podcast／教材鎖。"""
    if not indexes:
        return
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


def release_upload(lease, *, dsn=None) -> None:
    with database_session(dsn) as db:
        settle_upload(db, lease)


def release_stale_upload_reservations(*, dsn=None, now=None) -> None:
    """寫入者租約已過期的上傳保留（程序中斷、分段上傳一天沒有新段）由維護 worker 釋放。"""
    with database_session(dsn) as db:
        db.execute(
            text("DELETE FROM storage_reservations WHERE kind = 'upload' AND expires_at < :now"),
            {"now": now or datetime.now(UTC)},
        )
