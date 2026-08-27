from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from learning_adaptation.adaptive_plans import (
    AdaptiveAction,
    AdaptivePlanSnapshot,
    Suggestion,
)
from learning_adaptation.answer_events import AnswerFeedback
from learning_adaptation.assessment_items import StoredAssessment
from learning_adaptation.learning_states import LearningStateSnapshot
from learning_adaptation.map_context import MapContext
from learning_adaptation.study_sessions import StoredStudySession
from learning_adaptation.weaknesses import WeaknessSnapshot

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
    schema_: Literal["material-processing-create/v2"] = Field(alias="schema")
    material_id: UUID = Field(strict=False)
    source_artifact_id: UUID = Field(strict=False)


class MaterialOutputBinding(_ClosedModel):
    schema_: Literal["material-run-output-binding/v3"] = Field(alias="schema")
    producer_bundle_id: str = Field(
        pattern=r"^text-first-producer-bundle:sha256:[0-9a-f]{64}$"
    )
    producer_run_id: str = Field(
        pattern=r"^text-first-run:[0-9a-fA-F-]{36}$"
    )
    concept_evidence_output_id: str = Field(
        pattern=r"^concept-evidence-output:sha256:[0-9a-f]{64}$"
    )
    study_material_output_revision: str = Field(
        pattern=r"^study-material-output:sha256:[0-9a-f]{64}$"
    )
    knowledge_map_revision: str = Field(
        pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$"
    )
    runtime_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    processing: Literal["succeeded", "partial"]
    quality: Literal["needs_review"]
    decision: Literal["review"]
    reason_codes: list[str] = Field(min_length=1, max_length=64)
    ocr_calls: int = Field(ge=0)
    concept_calls: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "MaterialOutputBinding":
        if (
            len(self.reason_codes) != len(set(self.reason_codes))
            or self.reason_codes != sorted(self.reason_codes)
            or self.ocr_calls > self.page_count
        ):
            raise ValueError("MATERIAL_OUTPUT_BINDING_INVALID")
        return self


class MaterialProcessingRunView(_ClosedModel):
    schema_: Literal["material-processing-run/v2"] = Field(alias="schema")
    run_id: UUID
    material_id: UUID
    source_artifact_id: UUID
    status: Literal["pending", "running", "succeeded", "partial", "failed"]
    output_binding: MaterialOutputBinding | None
    error_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,99}$")
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @model_validator(mode="after")
    def validate_status_shape(self) -> "MaterialProcessingRunView":
        if self.status in {"succeeded", "partial"}:
            if (
                self.output_binding is None
                or self.output_binding.processing != self.status
                or self.error_code is not None
                or self.completed_at is None
            ):
                raise ValueError("MATERIAL_RUN_VIEW_INVALID")
        elif self.status == "failed":
            if self.output_binding is not None or self.error_code is None or self.completed_at is None:
                raise ValueError("MATERIAL_RUN_VIEW_INVALID")
        elif self.output_binding is not None or self.error_code is not None or self.completed_at is not None:
            raise ValueError("MATERIAL_RUN_VIEW_INVALID")
        return self


class RegionView(_ClosedModel):
    coordinate_space: Literal["unrotated_pdf_points"]
    bbox: list[float] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_box(self) -> "RegionView":
        if not all(math.isfinite(value) for value in self.bbox):
            raise ValueError("REGION_INVALID")
        if self.bbox[0] >= self.bbox[2] or self.bbox[1] >= self.bbox[3]:
            raise ValueError("REGION_INVALID")
        return self


