from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_map.artifacts import build_knowledge_map_view
from learning_state.assessment import canonical_sha256, validate_assessment
from learning_state.learning_state import validate_initial_learning_path
from pdf_evidence.study_material_output import validate_study_material_output

from .tables import (
    Assessment,
    KnowledgeMap,
    LearningPath,
    StudyMaterialOutput,
    database_session,
)


class DomainRevisionError(RuntimeError):
    """Domain revision 操作失敗且不揭露內容或資料庫細節。"""


@dataclass(frozen=True)
class DevelopmentBundle:
    """Server-only bundle；repr 不展開 domain documents。"""

    output_revision: str
    map_revision: str
    path_revision: str
    assessment_view_id: str
    study_material_output: dict[str, Any] = field(repr=False)
    knowledge_map: dict[str, Any] = field(repr=False)
    learning_path: dict[str, Any] = field(repr=False)
    knowledge_map_view: dict[str, Any] = field(repr=False)
    assessment_view: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class AssessmentScoringBundle:
    """只供 server scoring 使用的 exact Assessment bundle。"""

    learner_id: UUID
    material_id: UUID
    map_revision: str
    path_revision: str
    assessment_revision: str
    knowledge_map: dict[str, Any] = field(repr=False)
    learning_path: dict[str, Any] = field(repr=False)
    assessment: dict[str, Any] = field(repr=False)


def _reconstruct_assessment(
    assessment_revision: str,
    public_document: Any,
    answer_key_document: Any,
) -> dict[str, Any]:
    public_fields = {
        "schema",
        "assessment_view_id",
        "version",
        "knowledge_map_revision",
        "learning_path_revision",
        "scoring_rule_version",
        "questions",
        "practice_sets",
        "processing",
        "quality",
        "decision",
        "reason_code",
    }
    key_fields = {"schema", "assessment_id", "answer_keys"}
    if not isinstance(public_document, dict) or set(public_document) != public_fields:
        raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE")
    if (
        public_document["schema"] != "assessment-view/v1"
        or not isinstance(answer_key_document, dict)
        or set(answer_key_document) != key_fields
        or answer_key_document["schema"] != "assessment-answer-key/v1"
        or not isinstance(public_document["questions"], list)
        or not isinstance(answer_key_document["answer_keys"], list)
    ):
        raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE")
    public_content = {
        key: value
        for key, value in public_document.items()
        if key != "assessment_view_id"
    }
    digest = canonical_sha256(public_content)
    if public_document["assessment_view_id"] != f"assessment-view:sha256:{digest}":
        raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE")
    keys = answer_key_document["answer_keys"]
    if keys != sorted(keys, key=lambda item: item.get("question_id", "")):
        raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE")
    if any(
        not isinstance(item, dict)
        or set(item) != {"question_id", "answer_key_option_id"}
        for item in keys
    ):
        raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE")
    answer_by_question = {
        item["question_id"]: item["answer_key_option_id"] for item in keys
    }
    if len(answer_by_question) != len(keys):
        raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE")
    questions = []
    for question in public_document["questions"]:
        if (
            not isinstance(question, dict)
            or set(question)
            != {
                "question_id",
                "concept_id",
                "question_type",
                "prompt",
                "options",
                "source_evidence_ids",
            }
            or question["question_id"] not in answer_by_question
        ):
            raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE")
        questions.append(
            {
                **deepcopy(question),
                "answer_key_option_id": answer_by_question[question["question_id"]],
            }
        )
    if len(questions) != len(keys):
        raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE")
    return {
        "schema": "assessment/v1",
        "assessment_id": answer_key_document["assessment_id"],
        "version": public_document["version"],
        "revision": assessment_revision,
        "knowledge_map_revision": public_document["knowledge_map_revision"],
        "learning_path_revision": public_document["learning_path_revision"],
        "scoring_rule_version": public_document["scoring_rule_version"],
        "questions": questions,
        "practice_sets": deepcopy(public_document["practice_sets"]),
        "processing": public_document["processing"],
        "quality": public_document["quality"],
        "decision": public_document["decision"],
        "reason_code": public_document["reason_code"],
    }


