from __future__ import annotations

from uuid import UUID
from unicodedata import category

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .database import DatabaseConfigurationError
from .tables import Artifact, KnowledgeStructure, Material, MaterialProcessingRun, StudySession, MaterialSource, SourceNormalization, database_session


class MaterialLibraryError(RuntimeError):
    """教材庫儲存錯誤不包含 SQL、名稱或連線資訊。"""


def read_material_library(
    learner_id: UUID, *, material_id: UUID | None = None, dsn: str | None = None,
) -> list[dict]:
    """Project materials and one canonical persistent learning state per structure; never write learner data."""
    try:
        with database_session(dsn) as session:
            statement = select(
                Material.material_id, Material.source_artifact_id, Material.display_name,
                Material.created_at, Artifact.size_bytes, Material.ingestion_kind, Material.head_revision,
            ).outerjoin(Artifact, (Artifact.artifact_id == Material.source_artifact_id)
                   & (Artifact.material_id == Material.material_id)
                   & (Artifact.learner_id == Material.learner_id)).where(Material.learner_id == learner_id, Material.ingestion_kind == "sources-v2")
            if material_id is not None:
                statement = statement.where(Material.material_id == material_id)
            materials = session.execute(statement.order_by(Material.created_at.desc(), Material.material_id.desc())).mappings().all()
            if not materials:
                return []
            ids = [row["material_id"] for row in materials]
            runs = session.execute(select(
                MaterialProcessingRun.material_id, MaterialProcessingRun.run_id,
                MaterialProcessingRun.status, MaterialProcessingRun.progress_stage,
                MaterialProcessingRun.completed_pages, MaterialProcessingRun.total_pages,
                MaterialProcessingRun.error_code, MaterialProcessingRun.created_at, MaterialProcessingRun.cancel_requested_at,
                MaterialProcessingRun.base_revision,
            ).where(
                MaterialProcessingRun.learner_id == learner_id,
                MaterialProcessingRun.material_id.in_(ids),
            ).distinct(MaterialProcessingRun.material_id).order_by(
                MaterialProcessingRun.material_id, MaterialProcessingRun.created_at.desc(), MaterialProcessingRun.run_id.desc(),
            )).mappings().all()
            structures = session.execute(select(
                KnowledgeStructure.material_id, KnowledgeStructure.run_id,
                KnowledgeStructure.structure_revision.label("knowledge_structure_revision"),
                KnowledgeStructure.created_at, MaterialProcessingRun.status,
                MaterialProcessingRun.base_revision,
            ).join(MaterialProcessingRun, (MaterialProcessingRun.run_id == KnowledgeStructure.run_id)
                   & (MaterialProcessingRun.learner_id == KnowledgeStructure.learner_id)
                   & (MaterialProcessingRun.material_id == KnowledgeStructure.material_id)
            ).join(Material, (Material.material_id == KnowledgeStructure.material_id)
                   & (Material.learner_id == KnowledgeStructure.learner_id)
            ).where(
                KnowledgeStructure.learner_id == learner_id,
                KnowledgeStructure.material_id.in_(ids),
                MaterialProcessingRun.status.in_(("succeeded", "partial")),
                MaterialProcessingRun.output_binding["knowledge_structure_revision"].astext == KnowledgeStructure.structure_revision,
            ).order_by(KnowledgeStructure.created_at.desc(), KnowledgeStructure.run_id.desc())).mappings().all()
            studies = session.execute(select(
                StudySession.material_id, StudySession.study_session_id,
                StudySession.knowledge_structure_revision, StudySession.status,
                StudySession.current_concept_id, StudySession.started_at, KnowledgeStructure.run_id,
            ).join(KnowledgeStructure,
                (KnowledgeStructure.learner_id == StudySession.learner_id)
                & (KnowledgeStructure.material_id == StudySession.material_id)
                & (KnowledgeStructure.structure_revision == StudySession.knowledge_structure_revision),
            ).where(StudySession.learner_id == learner_id, StudySession.material_id.in_(ids))
              .order_by(StudySession.started_at.desc(), StudySession.study_session_id.desc())).mappings().all()
            source_rows=session.execute(select(MaterialSource,SourceNormalization,Artifact.size_bytes).join(SourceNormalization,SourceNormalization.source_id==MaterialSource.source_id).join(Artifact,Artifact.artifact_id==MaterialSource.original_artifact_id)
                .where(MaterialSource.learner_id==learner_id,MaterialSource.material_id.in_(ids)).distinct(MaterialSource.source_id).order_by(MaterialSource.source_id,SourceNormalization.created_at.desc())).all()
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise MaterialLibraryError("MATERIAL_LIBRARY_STORAGE_FAILED") from None

    latest = {row["material_id"]: {key: value for key, value in row.items() if key != "material_id"} for row in runs}
    published: dict[UUID, list[dict]] = {identity: [] for identity in ids}
    for row in structures:
        published[row["material_id"]].append({key: value for key, value in row.items() if key != "material_id"})
    sessions: dict[UUID, list[dict]] = {identity: [] for identity in ids}
    for row in studies:
        sessions[row["material_id"]].append({key: value for key, value in row.items() if key != "material_id"})
    sources={source.material_id:{"source_id":source.source_id,"normalization_id":job.normalization_id,"original_artifact_id":source.original_artifact_id,
        "original_name":source.original_name,"media_type":source.media_type,"status":job.status,"error_code":job.error_code,
        "normalized_artifact_id":job.normalized_artifact_id,"page_count":job.page_count} for source,job,_ in source_rows}
    original_sizes={identity:0 for identity in ids}
    counts={identity:0 for identity in ids}
    for source,job,size in source_rows:
        original_sizes[source.material_id]+=size
        counts[source.material_id]+=1
    return [{
        **{key:value for key,value in row.items() if key!="ingestion_kind"},
        "schema": "material-library-item/v3",
        "size_bytes":original_sizes.get(row["material_id"],row["size_bytes"] or 0),
        "source_count":counts[row["material_id"]],
        "source":sources.get(row["material_id"]),"ingestion_kind":"sources-v2",
        "display_name": row["display_name"] or f"教材 {row['created_at']:%Y-%m-%d} · {str(row['material_id'])[:8]}",
        "latest_attempt": latest.get(row["material_id"]),
        "available_structures": published[row["material_id"]],
        "study_sessions": sessions[row["material_id"]],
    } for row in materials]


def rename_material(learner_id: UUID, material_id: UUID, display_name: str, *, dsn: str | None = None) -> dict:
    """Rename only the learner-facing title; identities and source content stay immutable."""
    if not isinstance(display_name, str) or any(category(char) in {"Cc", "Cs"} for char in display_name):
        raise MaterialLibraryError("REQUEST_INVALID")
    name = display_name.strip()
    if not 1 <= len(name) <= 200:
        raise MaterialLibraryError("REQUEST_INVALID")
    try:
        with database_session(dsn) as session:
            material = session.scalar(select(Material).where(
                Material.learner_id == learner_id, Material.material_id == material_id,
            ).with_for_update())
            if material is None:
                raise MaterialLibraryError("RESOURCE_NOT_FOUND")
            if material.discard_requested_at is not None:
                raise MaterialLibraryError("MATERIAL_NOT_DISCARDABLE")
            material.display_name = name
    except (DatabaseConfigurationError, SQLAlchemyError):
        raise MaterialLibraryError("MATERIAL_LIBRARY_STORAGE_FAILED") from None
    updated = read_material_library(learner_id, material_id=material_id, dsn=dsn)
    if not updated:
        raise MaterialLibraryError("RESOURCE_NOT_FOUND")
    return updated[0]