class EvidenceView(_ClosedModel):
    evidence_id: str = Field(pattern=r"^evidence:sha256:[0-9a-f]{64}$")
    page_ref: str = Field(pattern=r"^page:sha256:[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    kind: str = Field(min_length=1, max_length=64)
    region: RegionView


class FormalClaimView(_ClosedModel):
    claim_id: str = Field(pattern=r"^claim:sha256:[0-9a-f]{64}$")
    text: str = Field(min_length=1)
    evidence: list[EvidenceView] = Field(min_length=1)


class SupplementaryResourceView(_ClosedModel):
    promotion_id: str = Field(pattern=r"^resource-promotion:sha256:[0-9a-f]{64}$")
    resource_concept_id: str = Field(pattern=r"^resource-concept:sha256:[0-9a-f]{64}$")
    resource_id: str = Field(pattern=r"^resource:sha256:[0-9a-f]{64}$")
    label: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authors: list[str] = Field(min_length=1)
    source_url: str = Field(min_length=1)
    citation: str = Field(min_length=1)
    license: str = Field(min_length=1)
    license_url: str = Field(min_length=1)
    use_boundary: str = Field(min_length=1)
    page_numbers: list[int] = Field(min_length=1)
    resource_evidence_ids: list[str] = Field(min_length=1)
    match_ids: list[str] = Field(min_length=1)
    study_concept_ids: list[str] = Field(min_length=1)
    match_reason: Literal["EXACT_NORMALIZED_LABEL"]

    @model_validator(mode="after")
    def validate_resource_provenance(self) -> "SupplementaryResourceView":
        if (
            self.page_numbers != sorted(set(self.page_numbers))
            or any(page < 1 for page in self.page_numbers)
            or self.resource_evidence_ids != sorted(set(self.resource_evidence_ids))
            or self.match_ids != sorted(set(self.match_ids))
            or self.study_concept_ids != sorted(set(self.study_concept_ids))
            or any(not value for value in self.authors)
        ):
            raise ValueError("SUPPLEMENTARY_RESOURCE_INVALID")
        return self


class FormalConceptView(_ClosedModel):
    formal_concept_id: str = Field(pattern=r"^formal-concept:sha256:[0-9a-f]{64}$")
    label: str = Field(min_length=1)
    claims: list[FormalClaimView] = Field(min_length=1)
    source_concept_ids: list[str] = Field(min_length=1)
    source_page_numbers: list[int] = Field(min_length=1)
    supplementary_resources: list[SupplementaryResourceView]
    quality: Literal["needs_review"]
    decision: Literal["review"]
    reason_codes: list[str] = Field(min_length=1, max_length=64)


class RelationEvidenceView(_ClosedModel):
    owner_formal_concept_id: str = Field(
        pattern=r"^formal-concept:sha256:[0-9a-f]{64}$"
    )
    claim_id: str = Field(pattern=r"^claim:sha256:[0-9a-f]{64}$")
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> "RelationEvidenceView":
        if self.evidence_ids != sorted(set(self.evidence_ids)):
            raise ValueError("RELATION_EVIDENCE_INVALID")
        return self


class FormalRelationView(_ClosedModel):
    relation_id: str = Field(pattern=r"^formal-relation:sha256:[0-9a-f]{64}$")
    type: Literal["prerequisite", "contains", "related"]
    source_formal_concept_id: str
    target_formal_concept_id: str
    relation_evidence: list[RelationEvidenceView] = Field(min_length=1)
    quality: Literal["needs_review"]
    decision: Literal["review"]
    reason_codes: list[str] = Field(min_length=1)
    is_in_prerequisite_cycle: bool


class RelationDiagnosticsView(_ClosedModel):
    possible_pairs: int = Field(ge=0)
    candidate_pairs: int = Field(ge=0)
    selected_pairs: int = Field(ge=0)
    selected_signal_counts: dict[str, int]
    evidence_gated_pairs: int = Field(ge=0)
    rejected_no_evidence: int = Field(ge=0)
    direction_conflicts: int = Field(ge=0)
    verifier_calls: int = Field(ge=0)
    verifier_accepted: int = Field(ge=0)
    verifier_rejected: int = Field(ge=0)
    verifier_unsupported: int = Field(ge=0)
    structural_proposals: int = Field(ge=0)
    contains_proposals: int = Field(ge=0)
    prerequisite_proposals: int = Field(ge=0)
    related_proposals: int = Field(ge=0)
    accepted_relations: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "RelationDiagnosticsView":
        allowed_signals = {
            "adjacent",
            "same_group",
            "same_page",
            "explicit_relation",
            "cross_reference",
            "label_mention",
            "shared_evidence",
            "shared_formula",
        }
        if (
            self.selected_pairs > self.candidate_pairs
            or self.candidate_pairs > self.possible_pairs
            or self.verifier_accepted + self.verifier_rejected > self.verifier_calls
            or self.structural_proposals
            != self.contains_proposals + self.prerequisite_proposals
            or any(
                signal not in allowed_signals or count < 0
                for signal, count in self.selected_signal_counts.items()
            )
        ):
            raise ValueError("RELATION_DIAGNOSTICS_INVALID")
        return self


class ResourceBindingView(_ClosedModel):
    context_revision: str = Field(pattern=r"^map-resource-context:sha256:[0-9a-f]{64}$")
    library_revision: str = Field(pattern=r"^resource-library:sha256:[0-9a-f]{64}$")
    matching_policy: Literal["resource-context-exact-distinct-source/v3"]
    promotion_policy: Literal["resource-formal-concept-promotion/v1"]


class ResourceDiagnosticsView(_ClosedModel):
    matches: int = Field(ge=0)
    promoted_matches: int = Field(ge=0)
    promoted_resources: int = Field(ge=0)
    dropped_matches: int = Field(ge=0)
    split_review_matches: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "ResourceDiagnosticsView":
        if self.matches != self.promoted_matches + self.dropped_matches + self.split_review_matches:
            raise ValueError("RESOURCE_DIAGNOSTICS_INVALID")
        return self


class ResourceDecisionView(_ClosedModel):
    decision_id: str = Field(
        pattern=r"^resource-promotion-decision:sha256:[0-9a-f]{64}$"
    )
    match_id: str = Field(pattern=r"^resource-match:sha256:[0-9a-f]{64}$")
    study_concept_id: str
    resource_concept_id: str = Field(pattern=r"^resource-concept:sha256:[0-9a-f]{64}$")
    formal_concept_ids: list[str]
    decision: Literal["review", "reject"]
    reason_code: Literal[
        "RESOURCE_SPLIT_REVIEW_REQUIRED", "RESOURCE_SOURCE_CONCEPT_DROPPED"
    ]

    @model_validator(mode="after")
    def validate_decision(self) -> "ResourceDecisionView":
        if self.formal_concept_ids != sorted(set(self.formal_concept_ids)):
            raise ValueError("RESOURCE_DECISION_INVALID")
        if self.decision == "reject":
            if self.reason_code != "RESOURCE_SOURCE_CONCEPT_DROPPED" or self.formal_concept_ids:
                raise ValueError("RESOURCE_DECISION_INVALID")
        elif self.reason_code != "RESOURCE_SPLIT_REVIEW_REQUIRED" or len(self.formal_concept_ids) < 2:
            raise ValueError("RESOURCE_DECISION_INVALID")
        return self


class ExcludedPageView(_ClosedModel):
    page_ref: str = Field(pattern=r"^page:sha256:[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    page_evidence_id: str | None
    last_stage: Literal["page_evidence", "concept"]
    processing: Literal["failed"]
    quality: Literal["needs_review"]
    decision: Literal["reject"]
    reason_codes: list[str] = Field(min_length=1, max_length=64)


class ArtifactStatusView(_ClosedModel):
    processing: Literal["succeeded", "partial", "failed"]
    quality: Literal["needs_review"]
    decision: Literal["review", "reject"]
    reason_codes: list[str] = Field(min_length=1, max_length=64)


class KnowledgeMapView(_ClosedModel):
    schema_: Literal["knowledge-map-view/v6"] = Field(alias="schema")
    material_ref: str = Field(pattern=r"^material:sha256:[0-9a-f]{64}$")
    knowledge_map_revision: str = Field(
        pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$"
    )
    source_output_id: str = Field(
        pattern=r"^study-material-output:sha256:[0-9a-f]{64}$"
    )
    status: ArtifactStatusView
    concepts: list[FormalConceptView]
    relations: list[FormalRelationView]
    relation_diagnostics: RelationDiagnosticsView
    resource_binding: ResourceBindingView
    resource_diagnostics: ResourceDiagnosticsView
    resource_decisions: list[ResourceDecisionView]
    initial_learning_path: list[str]
    excluded_pages: list[ExcludedPageView]

    @model_validator(mode="after")
    def validate_same_page_links(self) -> "KnowledgeMapView":
        concept_ids = {concept.formal_concept_id for concept in self.concepts}
        reason_lists = [
            self.status.reason_codes,
            *(concept.reason_codes for concept in self.concepts),
            *(relation.reason_codes for relation in self.relations),
            *(page.reason_codes for page in self.excluded_pages),
        ]
        if any(reasons != sorted(set(reasons)) for reasons in reason_lists):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if len(concept_ids) != len(self.concepts):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if set(self.initial_learning_path) != concept_ids:
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if len({page.page_ref for page in self.excluded_pages}) != len(self.excluded_pages):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if len({page.page_number for page in self.excluded_pages}) != len(self.excluded_pages):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if len({relation.relation_id for relation in self.relations}) != len(self.relations):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if any(
            relation.source_formal_concept_id not in concept_ids
            or relation.target_formal_concept_id not in concept_ids
            or relation.source_formal_concept_id == relation.target_formal_concept_id
            for relation in self.relations
        ):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        claims_by_concept = {
            concept.formal_concept_id: {
                claim.claim_id: {
                    evidence.evidence_id for evidence in claim.evidence
                }
                for claim in concept.claims
            }
            for concept in self.concepts
        }
        promoted_match_ids = [
            match_id
            for concept in self.concepts
            for resource in concept.supplementary_resources
            for match_id in resource.match_ids
        ]
        decision_match_ids = [decision.match_id for decision in self.resource_decisions]
        if (
            len(promoted_match_ids) != len(set(promoted_match_ids))
            or len(decision_match_ids) != len(set(decision_match_ids))
            or set(promoted_match_ids) & set(decision_match_ids)
            or len(promoted_match_ids) != self.resource_diagnostics.promoted_matches
            or sum(len(concept.supplementary_resources) for concept in self.concepts)
            != self.resource_diagnostics.promoted_resources
            or len(promoted_match_ids) + len(decision_match_ids)
            != self.resource_diagnostics.matches
            or any(
                not set(resource.study_concept_ids) <= set(concept.source_concept_ids)
                for concept in self.concepts
                for resource in concept.supplementary_resources
            )
        ):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if any(
            evidence.owner_formal_concept_id not in {
                relation.source_formal_concept_id,
                relation.target_formal_concept_id,
            }
            or evidence.claim_id not in claims_by_concept[
                evidence.owner_formal_concept_id
            ]
            or not set(evidence.evidence_ids)
            <= claims_by_concept[evidence.owner_formal_concept_id][evidence.claim_id]
            for relation in self.relations
            for evidence in relation.relation_evidence
        ):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if any(
            [
                (item.owner_formal_concept_id, item.claim_id)
                for item in relation.relation_evidence
            ]
            != sorted({
                (item.owner_formal_concept_id, item.claim_id)
                for item in relation.relation_evidence
            })
            for relation in self.relations
        ):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if any(
            evidence.page_number not in concept.source_page_numbers
            for concept in self.concepts
            for claim in concept.claims
            for evidence in claim.evidence
        ):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if self.excluded_pages and self.status.processing != "partial":
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        return self


class StudySessionCreate(_ClosedModel):
    schema_: Literal["study-session-create/v1"] = Field(alias="schema")
    material_id: UUID = Field(strict=False)
    knowledge_map_revision: str = Field(
        pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$"
    )
    current_formal_concept_id: str | None = Field(
        default=None,
        pattern=r"^formal-concept:sha256:[0-9a-f]{64}$",
    )


class StudySessionView(_ClosedModel):
    schema_: Literal["study-session/v1"] = Field(alias="schema")
    study_session_id: UUID
    material_id: UUID
    knowledge_map_revision: str
    current_formal_concept_id: str | None
    deferred_formal_concept_id: str | None
    status: Literal["active", "completed"]
    started_at: datetime
    completed_at: datetime | None
    event_watermark: int = Field(ge=0)


class StudyConceptContextView(_ClosedModel):
    formal_concept_id: str
    label: str = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=1)
    supplementary_resource_promotion_ids: list[str]


class StudyContextView(_ClosedModel):
    schema_: Literal["study-context/v1"] = Field(alias="schema")
    study_session_id: UUID
    base_knowledge_map_revision: str
    current_formal_concept_id: str | None
    deferred_formal_concept_id: str | None
    initial_learning_path: list[StudyConceptContextView] = Field(min_length=1)


class AssessmentCreate(_ClosedModel):
    schema_: Literal["assessment-create/v1"] = Field(alias="schema")
    target_claim_id: str = Field(pattern=r"^claim:sha256:[0-9a-f]{64}$")


class AssessmentOptionView(_ClosedModel):
    option_id: str
    text: str = Field(min_length=1)


class AssessmentView(_ClosedModel):
    schema_: Literal["single-choice-assessment-public/v1"] = Field(alias="schema")
    study_session_id: UUID
    knowledge_map_revision: str
    assessment_revision: str
    question_id: str
    target_formal_concept_id: str
    target_claim_id: str
    source_evidence_ids: list[str] = Field(min_length=1)
    question_type: Literal["single_choice"]
    prompt: str = Field(min_length=1)
    options: list[AssessmentOptionView] = Field(min_length=4, max_length=4)
    policy_revision: Literal["single-choice-assessment-policy/v1"]


class AnswerSubmissionCreate(_ClosedModel):
    schema_: Literal["answer-submission-create/v1"] = Field(alias="schema")
    question_id: str = Field(pattern=r"^question:sha256:[0-9a-f]{64}$")
    selected_option_id: str = Field(pattern=r"^option:sha256:[0-9a-f]{64}$")


class AnswerFeedbackView(_ClosedModel):
    schema_: Literal["answer-feedback/v1"] = Field(alias="schema")
    answer_event_id: UUID
    study_session_id: UUID
    assessment_revision: str
    question_id: str
    selected_option_id: str
    is_correct: bool
    rationale: str = Field(min_length=1)
    source_evidence_ids: list[str] = Field(min_length=1)
    event_number: int = Field(ge=1)
    created_at: datetime


class ConceptLearningStateView(_ClosedModel):
    formal_concept_id: str
    status: Literal["not_started", "learning", "needs_review", "mastered"]
    mastery_band: Literal["no_evidence", "developing", "demonstrated"]
    confidence: Literal["none", "limited", "supported"]
    needs_more_data: bool
    required_claim_ids: list[str]
    attempted_claim_ids: list[str]
    latest_correct_claim_ids: list[str]
    claim_coverage_complete: bool
    required_evidence_ids: list[str]
    observed_evidence_ids: list[str]
    evidence_coverage_complete: bool
    valid_attempts: int = Field(ge=0)
    correct_attempts: int = Field(ge=0)
    distinct_item_attempts: int = Field(ge=0)
    recent_result: Literal["correct", "incorrect"] | None
    repeated_error: bool
    post_error_improvement: bool
    explanation: str = Field(min_length=1)


class LearningStateView(_ClosedModel):
    schema_: Literal["learning-state/v1"] = Field(alias="schema")
    study_session_id: UUID
    base_knowledge_map_revision: str
    state_revision: str
    event_watermark: int = Field(ge=0)
    all_mastered: bool
    concept_states: list[ConceptLearningStateView] = Field(min_length=1)


class WeaknessFindingView(_ClosedModel):
    target_formal_concept_id: str
    target_label: str
    category: Literal["observed_weak", "needs_review", "not_enough_data"]
    confidence: Literal["none", "limited", "supported"]
    claim_coverage_complete: bool
    remediation_intent: Literal["practice", "review", "collect_more_data"]
    reason: str


class PrerequisiteGapView(_ClosedModel):
    category: Literal["possible_prerequisite_gap"]
    target_formal_concept_id: str
    prerequisite_formal_concept_id: str
    prerequisite_label: str
    relation_id: str
    prerequisite_status: Literal[
        "not_started", "learning", "needs_review", "mastered"
    ]
    prerequisite_confidence: Literal["none", "limited", "supported"]
    remediation_intent: Literal["relearn_prerequisite"]
    reason: str


class WeaknessView(_ClosedModel):
    schema_: Literal["weakness/v1"] = Field(alias="schema")
    study_session_id: UUID
    base_knowledge_map_revision: str
    source_learning_state_revision: str
    event_watermark: int = Field(ge=0)
    current_formal_concept_id: str | None
    weakness_revision: str
    findings: list[WeaknessFindingView]
    immediate_prerequisite_gaps: list[PrerequisiteGapView]


class AdaptiveRouteView(_ClosedModel):
    study_session_id: UUID
    formal_concept_id: str | None
    resource_promotion_id: str | None


class AdaptiveStepView(_ClosedModel):
    action: AdaptiveAction
    target_formal_concept_id: str | None
    target_label: str | None
    reason: str
    confidence: Literal["none", "limited", "supported"]
    claim_coverage_complete: bool
    route: AdaptiveRouteView


class AdaptivePlanView(_ClosedModel):
    schema_: Literal["adaptive-plan/v1"] = Field(alias="schema")
    study_session_id: UUID
    base_knowledge_map_revision: str
    inline_initial_learning_path_sha256: str
    source_learning_state_revision: str
    event_watermark: int = Field(ge=0)
    current_formal_concept_id: str | None
    deferred_formal_concept_id: str | None
    primary_step: AdaptiveStepView
    adaptive_plan_revision: str


class SuggestionView(_ClosedModel):
    schema_: Literal["learning-suggestion/v1"] = Field(alias="schema")
    adaptive_plan_revision: str
    study_session_id: UUID
    base_knowledge_map_revision: str
    action: AdaptiveAction
    target_formal_concept_id: str | None
    target_label: str | None
    reason: str
    confidence: Literal["none", "limited", "supported"]
    claim_coverage_complete: bool
    route: AdaptiveRouteView
    fallback_action: Literal["follow_path", "collect_more_data", "no_action"]
    fallback_reason: str


class AdaptiveResponseView(_ClosedModel):
    schema_: Literal["adaptive-response/v1"] = Field(alias="schema")
    plan: AdaptivePlanView
    suggestion: SuggestionView


class AdaptivePlanApply(_ClosedModel):
    schema_: Literal["adaptive-plan-apply/v1"] = Field(alias="schema")
    adaptive_plan_revision: str = Field(
        pattern=r"^adaptive-plan:sha256:[0-9a-f]{64}$"
    )


class ApiErrorView(_ClosedModel):
    schema_: Literal["api-error/v1"] = Field(alias="schema")
    request_id: UUID
    reason_code: ApiReasonCode
    retryable: bool
    message: Literal["Request could not be completed."]


def project_material_run(run: MaterialProcessingRun) -> MaterialProcessingRunView:
    """移除 learner 與 internal runtime binding。"""

    return MaterialProcessingRunView.model_validate(
        {
            "schema": "material-processing-run/v2",
            "run_id": run.run_id,
            "material_id": run.material_id,
            "source_artifact_id": run.source_artifact_id,
            "status": run.status,
            "output_binding": deepcopy(run.output_binding),
            "error_code": run.error_code,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "completed_at": run.completed_at,
        }
    )


def project_study_session(session: StoredStudySession) -> StudySessionView:
    return StudySessionView.model_validate(
        {
            "schema": "study-session/v1",
            "study_session_id": session.study_session_id,
            "material_id": session.material_id,
            "knowledge_map_revision": session.knowledge_map_revision,
            "current_formal_concept_id": session.current_formal_concept_id,
            "deferred_formal_concept_id": session.deferred_formal_concept_id,
            "status": session.status,
            "started_at": session.started_at,
            "completed_at": session.completed_at,
            "event_watermark": session.last_event_number,
        }
    )


def project_study_context(
    session: StoredStudySession,
    context: MapContext,
) -> StudyContextView:
    concepts = {
        concept.formal_concept_id: concept
        for concept in context.formal_concepts
    }
    return StudyContextView.model_validate(
        {
            "schema": "study-context/v1",
            "study_session_id": session.study_session_id,
            "base_knowledge_map_revision": context.knowledge_map_revision,
            "current_formal_concept_id": session.current_formal_concept_id,
            "deferred_formal_concept_id": session.deferred_formal_concept_id,
            "initial_learning_path": [
                {
                    "formal_concept_id": concept_id,
                    "label": concepts[concept_id].label,
                    "claim_ids": [
                        claim.claim_id for claim in concepts[concept_id].claims
                    ],
                    "supplementary_resource_promotion_ids": [
                        resource.promotion_id
                        for resource in concepts[
                            concept_id
                        ].supplementary_resources
                    ],
                }
                for concept_id in context.initial_learning_path
            ],
        }
    )


def project_assessment(assessment: StoredAssessment) -> AssessmentView:
    public = assessment.public_document.model_dump(mode="python", by_alias=True)
    public["study_session_id"] = assessment.study_session_id
    return AssessmentView.model_validate(public)


def project_answer_feedback(feedback: AnswerFeedback) -> AnswerFeedbackView:
    return AnswerFeedbackView.model_validate(
        feedback.model_dump(mode="python", by_alias=True)
    )


def project_learning_state(state: LearningStateSnapshot) -> LearningStateView:
    return LearningStateView.model_validate(
        {
            "schema": "learning-state/v1",
            "study_session_id": state.study_session_id,
            "base_knowledge_map_revision": state.base_knowledge_map_revision,
            "state_revision": state.state_revision,
            "event_watermark": state.event_watermark,
            "all_mastered": state.all_mastered,
            "concept_states": [
                concept.model_dump(
                    mode="python",
                    exclude={
                        "source_answer_event_ids",
                        "source_event_numbers",
                        "reason_code",
                    },
                )
                for concept in state.concept_states
            ],
        }
    )


def project_weakness(weakness: WeaknessSnapshot) -> WeaknessView:
    return WeaknessView.model_validate(
        {
            "schema": "weakness/v1",
            "study_session_id": weakness.study_session_id,
            "base_knowledge_map_revision": weakness.base_knowledge_map_revision,
            "source_learning_state_revision": (
                weakness.source_learning_state_revision
            ),
            "event_watermark": weakness.event_watermark,
            "current_formal_concept_id": weakness.current_formal_concept_id,
            "weakness_revision": weakness.weakness_revision,
            "findings": [
                finding.model_dump(
                    mode="python", exclude={"supporting_answer_event_ids"}
                )
                for finding in weakness.findings
            ],
            "immediate_prerequisite_gaps": [
                gap.model_dump(
                    mode="python", exclude={"supporting_answer_event_ids"}
                )
                for gap in weakness.immediate_prerequisite_gaps
            ],
        }
    )


def project_adaptive_response(
    plan: AdaptivePlanSnapshot,
    suggestion: Suggestion,
) -> AdaptiveResponseView:
    plan_view = {
        "schema": "adaptive-plan/v1",
        "study_session_id": plan.study_session_id,
        "base_knowledge_map_revision": plan.base_knowledge_map_revision,
        "inline_initial_learning_path_sha256": (
            plan.inline_initial_learning_path_sha256
        ),
        "source_learning_state_revision": plan.source_learning_state_revision,
        "event_watermark": plan.event_watermark,
        "current_formal_concept_id": plan.current_formal_concept_id,
        "deferred_formal_concept_id": plan.deferred_formal_concept_id,
        "primary_step": plan.primary_step.model_dump(
            mode="python", exclude={"supporting_formal_concept_ids"}
        ),
        "adaptive_plan_revision": plan.adaptive_plan_revision,
    }
    return AdaptiveResponseView.model_validate(
        {
            "schema": "adaptive-response/v1",
            "plan": plan_view,
            "suggestion": suggestion.model_dump(mode="python", by_alias=True),
        }
    )
