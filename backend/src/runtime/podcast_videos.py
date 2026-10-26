"""建立時勾選影片的 Podcast 在每集音訊完成後自動排隊影片；來源失效、取消或刪除後，晚到結果不得發布。"""

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4
import base64
import json
import logging
import math
from urllib.error import HTTPError

from sqlalchemy import delete, select, text
from content_identity import canonical_sha256
from .podcast_access import _locked
from .podcast_video_plan import (
    POLICY,
    SCHEMAS,
    page_windows,
    timeline_for_video,
    validate_plan,
    validate_visual_timing,
)
from .errors import SourceError
from .generation_jobs import WORKER_LEASE_TTL, cancellable_job, lease_is_current
from .audio import episode_wav
from .podcast_sources import source_context
from .podcast_script import digest
from .provider_http import generation_response, provider_request, provider_error_code
from . import luna_calls
from .storage.artifacts import (
    quarantine_artifact,
    reconcile_discarded_artifacts,
    write_blob,
)
from .storage_usage import lock_account, release, reserve_videos
from .storage.tables import Artifact, Material, Podcast, PodcastVideo as Video
from .storage.database import database_session

WORK_LOCK = 851_048_188_100_421_213


def identity(podcast, episode):
    return canonical_sha256(
        {
            "revision": podcast.knowledge_structure_revision,
            "audio": episode["audio"],
            "script": episode["script"],
            "claims": episode["claims"],
        }
    )


def _episode(podcast, index):
    if not 0 <= index < len(podcast.episodes):
        raise SourceError("RESOURCE_NOT_FOUND")
    return podcast.episodes[index]


def _row(db, podcast_id, index):
    return db.scalar(
        select(Video)
        .where(Video.podcast_id == podcast_id, Video.episode_index == index)
        .with_for_update()
    )