def _read_rows(
    session: Session,
    learner_id: UUID,
    material_id: UUID,
    output_revision: str,
    map_revision: str,
    path_revision: str,
    assessment_revision: str,
) -> tuple[Any, Any, Any, Any] | None:
    output_row = session.execute(
        select(StudyMaterialOutput.document).where(
            StudyMaterialOutput.learner_id == learner_id,
            StudyMaterialOutput.material_id == material_id,
            StudyMaterialOutput.output_revision == output_revision,
        )
    ).one_or_none()
    map_row = session.execute(
        select(KnowledgeMap.source_output_revision, KnowledgeMap.document).where(
            KnowledgeMap.learner_id == learner_id,
            KnowledgeMap.material_id == material_id,
            KnowledgeMap.map_revision == map_revision,
        )
    ).one_or_none()
    path_row = session.execute(
        select(LearningPath.map_revision, LearningPath.document).where(
            LearningPath.learner_id == learner_id,
            LearningPath.material_id == material_id,
            LearningPath.path_revision == path_revision,
        )
    ).one_or_none()
    assessment_row = session.execute(
        select(
            Assessment.map_revision,
            Assessment.path_revision,
            Assessment.public_document,
            Assessment.answer_key_document,
        ).where(
            Assessment.learner_id == learner_id,
            Assessment.material_id == material_id,
            Assessment.assessment_revision == assessment_revision,
        )
    ).one_or_none()
    if None in (output_row, map_row, path_row, assessment_row):
        return None
    return output_row, map_row, path_row, assessment_row


def _validated_read(
    rows: tuple[Any, Any, Any, Any],
    output_revision: str,
    map_revision: str,
    path_revision: str,
    assessment_revision: str,
) -> DevelopmentBundle:
    output_row, map_row, path_row, assessment_row = rows
    study_material_output = output_row[0]
    knowledge_map = map_row[1]
    learning_path = path_row[1]
    public_document = assessment_row[2]
    answer_key_document = assessment_row[3]
    try:
        assessment = _reconstruct_assessment(
            assessment_revision, public_document, answer_key_document
        )
        if (
            study_material_output.get("output_id") != output_revision
            or map_row[0] != output_revision
            or knowledge_map.get("revision") != map_revision
            or knowledge_map.get("source_output_id") != output_revision
            or knowledge_map.get("material_ref")
            != study_material_output.get("material_ref")
            or path_row[0] != map_revision
            or learning_path.get("revision") != path_revision
            or learning_path.get("knowledge_map_revision") != map_revision
            or learning_path.get("material_ref") != knowledge_map.get("material_ref")
            or assessment_row[0] != map_revision
            or assessment_row[1] != path_revision
            or validate_study_material_output(study_material_output) is not None
            or validate_initial_learning_path(learning_path, knowledge_map) is not None
            or validate_assessment(assessment, knowledge_map, path_revision) is not None
        ):
            raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE")
        knowledge_map_view = build_knowledge_map_view(knowledge_map, learning_path)
    except DomainRevisionError:
        raise
    except Exception:
        raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE") from None
    return DevelopmentBundle(
        output_revision=output_revision,
        map_revision=map_revision,
        path_revision=path_revision,
        assessment_view_id=public_document["assessment_view_id"],
        study_material_output=deepcopy(study_material_output),
        knowledge_map=deepcopy(knowledge_map),
        learning_path=deepcopy(learning_path),
        knowledge_map_view=knowledge_map_view,
        assessment_view=deepcopy(public_document),
    )


