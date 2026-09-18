"""Owner-scoped material deletion, including derived learning data and its source PDF."""
from uuid import UUID

from sqlalchemy import delete, func, select

from .material_processing import _request_cancellation_locked
from .storage.artifacts import quarantine_source_pdf, reconcile_discarded_sources
from .storage.tables import AnswerEvent, Assessment, Artifact, KnowledgeStructure, Material, MaterialProcessingRun, StudySession, SourceNormalization, MaterialSource, MaterialSourceSet, MaterialSourceSetItem, database_session


class MaterialDiscardError(RuntimeError):
    """Fixed reasons only; never expose database or filesystem details."""


def _locked_runs(session, material):
    return session.scalars(select(MaterialProcessingRun).where(
        MaterialProcessingRun.learner_id == material.learner_id,
        MaterialProcessingRun.material_id == material.material_id,
    ).order_by(MaterialProcessingRun.run_id).with_for_update()).all()


def request_material_discard(learner_id: UUID, material_id: UUID, *, dsn: str | None = None) -> str:
    try:
        with database_session(dsn) as session:
            material = session.scalar(select(Material).where(Material.material_id == material_id, Material.learner_id == learner_id).with_for_update())
            if material is None:
                raise MaterialDiscardError("RESOURCE_NOT_FOUND")
            runs = _locked_runs(session, material)
            if material.discard_requested_at is None:
                material.discard_requested_at = session.scalar(select(func.clock_timestamp()))
            for row in runs:
                _request_cancellation_locked(material, row, session)
        # Cancellation 的 transaction 必須先 commit；purge 再重新確認所有 runs。
        return "removed" if purge_discarded_material(learner_id, material_id, dsn=dsn) else "removing"
    except MaterialDiscardError:
        raise
    except Exception:
        raise MaterialDiscardError("MATERIAL_DISCARD_STORAGE_FAILED") from None


def purge_discarded_material(learner_id: UUID, material_id: UUID, *, dsn: str | None = None) -> bool:
    artifact_ids = []
    try:
        with database_session(dsn) as session:
            material = session.scalar(select(Material).where(Material.material_id == material_id, Material.learner_id == learner_id).with_for_update())
            if material is None:
                return True
            if material.discard_requested_at is None:
                return False
            runs = _locked_runs(session, material)
            if any(row.status in {"pending", "running"} for row in runs):
                return False
            # Lock parents before enumerating children so concurrent ensure/assessment/answer
            # transactions finish before purge; later writers cannot create orphan descendants.
            session.scalars(select(KnowledgeStructure).where(
                KnowledgeStructure.learner_id == learner_id, KnowledgeStructure.material_id == material_id,
            ).order_by(KnowledgeStructure.structure_revision).with_for_update()).all()
            study_ids = session.scalars(select(StudySession.study_session_id).where(
                StudySession.learner_id == learner_id, StudySession.material_id == material_id,
            ).order_by(StudySession.study_session_id).with_for_update()).all()
            normalizations=session.scalars(select(SourceNormalization).where(SourceNormalization.learner_id==learner_id,SourceNormalization.material_id==material_id).with_for_update()).all()
            now=session.scalar(select(func.clock_timestamp()))
            if any(n.status=="running" and n.lease_expires_at>now for n in normalizations):return False
            artifacts=session.scalars(select(Artifact).where(Artifact.learner_id==learner_id,Artifact.material_id==material_id).order_by(Artifact.artifact_id).with_for_update()).all()
            for artifact in artifacts:
                artifact_ids.append(artifact.artifact_id)
                quarantine_source_pdf(session,artifact.artifact_id)
            material.head_revision=None
            session.flush()
            session.execute(delete(AnswerEvent).where(AnswerEvent.study_session_id.in_(study_ids), AnswerEvent.material_id == material_id))
            session.execute(delete(Assessment).where(Assessment.study_session_id.in_(study_ids)))
            session.execute(delete(StudySession).where(StudySession.learner_id == learner_id, StudySession.material_id == material_id))
            session.execute(delete(KnowledgeStructure).where(KnowledgeStructure.learner_id == learner_id, KnowledgeStructure.material_id == material_id))
            session.execute(delete(MaterialProcessingRun).where(MaterialProcessingRun.learner_id == learner_id, MaterialProcessingRun.material_id == material_id))
            for table in (MaterialSourceSetItem,MaterialSourceSet,SourceNormalization,MaterialSource):
                session.execute(delete(table).where(table.learner_id==learner_id,table.material_id==material_id))
            session.execute(delete(Artifact).where(Artifact.learner_id == learner_id, Artifact.material_id == material_id))
            session.execute(delete(Material).where(Material.learner_id == learner_id, Material.material_id == material_id))
        for artifact_id in artifact_ids:reconcile_discarded_sources(dsn=dsn, artifact_id=artifact_id)
        return True
    except Exception as error:
        # 包含 commit 結果不明的連線錯誤；由新 transaction 查 DB authority 決定還原或 unlink。
        for artifact_id in artifact_ids:
            reconcile_discarded_sources(dsn=dsn, artifact_id=artifact_id)
        if isinstance(error, MaterialDiscardError):
            raise
        raise MaterialDiscardError("MATERIAL_DISCARD_STORAGE_FAILED") from None


def finish_material_discards(*, dsn: str | None = None) -> None:
    """Startup / worker retry: persisted intent and quarantine are the only recovery records."""
    reconcile_discarded_sources(dsn=dsn)
    with database_session(dsn) as session:
        identities = session.execute(select(Material.learner_id, Material.material_id).where(Material.discard_requested_at.is_not(None))).all()
    for learner_id, material_id in identities:
        purge_discarded_material(learner_id, material_id, dsn=dsn)
