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
    analysis_saved: bool = False
    base_revision: str | None = Field(default=None,exclude_if=lambda value:value is None)
    source_names: list[str] | None = Field(default=None,exclude_if=lambda value:value is None)
    schema_: Literal["material-processing-run/v5", "material-processing-run/v6"] = Field(alias="schema")
    input_source_set_id: UUID | None = Field(default=None,exclude_if=lambda value:value is None)
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
    base_revision: str | None = Field(default=None,exclude_if=lambda value:value is None)
    run_id: UUID
    status: Literal["pending", "running", "succeeded", "partial", "failed", "cancelled"]
    progress_stage: Literal["queued", "evidence", "semantics", "publishing", "completed"]
    completed_pages: int
    total_pages: int | None
    error_code: str | None
    cancel_requested_at: datetime | None
    created_at: datetime


class MaterialStructureLink(_Closed):
    base_revision: str | None = Field(default=None,exclude_if=lambda value:value is None)
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


class SourceView(_Closed):
    included: bool = False
    source_id: UUID
    normalization_id: UUID
    original_artifact_id: UUID
    original_name: str
    media_type: str
    status: Literal['pending','running','ready','failed']
    normalized_artifact_id: UUID | None
    page_count: int | None
    error_code: str | None


class MaterialLibraryItem(_Closed):
    head_revision: str | None = None
    source_count: int = 1
    schema_: Literal["material-library-item/v2", "material-library-item/v3"] = Field(alias="schema")
    source: SourceView | None = Field(default=None,exclude_if=lambda value:value is None)
    ingestion_kind: Literal["sources-v2"] | None = Field(default=None,exclude_if=lambda value:value is None)
    material_id: UUID
    source_artifact_id: UUID | None
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
    source_id: str | None = Field(default=None,exclude_if=lambda value:value is None)
    source_name: str | None = Field(default=None,exclude_if=lambda value:value is None)
    normalized_page: int | None = Field(default=None,exclude_if=lambda value:value is None)
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
    schema_: Literal["knowledge-structure-view/v2", "knowledge-structure-view/v3"] = Field(alias="schema")
    source_resolver: str | None = Field(default=None,exclude_if=lambda value:value is None)
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
    schema_: Literal["study-resume/v3"] = Field(default="study-resume/v3", alias="schema")
    session: StudySessionView
    run_id: UUID
    source_artifact_id: UUID
    knowledge_structure: KnowledgeStructureView
    progress: LearnerProgressView
    assessments: list[AssessmentRecordView]
    selected_assessment_revision: str | None
    assessment_sets: list[AssessmentSetSummary]
    selected_set_id: UUID | None


class AssessmentSetCreate(_Closed):
    schema_: Literal['assessment-set-create/v1'] = Field(alias='schema')
    target_concept_id: str


class AssessmentSetAction(_Closed):
    schema_: Literal['assessment-set-action/v1'] = Field(alias='schema')
    expected_set_version: int = Field(ge=1, strict=True)


class AssessmentSetAnswer(_Closed):
    assessment_revision: str
    question_id: str
    selected_option_id: str


class AssessmentSetSubmission(_Closed):
    schema_: Literal['assessment-set-submission/v1'] = Field(alias='schema')
    expected_set_version: int = Field(ge=1, strict=True)
    answers: list[AssessmentSetAnswer] = Field(min_length=1)


class AssessmentPlanTarget(_Closed):
    claim_id: str
    covered_claim_ids: list[str]
    reason: Literal['distinct_grounded_point']


class AssessmentPlanExcluded(_Closed):
    claim_id: str
    reason: Literal['no_content_evidence']


class AssessmentPlanView(_Closed):
    schema_: Literal['assessment-plan/v1'] = Field(alias='schema')
    study_session_id: UUID
    knowledge_structure_revision: str
    policy: Literal['single-concept-grounded-points/v1']
    concept_id: str
    point_count: int
    requested_count: int
    targets: list[AssessmentPlanTarget]
    excluded: list[AssessmentPlanExcluded]


class AssessmentReview(_Closed):
    schema_: Literal['assessment-review/v1'] = Field(alias='schema')
    expected_set_version: int = Field(ge=1, strict=True)
    target_claim_id: str
    action: Literal['review', 'defer']


class AssessmentCycleSummary(_Closed):
    diagnostic_set_id: UUID
    concept_id: str
    set_version: int
    outcome: Literal['in_progress','needs_review','ready_for_remediation','passed','incomplete','deferred']
    closed_at: datetime | None
    active_set_id: UUID | None
    passed_count: int
    remediation_passed_count: int
    pending_count: int
    unanswered_count: int
    unavailable_count: int


class AssessmentCyclePoint(_Closed):
    claim_id: str
    result: Literal['unavailable','unanswered','diagnostic_pass','needs_review','reviewed','remediation_pass','deferred']
    latest_answer_event_id: UUID | None
    latest_set_id: UUID | None


