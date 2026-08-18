"""建立、發布並重驗本機文字優先流程的封閉產物。"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

from .ocr_page_evidence import canonical_bytes, canonical_sha256


OUTPUT_SCHEMA = "concept-evidence-output/v2"
TERMINAL_SCHEMA = "text-first-run-terminal/v2"
BUNDLE_SCHEMA = "text-first-producer-bundle/v1"
AGGREGATION_POLICY = "whole-document-review-aggregation/v1"
MAX_BUNDLE_FILE_BYTES = 16 * 1024 * 1024
RUNTIME_LOCK_SHA256 = "b777829253b40ddca6a6fb9f076ddd6125f12b6e3f4450a85fa6737f5321f967"
_SHA_REF = re.compile(r"[a-z-]+:sha256:[0-9a-f]{64}")
KNOWN_REASONS = {
    "MEDIA_TYPE_INVALID",
    "SOURCE_READ_FAILED",
    "SOURCE_HASH_MISMATCH",
    "PDF_INVALID",
    "PDF_ENCRYPTED",
    "PAGE_SELECTION_INVALID",
    "MATERIAL_PAGE_LIMIT_EXCEEDED",
    "RUNTIME_BINDING_INVALID",
    "RUNTIME_BUSY",
    "PROTOCOL_LIMIT_EXCEEDED",
    "CHILD_TIMEOUT",
    "CHILD_EXITED",
    "CHILD_RESPONSE_INVALID",
    "MODEL_OOM",
    "MODEL_GENERATION_FAILED",
    "MODEL_INPUT_TOO_LARGE",
    "OCR_OUTPUT_INVALID",
    "OCR_LOCATOR_INVALID",
    "NO_USABLE_EVIDENCE",
    "PAGE_CONTENT_REVIEW_REQUIRED",
    "PAGE_CONTENT_EXCLUDED",
    "MODEL_OUTPUT_TOO_LARGE",
    "MODEL_OUTPUT_INVALID_JSON",
    "MODEL_OUTPUT_TRUNCATED",
    "CANDIDATE_SCHEMA_INVALID",
    "INVALID_CONCEPT_COUNT",
    "INVALID_TEXT_FIELD",
    "INVALID_KEY_POINTS",
    "INVALID_EVIDENCE_REFERENCES",
    "DUPLICATE_EVIDENCE_REFERENCE",
    "UNKNOWN_EVIDENCE_ID",
    "NO_USABLE_CONCEPT",
    "TRAILING_QUOTE_REMOVED",
    "SEMANTIC_REVIEW_REQUIRED",
    "CACHE_INVALID",
    "CACHE_WRITE_FAILED",
    "ARTIFACT_COLLISION",
    "FINAL_OUTPUT_WRITE_FAILED",
    "RUN_TERMINAL_WRITE_FAILED",
    "PRODUCER_BUNDLE_INVALID",
    "INTERNAL_FAILURE",
}


def clean_reasons(reasons: list[str]) -> list[str]:
    return sorted(
        {reason if reason in KNOWN_REASONS else "INTERNAL_FAILURE" for reason in reasons}
    )


def _closed(value: Any, fields: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == fields


def _reasons(value: Any, *, require_sorted: bool = True) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(reason, str) and reason in KNOWN_REASONS for reason in value)
        and len(value) == len(set(value))
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


def _page_is_valid(page: Any, source_binding: dict[str, Any], runtime_binding: dict[str, Any]) -> bool:
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
        or (page["processing"], page["quality"], page["decision"])
        != ("partial", "needs_review", "review")
        or not _reasons(page["reason_codes"], require_sorted=False)
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
    if not 1 <= len(evidence_ids) <= 64:
        return False
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
        ):
            return False
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
        or not 1 <= len(page_numbers) <= 32
        or canonical_sha256(runtime_binding) != RUNTIME_LOCK_SHA256
        or output["aggregation_policy"] != AGGREGATION_POLICY
        or (output["quality"], output["decision"]) != ("needs_review", "review")
        or output["processing"] not in {"succeeded", "partial"}
        or not _reasons(output["reason_codes"])
    ):
        return False
    pages = output["pages"]
    excluded_pages = output["excluded_pages"]
    if (
        not isinstance(pages, list)
        or not 1 <= len(pages) <= 32
        or not isinstance(excluded_pages, list)
        or len(excluded_pages) > 31
    ):
        return False
    if any(not _page_is_valid(page, source_binding, runtime_binding) for page in pages):
        return False
    if any(len(page["images"]) > 256 for page in pages):
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
            or not _reasons(page["reason_codes"])
        ):
            return False
        excluded_refs.add(page["page_ref"])
        excluded_numbers.add(page["page_number"])
    if set(page_refs.values()) | excluded_numbers != set(page_numbers):
        return False
    evidence_pages = {
        block["evidence_id"]: page["page_ref"]
        for page in pages
        for block in page["evidence_blocks"]
    }
    concept_ids: set[str] = set()
    concept_processing: set[str] = set()
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
            or not _reasons(concept["reason_codes"])
        ):
            return False
        concept_ids.add(concept["concept_id"])
        concept_processing.add(concept["processing"])
    if len(concept_processing) != 1:
        return False
    is_qualification_partial = concept_processing == {"partial"} and not excluded_pages
    if (output["processing"] == "partial") != (bool(excluded_pages) or is_qualification_partial):
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
            or not _reasons(rejected["reason_codes"])
        ):
            return False
    identity = dict(output)
    output_id = identity.pop("output_id")
    try:
        return (
            output_id == f"concept-evidence-output:sha256:{canonical_sha256(identity)}"
            and len(canonical_bytes(output)) <= MAX_BUNDLE_FILE_BYTES
        )
    except (RecursionError, TypeError, ValueError):
        return False


def _page_number(page_ref: str, pages: dict[str, dict[str, Any]]) -> int:
    page = pages.get(page_ref)
    if page is None or type(page.get("page_number")) is not int:
        raise ValueError("UNKNOWN_EVIDENCE_ID")
    return page["page_number"]


def _validate_page_links(
    page_artifacts: list[dict[str, Any]], semantic_pages: list[dict[str, Any]]
) -> None:
    """Concept 與 image-lite Evidence 必須留在自己的 PDF 頁面。"""

    pages: dict[str, dict[str, Any]] = {}
    evidence_pages: dict[str, str] = {}
    for page in page_artifacts:
        page_ref = page.get("page_ref")
        if not isinstance(page_ref, str) or page_ref in pages:
            raise ValueError("OCR_LOCATOR_INVALID")
        if page.get("coordinate_space") != "unrotated_pdf_points":
            raise ValueError("OCR_LOCATOR_INVALID")
        pages[page_ref] = page
        for block in page.get("evidence_blocks", []):
            evidence_id = block.get("evidence_id") if isinstance(block, dict) else None
            locator = block.get("locator") if isinstance(block, dict) else None
            if (
                not isinstance(evidence_id, str)
                or evidence_id in evidence_pages
                or not isinstance(locator, dict)
                or locator.get("page") != page.get("page_number")
            ):
                raise ValueError("OCR_LOCATOR_INVALID")
            evidence_pages[evidence_id] = page_ref
        for image in page.get("images", []):
            if not isinstance(image, dict):
                raise ValueError("OCR_LOCATOR_INVALID")
            references = image.get("caption_evidence_ids", []) + image.get(
                "nearby_evidence_ids", []
            )
            if any(evidence_pages.get(item) != page_ref for item in references):
                raise ValueError("UNKNOWN_EVIDENCE_ID")

    semantic_refs: set[str] = set()
    for semantic_page in semantic_pages:
        page_ref = semantic_page.get("page_ref")
        if not isinstance(page_ref, str) or page_ref in semantic_refs or page_ref not in pages:
            raise ValueError("UNKNOWN_EVIDENCE_ID")
        semantic_refs.add(page_ref)
        for concept in semantic_page.get("concepts", []):
            if concept.get("page_ref") != page_ref or any(
                evidence_pages.get(evidence_id) != page_ref
                for evidence_id in concept.get("evidence_ids", [])
            ):
                raise ValueError("UNKNOWN_EVIDENCE_ID")
    if semantic_refs != set(pages):
        raise ValueError("NO_USABLE_CONCEPT")


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
    _validate_page_links(pages, semantic_pages)

    concepts = []
    for semantic_page in semantic_pages:
        for source_concept in semantic_page["concepts"]:
            concept = deepcopy(source_concept)
            concept["processing"] = "succeeded"
            concept["quality"] = "needs_review"
            concept["decision"] = "review"
            concepts.append(concept)
    if not concepts:
        raise ValueError("NO_USABLE_CONCEPT")
    concepts.sort(key=lambda concept: (concept["page_ref"], concept["concept_id"]))

    rejected = [
        {"page_ref": page["page_ref"], **deepcopy(candidate)}
        for page in semantic_pages
        for candidate in page["rejected_candidates"]
    ]
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
        "material_id": pages[0]["material_id"],
        "material_revision": pages[0]["material_revision"],
        "source_binding": deepcopy(source_binding),
        "pages": deepcopy(pages),
        "excluded_pages": excluded,
        "concepts": concepts,
        "rejected_candidates": rejected,
        "runtime_binding": deepcopy(runtime_binding),
        "processing": "partial" if excluded else "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": clean_reasons(reasons),
    }
    output["output_id"] = f"concept-evidence-output:sha256:{canonical_sha256(output)}"
    if len(canonical_bytes(output)) > MAX_BUNDLE_FILE_BYTES:
        raise ValueError("PROTOCOL_LIMIT_EXCEEDED")
    if not validate_output_document(output):
        raise ValueError("PRODUCER_BUNDLE_INVALID")
    return output


def build_terminal(
    *,
    run_id: str,
    produced_at: str,
    output: dict[str, Any] | None,
    runtime_binding_sha256: str,
    reasons: list[str],
    duration_ms: int,
    ocr_calls: int,
    concept_calls: int,
    ocr_loads: int = 0,
    concept_loads: int = 0,
    page_count: int = 0,
    excluded_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    failed = output is None
    excluded_count = (
        len(output.get("excluded_pages", []))
        if output is not None
        else len(excluded_pages or [])
    )
    included_count = len(output.get("pages", [])) if output is not None else 0
    processing = "failed" if failed else output["processing"]
    return {
        "schema": TERMINAL_SCHEMA,
        "aggregation_policy": AGGREGATION_POLICY,
        "run_id": run_id,
        "produced_at": produced_at,
        "output_id": output["output_id"] if output is not None else None,
        "runtime_binding_sha256": runtime_binding_sha256,
        "page_count": page_count or included_count + excluded_count,
        "included_page_count": included_count,
        "excluded_page_count": excluded_count,
        "processing": processing,
        "quality": "needs_review",
        "decision": "reject" if failed else "review",
        "reason_codes": clean_reasons(reasons),
        "duration_ms": duration_ms,
        "ocr_calls": ocr_calls,
        "concept_calls": concept_calls,
        "ocr_loads": ocr_loads,
        "concept_loads": concept_loads,
    }