def read_development_bundle(
    learner_id: UUID,
    material_id: UUID,
    output_revision: str,
    map_revision: str,
    path_revision: str,
    assessment_revision: str,
    *,
    dsn: str | None = None,
) -> DevelopmentBundle:
    """讀取 exact revisions，重建驗證後只回傳 answer-free view。"""

    try:
        with database_session(dsn) as session:
            rows = _read_rows(
                session,
                learner_id,
                material_id,
                output_revision,
                map_revision,
                path_revision,
                assessment_revision,
            )
            if rows is None:
                raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE")
            return _validated_read(
                rows,
                output_revision,
                map_revision,
                path_revision,
                assessment_revision,
            )
    except DomainRevisionError:
        raise
    except Exception:
        raise DomainRevisionError("DOMAIN_BUNDLE_UNAVAILABLE") from None


def read_assessment_view(
    learner_id: UUID,
    material_id: UUID,
    output_revision: str,
    map_revision: str,
    path_revision: str,
    assessment_revision: str,
    *,
    dsn: str | None = None,
) -> dict[str, Any]:
    """以 owner-scoped exact revisions 回傳唯一 public-safe assessment view。"""

    bundle = read_development_bundle(
        learner_id,
        material_id,
        output_revision,
        map_revision,
        path_revision,
        assessment_revision,
        dsn=dsn,
    )
    return deepcopy(bundle.assessment_view)


def read_assessment_for_scoring(
    learner_id: UUID,
    material_id: UUID,
    map_revision: str,
    path_revision: str,
    assessment_revision: str,
    *,
    dsn: str | None = None,
) -> AssessmentScoringBundle:
    """重建 owner-scoped full Assessment，且不進入 public view。"""

    if not isinstance(learner_id, UUID) or not isinstance(material_id, UUID):
        raise DomainRevisionError("ASSESSMENT_NOT_AVAILABLE")
    try:
        with database_session(dsn) as session:
            assessment_row = session.execute(
                select(
                    Assessment.map_revision,
                    Assessment.path_revision,
                    Assessment.public_document,
                    Assessment.answer_key_document,
                ).where(
                    Assessment.learner_id == learner_id,
                    Assessment.material_id == material_id,
                    Assessment.assessment_revision == assessment_revision,
                )
            ).one_or_none()
            if assessment_row is None:
                raise DomainRevisionError("ASSESSMENT_NOT_AVAILABLE")
            if tuple(assessment_row[:2]) != (map_revision, path_revision):
                raise DomainRevisionError("REVISION_MISMATCH")
            map_row = session.execute(
                select(KnowledgeMap.document).where(
                    KnowledgeMap.learner_id == learner_id,
                    KnowledgeMap.material_id == material_id,
                    KnowledgeMap.map_revision == map_revision,
                )
            ).one_or_none()
            path_row = session.execute(
                select(LearningPath.map_revision, LearningPath.document).where(
                    LearningPath.learner_id == learner_id,
                    LearningPath.material_id == material_id,
                    LearningPath.path_revision == path_revision,
                )
            ).one_or_none()
            if map_row is None or path_row is None:
                raise DomainRevisionError("ASSESSMENT_NOT_AVAILABLE")
            knowledge_map = map_row[0]
            learning_path = path_row[1]
            assessment = _reconstruct_assessment(
                assessment_revision, assessment_row[2], assessment_row[3]
            )
            if (
                path_row[0] != map_revision
                or validate_initial_learning_path(learning_path, knowledge_map)
                is not None
                or validate_assessment(assessment, knowledge_map, path_revision)
                is not None
            ):
                raise DomainRevisionError("ASSESSMENT_NOT_AVAILABLE")
    except DomainRevisionError:
        raise
    except Exception:
        raise DomainRevisionError("ASSESSMENT_STORAGE_FAILED") from None
    return AssessmentScoringBundle(
        learner_id,
        material_id,
        map_revision,
        path_revision,
        assessment_revision,
        deepcopy(knowledge_map),
        deepcopy(learning_path),
        deepcopy(assessment),
    )
