"""固定來源的分集 Podcast；每次只確認一個腳本或音訊，重試不重做已保存結果。"""

from .storage.materials import require_material, validate_concept_selection
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import math
import secrets
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, update

from content_identity import canonical_sha256
from .storage.artifacts import (
    _key_digest,
    quarantine_artifact,
    reconcile_discarded_artifacts,
    write_blob,
)
from .storage.tables import KnowledgeStructure
from .storage.knowledge_structures import (
    _prune_unreferenced_structures,
    _read_verified_document,
    _view,
)
from .storage.tables import Artifact, Material, Podcast, PodcastScenes, PodcastVideo
from .storage.database import database_session
from .podcast_script import digest, validate_script
from .podcast_access import _locked
from .podcast_sources import plan_episodes, source_context, _context_pages
from . import retrieval, luna_calls
from .errors import SourceError, PodcastError
from .audio import FLAC_MEDIA_TYPE, validate_audio, wav_to_flac
from .storage_usage import (
    episode_estimates,
    lock_account,
    release,
    require_room,
    reserve,
    reserve_videos,
)


from .generation_jobs import WORKER_LEASE_TTL, lease_is_current

# 主持人名字庫：host.wav 為女聲、guest.wav 為男聲，名字對應原聲線；單人只用 host。
HOST_PAIRS = (
    ("小晴", "阿哲"),
    ("小安", "阿凱"),
    ("小語", "阿誠"),
    ("小芸", "阿翔"),
    ("小葵", "阿峰"),
    ("小珊", "阿傑"),
)


def _pick_hosts(delivery):
    host, guest = secrets.choice(HOST_PAIRS)
    return {"host": host, "guest": guest} if delivery == "dialogue" else {"host": host}


def _child_statuses(db, podcast_ids):
    statuses = {identity: {"videos": [], "scenes": []} for identity in podcast_ids}
    if statuses:
        for model, kind in ((PodcastVideo, "videos"), (PodcastScenes, "scenes")):
            for identity, status in db.execute(
                select(model.podcast_id, model.status).where(
                    model.podcast_id.in_(statuses)
                )
            ):
                statuses[identity][kind].append(status)
    return statuses


def _summary(row, material, child_statuses):
    videos = child_statuses[row.podcast_id]["videos"]
    scenes = child_statuses[row.podcast_id]["scenes"]
    # 分鏡 worker 只接 ready 節目；失敗節目的待處理分鏡不代表仍在生成。
    active_children = videos + (scenes if row.status == "ready" else [])
    generation = {
        "active": row.status in ("pending", "running")
        or any(status in ("pending", "running") for status in active_children),
        "completed_videos": videos.count("ready"),
        "failed_videos": videos.count("failed"),
        # 預期會有影片的集數：勾選影片的節目是全部分集扣掉已刪除影片的；
        # 沒勾選的只有實際存在的影片工作（通常為 0）。
        "expected_videos": (
            len(row.episodes) if row.video_enabled else len(videos)
        )
        - videos.count("removed"),
        # 沒勾影片或影片已刪除的分集只播音訊；勾選影片的節目缺少影片工作（0020 前的舊分集）
        # 時不開放預覽，由使用者按「接續生成」補建。
        "preview_ready": row.status == "ready"
        and all(e.get("audio") for e in row.episodes)
        and all(status in ("ready", "removed") for status in videos)
        and (not row.video_enabled or len(videos) >= len(row.episodes)),
        "retryable_videos": row.status == "ready"
        and (
            any(s in ("failed", "cancelled") for s in videos)
            or (row.video_enabled and len(videos) < len(row.episodes))
        ),
    }
    return {
        "podcast_id": row.podcast_id,
        "material_id": row.material_id,
        "material_name": material.display_name,
        "name": row.name,
        "delivery": row.episodes[0]["delivery"],
        "video_enabled": row.video_enabled,
        "knowledge_structure_revision": row.knowledge_structure_revision,
        "concept_ids": row.concept_ids,
        "status": row.status,
        "error_code": row.error_code,
        "version": row.version,
        "created_at": row.created_at,
        "episode_count": len(row.episodes),
        "generation": generation,
        "completed_episodes": sum(bool(e.get("audio")) for e in row.episodes),
        # 全部分集的音訊都完成後才給總時長；生成中不顯示會變動的數字。
        "duration_seconds": sum(e["audio"]["duration_seconds"] for e in row.episodes)
        if row.status == "ready" and all(e.get("audio") for e in row.episodes) else None,
        "is_current_revision": row.knowledge_structure_revision
        == material.head_revision,
    }


