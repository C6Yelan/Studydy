"""固定來源的分集 Podcast；每次只確認一個腳本或音訊，重試不重做已保存結果。"""

from .storage.materials import require_material, validate_concept_selection
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import math
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
from .storage.tables import (
    Artifact,
    Material,
    Podcast,
    PodcastScenes,
    PodcastVideo,
    database_session,
)
from .podcast_script import digest, validate_script
from .podcast_sources import plan_episodes, source_context, _context_pages
from . import retrieval
from .errors import SourceError, PodcastError
from .audio import validate_audio


from .generation_jobs import WORKER_LEASE_TTL


def _summary(row, material, db):
    videos = list(
        db.scalars(
            select(PodcastVideo.status).where(PodcastVideo.podcast_id == row.podcast_id)
        )
    )
    scenes = list(
        db.scalars(
            select(PodcastScenes.status).where(
                PodcastScenes.podcast_id == row.podcast_id
            )
        )
    )
    # 分鏡 worker 只接 ready 節目；失敗節目的待處理分鏡不代表仍在生成。
    active_children = videos + (scenes if row.status == "ready" else [])
    generation = {
        "active": row.status in ("pending", "running")
        or any(status in ("pending", "running") for status in active_children),
        "completed_videos": videos.count("ready"),
        "failed_videos": videos.count("failed"),
        "preview_ready": row.status == "ready"
        and all(e.get("audio") for e in row.episodes)
        and videos.count("ready") == len(row.episodes),
        "retryable_videos": row.status == "ready"
        and (
            len(videos) < len(row.episodes)
            or any(s in ("failed", "cancelled") for s in videos)
        ),
    }
    return {
        "podcast_id": row.podcast_id,
        "material_id": row.material_id,
        "material_name": material.display_name,
        "name": row.name,
        "delivery": row.episodes[0]["delivery"],
        "knowledge_structure_revision": row.knowledge_structure_revision,
        "concept_ids": row.concept_ids,
        "status": row.status,
        "error_code": row.error_code,
        "version": row.version,
        "created_at": row.created_at,
        "episode_count": len(row.episodes),
        "generation": generation,
        "completed_episodes": sum(bool(e.get("audio")) for e in row.episodes),
        "is_current_revision": row.knowledge_structure_revision
        == material.head_revision,
    }


def create_podcast(
    owner, material_id, revision, name, concept_ids, key, *, delivery, dsn=None
):
    name = validate_concept_selection(name, concept_ids)
    if delivery not in {"solo", "dialogue"}:
        raise PodcastError("REQUEST_INVALID")
    digest = _key_digest(key)
    selection = {
        "revision": revision,
        "name": name,
        "concept_ids": concept_ids,
        "delivery": delivery,
    }
    fingerprint = bytes.fromhex(canonical_sha256(selection))
    with database_session(dsn) as db:
        material = require_material(db, owner, material_id)
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
            return _summary(row, material, db)
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
        for episode in episodes:
            episode["delivery"] = delivery
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
        )
        db.add(row)
        db.flush()
        return _summary(row, material, db)


def _locked(db, owner, identity, *, allow_deleted=False, read=False):
    material_id = db.scalar(
        select(Podcast.material_id).where(
            Podcast.podcast_id == identity, Podcast.learner_id == owner
        )
    )
    if material_id is None:
        raise PodcastError("RESOURCE_NOT_FOUND")
    if read:
        # 固定 revision 的唯讀驗證可能耗時；不持有教材鎖阻擋生成結果保存。
        material = db.scalar(
            select(Material).where(
                Material.material_id == material_id,
                Material.learner_id == owner,
                Material.discard_requested_at.is_(None),
            )
        )
        if material is None:
            raise PodcastError("RESOURCE_NOT_FOUND")
    else:
        material = require_material(db, owner, material_id)
    row = db.get(Podcast, identity, populate_existing=True)
    if row is None or (row.status == "deleted" and not allow_deleted):
        raise PodcastError("RESOURCE_NOT_FOUND")
    return row, material


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
        return {
            "schema": "podcast-list/v1",
            "podcasts": [_summary(*row, db) for row in rows],
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
            **_summary(row, material, db),
            "source_resolver": meta["source_resolver"],
            "source_status": meta["status"],
            "excluded_pages": meta["excluded_pages"],
            "episodes": episodes,
        }


def change_podcast(owner, identity, action, expected_version, name=None, *, dsn=None):
    if action == "cancel":
        # 與教材一致：取消整份生成即刪除，不留下可接續的暫停項目。
        return delete_podcast(
            owner, identity, expected_version=expected_version, dsn=dsn
        )
    with database_session(dsn) as db:
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
            from .podcast_videos import enqueue

            if not _summary(row, material, db)["generation"]["retryable_videos"]:
                raise PodcastError("PODCAST_CONFLICT")
            for index in range(len(row.episodes)):
                video = enqueue(db, row, index)
                if video.status in ("failed", "cancelled"):
                    video.status = "pending"
                    video.error_code = None
                    video.lease_token = video.lease_expires_at = None
                    video.version += 1
        elif action == "retry" and row.status in {"failed", "cancelled"}:
            if row.status == "cancelled":
                for child in (PodcastVideo, PodcastScenes):
                    db.execute(
                        update(child)
                        .where(
                            child.podcast_id == identity, child.status == "cancelled"
                        )
                        .values(
                            status="pending", error_code=None, version=child.version + 1
                        )
                    )
            row.status = "pending"
            row.error_code = None
        else:
            raise PodcastError("PODCAST_CONFLICT")
        row.version += 1
        db.flush()
        return _summary(row, material, db)


def delete_podcast(owner, identity, *, expected_version=None, dsn=None):
    artifacts = []
    try:
        with database_session(dsn) as db:
            row, material = _locked(db, owner, identity, allow_deleted=True)
            if expected_version is not None and row.version != expected_version:
                raise PodcastError("PODCAST_CONFLICT")
            if row.status != "deleted":
                from .podcast_videos import discard

                artifacts.extend(discard(db, identity))
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
            episode = deepcopy(row.episodes[index])
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
                "owner": owner,
                "token": row.lease_token,
                "index": index,
                "episode": episode,
                "source_context": context,
            }
    return None


def finish_step(
    claim,
    *,
    script=None,
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
            or row.lease_token != claim["token"]
            or row.lease_expires_at <= datetime.now(UTC)
        ):
            return False
        episodes = deepcopy(row.episodes)
        episode = episodes[claim["index"]]
        if error:
            row.status, row.error_code = "failed", error
        else:
            if script is not None:
                episode["script"] = validate_script(script, episode)
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
                artifact = write_blob(
                    db,
                    row.learner_id,
                    row.material_id,
                    audio,
                    "podcast_audio",
                    "audio/wav",
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
                from .podcast_videos import enqueue

                enqueue(db, row, claim["index"])
                from .podcast_scenes import enqueue as enqueue_alignment

                enqueue_alignment(db, row, claim["index"])
            row.status = "ready" if all(e.get("audio") for e in episodes) else "pending"
        row.lease_token = row.lease_expires_at = None
        row.version += 1
        return True
