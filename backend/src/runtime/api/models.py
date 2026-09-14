from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ApiErrorView(_Closed):
    schema_: Literal["api-error/v1"] = Field(alias="schema")
    request_id: UUID
    reason_code: str
    retryable: bool
    message: Literal["Request could not be completed."]


class AccountCredentials(_Closed):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=15, max_length=128, repr=False)


class LearnerIdentityView(_Closed):
    schema_: Literal["learner-identity/v1"] = Field(default="learner-identity/v1", alias="schema")
    learner_id: UUID


class MaterialView(_Closed):
    schema_: Literal["material/v1"] = Field(alias="schema")
    material_id: UUID
    source_artifact_id: UUID
    source_sha256: str
    size_bytes: int


class MaterialProcessingCreate(_Closed):
    schema_: Literal["material-processing-create/v1"] = Field(alias="schema")
    material_id: UUID
    source_artifact_id: UUID


class MaterialOutputBindingView(_Closed):
    schema_: Literal["material-run-output-binding/v4"] = Field(alias="schema")
    knowledge_structure_revision: str
    runtime_lock_sha256: str
    page_count: int
    processing: Literal["succeeded", "partial"]
    quality: Literal["accepted", "needs_review"]
    decision: Literal["retain", "review"]
    reason_codes: list[str]
    ocr_calls: int
    semantic_calls: int


class MaterialDiscardView(_Closed):
    schema_: Literal["material-discard/v1"] = Field(default="material-discard/v1", alias="schema")
    material_id: UUID
    state: Literal["removing", "removed"]


class MaterialProcessingRunView(_Closed):
    schema_: Literal["material-processing-run/v5"] = Field(alias="schema")
    run_id: UUID
    material_id: UUID
    source_artifact_id: UUID
    status: Literal["pending", "running", "succeeded", "partial", "failed", "cancelled"]
    progress_stage: Literal["queued", "evidence", "semantics", "publishing", "completed"]
    completed_pages: int
    total_pages: int | None
    output_binding: MaterialOutputBindingView | None
    error_code: str | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class MaterialAttemptView(_Closed):
    run_id: UUID
    status: Literal["pending", "running", "succeeded", "partial", "failed", "cancelled"]
    progress_stage: Literal["queued", "evidence", "semantics", "publishing", "completed"]
    completed_pages: int
    total_pages: int | None
    error_code: str | None
    cancel_requested_at: datetime | None
    created_at: datetime


class MaterialStructureLink(_Closed):
    run_id: UUID
    knowledge_structure_revision: str
    created_at: datetime
    status: Literal["succeeded", "partial"]


class StudySessionLink(_Closed):
    study_session_id: UUID
    knowledge_structure_revision: str
    run_id: UUID
    current_concept_id: str | None
    status: Literal["active", "no_safe", "completed"]
    started_at: datetime


class MaterialLibraryItem(_Closed):
    schema_: Literal["material-library-item/v2"] = Field(alias="schema")
    material_id: UUID
    source_artifact_id: UUID
    display_name: str
    size_bytes: int
    created_at: datetime
    latest_attempt: MaterialAttemptView | None
    available_structures: list[MaterialStructureLink]
    study_sessions: list[StudySessionLink]


class MaterialRename(_Closed):
    schema_: Literal["material-rename/v1"] = Field(alias="schema")
    display_name: str


class MaterialLibraryView(_Closed):
    schema_: Literal["material-library/v2"] = Field(default="material-library/v2", alias="schema")
    materials: list[MaterialLibraryItem]


class SourceLocatorView(_Closed):
    page: int
    block_id: str
    region: list[float] = Field(min_length=4, max_length=4)


class EvidenceView(_Closed):
    evidence_id: str
    page_ref: str
    page: int
    block_order: int
    kind: str
    source: Literal["native_text", "unlimited_ocr"]
    source_locator: SourceLocatorView
    quote: str


class ClaimView(_Closed):
    claim_id: str
    text: str
    evidence: list[EvidenceView]


class ConceptView(_Closed):
    concept_id: str
    label: str
    aliases: list[str]
    claims: list[ClaimView]
    section_ids: list[str]
    source_pages: list[int]


class RelationView(_Closed):
    relation_id: str
    source_concept_id: str
    target_concept_id: str
    type: Literal["prerequisite", "part_of", "application", "example", "contrast"]
    learner_reason: str
    evidence_refs: list[str]
    context_refs: list[str]
    inference_basis: Literal["dependency", "composition", "usage", "instantiation", "comparison"]
    confidence: float


class SectionView(_Closed):
    section_id: str
    title: str
    order: int
    heading_evidence_id: str | None
    concept_ids: list[str]


class DocumentTreeView(_Closed):
    material_id: str
    sections: list[SectionView]


class LearningPathStepView(_Closed):
    position: int
    concept_id: str
    reason: Literal["document_order", "prerequisite"]


class StatusView(_Closed):
    processing: Literal["succeeded", "partial", "failed"]
    quality: Literal["accepted", "needs_review"]
    decision: Literal["retain", "review", "reject"]
    reason_codes: list[str]


