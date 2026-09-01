from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

from .artifact_reason_codes import formal_reason_codes, reason_codes_are_valid
from .concept_evidence_output import AGGREGATION_POLICY, validate_output_document
from .concept_generation import (
    build_semantic_request,
    claim_id,
    concept_id,
    fitted_semantic_request_matches_source,
    validate_semantic_request,
)
from .document_context import validate_document_context_shape
from .ocr_page_evidence import canonical_sha256


STUDY_MATERIAL_OUTPUT_SCHEMA = "study-material-output/v8"


def _valid_region(region: Any) -> bool:
    if not isinstance(region, dict) or set(region) != {"coordinate_space", "bbox"}:
        return False
    bbox = region["bbox"]
    return (
        region["coordinate_space"] == "unrotated_pdf_points"
        and isinstance(bbox, list)
        and len(bbox) == 4
        and all(type(value) in {int, float} and math.isfinite(value) for value in bbox)
        and bbox[0] < bbox[2]
        and bbox[1] < bbox[3]
    )


def _expected_identity(document: dict[str, Any]) -> str:
    content = {key: value for key, value in document.items() if key != "output_id"}
    return "study-material-output:sha256:" + canonical_sha256(content)


def _producer_identity_is_valid(producer_output: Any) -> bool:
    return validate_output_document(producer_output)


def _string_list(
    value: Any,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= minimum
        and (maximum is None or len(value) <= maximum)
        and all(isinstance(item, str) and bool(item) for item in value)
    )