class AssessmentCycleView(AssessmentCycleSummary):
    can_create_remediation: bool
    can_close: bool
    can_review: bool
    points: list[AssessmentCyclePoint]


class AssessmentSetSummary(_Closed):
    kind: Literal['diagnostic','remediation']
    diagnostic_set_id: UUID | None
    set_id: UUID
    target_concept_id: str
    status: Literal['preparing','partial_ready','failed','ready','in_progress','completed','cancelled']
    set_version: int
    requested_count: int
    published_count: int
    answered_count: int
    passed_count: int
    assessment_revisions: list[str]
    created_at: datetime
    completed_at: datetime | None


class AssessmentSetListView(_Closed):
    schema_: Literal['assessment-set-list/v3'] = Field(alias='schema')
    study_session_id: UUID
    knowledge_structure_revision: str
    active_set_ids: list[UUID]
    sets: list[AssessmentSetSummary]


class AssessmentSetItemView(_Closed):
    ordinal: int
    target_claim_id: str
    state: Literal['pending','generating','verified','published','failed','omitted']
    attempts: int
    failure_reason: str | None
    assessment: AssessmentView | None
    feedback: AnswerFeedbackView | None
    created_at: datetime | None
    can_submit: bool


class AssessmentSetView(AssessmentSetSummary):
    schema_: Literal['assessment-set/v2'] = Field(alias='schema')
    study_session_id: UUID
    material_id: UUID
    knowledge_structure_revision: str
    selection_policy: Literal['single-concept-grounded-points/v1','reviewed-wrong-points/v1']
    point_count: int
    excluded_count: int
    verified_count: int
    can_retry: bool
    can_publish_partial: bool
    can_complete: bool
    can_cancel: bool
    items: list[AssessmentSetItemView]
    cycle: AssessmentCycleView


class GuidanceApply(_Closed):
    schema_: Literal["guidance-apply/v2"] = Field(alias="schema")
    guidance_revision: str


class LearnerProgressView(_Closed):
    assessment_cycles: list[AssessmentCycleSummary]
    schema_: Literal["learner-progress/v3"] = Field(alias="schema")
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
    from ..storage.analysis_archive import has_analysis_checkpoint
    return MaterialProcessingRunView.model_validate({
        "analysis_saved": run.status == "failed" and has_analysis_checkpoint(run.learner_id, run.material_id, run.run_id),
        "schema": "material-processing-run/v6" if getattr(run,"input_source_set_id",None) else "material-processing-run/v5",
        "input_source_set_id":getattr(run,"input_source_set_id",None),
        "base_revision":getattr(run,"base_revision",None),
        "source_names":list(run.source_names) if getattr(run,"source_names",()) else None,
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

class MaterialDraftCreate(_Closed):
    schema_: Literal['material-draft-create/v1'] = Field(alias='schema')
    display_name: str

class MaterialDraftView(_Closed):
    schema_: Literal['material-draft/v1'] = Field(default='material-draft/v1',alias='schema')
    material_id: UUID

class SourceListView(_Closed):
    discard_requested: bool = False
    schema_: Literal['material-sources/v1'] = Field(default='material-sources/v1',alias='schema')
    material_id: UUID
    sources: list[SourceView]

class RevisionCreate(_Closed):
    schema_: Literal['material-revision-create/v1'] = Field(alias='schema')
    base_revision: str | None = Field(default=None,pattern=r'^knowledge-structure:sha256:[0-9a-f]{64}$')
    normalization_ids: list[UUID] = Field(min_length=1)


class RevisionCancel(_Closed):
    schema_: Literal['material-revision-cancel/v1'] = Field(alias='schema')
    base_revision: str = Field(pattern=r'^knowledge-structure:sha256:[0-9a-f]{64}$')

class MaterialReviewCreate(_Closed):
    schema_: Literal['material-review-create/v1'] = Field(alias='schema')
    base_revision: str = Field(pattern=r'^knowledge-structure:sha256:[0-9a-f]{64}$')

class FormatCapability(_Closed):
    extension: str
    media_type: str
    max_bytes: int

class SourceCapabilities(_Closed):
    schema_: Literal['source-capabilities/v1'] = Field(default='source-capabilities/v1',alias='schema')
    formats: list[FormatCapability]
    quality_notice: str

class PdfOrigin(_Closed):
    original_page: int = Field(ge=1)

class SlideOrigin(_Closed):
    original_slide_number: int = Field(ge=1)
    slide_id: str
    hidden: bool

class DocumentOrigin(_Closed):
    document_part: Literal["word/document.xml"]
    paragraph: int = Field(ge=1)

class TextOrigin(_Closed):
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

class EvidenceSourceView(_Closed):
    schema_: Literal['evidence-source/v1'] = Field(alias='schema')
    format: Literal['pdf','docx','pptx','doc','ppt','txt','md']
    original_name: str
    original_url: str
    preview_url: str
    normalized_page: int
    accuracy: Literal['exact','ambiguous','unavailable']
    origin_locators: list[PdfOrigin | SlideOrigin | DocumentOrigin | TextOrigin]
    label: str