def create_podcast(
    owner,
    material_id,
    revision,
    name,
    concept_ids,
    key,
    *,
    delivery,
    video=False,
    dsn=None,
):
    name = validate_concept_selection(name, concept_ids)
    if delivery not in {"solo", "dialogue"} or type(video) is not bool:
        raise PodcastError("REQUEST_INVALID")
    digest = _key_digest(key)
    selection = {
        "revision": revision,
        "name": name,
        "concept_ids": concept_ids,
        "delivery": delivery,
        "video": video,
    }
    fingerprint = bytes.fromhex(canonical_sha256(selection))
    def replay(db, material):
        row = db.scalar(
            select(Podcast).where(
                Podcast.learner_id == owner,
                Podcast.material_id == material_id,
                Podcast.idempotency_key_sha256 == digest,
            )
        )
        if row:
            if bytes(row.request_fingerprint) != fingerprint:
                raise PodcastError("IDEMPOTENCY_CONFLICT")
            if row.status == "deleted":
                raise PodcastError("RESOURCE_NOT_FOUND")
            return _summary(row, material, _child_statuses(db, [row.podcast_id]))
        return None

    # 第一段：驗證教材與版本、規劃分集（不取帳號鎖，耗時的讀取不擋同帳號的其他新增）。
    with database_session(dsn) as db:
        material = require_material(db, owner, material_id, read=True)
        if (existing := replay(db, material)) is not None:
            return existing
        document = _read_verified_document(db, owner, material_id, revision=revision)
        retrieval._scope(db, owner, material_id, revision)
        view = _view(document, material_id)
        selected = set(concept_ids)
        ids = {
            e["evidence_id"]
            for c in view["concepts"]
            if c["concept_id"] in selected
            for claim in c["claims"]
            for e in claim["evidence"]
        }
        passages = retrieval.evidence_context(db, owner, material_id, revision, ids)
        pages = _context_pages([p for p in passages if p["evidence_id"] in ids])
        episodes = plan_episodes(view, concept_ids, pages)
    # 每個 Podcast 抽一組主持人，同系列各集沿用。
    hosts = _pick_hosts(delivery)
    for episode in episodes:
        episode["delivery"] = delivery
        episode["hosts"] = dict(hosts)
    # 第二段短交易：帳號鎖 → 教材鎖，再次辨識重送與版本，核對額度後建立節目與每集固定保留。
    with database_session(dsn) as db:
        lock_account(db, owner)
        material = require_material(db, owner, material_id)
        if (existing := replay(db, material)) is not None:
            return existing
        retrieval._scope(db, owner, material_id, revision)
        estimate = episode_estimates(db)
        require_room(
            db,
            owner,
            len(episodes) * (estimate["audio"] + (estimate["video"] if video else 0)),
        )
        # 舊模式欄位保留歷史資料；新建固定儲存值，不再參與 API 或生成決策。
        row = Podcast(
            podcast_id=uuid4(),
            learner_id=owner,
            material_id=material_id,
            knowledge_structure_revision=revision,
            name=name,
            mode="quick",
            concept_ids=concept_ids,
            episodes=episodes,
            status="pending",
            version=1,
            created_at=datetime.now(UTC),
            idempotency_key_sha256=digest,
            request_fingerprint=fingerprint,
            video_enabled=video,
        )
        db.add(row)
        db.flush()
        for index in range(len(episodes)):
            for kind in ("podcast_audio", "podcast_video") if video else ("podcast_audio",):
                reserve(
                    db,
                    owner,
                    material_id,
                    kind,
                    estimate["audio" if kind == "podcast_audio" else "video"],
                    podcast_id=row.podcast_id,
                    episode_index=index,
                )
        return _summary(row, material, _child_statuses(db, [row.podcast_id]))