def _shape_is_valid(document: Any) -> bool:
    fields = {
        "schema", "run_id", "produced_at", "material_ref", "source_binding", "pages",
        "excluded_pages", "concepts", "evidence_index", "evidence_text_index",
        "document_contexts", "semantic_batches", "semantic_page_outcomes", "images",
        "processing", "quality",
        "decision", "reason_codes", "output_id",
    }
    if not isinstance(document, dict) or set(document) != fields:
        return False
    binding = document["source_binding"]
    if not isinstance(binding, dict) or set(binding) != {
        "source_sha256", "page_count", "producer_output_id", "runtime_binding_sha256"
    }:
        return False
    if (
        document["schema"] != STUDY_MATERIAL_OUTPUT_SCHEMA
        or type(binding["page_count"]) is not int
        or binding["page_count"] < 1
        or document["processing"] not in {"succeeded", "partial"}
        or (document["quality"], document["decision"]) != ("needs_review", "review")
        or not _string_list(document["reason_codes"], minimum=1)
        or not reason_codes_are_valid(document["reason_codes"], formal=True)
        or document["reason_codes"] != sorted(set(document["reason_codes"]))
    ):
        return False
    page_fields = {
        "page_ref", "page_number", "page_evidence_id", "native_evidence_ref",
        "processing", "quality", "decision", "reason_codes",
    }
    pages_by_ref: dict[str, int] = {}
    page_numbers: set[int] = set()
    if not isinstance(document["pages"], list) or not document["pages"]:
        return False
    for page in document["pages"]:
        if (
            not isinstance(page, dict)
            or set(page) != page_fields
            or not isinstance(page["page_ref"], str)
            or page["page_ref"] in pages_by_ref
            or type(page["page_number"]) is not int
            or not 1 <= page["page_number"] <= binding["page_count"]
            or page["page_number"] in page_numbers
            or page["processing"] not in {"succeeded", "partial"}
            or (page["quality"], page["decision"]) != ("needs_review", "review")
            or not _string_list(page["reason_codes"], minimum=1)
            or not reason_codes_are_valid(page["reason_codes"], formal=True)
        ):
            return False
        pages_by_ref[page["page_ref"]] = page["page_number"]
        page_numbers.add(page["page_number"])
    evidence_fields = {"evidence_id", "page_ref", "page_number", "kind", "region"}
    evidence_pages: dict[str, str] = {}
    evidence_by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(document["evidence_index"], list):
        return False
    for evidence in document["evidence_index"]:
        if (
            not isinstance(evidence, dict)
            or set(evidence) != evidence_fields
            or not isinstance(evidence["evidence_id"], str)
            or evidence["evidence_id"] in evidence_pages
            or evidence["page_ref"] not in pages_by_ref
            or evidence["page_number"] != pages_by_ref[evidence["page_ref"]]
            or not isinstance(evidence["kind"], str)
            or not 1 <= len(evidence["kind"]) <= 64
            or not _valid_region(evidence["region"])
        ):
            return False
        evidence_pages[evidence["evidence_id"]] = evidence["page_ref"]
        evidence_by_id[evidence["evidence_id"]] = evidence
    evidence_texts: set[str] = set()
    evidence_text_by_id: dict[str, str] = {}
    if (
        not isinstance(document["evidence_text_index"], list)
        or len(document["evidence_text_index"]) != len(evidence_pages)
    ):
        return False
    for evidence in document["evidence_text_index"]:
        if (
            not isinstance(evidence, dict)
            or set(evidence) != {"evidence_id", "text"}
            or evidence["evidence_id"] not in evidence_pages
            or evidence["evidence_id"] in evidence_texts
            or not isinstance(evidence["text"], str)
            or not evidence["text"]
        ):
            return False
        evidence_texts.add(evidence["evidence_id"])
        evidence_text_by_id[evidence["evidence_id"]] = evidence["text"]
    if evidence_texts != set(evidence_pages):
        return False
    contexts = document["document_contexts"]
    if (
        not isinstance(contexts, list)
        or len(contexts) != len(document["pages"])
        or any(not validate_document_context_shape(context) for context in contexts)
        or {context["page_ref"] for context in contexts} != set(pages_by_ref)
        or len({context["material_revision"] for context in contexts}) != 1
    ):
        return False
    current_evidence_ids: set[str] = set()
    current_blocks_by_evidence: dict[str, dict[str, Any]] = {}
    block_ids: set[str] = set()
    for context in contexts:
        if (
            context["material_id"] != document["material_ref"]
            or context["page_number"] != pages_by_ref[context["page_ref"]]
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
        for block in context["current_blocks"]:
            if (
                block["evidence_id"] not in evidence_pages
                or block["evidence_id"] in current_evidence_ids
                or evidence_pages[block["evidence_id"]] != context["page_ref"]
                or block["block_id"] in block_ids
            ):
                return False
            current_evidence_ids.add(block["evidence_id"])
            block_ids.add(block["block_id"])
            current_blocks_by_evidence[block["evidence_id"]] = {
                **block,
                "page_ref": context["page_ref"],
                "page_number": context["page_number"],
            }
    if current_evidence_ids != set(evidence_pages):
        return False
    excluded_context_refs = {
        page.get("page_ref")
        for page in document["excluded_pages"]
        if isinstance(page, dict)
    }
    for context in contexts:
        for block in context["context_blocks"]:
            current = current_blocks_by_evidence.get(block["evidence_id"])
            evidence = evidence_by_id.get(block["evidence_id"])
            if current is None:
                if block["page_ref"] not in excluded_context_refs:
                    return False
                continue
            if (
                evidence is None
                or current["page_ref"] != block["page_ref"]
                or current["page_number"] != block["page_number"]
                or current["block_id"] != block["block_id"]
                or current["section_id"] != block["section_id"]
                or evidence["kind"] != block["kind"]
                or evidence_text_by_id[block["evidence_id"]] != block["text"]
            ):
                return False
    semantic_batches = document["semantic_batches"]
    contexts_by_page = {context["page_ref"]: context for context in contexts}
    if not isinstance(semantic_batches, list):
        return False
    indexes_by_page: dict[str, list[int]] = {
        page_ref: [] for page_ref in pages_by_ref
    }
    source_requests = {}
    for page_ref, context in contexts_by_page.items():
        try:
            source_requests[page_ref], _ = build_semantic_request(
                {
                    "page_ref": page_ref,
                    "page_evidence_id": context["page_evidence_id"],
                    "evidence_blocks": [
                        {
                            "evidence_id": block["evidence_id"],
                            "kind": evidence_by_id[block["evidence_id"]]["kind"],
                            "text": evidence_text_by_id[block["evidence_id"]],
                        }
                        for block in context["current_blocks"]
                    ],
                },
                context,
            )
        except (KeyError, TypeError, ValueError):
            return False
    for batch in semantic_batches:
        if (
            not isinstance(batch, dict)
            or set(batch)
            != {
                "page_ref", "batch_index", "semantic_request_sha256",
                "semantic_request",
            }
            or batch["page_ref"] not in pages_by_ref
            or type(batch["batch_index"]) is not int
            or batch["batch_index"] < 0
            or not isinstance(batch["semantic_request_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", batch["semantic_request_sha256"])
            is None
            or not isinstance(batch["semantic_request"], dict)
            or canonical_sha256(batch["semantic_request"])
            != batch["semantic_request_sha256"]
        ):
            return False
        try:
            if (
                not validate_semantic_request(batch["semantic_request"])
                or not fitted_semantic_request_matches_source(
                    batch["semantic_request"],
                    source_requests[batch["page_ref"]],
                )
                or batch["semantic_request"]["document_context"][
                    "source_context_id"
                ] != contexts_by_page[batch["page_ref"]]["context_id"]
            ):
                return False
        except (KeyError, TypeError, ValueError):
            return False
        indexes_by_page[batch["page_ref"]].append(batch["batch_index"])
    if any(
        indexes != list(range(len(indexes)))
        for indexes in indexes_by_page.values()
    ):
        return False
    semantic_page_outcomes = document["semantic_page_outcomes"]
    outcome_fields = {
        "page_ref", "processing", "quality", "decision", "reason_codes",
    }
    if (
        not isinstance(semantic_page_outcomes, list)
        or len(semantic_page_outcomes) != len(pages_by_ref)
        or {outcome.get("page_ref") for outcome in semantic_page_outcomes}
        != set(pages_by_ref)
    ):
        return False
    for outcome in semantic_page_outcomes:
        expected_decision = "reject" if outcome.get("processing") == "failed" else "review"
        if (
            not isinstance(outcome, dict)
            or set(outcome) != outcome_fields
            or outcome["processing"] not in {"succeeded", "partial", "failed"}
            or outcome["quality"] != "needs_review"
            or outcome["decision"] != expected_decision
            or not _string_list(outcome["reason_codes"], minimum=1)
            or not reason_codes_are_valid(outcome["reason_codes"], formal=True)
        ):
            return False
    concept_fields = {
        "concept_id", "page_ref", "label", "claims", "processing", "quality",
        "decision", "reason_codes",
    }
    concept_ids: set[str] = set()
    if not isinstance(document["concepts"], list):
        return False
    for concept in document["concepts"]:
        if not isinstance(concept, dict) or set(concept) != concept_fields:
            return False
        if (
            not isinstance(concept["concept_id"], str)
            or concept["concept_id"] in concept_ids
            or concept["page_ref"] not in pages_by_ref
            or not isinstance(concept["label"], str)
            or not concept["label"]
            or not isinstance(concept["claims"], list)
            or not concept["claims"]
            or any(
                not _claim_is_valid(
                    claim,
                    concept["page_ref"],
                    evidence_pages,
                    index=index,
                )
                for index, claim in enumerate(concept["claims"])
            )
            or concept["concept_id"] != concept_id(
                concept["page_ref"],
                concept["label"],
                concept["claims"],
            )
            or concept["processing"] not in {"succeeded", "partial"}
            or (concept["quality"], concept["decision"])
            != ("needs_review", "review")
            or not _string_list(concept["reason_codes"], minimum=1)
            or not reason_codes_are_valid(concept["reason_codes"], formal=True)
        ):
            return False
        concept_ids.add(concept["concept_id"])
    image_fields = {
        "image_id", "page_ref", "page_number", "image_hash", "region",
        "caption_evidence_ids", "nearby_evidence_ids",
    }
    image_ids: set[str] = set()
    if not isinstance(document["images"], list):
        return False
    for image in document["images"]:
        if not isinstance(image, dict) or set(image) != image_fields:
            return False
        if not isinstance(image["caption_evidence_ids"], list) or not isinstance(
            image["nearby_evidence_ids"], list
        ):
            return False
        references = image["caption_evidence_ids"] + image["nearby_evidence_ids"]
        if (
            not isinstance(image["image_id"], str)
            or image["image_id"] in image_ids
            or image["page_ref"] not in pages_by_ref
            or image["page_number"] != pages_by_ref[image["page_ref"]]
            or not _valid_region(image["region"])
            or not all(isinstance(items, list) for items in (image["caption_evidence_ids"], image["nearby_evidence_ids"]))
            or len(references) != len(set(references))
            or any(evidence_pages.get(reference) != image["page_ref"] for reference in references)
        ):
            return False
        image_ids.add(image["image_id"])
    excluded_fields = {
        "page_ref", "page_number", "page_evidence_id", "last_stage", "processing",
        "quality", "decision", "reason_codes",
    }
    excluded_numbers: set[int] = set()
    if not isinstance(document["excluded_pages"], list):
        return False
    for page in document["excluded_pages"]:
        if (
            not isinstance(page, dict)
            or set(page) != excluded_fields
            or page["page_ref"] in pages_by_ref
            or type(page["page_number"]) is not int
            or not 1 <= page["page_number"] <= binding["page_count"]
            or page["page_number"] in excluded_numbers
            or page["last_stage"] not in {"page_evidence", "concept"}
            or (page["processing"], page["quality"], page["decision"])
            != ("failed", "needs_review", "reject")
            or not _string_list(page["reason_codes"], minimum=1)
            or not reason_codes_are_valid(page["reason_codes"], formal=True)
        ):
            return False
        excluded_numbers.add(page["page_number"])
    is_partial = (
        bool(document["excluded_pages"])
        or any(page["processing"] == "partial" for page in document["pages"])
        or any(concept["processing"] == "partial" for concept in document["concepts"])
        or any(
            outcome["processing"] != "succeeded"
            for outcome in semantic_page_outcomes
        )
    )
    return (
        page_numbers | excluded_numbers == set(range(1, binding["page_count"] + 1))
        and (document["processing"] == "partial") == is_partial
    )


def build_study_material_output(producer_output: dict[str, Any]) -> dict[str, Any]:
    """只保留目前 Map consumer 需要的 Concept、Evidence 與 image-lite locator。"""

    if not _producer_identity_is_valid(producer_output):
        raise ValueError("STUDY_MATERIAL_SOURCE_INVALID")
    if producer_output.get("aggregation_policy") != AGGREGATION_POLICY:
        raise ValueError("STUDY_MATERIAL_SOURCE_INVALID")
    source_binding = producer_output.get("source_binding")
    if not isinstance(source_binding, dict):
        raise ValueError("STUDY_MATERIAL_SOURCE_INVALID")
    page_numbers = source_binding.get("page_numbers")
    if (
        not isinstance(page_numbers, list)
        or not page_numbers
        or page_numbers != list(range(1, len(page_numbers) + 1))
    ):
        raise ValueError("STUDY_MATERIAL_SOURCE_INVALID")

    material_id = producer_output.get("material_id")
    material_revision = producer_output.get("material_revision")
    pages = []
    evidence_index = []
    evidence_text_index = []
    evidence_pages: dict[str, str] = {}
    page_numbers_seen: set[int] = set()
    page_refs: set[str] = set()
    images = []
    for source_page in producer_output.get("pages", []):
        if (
            not isinstance(source_page, dict)
            or source_page.get("material_id") != material_id
            or source_page.get("material_revision") != material_revision
            or source_page.get("coordinate_space") != "unrotated_pdf_points"
        ):
            raise ValueError("STUDY_MATERIAL_PAGE_INVALID")
        page_ref = source_page.get("page_ref")
        page_number = source_page.get("page_number")
        if (
            not isinstance(page_ref, str)
            or type(page_number) is not int
            or page_ref in page_refs
            or page_number in page_numbers_seen
            or page_number not in page_numbers
        ):
            raise ValueError("STUDY_MATERIAL_PAGE_INVALID")
        page_refs.add(page_ref)
        page_numbers_seen.add(page_number)
        pages.append(
            {
                "page_ref": page_ref,
                "page_number": page_number,
                "page_evidence_id": source_page["page_evidence_id"],
                "native_evidence_ref": source_page["native_evidence_ref"],
                "processing": source_page["processing"],
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": formal_reason_codes(
                    source_page["reason_codes"] + ["CONTENT_REVIEW_REQUIRED"]
                ),
            }
        )
        for block in source_page.get("evidence_blocks", []):
            locator = block.get("locator") if isinstance(block, dict) else None
            evidence_id = block.get("evidence_id") if isinstance(block, dict) else None
            region = (
                {
                    "coordinate_space": "unrotated_pdf_points",
                    "bbox": deepcopy(locator.get("region")),
                }
                if isinstance(locator, dict)
                else None
            )
            if (
                not isinstance(evidence_id, str)
                or evidence_id in evidence_pages
                or not _valid_region(region)
                or locator.get("page") != page_number
            ):
                raise ValueError("STUDY_MATERIAL_EVIDENCE_INVALID")
            evidence_pages[evidence_id] = page_ref
            evidence_index.append(
                {
                    "evidence_id": evidence_id,
                    "page_ref": page_ref,
                    "page_number": page_number,
                    "kind": block["kind"],
                    "region": region,
                }
            )
            evidence_text_index.append(
                {"evidence_id": evidence_id, "text": block["text"]}
            )
        for image in source_page.get("images", []):
            references = image.get("caption_evidence_ids", []) + image.get(
                "nearby_evidence_ids", []
            )
            if any(evidence_pages.get(evidence_id) != page_ref for evidence_id in references):
                raise ValueError("STUDY_MATERIAL_EVIDENCE_INVALID")
            region = {
                "coordinate_space": "unrotated_pdf_points",
                "bbox": deepcopy(image.get("region")),
            }
            if not _valid_region(region):
                raise ValueError("STUDY_MATERIAL_EVIDENCE_INVALID")
            images.append(
                {
                    "image_id": image["image_id"],
                    "page_ref": page_ref,
                    "page_number": page_number,
                    "image_hash": image.get("image_hash"),
                    "region": region,
                    "caption_evidence_ids": deepcopy(image["caption_evidence_ids"]),
                    "nearby_evidence_ids": deepcopy(image["nearby_evidence_ids"]),
                }
            )

    concepts = []
    for source_concept in producer_output.get("concepts", []):
        page_ref = source_concept.get("page_ref") if isinstance(source_concept, dict) else None
        if (
            page_ref not in page_refs
            or not isinstance(source_concept.get("claims"), list)
            or not source_concept["claims"]
            or any(
                not _claim_is_valid(
                    claim,
                    page_ref,
                    evidence_pages,
                    index=index,
                )
                for index, claim in enumerate(source_concept["claims"])
            )
            or (
                source_concept.get("processing"),
                source_concept.get("quality"),
                source_concept.get("decision"),
            )
            not in {
                ("succeeded", "needs_review", "review"),
                ("partial", "needs_review", "review"),
            }
        ):
            raise ValueError("STUDY_MATERIAL_CONCEPT_INVALID")
        concepts.append(deepcopy(source_concept))

    excluded_pages = deepcopy(producer_output.get("excluded_pages", []))
    excluded_numbers: set[int] = set()
    for excluded in excluded_pages:
        if (
            not isinstance(excluded, dict)
            or excluded.get("processing") != "failed"
            or excluded.get("quality") != "needs_review"
            or excluded.get("decision") != "reject"
            or excluded.get("page_ref") in page_refs
            or type(excluded.get("page_number")) is not int
            or excluded["page_number"] not in page_numbers
            or excluded["page_number"] in excluded_numbers
        ):
            raise ValueError("STUDY_MATERIAL_PAGE_INVALID")
        excluded_numbers.add(excluded["page_number"])
    if page_numbers_seen | excluded_numbers != set(page_numbers):
        raise ValueError("STUDY_MATERIAL_PAGE_INVALID")

    processing = producer_output["processing"]
    document = {
        "schema": STUDY_MATERIAL_OUTPUT_SCHEMA,
        "run_id": producer_output["run_id"],
        "produced_at": producer_output["produced_at"],
        "material_ref": material_id,
        "source_binding": {
            "source_sha256": source_binding["source_sha256"],
            "page_count": len(page_numbers),
            "producer_output_id": producer_output["output_id"],
            "runtime_binding_sha256": canonical_sha256(
                producer_output["runtime_binding"]
            ),
        },
        "pages": sorted(pages, key=lambda page: page["page_number"]),
        "excluded_pages": sorted(
            excluded_pages, key=lambda page: page["page_number"]
        ),
        "concepts": sorted(
            concepts, key=lambda concept: (concept["page_ref"], concept["concept_id"])
        ),
        "evidence_index": sorted(
            evidence_index, key=lambda evidence: evidence["evidence_id"]
        ),
        "evidence_text_index": sorted(
            evidence_text_index, key=lambda evidence: evidence["evidence_id"]
        ),
        "document_contexts": deepcopy(producer_output["document_contexts"]),
        "semantic_batches": deepcopy(producer_output["semantic_batches"]),
        "semantic_page_outcomes": deepcopy(
            producer_output["semantic_page_outcomes"]
        ),
        "images": sorted(images, key=lambda image: image["image_id"]),
        "processing": processing,
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": formal_reason_codes(
            producer_output["reason_codes"]
            + (["PAGE_CONTENT_EXCLUDED"] if excluded_pages else [])
        ),
    }
    document["output_id"] = _expected_identity(document)
    if not _shape_is_valid(document):
        raise ValueError("STUDY_MATERIAL_OUTPUT_INVALID")
    return document


