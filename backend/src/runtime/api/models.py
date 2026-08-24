from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class FormalConceptView(_ClosedModel):
    formal_concept_id: str = Field(pattern=r"^formal-concept:sha256:[0-9a-f]{64}$")
    label: str = Field(min_length=1)
    claims: list[FormalClaimView] = Field(min_length=1)
    source_concept_ids: list[str] = Field(min_length=1)
    source_page_numbers: list[int] = Field(min_length=1)
    quality: Literal["needs_review"]
    decision: Literal["review"]
    reason_codes: list[str] = Field(min_length=1, max_length=64)


class FormalRelationView(_ClosedModel):
    relation_id: str = Field(pattern=r"^formal-relation:sha256:[0-9a-f]{64}$")
    type: Literal["prerequisite", "contains", "related"]
    source_formal_concept_id: str
    target_formal_concept_id: str
    source_evidence_ids: list[str] = Field(min_length=1)
    target_evidence_ids: list[str] = Field(min_length=1)
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
    verifier_rejected: int = Field(ge=0)
    verifier_unsupported: int = Field(ge=0)
    accepted_relations: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "RelationDiagnosticsView":
        allowed_signals = {
            "adjacent",
            "same_group",
            "same_page",
            "explicit_relation",
            "cross_reference",
            "shared_evidence",
            "shared_formula",
        }
        if (
            self.selected_pairs > self.candidate_pairs
            or self.candidate_pairs > self.possible_pairs
            or any(
                signal not in allowed_signals or count < 0
                for signal, count in self.selected_signal_counts.items()
            )
        ):
            raise ValueError("RELATION_DIAGNOSTICS_INVALID")
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
    schema_: Literal["knowledge-map-view/v4"] = Field(alias="schema")
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
        evidence_by_concept = {
            concept.formal_concept_id: {
                evidence.evidence_id
                for claim in concept.claims
                for evidence in claim.evidence
            }
            for concept in self.concepts
        }
        if any(
            not set(relation.source_evidence_ids)
            <= evidence_by_concept[relation.source_formal_concept_id]
            or not set(relation.target_evidence_ids)
            <= evidence_by_concept[relation.target_formal_concept_id]
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