class ExcludedPageView(_Closed):
    page_ref: str
    page: int
    stage: Literal["evidence"]
    reason_code: str


class KnowledgeStructureView(_Closed):
    schema_: Literal["knowledge-structure-view/v2"] = Field(alias="schema")
    material_id: str
    knowledge_structure_revision: str
    status: StatusView
    document_tree: DocumentTreeView
    concepts: list[ConceptView]
    relations: list[RelationView]
    initial_learning_path: list[LearningPathStepView]
    excluded_pages: list[ExcludedPageView]


class StudySessionCreate(_Closed):
    """Ensure a persistent state; current_concept_id applies only to its initial creation."""
    schema_: Literal["study-session-create/v2"] = Field(alias="schema")
    material_id: UUID
    knowledge_structure_revision: str
    current_concept_id: str | None = None


class StudySessionFocus(_Closed):
    schema_: Literal["study-session-focus/v1"] = Field(alias="schema")
    current_concept_id: str


class StudySessionView(_Closed):
    schema_: Literal["study-session/v2"] = Field(alias="schema")
    study_session_id: UUID
    material_id: UUID
    knowledge_structure_revision: str
    current_concept_id: str | None
    deferred_concept_ids: list[str]
    no_safe_claim_ids: list[str]
    status: Literal["active", "no_safe", "completed"]
    started_at: datetime
    completed_at: datetime | None
    event_watermark: int


class AssessmentCreate(_Closed):
    schema_: Literal["assessment-create/v2"] = Field(alias="schema")
    target_claim_id: str


class AssessmentOptionView(_Closed):
    option_id: str
    text: str


class AssessmentView(_Closed):
    schema_: Literal["single-choice-assessment/v2"] = Field(alias="schema")
    assessment_revision: str
    study_session_id: UUID
    knowledge_structure_revision: str
    question_id: str
    target_concept_id: str
    target_claim_id: str
    source_evidence_ids: list[str]
    question_type: Literal["single_choice"]
    prompt: str
    options: list[AssessmentOptionView]


class AnswerSubmissionCreate(_Closed):
    schema_: Literal["answer-submission-create/v2"] = Field(alias="schema")
    question_id: str
    selected_option_id: str


class AnswerFeedbackView(_Closed):
    schema_: Literal["answer-feedback/v2"] = Field(alias="schema")
    answer_event_id: UUID
    study_session_id: UUID
    assessment_revision: str
    question_id: str
    selected_option_id: str
    is_correct: bool
    rationale: str
    source_evidence_ids: list[str]
    event_number: int
    created_at: datetime


class AssessmentRecordView(_Closed):
    assessment: AssessmentView
    feedback: AnswerFeedbackView | None
    created_at: datetime
    can_submit: bool


class StudyResumeView(_Closed):
    schema_: Literal["study-resume/v1"] = Field(default="study-resume/v1", alias="schema")
    session: StudySessionView
    run_id: UUID
    source_artifact_id: UUID
    knowledge_structure: KnowledgeStructureView
    progress: LearnerProgressView
    assessments: list[AssessmentRecordView]
    selected_assessment_revision: str | None


class GuidanceApply(_Closed):
    schema_: Literal["guidance-apply/v2"] = Field(alias="schema")
    guidance_revision: str


class LearnerProgressView(_Closed):
    schema_: Literal["learner-progress/v2"] = Field(alias="schema")
    study_session_id: UUID
    knowledge_structure_revision: str
    event_watermark: int
    current_concept_id: str | None
    deferred_concept_ids: list[str]
    concept_states: list[dict[str, Any]]
    weaknesses: list[dict[str, Any]]
    next_action: dict[str, Any]
    guidance_revision: str


def project_material_run(run: Any) -> MaterialProcessingRunView:
    return MaterialProcessingRunView.model_validate({
        "schema": "material-processing-run/v5",
        **{name: getattr(run, name) for name in (
            "run_id", "material_id", "source_artifact_id", "status", "progress_stage",
            "completed_pages", "total_pages", "output_binding", "error_code", "cancel_requested_at",
            "created_at", "updated_at", "completed_at",
        )},
    })


def project_study_session(session: Any) -> StudySessionView:
    return StudySessionView.model_validate({
        "schema": "study-session/v2",
        "study_session_id": session.study_session_id,
        "material_id": session.material_id,
        "knowledge_structure_revision": session.knowledge_structure_revision,
        "current_concept_id": session.current_concept_id,
        "deferred_concept_ids": list(session.deferred_concept_ids),
        "no_safe_claim_ids": list(session.no_safe_claim_ids),
        "status": session.status,
        "started_at": session.started_at,
        "completed_at": session.completed_at,
        "event_watermark": session.last_event_number,
    })


def project_assessment(assessment: Any) -> AssessmentView:
    return AssessmentView.model_validate(assessment.public_document)


def project_answer_feedback(feedback: Any) -> AnswerFeedbackView:
    return AnswerFeedbackView.model_validate(feedback.model_dump(by_alias=True))


def project_learner_progress(progress: Any) -> LearnerProgressView:
    document = progress.model_dump()
    document["schema"] = document.pop("schema_")
    return LearnerProgressView.model_validate(document)
