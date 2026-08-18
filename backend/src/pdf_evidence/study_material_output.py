"""把已驗證的文字優先 producer output 整理成 Study Material Output。"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from .concept_evidence_output import AGGREGATION_POLICY, validate_output_document
from .ocr_page_evidence import canonical_sha256


STUDY_MATERIAL_OUTPUT_SCHEMA = "study-material-output/v3"


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


def _string_list(value: Any, *, minimum: int = 0, maximum: int = 256) -> bool:
    return (
        isinstance(value, list)
        and minimum <= len(value) <= maximum
        and all(isinstance(item, str) and 1 <= len(item) <= 1_000 for item in value)
    )


def _shape_is_valid(document: Any) -> bool:
    fields = {
        "schema", "run_id", "produced_at", "material_ref", "source_binding", "pages",
        "excluded_pages", "concepts", "evidence_index", "images", "processing",
        "quality", "decision", "reason_codes", "output_id",
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
        or not 1 <= binding["page_count"] <= 32
        or document["processing"] not in {"succeeded", "partial"}
        or (document["quality"], document["decision"]) != ("needs_review", "review")
        or not _string_list(document["reason_codes"], minimum=1)
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
            or (page["processing"], page["quality"], page["decision"])
            != ("succeeded", "needs_review", "review")
            or not _string_list(page["reason_codes"], minimum=1)
        ):
            return False
        pages_by_ref[page["page_ref"]] = page["page_number"]
        page_numbers.add(page["page_number"])
    evidence_fields = {"evidence_id", "page_ref", "page_number", "kind", "region"}
    evidence_pages: dict[str, str] = {}
    if not isinstance(document["evidence_index"], list) or len(document["evidence_index"]) > 2_048:
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
    concept_fields = {
        "concept_id", "page_ref", "label", "definition", "key_points", "evidence_ids",
        "processing", "quality", "decision", "reason_codes",
    }
    concept_ids: set[str] = set()
    if not isinstance(document["concepts"], list) or not document["concepts"]:
        return False
    for concept in document["concepts"]:
        if not isinstance(concept, dict) or set(concept) != concept_fields:
            return False
        references = concept["evidence_ids"]
        if (
            not isinstance(concept["concept_id"], str)
            or concept["concept_id"] in concept_ids
            or concept["page_ref"] not in pages_by_ref
            or not isinstance(concept["label"], str)
            or not 1 <= len(concept["label"]) <= 120
            or not isinstance(concept["definition"], str)
            or not 1 <= len(concept["definition"]) <= 1_000
            or not _string_list(concept["key_points"], minimum=1, maximum=10)
            or any(len(point) > 300 for point in concept["key_points"])
            or not _string_list(references, minimum=1, maximum=16)
            or len(references) != len(set(references))
            or any(evidence_pages.get(reference) != concept["page_ref"] for reference in references)
            or (concept["processing"], concept["quality"], concept["decision"])
            != ("succeeded", "needs_review", "review")
            or not _string_list(concept["reason_codes"], minimum=1)
        ):
            return False
        concept_ids.add(concept["concept_id"])
    image_fields = {
        "image_id", "page_ref", "page_number", "image_hash", "region",
        "caption_evidence_ids", "nearby_evidence_ids",
    }
    image_ids: set[str] = set()
    if not isinstance(document["images"], list) or len(document["images"]) > 8_192:
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
        ):
            return False
        excluded_numbers.add(page["page_number"])
    return (
        page_numbers | excluded_numbers == set(range(1, binding["page_count"] + 1))
        and (document["processing"] == "partial") == bool(document["excluded_pages"])
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
        or not 1 <= len(page_numbers) <= 32
        or page_numbers != list(range(1, len(page_numbers) + 1))
    ):
        raise ValueError("STUDY_MATERIAL_SOURCE_INVALID")

    material_id = producer_output.get("material_id")
    material_revision = producer_output.get("material_revision")
    pages = []
    evidence_index = []
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
                "processing": "succeeded",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": ["PAGE_CONTENT_REVIEW_REQUIRED"],
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
        evidence_ids = source_concept.get("evidence_ids", []) if isinstance(source_concept, dict) else []
        if (
            page_ref not in page_refs
            or not evidence_ids
            or any(evidence_pages.get(evidence_id) != page_ref for evidence_id in evidence_ids)
            or (
                source_concept.get("processing"),
                source_concept.get("quality"),
                source_concept.get("decision"),
            )
            != ("succeeded", "needs_review", "review")
        ):
            raise ValueError("STUDY_MATERIAL_CONCEPT_INVALID")
        concepts.append(deepcopy(source_concept))
    if not concepts:
        raise ValueError("STUDY_MATERIAL_CONCEPT_INVALID")

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

    processing = "partial" if excluded_pages else "succeeded"
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
        "images": sorted(images, key=lambda image: image["image_id"]),
        "processing": processing,
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": sorted(
            set(producer_output["reason_codes"])
            | ({"PAGE_CONTENT_EXCLUDED"} if excluded_pages else set())
        ),
    }
    document["output_id"] = _expected_identity(document)
    if not _shape_is_valid(document):
        raise ValueError("STUDY_MATERIAL_OUTPUT_INVALID")
    return document


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