def enqueue(db, podcast, index):
    episode = _episode(podcast, index)
    if not episode["audio"] or not episode["script"]:
        raise SourceError("SOURCE_NOT_READY")
    row = _row(db, podcast.podcast_id, index)
    if row:
        if row.source_sha256 != identity(podcast, episode):
            if row.status != "cancelled":
                raise SourceError("VIDEO_SOURCE_CHANGED")
            # 已取消的舊工作可隨修正版音訊重建；清除舊租約，拒收遲到結果。
            row.source_sha256 = identity(podcast, episode)
            row.artifact_id = row.manifest = row.error_code = None
            row.lease_token = row.lease_expires_at = None
            row.status = "pending"
            row.version += 1
        return row
    row = Video(
        video_id=uuid4(),
        podcast_id=podcast.podcast_id,
        episode_index=index,
        source_sha256=identity(podcast, episode),
        status="pending",
        version=1,
        created_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def _view(podcast, index, row):
    episode = _episode(podcast, index)
    current = bool(
        row and episode["audio"] and row.source_sha256 == identity(podcast, episode)
    )
    status = (
        row.status
        if current
        else ("unprepared" if episode["audio"] else "waiting_audio")
    )
    result = {
        "schema": "podcast-video/v1",
        "podcast_id": podcast.podcast_id,
        "episode_index": index,
        "status": status,
        "version": row.version if row else 0,
        "error_code": row.error_code
        if current
        else ("VIDEO_SOURCE_CHANGED" if row else None),
        "video": None,
    }
    if status == "ready":
        manifest = row.manifest
        result["video"] = {
            key: deepcopy(manifest[key])
            for key in (
                "audio_sha256",
                "script_sha256",
                "width",
                "height",
                "fps",
                "duration",
                "pages",
            )
        }
        result["video"].update(sha256=manifest["sha256"], artifact_id=row.artifact_id)
    return result


def read(owner, podcast_id, index, *, dsn=None):
    with database_session(dsn) as db:
        podcast, _ = _locked(db, owner, podcast_id, read=True)
        return _view(
            podcast,
            index,
            db.scalar(
                select(Video).where(
                    Video.podcast_id == podcast_id, Video.episode_index == index
                )
            ),
        )


def prepare(owner, podcast_id, index, *, dsn=None):
    with database_session(dsn) as db:
        # 鎖順序：帳號鎖 → 教材／Podcast。
        lock_account(db, owner)
        podcast, _ = _locked(db, owner, podcast_id)
        episode = _episode(podcast, index)
        row = _row(db, podcast_id, index)
        # 只有會真正排入佇列的情況才保留：新建工作，或來源已變的取消工作被重建。
        # 同來源的 cancelled／failed 不會因 prepare 排隊（要用重試），不占額度。
        if episode["audio"] and episode["script"] and (
            row is None
            or (row.status == "cancelled" and row.source_sha256 != identity(podcast, episode))
        ):
            reserve_videos(db, podcast, [index])
        return _view(podcast, index, enqueue(db, podcast, index))


def action(owner, podcast_id, index, action, version, *, dsn=None):
    with database_session(dsn) as db:
        if action == "retry":
            # 鎖順序：帳號鎖 → 教材／Podcast。
            lock_account(db, owner)
        podcast, _ = _locked(db, owner, podcast_id)
        episode = _episode(podcast, index)
        row = _row(db, podcast_id, index)
        if not row or row.version != version:
            raise SourceError("VIDEO_CONFLICT")
        if action == "cancel" and row.status in ("pending", "running"):
            row.status = "cancelled"
            release(db, podcast_id=podcast_id, kind="podcast_video", episode_index=index)
        elif (
            action == "retry"
            and row.status in ("failed", "cancelled")
            and episode["audio"]
            and row.source_sha256 == identity(podcast, episode)
        ):
            reserve_videos(db, podcast, [index])
            row.status = "pending"
            row.error_code = None
        else:
            raise SourceError("VIDEO_CONFLICT")
        row.lease_token = row.lease_expires_at = None
        row.version += 1
        return _view(podcast, index, row)


def ready_manifest(owner, podcast_id, index, *, dsn=None):
    with database_session(dsn) as db:
        podcast, _ = _locked(db, owner, podcast_id, read=True)
        row = db.scalar(
            select(Video).where(
                Video.podcast_id == podcast_id, Video.episode_index == index
            )
        )
        view = _view(podcast, index, row)
        if view["status"] != "ready":
            return None
        return {"artifact_id": row.artifact_id, **deepcopy(row.manifest)}


def remove(owner, podcast_id, index, *, dsn=None):
    """只刪除本集已完成的影片 artifact 與檔案；音訊、分鏡對齊與收聽進度不受影響。

    manifest 保留為 removed：舊版講稿的逐字稿時間軸只存在影片 manifest，刪影片不能讓它失效。
    removed 不會被接續生成或自動排隊重建。
    """
    artifact_id = None
    try:
        with database_session(dsn) as db:
            podcast, _ = _locked(db, owner, podcast_id)
            _episode(podcast, index)
            row = _row(db, podcast_id, index)
            if not row or row.status != "ready":
                raise SourceError("VIDEO_CONFLICT")
            artifact_id = row.artifact_id
            row.status = "removed"
            row.artifact_id = None
            row.version += 1
            db.flush()
            quarantine_artifact(db, artifact_id)
            db.delete(db.get(Artifact, artifact_id))
            db.flush()
            return _view(podcast, index, row)
    finally:
        if artifact_id is not None:
            reconcile_discarded_artifacts(dsn=dsn, artifact_id=artifact_id)


def timeline_manifest(owner, podcast_id, index, *, dsn=None):
    """時間軸可沿用已完成或已刪除影片的 manifest（與目前音訊、講稿一致時）。"""
    with database_session(dsn) as db:
        podcast, _ = _locked(db, owner, podcast_id, read=True)
        row = db.scalar(
            select(Video).where(
                Video.podcast_id == podcast_id, Video.episode_index == index
            )
        )
        if _view(podcast, index, row)["status"] not in ("ready", "removed"):
            return None
        return deepcopy(row.manifest)


def discard(db, podcast_id):
    rows = db.scalars(
        select(Video).where(Video.podcast_id == podcast_id).with_for_update()
    ).all()
    artifacts = [row.artifact_id for row in rows if row.artifact_id]
    db.execute(delete(Video).where(Video.podcast_id == podcast_id))
    db.flush()
    for artifact_id in artifacts:
        quarantine_artifact(db, artifact_id)
        db.delete(db.get(Artifact, artifact_id))
    return artifacts


def claim(*, dsn=None):
    with database_session(dsn) as db:
        if not db.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": WORK_LOCK}
        ):
            return None
        now = datetime.now(UTC)
        # 多個 API 程序共用同一條影片工作列，不同時占用 CPU 或重複呼叫模型。
        if db.scalar(
            select(Video.video_id)
            .where(Video.status == "running", Video.lease_expires_at > now)
            .limit(1)
        ):
            return None
        candidate = db.execute(
            select(Video.video_id, Podcast.learner_id, Podcast.podcast_id)
            .join(Podcast, Podcast.podcast_id == Video.podcast_id)
            .join(Material, Material.material_id == Podcast.material_id)
            .where(
                Video.status.in_(("pending", "running")),
                Podcast.status != "deleted",
                Material.discard_requested_at.is_(None),
            )
            .order_by(Video.created_at, Video.episode_index)
            .limit(1)
        ).first()
        if not candidate:
            return None
        try:
            podcast, _ = _locked(
                db, candidate.learner_id, candidate.podcast_id
            )
        except RuntimeError:
            return None
        row = db.get(Video, candidate.video_id, populate_existing=True)
        if not row or row.status not in ("pending", "running"):
            return None
        if row.status == "running":
            # 回應不明的模型請求不自動重播；由使用者決定重試。
            row.status = "failed"
            row.error_code = "VIDEO_INTERRUPTED"
            row.lease_token = row.lease_expires_at = None
            row.version += 1
            release(db, podcast_id=row.podcast_id, kind="podcast_video", episode_index=row.episode_index)
            return None
        episode = deepcopy(_episode(podcast, row.episode_index))
        if row.source_sha256 != identity(podcast, episode):
            row.status = "failed"
            row.error_code = "VIDEO_SOURCE_CHANGED"
            row.version += 1
            release(db, podcast_id=row.podcast_id, kind="podcast_video", episode_index=row.episode_index)
            return None
        row.status = "running"
        row.lease_token = uuid4()
        row.lease_expires_at = now + WORKER_LEASE_TTL
        row.version += 1
        return {
            "video_id": row.video_id,
            "material_id": podcast.material_id,
            "podcast_id": row.podcast_id,
            "index": row.episode_index,
            "token": row.lease_token,
            "owner": podcast.learner_id,
            "episode": episode,
            "source_sha256": row.source_sha256,
            "source_context": source_context(
                db,
                podcast.learner_id,
                podcast.material_id,
                podcast.knowledge_structure_revision,
                episode,
            ),
            "source_resolver": f"/v1/materials/{podcast.material_id}/knowledge-structures/{podcast.knowledge_structure_revision}/evidence",
        }