def _claim_is_valid(
    claim: Any,
    page_ref: str,
    evidence_pages: dict[str, str],
    *,
    index: int,
) -> bool:
    if not isinstance(claim, dict) or set(claim) != {"claim_id", "text", "evidence_ids"}:
        return False
    references = claim["evidence_ids"]
    return (
        claim["claim_id"]
        == claim_id(
            page_ref,
            {"text": claim["text"], "evidence_ids": references},
            index=index,
        )
        and isinstance(claim["text"], str)
        and bool(claim["text"])
        and _string_list(references, minimum=1)
        and len(references) == len(set(references))
        and all(evidence_pages.get(reference) == page_ref for reference in references)
    )


def validate_study_material_output(
    document: Any, producer_output: dict[str, Any] | None = None
) -> str | None:
    """重建時使用 exact producer；一般讀取至少重驗 schema 與 identity。"""

    if not _shape_is_valid(document):
        return "STUDY_MATERIAL_OUTPUT_INVALID"
    try:
        if document.get("output_id") != _expected_identity(document):
            return "STUDY_MATERIAL_OUTPUT_INVALID"
        if producer_output is not None and document != build_study_material_output(
            producer_output
        ):
            return "STUDY_MATERIAL_OUTPUT_INVALID"
    except (KeyError, TypeError, ValueError):
        return "STUDY_MATERIAL_OUTPUT_INVALID"
    return None
