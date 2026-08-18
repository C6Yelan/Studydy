"""Formal material runtime 的封閉 public models。"""

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
    schema_: Literal["material-run-output-binding/v2"] = Field(alias="schema")
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
    page_count: int = Field(ge=1, le=32)
    processing: Literal["succeeded", "partial"]
    quality: Literal["needs_review"]
    decision: Literal["review"]
    reason_codes: list[str] = Field(min_length=1, max_length=64)
    ocr_calls: int = Field(ge=0, le=32)
    concept_calls: int = Field(ge=0, le=64)

    @model_validator(mode="after")
    def validate_counts(self) -> "MaterialOutputBinding":
        if (
            len(self.reason_codes) != len(set(self.reason_codes))
            or self.reason_codes != sorted(self.reason_codes)
            or self.ocr_calls > self.page_count
            or self.concept_calls > 2 * self.page_count
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
    def validate_terminal_shape(self) -> "MaterialProcessingRunView":
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
    page_number: int = Field(ge=1, le=32)
    kind: str = Field(min_length=1, max_length=64)
    region: RegionView


class ReviewConceptView(_ClosedModel):
    concept_id: str = Field(pattern=r"^concept:sha256:[0-9a-f]{64}$")
    label: str = Field(min_length=1, max_length=120)
    definition: str = Field(min_length=1, max_length=1_000)
    key_points: list[str] = Field(min_length=1, max_length=10)
    page_ref: str = Field(pattern=r"^page:sha256:[0-9a-f]{64}$")
    evidence: list[EvidenceView] = Field(min_length=1, max_length=16)
    quality: Literal["needs_review"]
    decision: Literal["review"]
    reason_codes: list[str] = Field(min_length=1, max_length=64)


class ImageLiteView(_ClosedModel):
    image_id: str = Field(pattern=r"^image:sha256:[0-9a-f]{64}$")
    page_ref: str = Field(pattern=r"^page:sha256:[0-9a-f]{64}$")
    page_number: int = Field(ge=1, le=32)
    region: RegionView
    evidence: list[EvidenceView] = Field(max_length=8)


class ExcludedPageView(_ClosedModel):
    page_ref: str = Field(pattern=r"^page:sha256:[0-9a-f]{64}$")
    page_number: int = Field(ge=1, le=32)
    page_evidence_id: str | None
    last_stage: Literal["page_evidence", "concept"]
    processing: Literal["failed"]
    quality: Literal["needs_review"]
    decision: Literal["reject"]
    reason_codes: list[str] = Field(min_length=1, max_length=64)


class ArtifactStatusView(_ClosedModel):
    processing: Literal["succeeded", "partial"]
    quality: Literal["needs_review"]
    decision: Literal["review"]
    reason_codes: list[str] = Field(min_length=1, max_length=64)


class KnowledgeMapView(_ClosedModel):
    schema_: Literal["knowledge-map-view/v2"] = Field(alias="schema")
    material_ref: str = Field(pattern=r"^material:sha256:[0-9a-f]{64}$")
    knowledge_map_revision: str = Field(
        pattern=r"^knowledge-map:sha256:[0-9a-f]{64}$"
    )
    source_output_id: str = Field(
        pattern=r"^study-material-output:sha256:[0-9a-f]{64}$"
    )
    status: ArtifactStatusView
    concepts: list[ReviewConceptView] = Field(min_length=1, max_length=768)
    images: list[ImageLiteView] = Field(max_length=8_192)
    excluded_pages: list[ExcludedPageView] = Field(max_length=32)

    @model_validator(mode="after")
    def validate_same_page_links(self) -> "KnowledgeMapView":
        included_refs = {concept.page_ref for concept in self.concepts}
        reason_lists = [
            self.status.reason_codes,
            *(concept.reason_codes for concept in self.concepts),
            *(page.reason_codes for page in self.excluded_pages),
        ]
        if any(reasons != sorted(set(reasons)) for reasons in reason_lists):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if len({concept.concept_id for concept in self.concepts}) != len(self.concepts):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if len({image.image_id for image in self.images}) != len(self.images):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if len({page.page_ref for page in self.excluded_pages}) != len(self.excluded_pages):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if len({page.page_number for page in self.excluded_pages}) != len(self.excluded_pages):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if any(len({item.evidence_id for item in concept.evidence}) != len(concept.evidence) for concept in self.concepts):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if any(len({item.evidence_id for item in image.evidence}) != len(image.evidence) for image in self.images):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if any(
            evidence.page_ref != concept.page_ref
            for concept in self.concepts
            for evidence in concept.evidence
        ):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if any(
            evidence.page_ref != image.page_ref
            for image in self.images
            for evidence in image.evidence
        ):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if any(page.page_ref in included_refs for page in self.excluded_pages):
            raise ValueError("KNOWLEDGE_MAP_VIEW_INVALID")
        if (self.status.processing == "partial") != bool(self.excluded_pages):
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