def list_podcasts(owner, *, dsn=None):
    with database_session(dsn) as db:
        rows = db.execute(
            select(Podcast, Material)
            .join(Material, Podcast.material_id == Material.material_id)
            .where(
                Podcast.learner_id == owner,
                Podcast.status != "deleted",
                Material.discard_requested_at.is_(None),
            )
            .order_by(Podcast.created_at.desc())
        ).all()
        statuses = _child_statuses(db, [row.podcast_id for row, _ in rows])
        return {
            "schema": "podcast-list/v1",
            "podcasts": [_summary(*row, statuses) for row in rows],
        }


def read_podcast(owner, identity, *, dsn=None):
    with database_session(dsn) as db:
        row, material = _locked(db, owner, identity, read=True)
        from .storage.knowledge_reads import metadata

        meta = metadata(db, owner, row.material_id, row.knowledge_structure_revision)
        run_id = db.scalar(
            select(KnowledgeStructure.run_id).where(
                KnowledgeStructure.learner_id == owner,
                KnowledgeStructure.material_id == row.material_id,
                KnowledgeStructure.structure_revision
                == row.knowledge_structure_revision,
            )
        )
        # 播放頁僅需來源入口與狀態，不讀整份教材或展開概念視圖。
        episodes = deepcopy(row.episodes)
        for episode in episodes:
            episode["script_sha256"] = (
                digest(episode["script"]) if episode["script"] else None
            )
        return {
            "schema": "podcast/v1",
            "run_id": str(run_id),
            **_summary(row, material, _child_statuses(db, [row.podcast_id])),
            "source_resolver": meta["source_resolver"],
            "source_status": meta["status"],
            "excluded_pages": meta["excluded_pages"],
            "episodes": episodes,
        }


def _requeue_videos(db, row):
    """接續生成（不論節目是 ready、failed 或 cancelled，規則相同）：失敗或取消的影片恢復成 pending，
    勾選影片的節目另補建已有音訊卻缺少的影片工作（0020 前的舊分集；已刪除的 removed 與沒勾選的
    節目不補）。只為這些真正排隊的工作核對並保留影片額度；呼叫端已持有帳號鎖。"""
    statuses = ("failed", "cancelled")
    from .podcast_videos import enqueue

    rows = db.scalars(
        select(PodcastVideo)
        .where(PodcastVideo.podcast_id == row.podcast_id)
        .with_for_update()
    ).all()
    retry = [video for video in rows if video.status in statuses]
    existing = {video.episode_index for video in rows}
    missing = (
        [
            index
            for index, episode in enumerate(row.episodes)
            if index not in existing and episode.get("audio") and episode.get("script")
        ]
        if row.video_enabled
        else []
    )
    reserve_videos(db, row, [video.episode_index for video in retry] + missing)
    for index in missing:
        enqueue(db, row, index)
    for video in retry:
        video = enqueue(db, row, video.episode_index)
        if video.status in statuses:
            video.status = "pending"
            video.error_code = None
            video.lease_token = video.lease_expires_at = None
            video.version += 1