def validate_bundle(state, bundle):
    try:
        episode = state["episode"]
        metadata = bundle["render"]
        if (
            bundle["policy"] != POLICY
            or not isinstance(bundle["model"], str)
            or not bundle["model"]
        ):
            raise SourceError("VIDEO_RESULT_INVALID")
        raw = base64.b64decode(bundle["video"], validate=True)
        if (
            not 32 < len(raw) <= 100 * 1024 * 1024
            or raw[4:8] != b"ftyp"
            or sha256(raw).hexdigest() != metadata["sha256"]
            or (metadata["width"], metadata["height"], metadata["fps"])
            != (1920, 1080, 60)
            or type(metadata["duration"]) not in (int, float)
            or not math.isfinite(metadata["duration"])
            or abs(metadata["duration"] - episode["audio"]["duration_seconds"]) > 0.05
        ):
            raise SourceError("VIDEO_RESULT_INVALID")
        if (
            not isinstance(bundle["plan"], dict)
            or bundle["plan"].get("schema") not in SCHEMAS
        ):
            raise SourceError("VIDEO_RESULT_INVALID")
        plan = validate_plan(bundle["plan"], bundle["cues"])
        timeline = timeline_for_video(
            state["podcast_id"],
            state["index"],
            episode,
            bundle["cues"],
            bundle["alignment"],
            state["source_resolver"],
        )
        validate_visual_timing(plan, timeline)
        manifest = {
            **metadata,
            "audio_sha256": episode["audio"]["sha256"],
            "script_sha256": digest(episode["script"]),
            "policy": bundle["policy"],
            "model": bundle["model"],
            "timeline": timeline,
            "storyboard": plan,
            "pages": [
                {"title": p["title"], "start": start, "end": end}
                for p, (start, end) in zip(plan["pages"], page_windows(timeline, plan))
            ],
        }
        if "caption_alignment" in bundle["alignment"]:
            manifest["caption_alignment"] = deepcopy(
                bundle["alignment"]["caption_alignment"]
            )
        return raw, manifest
    except (KeyError, TypeError, ValueError) as error:
        code = str(error).split(":", 1)[0]
        raise SourceError(
            code if code.startswith("VIDEO_") else "VIDEO_RESULT_INVALID"
        ) from None


