"""背景生成的續租、取消與 HTTP 等待；生成耗時不等於工作失聯。"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import logging
from threading import Event, Thread
from urllib.request import urlopen
from uuid import uuid4
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from .storage.tables import (
    Material,
    CardSet,
    Podcast,
    PodcastVideo,
    PodcastScenes,
    VoiceTurn,
    VoiceConversation,
    TopicScope,
    MaterialResearch,
    SourceNormalization,
)
from .storage.database import database_session
from .provider_http import provider_request
from .errors import SourceError

WORKER_LEASE_TTL = timedelta(minutes=15)


class JobCancelled(RuntimeError):
    def __init__(self):
        super().__init__("GENERATION_CANCELLED")


def lease_is_current(row, token, *, now=None):
    """續租與保存共用租約判定；狀態、擁有權及父資料由各工作交易核對。"""
    return (
        row.lease_token == token
        and row.lease_expires_at is not None
        and row.lease_expires_at > (now if now is not None else datetime.now(UTC))
    )


def _source_active(db, row):
    if isinstance(row, CardSet):
        if row.deleted_at is not None:
            return False
        material_id = row.material_id
    elif isinstance(row, (Podcast, PodcastVideo, PodcastScenes)):
        parent = row if isinstance(row, Podcast) else db.get(Podcast, row.podcast_id)
        if parent is None or parent.status in ("cancelled", "deleted"):
            return False
        material_id = parent.material_id
    elif isinstance(row, VoiceTurn):
        parent = db.get(VoiceConversation, row.conversation_id)
        if parent is None or parent.deleted_at is not None:
            return False
        material_id = parent.material_id
    elif isinstance(row, SourceNormalization):
        material_id = row.material_id
    elif isinstance(row, TopicScope):
        if row.status != "pending":
            return False
        material_id = row.material_id
        if material_id is None:
            return True
    elif isinstance(row, MaterialResearch):
        if row.deleted_at is not None:
            return False
        if row.topic_id:
            topic = db.get(TopicScope, row.topic_id)
            if topic is None or topic.status != "approved":
                return False
        material_id = row.material_id
        if material_id is None:
            return True
    else:
        raise TypeError("unsupported generation job")
    material = db.get(Material, material_id)
    return material is not None and material.discard_requested_at is None


@contextmanager
def provider_job(cancellation_check, metadata=None):
    """把來源取消傳到 provider，包含仍在等待共用資源的請求。"""
    metadata = metadata or {"job_id": str(uuid4()), "job_token": str(uuid4())}
    done = Event()
    failures = []

    def watch():
        while not done.wait(0.5):
            if not failures:
                try:
                    cancellation_check()
                    continue
                except Exception as error:
                    failures.append(error)
            try:
                try:
                    request = provider_request(
                        "/jobs/cancel", metadata,
                        unavailable=SourceError("GENERATION_PROVIDER_UNAVAILABLE"),
                    )
                except SourceError:
                    return
                with urlopen(request, timeout=5) as response:
                    response.read(4096)
                return
            except Exception:
                logging.getLogger(__name__).warning("GENERATION_CANCEL_DELIVERY_FAILED")

    watcher = Thread(target=watch, name="generation-cancel-watch", daemon=True)
    watcher.start()
    try:
        yield metadata
    finally:
        done.set()
        watcher.join(timeout=6)


def check_job(model, identity, lease_token, *, dsn, statuses=("running",)):
    try:
        with database_session(dsn) as db:
            row = db.get(model, identity)
            now = datetime.now(UTC)
            active = (
                row is not None
                and row.status in statuses
                and lease_is_current(row, lease_token, now=now)
                and _source_active(db, row)
            )
            if active and row.lease_expires_at < now + WORKER_LEASE_TTL / 2:
                renewed = db.execute(
                    update(model)
                    .where(
                        model.__mapper__.primary_key[0] == identity,
                        model.status.in_(statuses),
                        model.lease_token == lease_token,
                        model.lease_expires_at > now,
                    )
                    .values(lease_expires_at=now + WORKER_LEASE_TTL)
                )
                active = renewed.rowcount == 1
    except SQLAlchemyError:
        # 暫時無法連線不代表使用者已取消；失去租約後仍不允許復活。
        logging.getLogger(__name__).warning("GENERATION_LEASE_CHECK_FAILED")
        return
    if not active:
        raise JobCancelled()


@contextmanager
def cancellable_job(model, identity, lease_token, *, dsn, statuses=("running",)):
    def check():
        check_job(model, identity, lease_token, dsn=dsn, statuses=statuses)

    with provider_job(
        check, {"job_id": str(identity), "job_token": str(lease_token)}
    ) as metadata:
        yield metadata
