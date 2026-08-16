from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..learning_update import LearningStateRecord
from ..material_processing import MaterialProcessingRun

ApiReasonCode = Literal[
    "REQUEST_INVALID",
    "SESSION_REQUIRED",
    "ORIGIN_NOT_ALLOWED",
    "RESOURCE_NOT_FOUND",
    "IDEMPOTENCY_CONFLICT",
    "MATERIAL_TOO_LARGE",
    "UNSUPPORTED_MEDIA_TYPE",
    "STORAGE_UNAVAILABLE",
    "INTERNAL_ERROR",
]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class MaterialView(_ClosedModel):
    schema_: Literal["material/v1"] = Field(alias="schema")
    material_id: UUID
    source_artifact_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=104_857_600)


class MaterialProcessingCreate(_ClosedModel):
    schema_: Literal["material-processing-create/v1"] = Field(alias="schema")
    material_id: UUID = Field(strict=False)
    source_artifact_id: UUID = Field(strict=False)
    subject: str = Field(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", max_length=64)


class ProviderCallCounts(_ClosedModel):
    page_structure: int = Field(ge=0)
    visual_alignment_adjudication: int = Field(ge=0)
    concept_candidate: int = Field(ge=0)
    concept_content: int = Field(ge=0)
    total: int = Field(ge=0)


class MaterialOutputBinding(_ClosedModel):
    schema_: Literal["material-run-output-binding/v1"] = Field(alias="schema")
    study_material_output_revision: str = Field(pattern=r"^study-material-output:sha256:[0-9a-f]{64}$")
    catalog_revision: str = Field(pattern=r"^resource-catalog:sha256:[0-9a-f]{64}$")
    learning_resource_result_revision: str = Field(pattern=r"^learning-resource-result:sha256:[0-9a-f]{64}$")
    knowledge_map_revision: str = Field(pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$")
    learning_path_revision: str = Field(pattern=r"^initial-learning-path:sha256:[0-9a-f]{64}$")
    assessment_revision: str = Field(pattern=r"^assessment:sha256:[0-9a-f]{64}$")
    processing: Literal["succeeded", "partial"]
    quality: Literal["accepted", "needs_review"]
    decision: Literal["retain", "review"]
    reason_code: Literal[
        "DEVELOPMENT_OUTPUT_ACCEPTED",
        "DEVELOPMENT_OUTPUT_NEEDS_REVIEW",
        "DEVELOPMENT_FULL_DOCUMENT_PARTIAL",
    ]
    provider_call_counts: ProviderCallCounts
    development_only: Literal[True]

    @model_validator(mode="after")
    def validate_outcome(self) -> "MaterialOutputBinding":
        outcome = (self.processing, self.quality, self.decision, self.reason_code)
        if outcome not in {
            ("succeeded", "accepted", "retain", "DEVELOPMENT_OUTPUT_ACCEPTED"),
            ("succeeded", "needs_review", "review", "DEVELOPMENT_OUTPUT_NEEDS_REVIEW"),
            ("partial", "needs_review", "review", "DEVELOPMENT_FULL_DOCUMENT_PARTIAL"),
        }:
            raise ValueError("MATERIAL_OUTPUT_BINDING_INVALID")
        return self


class MaterialProcessingRunView(_ClosedModel):
    schema_: Literal["material-processing-run/v1"] = Field(alias="schema")
    run_id: UUID
    material_id: UUID
    source_artifact_id: UUID
    status: Literal["pending", "running", "succeeded", "partial", "failed"]
    catalog_revision: str | None = Field(
        default=None, pattern=r"^resource-catalog:sha256:[0-9a-f]{64}$"
    )
    output_binding: MaterialOutputBinding | None
    error_code: Literal[
        "RESTART_INTERRUPTED",
        "MATERIAL_CONFIGURATION_INVALID",
        "MATERIAL_ANALYSIS_FAILED",
        "LOCAL_PROVIDER_TIMEOUT",
        "LOCAL_PROVIDER_RATE_LIMITED",
        "LOCAL_PROVIDER_TRANSIENT_ERROR",
        "CONTROLLED_RESOURCE_INVALID",
        "MATERIAL_OUTPUT_FAILED",
    ] | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> "MaterialProcessingRunView":
        if self.output_binding is not None and (
            self.catalog_revision != self.output_binding.catalog_revision
        ):
            raise ValueError("MATERIAL_RUN_VIEW_INVALID")
        if self.status in {"succeeded", "partial"}:
            if (
                self.output_binding is None
                or self.error_code is not None
                or self.completed_at is None
            ):
                raise ValueError("MATERIAL_RUN_VIEW_INVALID")
        elif self.status == "failed":
            if (
                self.output_binding is not None
                or self.error_code is None
                or self.completed_at is None
            ):
                raise ValueError("MATERIAL_RUN_VIEW_INVALID")
        elif self.output_binding is not None or self.error_code is not None or self.completed_at is not None:
            raise ValueError("MATERIAL_RUN_VIEW_INVALID")
        return self


class AssessmentResponseItem(_ClosedModel):
    question_id: str = Field(min_length=1, max_length=256)
    selected_option_id: str = Field(min_length=1, max_length=256)


class RegionView(_ClosedModel):
    bbox: list[float] = Field(min_length=4, max_length=4)
    coordinate_space: str


class EvidenceView(_ClosedModel):
    element_id: str
    evidence_id: str
    material_ref: str
    page_number: int = Field(ge=1)
    page_ref: str
    region: RegionView


class MapPageView(_ClosedModel):
    page_number: int = Field(ge=1)
    page_ref: str
    page_evidence_ref: str
    page_structure_ref: str


class MapMemberView(_ClosedModel):
    candidate_id: str
    name: str
    definition: str
    scope: str
    page_number: int = Field(ge=1)
    page_ref: str
    evidence_ids: list[str]


class MapConceptView(_ClosedModel):
    concept_id: str
    normalized_name: str
    members: list[MapMemberView]
    processing: str
    quality: str
    decision: str
    reason_code: str


class MapRelationView(_ClosedModel):
    relation_id: str
    schema_: Literal["relation/v1"] = Field(alias="schema")
    type: str
    source_concept_id: str
    target_concept_id: str
    statement: str
    evidence_ids: list[str]
    processing: str
    quality: str
    decision: str
    reason_code: str


class MapReviewView(_ClosedModel):
    review_id: str
    kind: str
    source_concept_id: str
    target_concept_id: str
    statement: str
    evidence_ids: list[str]
    quality: str
    reason_code: str


class MapLimitationView(_ClosedModel):
    affected_page_refs: list[str]
    reason_code: Literal["FORMAL_PROVIDER_DEFERRED", "CONCEPT_CONTEXT_UNAVAILABLE"]


class ExcludedPageView(_ClosedModel):
    page_ref: str
    page_number: int = Field(ge=1)
    page_evidence_ref: str
    last_stage: Literal["page_structure", "visual_alignment"]
    processing: Literal["failed"]
    quality: Literal["unsupported"]
    decision: Literal["reject"]
    reason_code: Literal[
        "PAGE_STRUCTURE_INVALID", "VISUAL_ALIGNMENT_REVIEW_REJECTED"
    ]


class MapExcludedLimitationView(_ClosedModel):
    affected_pages: list[ExcludedPageView]
    reason_code: Literal["PAGE_CONTENT_EXCLUDED"]


class KnowledgeMapView(_ClosedModel):
    schema_: Literal["knowledge-map/v1"] = Field(alias="schema")
    revision: str = Field(pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$")
    source_output_id: str = Field(pattern=r"^study-material-output:sha256:[0-9a-f]{64}$")
    material_ref: str
    pages: list[MapPageView]
    concepts: list[MapConceptView]
    evidence_index: list[EvidenceView]
    relations: list[MapRelationView]
    review_items: list[MapReviewView]
    known_limitations: list[MapLimitationView | MapExcludedLimitationView]
    processing: str
    quality: str
    decision: str
    reason_code: str


class LearningPathView(_ClosedModel):
    schema_: Literal["initial-learning-path/v1"] = Field(alias="schema")
    revision: str = Field(pattern=r"^initial-learning-path:sha256:[0-9a-f]{64}$")
    knowledge_map_revision: str = Field(pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$")
    material_ref: str
    ordered_concept_ids: list[str]
    processing: str
    quality: str
    decision: str
    reason_code: str


class DerivedMapMemberView(_ClosedModel):
    name: str
    definition: str
    page_number: int = Field(ge=1)


class PositionView(_ClosedModel):
    x: int
    y: int


class DerivedConceptView(_ClosedModel):
    id: str
    label: str
    definition: str
    members: list[DerivedMapMemberView]
    evidence: list[EvidenceView]
    position: PositionView
    quality: str
    reason_code: str


class DerivedRelationView(_ClosedModel):
    id: str
    source: str
    target: str
    statement: str
    evidence: list[EvidenceView]
    reason_code: str
    type: str


class DerivedReviewView(_ClosedModel):
    id: str
    source: str
    target: str
    statement: str
    evidence: list[EvidenceView]
    reason_code: str
    kind: str


class ArtifactStatusView(_ClosedModel):
    processing: str
    quality: str
    decision: str
    reason_code: str


class DerivedPathView(ArtifactStatusView):
    ordered_concept_ids: list[str]


class DerivedLimitationView(_ClosedModel):
    reason_code: str
    page_numbers: list[int]
    affected_page_count: int = Field(ge=0)


class KnowledgeMapDerivedView(_ClosedModel):
    schema_: Literal["knowledge-map-view/v1"] = Field(alias="schema")
    material_ref: str
    knowledge_map_revision: str = Field(pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$")
    learning_path_revision: str = Field(pattern=r"^initial-learning-path:sha256:[0-9a-f]{64}$")
    status: ArtifactStatusView
    concepts: list[DerivedConceptView]
    relations: list[DerivedRelationView]
    review_items: list[DerivedReviewView]
    path: DerivedPathView
    limitations: list[DerivedLimitationView]


class ResourceItemView(_ClosedModel):
    resource_id: str
    concept_id: str
    subject: str
    resource_key: str
    title: str
    source_locator: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    use_boundary: str
    learning_use: str
    match_basis: str
    matched_terms: list[str]
    processing: str
    quality: str
    decision: str
    reason_code: str


class LearningResourceResultView(_ClosedModel):
    schema_: Literal["learning-resource-result-view/v1"] = Field(alias="schema")
    result_revision: str = Field(pattern=r"^learning-resource-result:sha256:[0-9a-f]{64}$")
    source_study_material_output_revision: str = Field(pattern=r"^study-material-output:sha256:[0-9a-f]{64}$")
    catalog_revision: str = Field(pattern=r"^resource-catalog:sha256:[0-9a-f]{64}$")
    subject: str
    resources: list[ResourceItemView]
    produced_at: datetime = Field(strict=False)
    run_id: UUID = Field(strict=False)
    processing: str
    quality: str
    decision: str
    reason_code: str


class AssessmentOptionView(_ClosedModel):
    option_id: str
    text: str


class AssessmentQuestionView(_ClosedModel):
    question_id: str
    concept_id: str
    question_type: Literal["single_choice"]
    prompt: str
    options: list[AssessmentOptionView]
    source_evidence_ids: list[str]


class AssessmentPracticeSetView(_ClosedModel):
    practice_set_id: str
    concept_id: str
    question_ids: list[str]


class AssessmentPublicView(_ClosedModel):
    schema_: Literal["assessment-view/v1"] = Field(alias="schema")
    assessment_view_id: str = Field(pattern=r"^assessment-view:sha256:[0-9a-f]{64}$")
    version: str
    knowledge_map_revision: str = Field(pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$")
    learning_path_revision: str = Field(pattern=r"^initial-learning-path:sha256:[0-9a-f]{64}$")
    scoring_rule_version: str
    questions: list[AssessmentQuestionView]
    practice_sets: list[AssessmentPracticeSetView]
    processing: str
    quality: str
    decision: str
    reason_code: str


class LearningUpdateCreate(_ClosedModel):
    schema_: Literal["learning-update-create/v1"] = Field(alias="schema")
    material_id: UUID = Field(strict=False)
    map_revision: str = Field(min_length=1, max_length=256)
    path_revision: str = Field(min_length=1, max_length=256)
    assessment_revision: str = Field(min_length=1, max_length=256)
    responses: list[AssessmentResponseItem] = Field(min_length=1, max_length=200)


class MasteryView(_ClosedModel):
    concept_id: str
    valid_answer_count: int = Field(ge=0)
    correct_rate: float | None = Field(default=None, ge=0, le=1)
    practice_score: float = Field(ge=0, le=1)
    review_score: float = Field(ge=0, le=1)
    completion_score: float = Field(ge=0, le=1)
    recent_error_penalty: float | None = Field(default=None, ge=0, le=1)
    mastery_score: float | None = Field(default=None, ge=0, le=1)
    raw_band: Literal["weak", "learning", "mastered"] | None
    final_status: Literal["not_started", "weak", "review", "learning", "mastered"]
    needs_review: bool
    source_answer_event_ids: list[str]
    source_learning_event_ids: list[str]
    reason_codes: list[str]


class WeaknessView(_ClosedModel):
    concept_id: str
    kind: Literal["remediation_required", "weak_mastery"]
    reason_codes: list[str]
    source_answer_event_ids: list[str]
    source_learning_event_ids: list[str]


class SuggestionView(_ClosedModel):
    is_personalized: bool
    action: Literal["no_action", "review_concept", "practice_concept", "start_concept", "follow_initial_path"]
    target_concept_id: str | None
    mastery_data_score: float | None = Field(default=None, ge=0, le=1)
    weakness_score: float | None = Field(default=None, ge=0, le=1)
    path_alignment_score: float | None = Field(default=None, ge=0, le=1)
    prerequisite_score: float | None = Field(default=None, ge=0, le=1)
    action_clarity_score: float | None = Field(default=None, ge=0, le=1)
    learning_suggestion_score: float | None = Field(default=None, ge=0, le=1)
    level: Literal["no_action", "low", "medium", "high"]
    fallback_action: Literal["follow_initial_path"] | None
    fallback_target_concept_id: str | None
    needs_review: bool
    decision: Literal["retain", "review", "reject"]
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    source_answer_event_ids: list[str]
    source_learning_event_ids: list[str]


class LearningStateView(_ClosedModel):
    schema_: Literal["learning-state-view/v1"] = Field(alias="schema")
    state_revision: str = Field(pattern=r"^learning-state:sha256:[0-9a-f]{64}$")
    knowledge_map_revision: str = Field(pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$")
    learning_path_revision: str = Field(pattern=r"^initial-learning-path:sha256:[0-9a-f]{64}$")
    assessment_id: str
    assessment_revision: str = Field(pattern=r"^assessment:sha256:[0-9a-f]{64}$")
    scoring_rule_version: Literal["single-choice-exact/v1"]
    source_answer_event_ids: list[str]
    source_learning_event_ids: list[str]
    mastery: list[MasteryView]
    weaknesses: list[WeaknessView]
    suggestion: SuggestionView
    processing: Literal["succeeded", "partial"]
    quality: Literal["accepted", "needs_review"]
    decision: Literal["retain", "review"]
    reason_code: Literal["LEARNING_STATE_ACCEPTED", "LEARNING_STATE_NEEDS_REVIEW"]

    @model_validator(mode="after")
    def validate_outcome(self) -> "LearningStateView":
        if (
            self.processing,
            self.quality,
            self.decision,
            self.reason_code,
        ) not in {
            ("succeeded", "accepted", "retain", "LEARNING_STATE_ACCEPTED"),
            ("partial", "needs_review", "review", "LEARNING_STATE_NEEDS_REVIEW"),
        }:
            raise ValueError("LEARNING_STATE_VIEW_INVALID")
        return self


class ApiErrorView(_ClosedModel):
    schema_: Literal["api-error/v1"] = Field(alias="schema")
    request_id: UUID
    reason_code: ApiReasonCode
    retryable: bool
    message: Literal["Request could not be completed."]


def project_material_run(run: MaterialProcessingRun) -> MaterialProcessingRunView:
    """移除 learner 與 internal pipeline binding。"""

    return MaterialProcessingRunView.model_validate(
        {
            "schema": "material-processing-run/v1",
            "run_id": run.run_id,
            "material_id": run.material_id,
            "source_artifact_id": run.source_artifact_id,
            "status": run.status,
            "catalog_revision": run.catalog_revision,
            "output_binding": deepcopy(run.output_binding),
            "error_code": run.error_code,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "completed_at": run.completed_at,
        }
    )


def project_learning_state(record: LearningStateRecord) -> LearningStateView:
    """把已重驗 state 轉為不含 learner_id 的公開 view。"""

    document = record.document
    return LearningStateView.model_validate(
        {
            "schema": "learning-state-view/v1",
            "state_revision": document["revision"],
            "knowledge_map_revision": document["knowledge_map_revision"],
            "learning_path_revision": document["learning_path_revision"],
            "assessment_id": document["assessment_id"],
            "assessment_revision": document["assessment_revision"],
            "scoring_rule_version": document["scoring_rule_version"],
            "source_answer_event_ids": deepcopy(document["source_answer_event_ids"]),
            "source_learning_event_ids": deepcopy(document["source_learning_event_ids"]),
            "mastery": deepcopy(document["mastery"]),
            "weaknesses": deepcopy(document["weaknesses"]),
            "suggestion": deepcopy(document["suggestion"]),
            "processing": document["processing"],
            "quality": document["quality"],
            "decision": document["decision"],
            "reason_code": document["reason_code"],
        }
    )


def project_resource_result(document: dict[str, Any]) -> dict[str, Any]:
    """移除每筆 Resource 的 physical artifact reference。"""

    root_keys = {
        "result_revision",
        "schema",
        "source_study_material_output_revision",
        "catalog_revision",
        "subject",
        "resources",
        "produced_at",
        "run_id",
        "processing",
        "quality",
        "decision",
        "reason_code",
    }
    resource_keys = {
        "resource_id",
        "concept_id",
        "subject",
        "resource_key",
        "title",
        "source_locator",
        "artifact_sha256",
        "use_boundary",
        "learning_use",
        "match_basis",
        "matched_terms",
        "processing",
        "quality",
        "decision",
        "reason_code",
    }
    if not isinstance(document, dict) or set(document) != root_keys:
        raise ValueError("RESOURCE_PROJECTION_INVALID")
    resources = document.get("resources")
    if not isinstance(resources, list) or any(
        not isinstance(resource, dict) or set(resource) != resource_keys | {"artifact_ref"}
        for resource in resources
    ):
        raise ValueError("RESOURCE_PROJECTION_INVALID")
    projected = {key: deepcopy(document[key]) for key in root_keys - {"schema", "resources"}}
    projected["schema"] = "learning-resource-result-view/v1"
    projected["resources"] = [
        {key: deepcopy(resource[key]) for key in resource_keys}
        for resource in resources
    ]
    return projected