def finish(state, *, bundle=None, error=None, dsn=None):
    with database_session(dsn) as db:
        try:
            podcast, _ = _locked(db, state["owner"], state["podcast_id"])
        except RuntimeError:
            return False
        row = db.get(Video, state["video_id"], populate_existing=True)
        if (
            not row
            or row.status != "running"
            or not lease_is_current(row, state["token"])
        ):
            return False
        if row.source_sha256 != identity(podcast, _episode(podcast, state["index"])):
            error = "VIDEO_SOURCE_CHANGED"
        if error:
            row.status = "failed"
            row.error_code = error
        else:
            raw, manifest = validate_bundle(state, bundle)
            artifact = write_blob(
                db,
                podcast.learner_id,
                podcast.material_id,
                raw,
                "podcast_video",
                "video/mp4",
            )
            row.artifact_id = artifact.artifact_id
            row.manifest = manifest
            row.status = "ready"
            row.error_code = None
        # 成品寫入或失敗都在同一交易釋放保留；重試時重新核對並保留。
        release(db, podcast_id=row.podcast_id, kind="podcast_video", episode_index=row.episode_index)
        row.lease_token = row.lease_expires_at = None
        row.version += 1
    luna_calls.completed("failed" if error else "succeeded", error)
    return True


def provider(state, audio):
    body = {key: state[key] for key in ("episode", "source_context", "source_resolver")}
    body.update(
        podcast_id=str(state["podcast_id"]),
        episode_index=state["index"],
        audio=base64.b64encode(audio).decode(),
        job_id=str(state["video_id"]),
        job_token=str(state["token"]),
    )
    request = provider_request(
        "/video", body, unavailable=SourceError("VIDEO_PROVIDER_UNAVAILABLE")
    )
    try:
        with generation_response(request) as response:
            raw = response.read(140 * 1024 * 1024 + 1)
    except HTTPError as error:
        code = provider_error_code(error)
        allowed = {
            "VIDEO_AUDIO_INVALID",
            "VIDEO_CANCELLED",
            "VIDEO_SOURCE_CHANGED",
            "VIDEO_DISK_SPACE_LOW",
            "VIDEO_TRANSCRIPT_INVALID",
            "VIDEO_STORYBOARD_INVALID",
            "VIDEO_LAYOUT_INVALID",
            "VIDEO_STORYBOARD_NEEDS_REVIEW",
            "VIDEO_RENDER_FAILED",
            "VIDEO_TOO_LARGE",
            "VIDEO_ALIGNMENT_INVALID",
            "VIDEO_PROVIDER_UNAVAILABLE",
            "SCENE_ALIGNMENT_FAILED",
            "LUNA_GENERATION_FAILED",
            "LUNA_GENERATION_TIMEOUT",
            "LUNA_CALL_LIMIT_EXCEEDED",
        }
        raise SourceError(
            code if code in allowed else "VIDEO_PROVIDER_FAILED"
        ) from None
    if len(raw) > 140 * 1024 * 1024:
        raise SourceError("VIDEO_RESULT_INVALID")
    result = json.loads(raw)
    # 影片 bytes 不作候選重複保存；模型分鏡在 provider 私人記憶體中等待終態。
    luna_calls.capture({key: value for key, value in result.items() if key not in {"video", "audio"}})
    return result


def step(*, dsn=None):
    state = claim(dsn=dsn)
    if state is None:
        return False
    trace = luna_calls.start("podcast_video", state["video_id"], state["token"],
        state["material_id"], state["owner"])
    saving = False
    try:
        with cancellable_job(Video, state["video_id"], state["token"], dsn=dsn):
            audio = episode_wav(state["owner"], state["episode"], dsn=dsn)
            bundle = provider(state, audio)
            saving = True
            finish(state, bundle=bundle, dsn=dsn)
    except Exception as error:
        luna_calls.rejected(str(error))
        if isinstance(error, (SourceError, ValueError)) and str(error).startswith(
            ("VIDEO_", "SCENE_", "LUNA_")
        ):
            code = str(error).split(":", 1)[0]
        else:
            code = "VIDEO_STORAGE_FAILED" if saving else "VIDEO_PROVIDER_FAILED"
        try:
            finish(state, error=code, dsn=dsn)
        except Exception:
            logging.getLogger(__name__).warning("VIDEO_RESULT_SAVE_FAILED")
    finally:
        luna_calls.close(trace)
    return True
