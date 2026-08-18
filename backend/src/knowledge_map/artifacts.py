"""由 Study Material Output 建立只供複核的 Knowledge Map。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.study_material_output import _shape_is_valid, validate_study_material_output


KNOWLEDGE_MAP_SCHEMA = "knowledge-map/v2"
KNOWLEDGE_MAP_VIEW_SCHEMA = "knowledge-map-view/v2"


def _revision(document: dict[str, Any]) -> str:
    content = {key: value for key, value in document.items() if key != "revision"}
    return "knowledge-map:sha256:" + canonical_sha256(content)


def _shape_is_closed(knowledge_map: Any) -> bool:
    fields = {
        "schema", "source_output_id", "material_ref", "pages", "excluded_pages",
        "concepts", "evidence_index", "images", "processing", "quality", "decision",
        "reason_codes", "revision",
    }
    if not isinstance(knowledge_map, dict) or set(knowledge_map) != fields:
        return False
    if not isinstance(knowledge_map["pages"], list) or not isinstance(
        knowledge_map["excluded_pages"], list
    ):
        return False
    page_count = len(knowledge_map["pages"]) + len(knowledge_map["excluded_pages"])
    source_binding = {
        "source_sha256": "0" * 64,
        "page_count": page_count,
        "producer_output_id": "concept-evidence-output:sha256:" + "0" * 64,
        "runtime_binding_sha256": "0" * 64,
    }
    study_shape = {
        "schema": "study-material-output/v3",
        "run_id": "text-first-run:00000000-0000-4000-8000-000000000000",
        "produced_at": "2026-01-01T00:00:00Z",
        "material_ref": knowledge_map["material_ref"],
        "source_binding": source_binding,
        "pages": knowledge_map["pages"],
        "excluded_pages": knowledge_map["excluded_pages"],
        "concepts": knowledge_map["concepts"],
        "evidence_index": knowledge_map["evidence_index"],
        "images": knowledge_map["images"],
        "processing": knowledge_map["processing"],
        "quality": knowledge_map["quality"],
        "decision": knowledge_map["decision"],
        "reason_codes": knowledge_map["reason_codes"],
    }
    study_shape["output_id"] = "study-material-output:sha256:" + canonical_sha256(study_shape)
    return _shape_is_valid(study_shape)


def build_review_knowledge_map(study_material_output: dict[str, Any]) -> dict[str, Any]:
    """不把 Qwen Concept 推升成正式 Relation 或 Learning Path。"""

    if validate_study_material_output(study_material_output) is not None:
        raise ValueError("KNOWLEDGE_MAP_SOURCE_INVALID")
    document = {
        "schema": KNOWLEDGE_MAP_SCHEMA,
        "source_output_id": study_material_output["output_id"],
        "material_ref": study_material_output["material_ref"],
        "pages": deepcopy(study_material_output["pages"]),
        "excluded_pages": deepcopy(study_material_output["excluded_pages"]),
        "concepts": deepcopy(study_material_output["concepts"]),
        "evidence_index": deepcopy(study_material_output["evidence_index"]),
        "images": deepcopy(study_material_output["images"]),
        "processing": study_material_output["processing"],
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": sorted(
            set(study_material_output["reason_codes"])
            | {"KNOWLEDGE_MAP_REVIEW_REQUIRED"}
        ),
    }
    document["revision"] = _revision(document)
    return document


def validate_knowledge_map(
    knowledge_map: Any, study_material_output: dict[str, Any] | None = None
) -> str | None:
    try:
        if not _shape_is_closed(knowledge_map) or knowledge_map["schema"] != KNOWLEDGE_MAP_SCHEMA:
            return "KNOWLEDGE_MAP_INVALID"
        if knowledge_map.get("revision") != _revision(knowledge_map):
            return "KNOWLEDGE_MAP_INVALID"
        if study_material_output is not None and knowledge_map != build_review_knowledge_map(
            study_material_output
        ):
            return "KNOWLEDGE_MAP_INVALID"
    except (KeyError, TypeError, ValueError):
        return "KNOWLEDGE_MAP_INVALID"
    return None


def build_knowledge_map_view(knowledge_map: dict[str, Any]) -> dict[str, Any]:
    """公開 view 不含 Evidence text、runtime binding 或 raw model/OCR 內容。"""

    if validate_knowledge_map(knowledge_map) is not None:
        raise ValueError("KNOWLEDGE_MAP_INVALID")
    evidence_by_id = {
        evidence["evidence_id"]: evidence
        for evidence in knowledge_map["evidence_index"]
    }
    concepts = []
    for concept in knowledge_map["concepts"]:
        evidence = []
        for evidence_id in concept["evidence_ids"]:
            locator = evidence_by_id.get(evidence_id)
            if locator is None or locator["page_ref"] != concept["page_ref"]:
                raise ValueError("KNOWLEDGE_MAP_INVALID")
            evidence.append(deepcopy(locator))
        concepts.append(
            {
                "concept_id": concept["concept_id"],
                "label": concept["label"],
                "definition": concept["definition"],
                "key_points": deepcopy(concept["key_points"]),
                "page_ref": concept["page_ref"],
                "evidence": evidence,
                "quality": concept["quality"],
                "decision": concept["decision"],
                "reason_codes": deepcopy(concept["reason_codes"]),
            }
        )
    images = []
    for image in knowledge_map["images"]:
        references = []
        for evidence_id in image["caption_evidence_ids"] + image["nearby_evidence_ids"]:
            locator = evidence_by_id.get(evidence_id)
            if locator is None or locator["page_ref"] != image["page_ref"]:
                raise ValueError("KNOWLEDGE_MAP_INVALID")
            references.append(deepcopy(locator))
        images.append(
            {
                "image_id": image["image_id"],
                "page_ref": image["page_ref"],
                "page_number": image["page_number"],
                "region": deepcopy(image["region"]),
                "evidence": references,
            }
        )
    return {
        "schema": KNOWLEDGE_MAP_VIEW_SCHEMA,
        "material_ref": knowledge_map["material_ref"],
        "knowledge_map_revision": knowledge_map["revision"],
        "source_output_id": knowledge_map["source_output_id"],
        "status": {
            "processing": knowledge_map["processing"],
            "quality": knowledge_map["quality"],
            "decision": knowledge_map["decision"],
            "reason_codes": deepcopy(knowledge_map["reason_codes"]),
        },
        "concepts": concepts,
        "images": images,
        "excluded_pages": deepcopy(knowledge_map["excluded_pages"]),
    }
