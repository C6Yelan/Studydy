from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

from .artifact_reason_codes import (
    formal_reason_codes,
    reason_codes_are_valid,
)
from .concept_generation import (
    build_semantic_request,
    claim_id,
    concept_id,
    fitted_semantic_request_matches_source,
    validate_semantic_request,
)
from .document_context import (
    validate_document_context,
    validate_document_context_shape,
)
from .ocr_page_evidence import canonical_bytes, canonical_sha256


OUTPUT_SCHEMA = "concept-evidence-output/v5"
AGGREGATION_POLICY = "whole-document-review-aggregation/v1"
MAX_ARTIFACT_FILE_BYTES = 16 * 1024 * 1024
RUNTIME_LOCK_SHA256 = "dd9415eb4fbec268663e9ba8bf80b065bbaab86b1824e55fc54c97be5b004358"


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


def _semantic_request_valid(request: Any) -> bool:
    try:
        return bool(validate_semantic_request(request))
    except (KeyError, TypeError, ValueError):
        return False


def _claim_is_valid(
    claim: Any,
    page_ref: str,
    evidence_pages: dict[str, str],
    kind: str,
    *,
    index: int | None = None,
) -> bool:
    if not _closed(claim, {"claim_id", "text", "evidence_ids"}):
        return False
    references = claim["evidence_ids"]
    return (
        claim["claim_id"]
        == claim_id(
            page_ref,
            kind,
            {"text": claim["text"], "evidence_ids": references},
            index=index,
        )
        and isinstance(claim["text"], str)
        and bool(claim["text"])
        and isinstance(references, list)
        and bool(references)
        and len(references) == len(set(references))
        and all(evidence_pages.get(reference) == page_ref for reference in references)
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
        "page_number", "geometry", "coordinate_space", "native_evidence_ref", "route", "render",
        "evidence_blocks", "images", "input_binding", "processing_policy",
        "normalizer_policy", "produced_at", "processing", "quality", "decision",
        "reason_codes", "page_evidence_id",
    }
    if not _closed(page, fields) or page["schema"] != "page-evidence/v3":
        return False
    if (
        page["material_id"] != f"material:sha256:{source_binding['source_sha256']}"
        or type(page["page_number"]) is not int
        or page["page_number"] not in source_binding["page_numbers"]
        or page["coordinate_space"] != "unrotated_pdf_points"
        or not isinstance(page["evidence_blocks"], list)
        or not isinstance(page["images"], list)
        or page["route"] not in {"native_sufficient", "OCR_needed"}
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
    if not _closed(input_binding, {"source_sha256", "page_number", "render_sha256", "route", "page", "ocr"}):
        return False
    if (
        input_binding["source_sha256"] != source_binding["source_sha256"]
        or input_binding["page_number"] != page["page_number"]
        or input_binding["render_sha256"] != render["sha256"]
        or input_binding["route"] != page["route"]
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
            or block["source"] not in {"native_text", "unlimited_ocr"}
            or (block["source"] == "native_text") != (page["route"] == "native_sufficient")
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
        "rejected_candidates", "document_contexts", "semantic_batches",
        "runtime_binding", "processing", "quality", "decision", "reason_codes",
        "output_id",
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
    document_contexts = output["document_contexts"]
    if (
        not isinstance(document_contexts, list)
        or len(document_contexts) != len(pages)
        or any(not isinstance(context, dict) for context in document_contexts)
        or {context.get("page_ref") for context in document_contexts}
        != set(page_refs)
        or any(not validate_document_context_shape(context) for context in document_contexts)
    ):
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
    evidence_blocks: dict[str, dict[str, Any]] = {}
    for page in pages:
        for block in page["evidence_blocks"]:
            evidence_id = block["evidence_id"]
            if evidence_id in evidence_pages:
                return False
            evidence_pages[evidence_id] = page["page_ref"]
            evidence_blocks[evidence_id] = block
    contexts_by_page = {
        context["page_ref"]: context for context in document_contexts
    }
    current_blocks: dict[str, dict[str, Any]] = {}
    for page_ref, context in contexts_by_page.items():
        if (
            context["material_id"] != output["material_id"]
            or context["material_revision"] != output["material_revision"]
            or context["page_number"] != page_refs[page_ref]
        ):
            return False
        reading_orders = [
            block["reading_order"] for block in context["current_blocks"]
        ]
        if (
            reading_orders != sorted(reading_orders)
            or len(reading_orders) != len(set(reading_orders))
        ):
            return False
        for current in context["current_blocks"]:
            source = evidence_blocks.get(current["evidence_id"])
            if (
                source is None
                or evidence_pages[current["evidence_id"]] != page_ref
                or source["block_id"] != current["block_id"]
                or source["reading_order"] != current["reading_order"]
                or current["evidence_id"] in current_blocks
            ):
                return False
            current_blocks[current["evidence_id"]] = {
                **current,
                "page_ref": page_ref,
                "page_number": context["page_number"],
            }
    if set(current_blocks) != set(evidence_blocks):
        return False
    for context in document_contexts:
        for block in context["context_blocks"]:
            current = current_blocks.get(block["evidence_id"])
            source = evidence_blocks.get(block["evidence_id"])
            if current is None:
                if block["page_ref"] not in excluded_refs:
                    return False
                continue
            if (
                source is None
                or current["page_ref"] != block["page_ref"]
                or current["page_number"] != block["page_number"]
                or current["block_id"] != block["block_id"]
                or current["section_id"] != block["section_id"]
                or source["reading_order"] != block["reading_order"]
                or source["kind"] != block["kind"]
                or source["text"] != block["text"]
            ):
                return False
    semantic_batches = output["semantic_batches"]
    if not isinstance(semantic_batches, list):
        return False
    batch_indexes: dict[str, list[int]] = {page_ref: [] for page_ref in page_refs}
    batch_identities: set[tuple[str, str]] = set()
    pages_by_ref = {page["page_ref"]: page for page in pages}
    source_requests = {}
    for page_ref, context in contexts_by_page.items():
        source_page = deepcopy(pages_by_ref[page_ref])
        source_page["page_evidence_id"] = context["page_evidence_id"]
        source_requests[page_ref], _ = build_semantic_request(
            source_page, context
        )
    for batch in semantic_batches:
        if (
            not _closed(
                batch,
                {
                    "page_ref", "batch_index", "semantic_request_sha256",
                    "semantic_request",
                },
            )
            or batch["page_ref"] not in page_refs
            or type(batch["batch_index"]) is not int
            or batch["batch_index"] < 0
            or not isinstance(batch["semantic_request_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", batch["semantic_request_sha256"])
            is None
            or not isinstance(batch["semantic_request"], dict)
            or canonical_sha256(batch["semantic_request"])
            != batch["semantic_request_sha256"]
            or not _semantic_request_valid(batch["semantic_request"])
            or not fitted_semantic_request_matches_source(
                batch["semantic_request"], source_requests[batch["page_ref"]]
            )
            or batch["semantic_request"]["document_context"][
                "source_context_id"
            ] != contexts_by_page[batch["page_ref"]]["context_id"]
            or (
                batch["semantic_request_sha256"],
                batch["semantic_request"]["document_context"][
                    "document_context_id"
                ],
            )
            in batch_identities
        ):
            return False
        batch_indexes[batch["page_ref"]].append(batch["batch_index"])
        batch_identities.add(
            (
                batch["semantic_request_sha256"],
                batch["semantic_request"]["document_context"][
                    "document_context_id"
                ],
            )
        )
    if any(
        indexes != list(range(len(indexes)))
        for indexes in batch_indexes.values()
    ):
        return False
    concept_ids: set[str] = set()
    concept_page_refs: set[str] = set()
    has_partial_concept = False
    concept_fields = {
        "concept_id", "page_ref", "label", "definition", "key_points",
        "processing", "quality", "decision", "reason_codes",
    }
    if not isinstance(output["concepts"], list):
        return False
    for concept in output["concepts"]:
        if not _closed(concept, concept_fields):
            return False
        if (
            concept["concept_id"] in concept_ids
            or concept["page_ref"] not in page_refs
            or not _claim_is_valid(
                concept["definition"], concept["page_ref"], evidence_pages, "definition"
            )
            or not isinstance(concept["key_points"], list)
            or not concept["key_points"]
            or any(
                not _claim_is_valid(
                    point,
                    concept["page_ref"],
                    evidence_pages,
                    "key_point",
                    index=index,
                )
                for index, point in enumerate(concept["key_points"])
            )
            or concept["concept_id"] != concept_id(
                concept["page_ref"],
                concept["label"],
                concept["definition"],
                concept["key_points"],
            )
            or concept["processing"] not in {"succeeded", "partial"}
            or (concept["quality"], concept["decision"])
            != ("needs_review", "review")
            or not _reasons(concept["reason_codes"], formal=True)
        ):
            return False
        concept_ids.add(concept["concept_id"])
        concept_page_refs.add(concept["page_ref"])
        has_partial_concept = has_partial_concept or concept["processing"] == "partial"
    rejected_fields = {
        "page_ref", "candidate_index", "processing", "quality", "decision", "reason_codes"
    }
    if not isinstance(output["rejected_candidates"], list):
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
        or not output["concepts"]
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
    context_pages: list[dict[str, Any]],
    document_contexts: list[dict[str, Any]],
    semantic_pages: list[dict[str, Any]],
    runtime_binding: dict[str, Any],
    run_reasons: list[str],
    excluded_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    excluded = deepcopy(excluded_pages or [])
    if not pages or not semantic_pages or len(pages) != len(semantic_pages):
        raise ValueError("ARTIFACT_INVALID")

    semantic_by_page = {
        page["page_ref"]: page for page in semantic_pages
    }
    contexts_by_page = {
        context["page_ref"]: context for context in document_contexts
    }
    page_refs = {page["page_ref"] for page in pages}
    if (
        set(semantic_by_page) != page_refs
        or set(contexts_by_page) != page_refs
        or any(
            not validate_document_context_shape(context)
            or not validate_document_context(context, context_pages)
            for context in document_contexts
        )
    ):
        raise ValueError("ARTIFACT_INVALID")
    formal_pages = deepcopy(pages)
    for page in formal_pages:
        semantic_page = semantic_by_page[page["page_ref"]]
        if semantic_page["processing"] == "partial":
            page["processing"] = "partial"
        page["reason_codes"] = formal_reason_codes(
            page["reason_codes"] + semantic_page["reason_codes"]
        )
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
    if not concepts:
        reasons.append("NO_USABLE_CONCEPT")
    semantic_batches = [
        {
            "page_ref": semantic_page["page_ref"],
            **deepcopy(batch),
        }
        for semantic_page in semantic_pages
        for batch in semantic_page["input_binding"]["batch_bindings"]
    ]
    semantic_batches.sort(
        key=lambda batch: (
            page_numbers[batch["page_ref"]], batch["batch_index"]
        )
    )
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
        "document_contexts": [
            deepcopy(contexts_by_page[page["page_ref"]])
            for page in formal_pages
        ],
        "semantic_batches": semantic_batches,
        "concepts": concepts,
        "rejected_candidates": rejected,
        "runtime_binding": deepcopy(runtime_binding),
        "processing": "partial" if (
            excluded
            or any(page["processing"] == "partial" for page in formal_pages)
            or any(page["processing"] == "partial" for page in semantic_pages)
            or not concepts
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