def change_podcast(owner, identity, action, expected_version, name=None, *, dsn=None):
    if action == "cancel":
        # 與教材一致：取消整份生成即刪除，不留下可接續的暫停項目。
        return delete_podcast(
            owner, identity, expected_version=expected_version, dsn=dsn
        )
    with database_session(dsn) as db:
        if action == "retry":
            # 鎖順序：帳號鎖 → 教材／Podcast。
            lock_account(db, owner)
        row, material = _locked(db, owner, identity)
        if row.version != expected_version:
            raise PodcastError("PODCAST_CONFLICT")
        if action == "retry":
            retrieval._scope(
                db, owner, row.material_id, row.knowledge_structure_revision
            )
        if action == "rename":
            row.name = validate_concept_selection(name, row.concept_ids)
        elif action == "retry" and row.status == "ready":
            summary = _summary(row, material, _child_statuses(db, [row.podcast_id]))
            if not summary["generation"]["retryable_videos"]:
                raise PodcastError("PODCAST_CONFLICT")
            _requeue_videos(db, row)
        elif action == "retry" and row.status in {"failed", "cancelled"}:
            _requeue_videos(db, row)
            if row.status == "cancelled":
                db.execute(
                    update(PodcastScenes)
                    .where(
                        PodcastScenes.podcast_id == identity,
                        PodcastScenes.status == "cancelled",
                    )
                    .values(
                        status="pending",
                        error_code=None,
                        version=PodcastScenes.version + 1,
                    )
                )
            row.status = "pending"
            row.error_code = None
        else:
            raise PodcastError("PODCAST_CONFLICT")
        row.version += 1
        db.flush()
        return _summary(row, material, _child_statuses(db, [row.podcast_id]))


def delete_podcast(owner, identity, *, expected_version=None, dsn=None):
    artifacts = []
    try:
        with database_session(dsn) as db:
            row, material = _locked(db, owner, identity, allow_deleted=True)
            if expected_version is not None and row.version != expected_version:
                raise PodcastError("PODCAST_CONFLICT")
            if row.status != "deleted":
                from .podcast_videos import discard
                from .podcast_progress import forget_positions

                artifacts.extend(discard(db, identity))
                forget_positions(db, identity)
                release(db, podcast_id=identity)
                db.execute(
                    delete(PodcastScenes).where(PodcastScenes.podcast_id == identity)
                )
                for episode in row.episodes:
                    if episode.get("audio"):
                        artifact_id = UUID(episode["audio"]["artifact_id"])
                        artifacts.append(artifact_id)
                        quarantine_artifact(db, artifact_id)
                        db.delete(db.get(Artifact, artifact_id))
                row.status = "deleted"
                row.lease_token = row.lease_expires_at = None
                row.knowledge_structure_revision = None
                row.name, row.concept_ids, row.episodes = "", [], []
                row.error_code = None
                row.version += 1
                db.flush()
                _prune_unreferenced_structures(
                    db, owner, material.material_id, material.head_revision
                )
    finally:
        for artifact_id in artifacts:
            reconcile_discarded_artifacts(dsn=dsn, artifact_id=artifact_id)
    return {"schema": "podcast-deleted/v1", "podcast_id": identity}


def claim_step(*, dsn=None):
    now = datetime.now(UTC)
    with database_session(dsn) as db:
        candidates = db.execute(
            select(Podcast.podcast_id, Podcast.learner_id)
            .where(
                or_(
                    Podcast.status == "pending",
                    (Podcast.status == "running") & (Podcast.lease_expires_at < now),
                )
            )
            .order_by(Podcast.created_at)
            .limit(8)
        ).all()
        for identity, owner in candidates:
            try:
                row, _ = _locked(db, owner, identity)
            except (PodcastError, RuntimeError):
                continue
            if row.status != "pending" and not (
                row.status == "running" and row.lease_expires_at < now
            ):
                continue
            row.status, row.lease_token = "running", uuid4()
            row.lease_expires_at = now + WORKER_LEASE_TTL
            row.version += 1
            index = next(i for i, e in enumerate(row.episodes) if not e.get("audio"))
            if row.episodes[index]["script"] is None and "hosts" not in row.episodes[index]:
                # 節目化前建立、尚未寫完的 Podcast：補一組主持人給還沒有名字的集數，已完成集數不改。
                hosts = _pick_hosts(row.episodes[index]["delivery"])
                row.episodes = [
                    e
                    if "hosts" in e or e["script"] is not None
                    else {**e, "hosts": dict(hosts)}
                    for e in row.episodes
                ]
            episode = deepcopy(row.episodes[index])
            following = row.episodes[index + 1] if index + 1 < len(row.episodes) else None
            context = {}
            try:
                if episode["script"] is None:
                    context = source_context(
                        db,
                        owner,
                        row.material_id,
                        row.knowledge_structure_revision,
                        episode,
                    )
                else:
                    retrieval._scope(
                        db, owner, row.material_id, row.knowledge_structure_revision
                    )
            except (PodcastError, SourceError) as error:
                row.status = "failed"
                row.error_code = str(error)
                row.lease_token = row.lease_expires_at = None
                continue
            return {
                "podcast_id": row.podcast_id,
                "material_id": row.material_id,
                "owner": owner,
                "token": row.lease_token,
                "index": index,
                "episode": episode,
                "source_context": context,
                # 講稿開場與下集預告所需的系列位置；不送下一集的原文。
                "series": {
                    "episode_number": index + 1,
                    "episode_count": len(row.episodes),
                    "next_concepts": list(
                        dict.fromkeys(c["label"] for c in following["claims"])
                    )
                    if following
                    else [],
                },
            }
    return None


