from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

from .artifact_reason_codes import (
    formal_reason_codes,
    reason_codes_are_valid,
)
from .ocr_page_evidence import canonical_bytes, canonical_sha256


OUTPUT_SCHEMA = "concept-evidence-output/v2"
AGGREGATION_POLICY = "whole-document-review-aggregation/v1"
MAX_ARTIFACT_FILE_BYTES = 16 * 1024 * 1024
RUNTIME_LOCK_SHA256 = "f8261c513b84947486ba9c116976fb7c38da3e6e84bb6de02852b17bf7372137"


def _closed(value: Any, fields: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == fields


def _reasons(
    value: Any, *, formal: bool, require_sorted: bool = True
) -> bool:
    return (
        reason_codes_are_valid(value, formal=formal)
        and (not require_sorted or value == sorted(value))
    )


def _box(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(type(number) in {int, float} and math.isfinite(number) for number in value)
        and value[0] < value[2]
        and value[1] < value[3]
    )


def validate_page_evidence(
    page: Any,
    source_binding: dict[str, Any],
    runtime_binding: dict[str, Any],
    *,
    formal_reasons: bool,
) -> bool:
    fields = {
        "schema", "material_id", "material_revision", "section_id", "page_ref",
        "page_number", "geometry", "coordinate_space", "native_evidence_ref", "render",
        "evidence_blocks", "images", "input_binding", "processing_policy",
        "normalizer_policy", "produced_at", "processing", "quality", "decision",
        "reason_codes", "page_evidence_id",
    }
    if not _closed(page, fields) or page["schema"] != "page-evidence/v2":
        return False
    if (
        page["material_id"] != f"material:sha256:{source_binding['source_sha256']}"
        or type(page["page_number"]) is not int
        or page["page_number"] not in source_binding["page_numbers"]
        or page["coordinate_space"] != "unrotated_pdf_points"
        or not isinstance(page["evidence_blocks"], list)
        or not isinstance(page["images"], list)
        or page["processing"] not in {"succeeded", "partial"}
        or (page["quality"], page["decision"]) != ("needs_review", "review")
        or not _reasons(
            page["reason_codes"],
            formal=formal_reasons,
            require_sorted=False,
        )
    ):
        return False
    geometry = page["geometry"]
    if not _closed(
        geometry,
        {"visible_points", "unrotated_points", "rotation_degrees", "derotation_matrix"},
    ):
        return False
    if (
        not _box(geometry["visible_points"])
        or not _box(geometry["unrotated_points"])
        or geometry["rotation_degrees"] not in {0, 90, 180, 270}
        or not isinstance(geometry["derotation_matrix"], list)
        or len(geometry["derotation_matrix"]) != 6
        or any(type(number) not in {int, float} or not math.isfinite(number) for number in geometry["derotation_matrix"])
    ):
        return False
    render = page["render"]
    if not _closed(
        render,
        {"schema", "policy", "dpi", "colorspace", "format", "coverage", "pymupdf_version", "width", "height", "sha256"},
    ):
        return False
    if (
        render["schema"] != "page-render/v1"
        or render["policy"] != "pymupdf-rgb-200dpi/v1"
        or render["dpi"] != 200
        or render["colorspace"] != "RGB"
        or render["format"] != "PNG"
        or render["coverage"] != "full_visible_page"
        or type(render["width"]) is not int
        or type(render["height"]) is not int
        or render["width"] < 1
        or render["height"] < 1
        or not isinstance(render["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", render["sha256"]) is None
    ):
        return False
    input_binding = page["input_binding"]
    if not _closed(input_binding, {"source_sha256", "page_number", "render_sha256", "page", "ocr"}):
        return False
    if (
        input_binding["source_sha256"] != source_binding["source_sha256"]
        or input_binding["page_number"] != page["page_number"]
        or input_binding["render_sha256"] != render["sha256"]
        or input_binding["page"] != runtime_binding.get("page")
        or input_binding["ocr"] != runtime_binding.get("ocr")
    ):
        return False
    evidence_ids: set[str] = set()
    for block in page["evidence_blocks"]:
        if not _closed(
            block,
            {"evidence_id", "block_id", "ocr_type", "kind", "text", "reading_order", "locator", "render_region", "source"},
        ):
            return False
        locator = block["locator"]
        if (
            not _closed(locator, {"page", "block_id", "region"})
            or locator["page"] != page["page_number"]
            or locator["block_id"] != block["block_id"]
            or not _box(locator["region"])
            or not _box(block["render_region"])
            or block["source"] != "unlimited_ocr"
            or block["evidence_id"] in evidence_ids
        ):
            return False
        evidence_ids.add(block["evidence_id"])
    if not evidence_ids:
        return False
    image_ids: set[str] = set()
    for image in page["images"]:
        if not _closed(
            image,
            {"image_id", "image_hash", "region", "caption_evidence_ids", "nearby_evidence_ids"},
        ):
            return False
        references = image["caption_evidence_ids"] + image["nearby_evidence_ids"]
        if (
            not _box(image["region"])
            or not all(isinstance(items, list) for items in (image["caption_evidence_ids"], image["nearby_evidence_ids"]))
            or len(references) != len(set(references))
            or not set(references) <= evidence_ids
            or not isinstance(image["image_id"], str)
            or image["image_id"] in image_ids
        ):
            return False
        image_ids.add(image["image_id"])
    identity = dict(page)
    page_evidence_id = identity.pop("page_evidence_id")
    return page_evidence_id == f"page-evidence:sha256:{canonical_sha256(identity)}"


def validate_output_document(output: Any) -> bool:
    """重驗完整 producer output；identity 重算也不能帶入額外欄位。"""

    fields = {
        "schema", "aggregation_policy", "run_id", "produced_at", "material_id",
        "material_revision", "source_binding", "pages", "excluded_pages", "concepts",
        "rejected_candidates", "runtime_binding", "processing", "quality", "decision",
        "reason_codes", "output_id",
    }
    if not _closed(output, fields) or output["schema"] != OUTPUT_SCHEMA:
        return False
    source_binding = output["source_binding"]
    runtime_binding = output["runtime_binding"]
    if not _closed(source_binding, {"source_sha256", "page_numbers"}):
        return False
    page_numbers = source_binding["page_numbers"]
    if (
        not isinstance(source_binding["source_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", source_binding["source_sha256"]) is None
        or not isinstance(page_numbers, list)
        or page_numbers != list(range(1, len(page_numbers) + 1))
        or not page_numbers
        or canonical_sha256(runtime_binding) != RUNTIME_LOCK_SHA256
        or output["aggregation_policy"] != AGGREGATION_POLICY
        or (output["quality"], output["decision"]) != ("needs_review", "review")
        or output["processing"] not in {"succeeded", "partial"}
        or not _reasons(output["reason_codes"], formal=True)
    ):
        return False
    pages = output["pages"]
    excluded_pages = output["excluded_pages"]
    if (
        not isinstance(pages, list)
        or not pages
        or not isinstance(excluded_pages, list)
    ):
        return False
    if any(
        not validate_page_evidence(
            page,
            source_binding,
            runtime_binding,
            formal_reasons=True,
        )
        for page in pages
    ):
        return False
    page_refs = {page["page_ref"]: page["page_number"] for page in pages}
    if len(page_refs) != len(pages) or len(set(page_refs.values())) != len(pages):
        return False
    excluded_refs: set[str] = set()
    excluded_numbers: set[int] = set()
    excluded_fields = {
        "page_ref", "page_number", "page_evidence_id", "last_stage", "processing",
        "quality", "decision", "reason_codes",
    }
    for page in excluded_pages:
        if (
            not _closed(page, excluded_fields)
            or page["page_ref"] in page_refs
            or page["page_ref"] in excluded_refs
            or type(page["page_number"]) is not int
            or page["page_number"] not in page_numbers
            or page["page_number"] in excluded_numbers
            or page["last_stage"] not in {"page_evidence", "concept"}
            or (page["processing"], page["quality"], page["decision"])
            != ("failed", "needs_review", "reject")
            or not _reasons(page["reason_codes"], formal=True)
        ):
            return False
        excluded_refs.add(page["page_ref"])
        excluded_numbers.add(page["page_number"])
    if set(page_refs.values()) | excluded_numbers != set(page_numbers):
        return False
    evidence_pages: dict[str, str] = {}
    for page in pages:
        for block in page["evidence_blocks"]:
            evidence_id = block["evidence_id"]
            if evidence_id in evidence_pages:
                return False
            evidence_pages[evidence_id] = page["page_ref"]
    concept_ids: set[str] = set()
    concept_page_refs: set[str] = set()
    has_partial_concept = False
    concept_fields = {
        "concept_id", "page_ref", "label", "definition", "key_points", "evidence_ids",
        "processing", "quality", "decision", "reason_codes",
    }
    if not isinstance(output["concepts"], list) or not 1 <= len(output["concepts"]) <= 24 * len(pages):
        return False
    for concept in output["concepts"]:
        if not _closed(concept, concept_fields):
            return False
        references = concept["evidence_ids"]
        if (
            concept["concept_id"] in concept_ids
            or concept["page_ref"] not in page_refs
            or not isinstance(references, list)
            or not 1 <= len(references) <= 16
            or len(references) != len(set(references))
            or any(evidence_pages.get(reference) != concept["page_ref"] for reference in references)
            or concept["processing"] not in {"succeeded", "partial"}
            or (concept["quality"], concept["decision"])
            != ("needs_review", "review")
            or not _reasons(concept["reason_codes"], formal=True)
        ):
            return False
        concept_ids.add(concept["concept_id"])
        concept_page_refs.add(concept["page_ref"])
        has_partial_concept = has_partial_concept or concept["processing"] == "partial"
    if concept_page_refs != set(page_refs):
        return False
    rejected_fields = {
        "page_ref", "candidate_index", "processing", "quality", "decision", "reason_codes"
    }
    if not isinstance(output["rejected_candidates"], list) or len(output["rejected_candidates"]) > 24 * len(pages):
        return False
    for rejected in output["rejected_candidates"]:
        if (
            not _closed(rejected, rejected_fields)
            or rejected["page_ref"] not in page_refs
            or type(rejected["candidate_index"]) is not int
            or rejected["candidate_index"] < 0
            or (rejected["processing"], rejected["quality"], rejected["decision"])
            != ("failed", "needs_review", "reject")
            or not _reasons(rejected["reason_codes"], formal=True)
        ):
            return False
    is_partial = (
        bool(excluded_pages)
        or any(page["processing"] == "partial" for page in pages)
        or has_partial_concept
        or bool(output["rejected_candidates"])
    )
    if (output["processing"] == "partial") != is_partial:
        return False
    identity = dict(output)
    output_id = identity.pop("output_id")
    try:
        return (
            output_id == f"concept-evidence-output:sha256:{canonical_sha256(identity)}"
            and len(canonical_bytes(output)) <= MAX_ARTIFACT_FILE_BYTES
        )
    except (RecursionError, TypeError, ValueError):
        return False


def build_output(
    *,
    run_id: str,
    produced_at: str,
    source_binding: dict[str, Any],
    pages: list[dict[str, Any]],
    semantic_pages: list[dict[str, Any]],
    runtime_binding: dict[str, Any],
    run_reasons: list[str],
    excluded_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    excluded = deepcopy(excluded_pages or [])
    if not pages or not semantic_pages:
        raise ValueError("NO_USABLE_CONCEPT")

    formal_pages = deepcopy(pages)
    for page in formal_pages:
        page["reason_codes"] = formal_reason_codes(page["reason_codes"])
        page.pop("page_evidence_id")
        page["page_evidence_id"] = (
            "page-evidence:sha256:" + canonical_sha256(page)
        )
    for page in excluded:
        page["reason_codes"] = formal_reason_codes(page["reason_codes"])

    concepts = []
    for semantic_page in semantic_pages:
        for source_concept in semantic_page["concepts"]:
            concept = deepcopy(source_concept)
            concept["processing"] = semantic_page["processing"]
            concept["quality"] = "needs_review"
            concept["decision"] = "review"
            concept["reason_codes"] = formal_reason_codes(
                concept["reason_codes"]
            )
            concepts.append(concept)
    if not concepts:
        raise ValueError("NO_USABLE_CONCEPT")
    page_numbers = {page["page_ref"]: page["page_number"] for page in formal_pages}
    concepts.sort(
        key=lambda concept: (page_numbers[concept["page_ref"]], concept["concept_id"])
    )

    rejected = []
    for page in semantic_pages:
        for source_candidate in page["rejected_candidates"]:
            candidate = {"page_ref": page["page_ref"], **deepcopy(source_candidate)}
            candidate["reason_codes"] = formal_reason_codes(
                candidate["reason_codes"]
            )
            rejected.append(candidate)
    reasons = run_reasons + ["SEMANTIC_REVIEW_REQUIRED"]
    reasons.extend(reason for page in pages for reason in page["reason_codes"])
    reasons.extend(reason for page in semantic_pages for reason in page["reason_codes"])
    if excluded:
        reasons.append("PAGE_CONTENT_EXCLUDED")
    output = {
        "schema": OUTPUT_SCHEMA,
        "aggregation_policy": AGGREGATION_POLICY,
        "run_id": run_id,
        "produced_at": produced_at,
        "material_id": formal_pages[0]["material_id"],
        "material_revision": formal_pages[0]["material_revision"],
        "source_binding": deepcopy(source_binding),
        "pages": formal_pages,
        "excluded_pages": excluded,
        "concepts": concepts,
        "rejected_candidates": rejected,
        "runtime_binding": deepcopy(runtime_binding),
        "processing": "partial" if (
            excluded
            or any(page["processing"] == "partial" for page in formal_pages)
            or any(page["processing"] == "partial" for page in semantic_pages)
        ) else "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": formal_reason_codes(reasons),
    }
    output["output_id"] = f"concept-evidence-output:sha256:{canonical_sha256(output)}"
    if len(canonical_bytes(output)) > MAX_ARTIFACT_FILE_BYTES:
        raise ValueError("PROTOCOL_LIMIT_EXCEEDED")
    if not validate_output_document(output):
        raise ValueError("PRODUCER_BUNDLE_INVALID")
    return output