def finish_step(
    claim,
    *,
    script=None,
    title=None,
    audio=None,
    audio_provider=None,
    audio_mastering=None,
    error=None,
    dsn=None,
):
    with database_session(dsn) as db:
        try:
            row, _ = _locked(db, claim["owner"], claim["podcast_id"])
        except RuntimeError:
            return False
        if (
            row.status != "running"
            or not lease_is_current(row, claim["token"])
        ):
            return False
        episodes = deepcopy(row.episodes)
        episode = episodes[claim["index"]]
        if error:
            row.status, row.error_code = "failed", error
        else:
            if script is not None:
                episode["script"] = validate_script(script, episode)
                if title:
                    episode["title"] = title
            elif audio is not None and episode["script"]:
                if (
                    not isinstance(audio_provider, str)
                    or not audio_provider.strip()
                    or len(audio_provider) > 300
                ):
                    raise PodcastError("PODCAST_AUDIO_INVALID")
                if audio_mastering is not None:
                    try:
                        if not (
                            set(audio_mastering)
                            == {
                                "policy",
                                "integrated_lufs",
                                "true_peak_dbtp",
                                "loudness_range_lu",
                            }
                        ):
                            raise ValueError()
                        if not (audio_mastering["policy"] == "podcast-mastering/v1"):
                            raise ValueError()
                        if not (
                            all(
                                type(audio_mastering[k]) in (int, float)
                                and math.isfinite(audio_mastering[k])
                                for k in (
                                    "integrated_lufs",
                                    "true_peak_dbtp",
                                    "loudness_range_lu",
                                )
                            )
                        ):
                            raise ValueError()
                    except (ValueError, KeyError, TypeError):
                        raise PodcastError("PODCAST_AUDIO_INVALID") from None
                duration = validate_audio(audio)
                # 無損 FLAC 保存；sha256 仍記發布時的 WAV，影片與對齊的音訊身分不變。
                artifact = write_blob(
                    db,
                    row.learner_id,
                    row.material_id,
                    wav_to_flac(audio),
                    "podcast_audio",
                    FLAC_MEDIA_TYPE,
                )
                # 成品與釋放保留在同一交易：改計實際大小。
                release(
                    db,
                    podcast_id=row.podcast_id,
                    kind="podcast_audio",
                    episode_index=claim["index"],
                )
                episode["audio"] = {
                    "artifact_id": str(artifact.artifact_id),
                    "sha256": sha256(audio).hexdigest(),
                    "duration_seconds": duration,
                    "provider": audio_provider,
                    **(
                        {"mastering": audio_mastering}
                        if audio_mastering is not None
                        else {}
                    ),
                }

            else:
                raise PodcastError("PODCAST_SCRIPT_INVALID")
            row.episodes = episodes
            if audio is not None:
                if row.video_enabled:
                    from .podcast_videos import enqueue

                    enqueue(db, row, claim["index"])
                from .podcast_scenes import enqueue as enqueue_alignment

                enqueue_alignment(db, row, claim["index"])
            row.status = "ready" if all(e.get("audio") for e in episodes) else "pending"
        row.lease_token = row.lease_expires_at = None
        row.version += 1
    if script is not None or error:
        luna_calls.completed("failed" if error else "succeeded", error)
    return True
