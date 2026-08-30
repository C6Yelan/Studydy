from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any
import unicodedata

from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.study_material_output import validate_study_material_output


RESOURCE_LIBRARY_SCHEMA = "resource-library/v1"
MAP_RESOURCE_CONTEXT_SCHEMA = "map-resource-context/v1"
LOCATOR_POLICY = "resource-native-quote-locator/v2"
QUALITY_POLICY = "resource-concept-full-source-review/v2"
LABEL_NORMALIZER = "resource-label-exact-normalized/v1"
MATCHING_POLICY = "resource-context-exact-distinct-source/v3"
PROMOTION_POLICY = "resource-formal-concept-promotion/v1"

_ACCEPTED_STATUS = ("succeeded", "accepted", "retain")
_MATCH_STATUS = ("partial", "needs_review", "review")
_MATCH_REASON = "RESOURCE_MATCH_REQUIRES_FORMAL_CONCEPT_PROMOTION"
_NO_LABEL_REASON = "RESOURCE_NO_EXACT_LABEL_MATCH"
_NO_DISTINCT_SOURCE_REASON = "RESOURCE_NO_DISTINCT_SOURCE_MATCH"

_SOURCE_INPUT_FIELDS = {
    "source_sha256",
    "page_count",
    "title",
    "authors",
    "source_url",
    "citation",
    "license",
    "license_url",
    "use_boundary",
}
_SOURCE_FIELDS = _SOURCE_INPUT_FIELDS | {"resource_id"}
_ENTRY_FIELDS = {"source_sha256", "page_number", "label", "evidence"}
_REVIEWED_EVIDENCE_FIELDS = {"quote", "region"}
_EVIDENCE_FIELDS = {
    "evidence_id",
    "resource_id",
    "page_ref",
    "page_number",
    "quote",
    "quote_sha256",
    "region",
    "processing",
    "quality",
    "decision",
    "reason_codes",
}
_CONCEPT_FIELDS = {
    "concept_id",
    "page_ref",
    "label",
    "evidence_ids",
    "processing",
    "quality",
    "decision",
    "reason_codes",
}
_LIBRARY_FIELDS = {
    "schema",
    "library_revision",
    "locator_policy",
    "quality_policy",
    "label_normalizer",
    "sources",
    "evidence",
    "concepts",
    "processing",
    "quality",
    "decision",
    "reason_codes",
}


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _clean_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _normalized_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    without_punctuation = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    return " ".join(without_punctuation.split())


def _valid_reason_codes(value: Any, expected: list[str]) -> bool:
    return (
        isinstance(value, list)
        and value == expected
        and value == sorted(set(value))
    )


def _has_status(item: dict[str, Any], expected: tuple[str, str, str]) -> bool:
    return (
        item.get("processing"),
        item.get("quality"),
        item.get("decision"),
    ) == expected


def _valid_region(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"coordinate_space", "bbox"}:
        return False
    bbox = value["bbox"]
    return (
        value["coordinate_space"] == "unrotated_pdf_points"
        and isinstance(bbox, list)
        and len(bbox) == 4
        and all(type(number) in {int, float} and math.isfinite(number) for number in bbox)
        and bbox[0] < bbox[2]
        and bbox[1] < bbox[3]
    )


def _resource_id(source_sha256: str, page_count: int) -> str:
    identity = {"source_sha256": source_sha256, "page_count": page_count}
    return "resource:sha256:" + canonical_sha256(identity)


def _page_ref(resource_id: str, page_number: int) -> str:
    return "resource-page:sha256:" + canonical_sha256(
        {"resource_id": resource_id, "page_number": page_number}
    )


def _library_revision(document: dict[str, Any]) -> str:
    content = {key: value for key, value in document.items() if key != "library_revision"}
    return "resource-library:sha256:" + canonical_sha256(content)


def _context_revision(document: dict[str, Any]) -> str:
    content = {key: value for key, value in document.items() if key != "context_revision"}
    return "map-resource-context:sha256:" + canonical_sha256(content)


def _build_resource_library(
    reviewed_sources: Any, reviewed_entries: Any
) -> dict[str, Any]:
    """把已複核來源與頁面 Evidence 建成唯一的正式 Resource library。"""

    if (
        not isinstance(reviewed_sources, list)
        or not reviewed_sources
        or not isinstance(reviewed_entries, list)
        or not reviewed_entries
    ):
        raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")

    sources = []
    sources_by_sha: dict[str, dict[str, Any]] = {}
    for reviewed_source in reviewed_sources:
        if not isinstance(reviewed_source, dict) or set(reviewed_source) != _SOURCE_INPUT_FIELDS:
            raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")
        source_sha256 = reviewed_source["source_sha256"]
        page_count = reviewed_source["page_count"]
        authors = reviewed_source["authors"]
        text_fields = (
            "title",
            "source_url",
            "citation",
            "license",
            "license_url",
            "use_boundary",
        )
        if not _is_sha256(source_sha256) or source_sha256 in sources_by_sha:
            raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")
        if type(page_count) is not int or page_count < 1:
            raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")
        if (
            not isinstance(authors, list)
            or not authors
            or any(not _is_nonempty_text(author) for author in authors)
            or authors != list(dict.fromkeys(authors))
            or any(not _is_nonempty_text(reviewed_source[field]) for field in text_fields)
        ):
            raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")
        source = {
            "resource_id": _resource_id(source_sha256, page_count),
            "source_sha256": source_sha256,
            "page_count": page_count,
            "title": reviewed_source["title"],
            "authors": deepcopy(authors),
            "source_url": reviewed_source["source_url"],
            "citation": reviewed_source["citation"],
            "license": reviewed_source["license"],
            "license_url": reviewed_source["license_url"],
            "use_boundary": reviewed_source["use_boundary"],
        }
        sources.append(source)
        sources_by_sha[source_sha256] = source

    evidence_by_id: dict[str, dict[str, Any]] = {}
    concepts = []
    concept_ids: set[str] = set()
    used_source_sha256: set[str] = set()
    for reviewed_entry in reviewed_entries:
        if not isinstance(reviewed_entry, dict) or set(reviewed_entry) != _ENTRY_FIELDS:
            raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")
        source_sha256 = reviewed_entry["source_sha256"]
        source = sources_by_sha.get(source_sha256)
        page_number = reviewed_entry["page_number"]
        reviewed_evidence = reviewed_entry["evidence"]
        if (
            source is None
            or type(page_number) is not int
            or not 1 <= page_number <= source["page_count"]
            or not _is_nonempty_text(reviewed_entry["label"])
            or not isinstance(reviewed_evidence, list)
            or not reviewed_evidence
        ):
            raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")

        label = _clean_text(reviewed_entry["label"])
        if not label or not _normalized_label(label):
            raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")
        page_ref = _page_ref(source["resource_id"], page_number)
        evidence_ids = []
        for reviewed_item in reviewed_evidence:
            if (
                not isinstance(reviewed_item, dict)
                or set(reviewed_item) != _REVIEWED_EVIDENCE_FIELDS
                or not _is_nonempty_text(reviewed_item["quote"])
                or not _valid_region(reviewed_item["region"])
            ):
                raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")
            quote = _clean_text(reviewed_item["quote"])
            if not quote:
                raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")
            region = deepcopy(reviewed_item["region"])
            quote_sha256 = hashlib.sha256(quote.encode("utf-8")).hexdigest()
            evidence_identity = {
                "locator_policy": LOCATOR_POLICY,
                "page_ref": page_ref,
                "quote_sha256": quote_sha256,
                "region": region,
            }
            evidence_id = "resource-evidence:sha256:" + canonical_sha256(
                evidence_identity
            )
            evidence = {
                "evidence_id": evidence_id,
                "resource_id": source["resource_id"],
                "page_ref": page_ref,
                "page_number": page_number,
                "quote": quote,
                "quote_sha256": quote_sha256,
                "region": region,
                "processing": "succeeded",
                "quality": "accepted",
                "decision": "retain",
                "reason_codes": [],
            }
            previous_evidence = evidence_by_id.get(evidence_id)
            if previous_evidence is not None and previous_evidence != evidence:
                raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")
            evidence_by_id[evidence_id] = evidence
            evidence_ids.append(evidence_id)
        evidence_ids = sorted(set(evidence_ids))
        concept_identity = {
            "quality_policy": QUALITY_POLICY,
            "page_ref": page_ref,
            "label": label,
            "evidence_ids": evidence_ids,
        }
        concept_id = "resource-concept:sha256:" + canonical_sha256(
            concept_identity
        )
        if concept_id in concept_ids:
            raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")
        concept_ids.add(concept_id)
        concepts.append(
            {
                "concept_id": concept_id,
                "page_ref": page_ref,
                "label": label,
                "evidence_ids": evidence_ids,
                "processing": "succeeded",
                "quality": "accepted",
                "decision": "retain",
                "reason_codes": [],
            }
        )
        used_source_sha256.add(source_sha256)

    if used_source_sha256 != set(sources_by_sha):
        raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID")

    document = {
        "schema": RESOURCE_LIBRARY_SCHEMA,
        "locator_policy": LOCATOR_POLICY,
        "quality_policy": QUALITY_POLICY,
        "label_normalizer": LABEL_NORMALIZER,
        "sources": sorted(sources, key=lambda source: source["resource_id"]),
        "evidence": sorted(
            evidence_by_id.values(),
            key=lambda item: (item["page_ref"], item["evidence_id"]),
        ),
        "concepts": sorted(
            concepts, key=lambda item: (item["page_ref"], item["concept_id"])
        ),
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_codes": [],
    }
    document["library_revision"] = _library_revision(document)
    if validate_resource_library(document) is not None:
        raise ValueError("RESOURCE_LIBRARY_BUILD_INVALID")
    return document


def build_resource_library(
    reviewed_sources: Any, reviewed_entries: Any
) -> dict[str, Any]:
    """把已複核來源與頁面 Evidence 建成唯一的正式 Resource library。"""

    try:
        return _build_resource_library(reviewed_sources, reviewed_entries)
    except (KeyError, RecursionError, TypeError) as error:
        raise ValueError("RESOURCE_LIBRARY_INPUT_INVALID") from error


def _validate_resource_library(document: Any) -> str | None:
    """Closed-validate正式 library、stable IDs與完整 Evidence 解參考。"""

    if not isinstance(document, dict) or set(document) != _LIBRARY_FIELDS:
        return "RESOURCE_LIBRARY_INVALID"
    if (
        document["schema"] != RESOURCE_LIBRARY_SCHEMA
        or document["locator_policy"] != LOCATOR_POLICY
        or document["quality_policy"] != QUALITY_POLICY
        or document["label_normalizer"] != LABEL_NORMALIZER
        or not _has_status(document, _ACCEPTED_STATUS)
        or not _valid_reason_codes(document["reason_codes"], [])
        or not isinstance(document["sources"], list)
        or not document["sources"]
        or not isinstance(document["evidence"], list)
        or not document["evidence"]
        or not isinstance(document["concepts"], list)
        or not document["concepts"]
    ):
        return "RESOURCE_LIBRARY_INVALID"

    sources_by_id: dict[str, dict[str, Any]] = {}
    source_sha256_seen: set[str] = set()
    for source in document["sources"]:
        if not isinstance(source, dict) or set(source) != _SOURCE_FIELDS:
            return "RESOURCE_LIBRARY_INVALID"
        authors = source["authors"]
        text_fields = (
            "title",
            "source_url",
            "citation",
            "license",
            "license_url",
            "use_boundary",
        )
        if (
            not _is_sha256(source["source_sha256"])
            or source["source_sha256"] in source_sha256_seen
            or type(source["page_count"]) is not int
            or source["page_count"] < 1
            or source["resource_id"]
            != _resource_id(source["source_sha256"], source["page_count"])
            or source["resource_id"] in sources_by_id
            or not isinstance(authors, list)
            or not authors
            or any(not _is_nonempty_text(author) for author in authors)
            or authors != list(dict.fromkeys(authors))
            or any(not _is_nonempty_text(source[field]) for field in text_fields)
        ):
            return "RESOURCE_LIBRARY_INVALID"
        source_sha256_seen.add(source["source_sha256"])
        sources_by_id[source["resource_id"]] = source
    if document["sources"] != sorted(
        document["sources"], key=lambda source: source["resource_id"]
    ):
        return "RESOURCE_LIBRARY_INVALID"

    evidence_by_id: dict[str, dict[str, Any]] = {}
    used_resource_ids: set[str] = set()
    for evidence in document["evidence"]:
        if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_FIELDS:
            return "RESOURCE_LIBRARY_INVALID"
        source = sources_by_id.get(evidence["resource_id"])
        if (
            source is None
            or evidence["evidence_id"] in evidence_by_id
            or type(evidence["page_number"]) is not int
            or not 1 <= evidence["page_number"] <= source["page_count"]
            or evidence["page_ref"]
            != _page_ref(evidence["resource_id"], evidence["page_number"])
            or not _is_nonempty_text(evidence["quote"])
            or evidence["quote"] != _clean_text(evidence["quote"])
            or evidence["quote_sha256"]
            != hashlib.sha256(evidence["quote"].encode("utf-8")).hexdigest()
            or not _valid_region(evidence["region"])
            or not _has_status(evidence, _ACCEPTED_STATUS)
            or not _valid_reason_codes(evidence["reason_codes"], [])
        ):
            return "RESOURCE_LIBRARY_INVALID"
        identity = {
            "locator_policy": LOCATOR_POLICY,
            "page_ref": evidence["page_ref"],
            "quote_sha256": evidence["quote_sha256"],
            "region": evidence["region"],
        }
        if evidence["evidence_id"] != "resource-evidence:sha256:" + canonical_sha256(
            identity
        ):
            return "RESOURCE_LIBRARY_INVALID"
        evidence_by_id[evidence["evidence_id"]] = evidence
        used_resource_ids.add(evidence["resource_id"])
    if document["evidence"] != sorted(
        document["evidence"], key=lambda item: (item["page_ref"], item["evidence_id"])
    ):
        return "RESOURCE_LIBRARY_INVALID"

    concept_ids: set[str] = set()
    for concept in document["concepts"]:
        if not isinstance(concept, dict) or set(concept) != _CONCEPT_FIELDS:
            return "RESOURCE_LIBRARY_INVALID"
        evidence_ids = concept["evidence_ids"]
        if (
            concept["concept_id"] in concept_ids
            or not _is_nonempty_text(concept["label"])
            or concept["label"] != _clean_text(concept["label"])
            or not _normalized_label(concept["label"])
            or not isinstance(evidence_ids, list)
            or not evidence_ids
            or evidence_ids != sorted(set(evidence_ids))
            or not _has_status(concept, _ACCEPTED_STATUS)
            or not _valid_reason_codes(concept["reason_codes"], [])
        ):
            return "RESOURCE_LIBRARY_INVALID"
        referenced_evidence = [evidence_by_id.get(evidence_id) for evidence_id in evidence_ids]
        if (
            any(evidence is None for evidence in referenced_evidence)
            or any(evidence["page_ref"] != concept["page_ref"] for evidence in referenced_evidence)
            or len({evidence["resource_id"] for evidence in referenced_evidence}) != 1
        ):
            return "RESOURCE_LIBRARY_INVALID"
        identity = {
            "quality_policy": QUALITY_POLICY,
            "page_ref": concept["page_ref"],
            "label": concept["label"],
            "evidence_ids": evidence_ids,
        }
        if concept["concept_id"] != "resource-concept:sha256:" + canonical_sha256(
            identity
        ):
            return "RESOURCE_LIBRARY_INVALID"
        concept_ids.add(concept["concept_id"])
    if (
        document["concepts"]
        != sorted(
            document["concepts"],
            key=lambda item: (item["page_ref"], item["concept_id"]),
        )
        or used_resource_ids != set(sources_by_id)
        or document["library_revision"] != _library_revision(document)
    ):
        return "RESOURCE_LIBRARY_INVALID"
    return None


def validate_resource_library(document: Any) -> str | None:
    """Closed-validate正式 library、stable IDs與完整 Evidence 解參考。"""

    try:
        return _validate_resource_library(document)
    except (KeyError, RecursionError, TypeError, ValueError):
        return "RESOURCE_LIBRARY_INVALID"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("RESOURCE_LIBRARY_INVALID")
        document[key] = value
    return document


def _reject_nonfinite_number(_: str) -> None:
    raise ValueError("RESOURCE_LIBRARY_INVALID")


def load_bundled_resource_library() -> dict[str, Any]:
    """只從套件內自包含 JSON 載入 library；損壞或缺檔一律失敗。"""

    path = Path(__file__).with_name("data") / "resource_library_v1.json"
    try:
        document = json.loads(
            path.read_bytes(),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_number,
        )
    except (
        OSError,
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise ValueError("RESOURCE_LIBRARY_LOAD_FAILED") from error
    if validate_resource_library(document) is not None:
        raise ValueError("RESOURCE_LIBRARY_LOAD_FAILED")
    return document


def _resource_concepts_by_label(
    library: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    evidence_by_id = {
        evidence["evidence_id"]: evidence for evidence in library["evidence"]
    }
    sources_by_id = {
        source["resource_id"]: source for source in library["sources"]
    }
    source_sha_by_concept_id = {}
    concepts_by_label: dict[str, list[dict[str, Any]]] = {}
    for concept in library["concepts"]:
        evidence = evidence_by_id[concept["evidence_ids"][0]]
        source_sha_by_concept_id[concept["concept_id"]] = sources_by_id[
            evidence["resource_id"]
        ]["source_sha256"]
        concepts_by_label.setdefault(_normalized_label(concept["label"]), []).append(
            concept
        )
    return concepts_by_label, source_sha_by_concept_id


def _study_output_is_valid(study_output: Any) -> bool:
    try:
        return validate_study_material_output(study_output) is None
    except (KeyError, RecursionError, TypeError, ValueError):
        return False


def _build_context_document(
    study_output: dict[str, Any], library: dict[str, Any]
) -> dict[str, Any]:
    concepts_by_label, source_sha_by_concept_id = _resource_concepts_by_label(
        library
    )
    study_source_sha256 = study_output["source_binding"]["source_sha256"]
    matches = []
    has_exact_label = False
    for study_concept in study_output["concepts"]:
        resource_concepts = concepts_by_label.get(
            _normalized_label(study_concept["label"]), []
        )
        if resource_concepts:
            has_exact_label = True
        for resource_concept in resource_concepts:
            resource_concept_id = resource_concept["concept_id"]
            if source_sha_by_concept_id[resource_concept_id] == study_source_sha256:
                continue
            identity = {
                "schema": MAP_RESOURCE_CONTEXT_SCHEMA,
                "matching_policy": MATCHING_POLICY,
                "study_output_id": study_output["output_id"],
                "resource_library_revision": library["library_revision"],
                "study_concept_id": study_concept["concept_id"],
                "resource_concept_id": resource_concept_id,
                "match_reason": "EXACT_NORMALIZED_LABEL",
            }
            matches.append(
                {
                    "match_id": "resource-match:sha256:" + canonical_sha256(identity),
                    "study_concept_id": study_concept["concept_id"],
                    "resource_concept_id": resource_concept_id,
                    "match_reason": "EXACT_NORMALIZED_LABEL",
                    "processing": "partial",
                    "quality": "needs_review",
                    "decision": "review",
                    "reason_codes": [_MATCH_REASON],
                }
            )
    matches.sort(
        key=lambda match: (
            match["study_concept_id"],
            match["resource_concept_id"],
            match["match_id"],
        )
    )
    if matches:
        status = _MATCH_STATUS
        reason_codes = [_MATCH_REASON]
    else:
        status = _ACCEPTED_STATUS
        reason_codes = [
            _NO_DISTINCT_SOURCE_REASON if has_exact_label else _NO_LABEL_REASON
        ]
    document = {
        "schema": MAP_RESOURCE_CONTEXT_SCHEMA,
        "matching_policy": MATCHING_POLICY,
        "study_output_id": study_output["output_id"],
        "resource_library_revision": library["library_revision"],
        "matches": matches,
        "processing": status[0],
        "quality": status[1],
        "decision": status[2],
        "reason_codes": reason_codes,
    }
    document["context_revision"] = _context_revision(document)
    return document


def build_map_resource_context(
    study_output: Any, library: Any
) -> dict[str, Any]:
    """以完整 normalized label equality 建立可交給 Map 的 Resource context。"""

    if (
        not _study_output_is_valid(study_output)
        or validate_resource_library(library) is not None
    ):
        raise ValueError("MAP_RESOURCE_CONTEXT_INPUT_INVALID")
    return _build_context_document(study_output, library)


def _validate_map_resource_context(
    context: Any, study_output: Any, library: Any
) -> str | None:
    """重算完整 matching 結果，避免漏配、錯配或成功狀態失真。"""

    if (
        not _study_output_is_valid(study_output)
        or validate_resource_library(library) is not None
    ):
        return "MAP_RESOURCE_CONTEXT_INVALID"
    expected = _build_context_document(study_output, library)
    if context != expected:
        return "MAP_RESOURCE_CONTEXT_INVALID"
    return None


def validate_map_resource_context(
    context: Any, study_output: Any, library: Any
) -> str | None:
    """重算完整 matching 結果，避免漏配、錯配或成功狀態失真。"""

    try:
        return _validate_map_resource_context(context, study_output, library)
    except (KeyError, RecursionError, TypeError, ValueError):
        return "MAP_RESOURCE_CONTEXT_INVALID"


def promote_resources_to_formal_concepts(
    formal_concepts: list[dict[str, Any]],
    context: Any,
    study_output: Any,
    library: Any,
) -> dict[str, Any]:
    """依 Formal Resolution provenance promotion 已有 match，不重做 matching。"""

    if validate_map_resource_context(context, study_output, library) is not None:
        raise ValueError("RESOURCE_PROMOTION_INPUT_INVALID")
    if not isinstance(formal_concepts, list) or any(
        not isinstance(concept, dict)
        or not isinstance(concept.get("formal_concept_id"), str)
        or not isinstance(concept.get("source_concept_ids"), list)
        or not concept["source_concept_ids"]
        or concept.get("operation") not in {"KEEP", "MERGE"}
        for concept in formal_concepts
    ):
        raise ValueError("RESOURCE_PROMOTION_INPUT_INVALID")

    concepts_by_id = {concept["concept_id"]: concept for concept in library["concepts"]}
    evidence_by_id = {evidence["evidence_id"]: evidence for evidence in library["evidence"]}
    sources_by_id = {source["resource_id"]: source for source in library["sources"]}
    formal_by_source: dict[str, list[dict[str, Any]]] = {}
    promoted_formal = deepcopy(formal_concepts)
    for formal in promoted_formal:
        formal["supplementary_resources"] = []
        for source_concept_id in formal["source_concept_ids"]:
            formal_by_source.setdefault(source_concept_id, []).append(formal)

    decisions = []
    promoted_matches = 0
    dropped_matches = 0
    split_review_matches = 0
    resources_by_formal: dict[str, dict[str, dict[str, Any]]] = {
        formal["formal_concept_id"]: {} for formal in promoted_formal
    }
    for match in context["matches"]:
        target_nodes = formal_by_source.get(match["study_concept_id"], [])
        if len(target_nodes) != 1:
            raise ValueError("RESOURCE_PROMOTION_INPUT_INVALID")

        resource_concept = concepts_by_id[match["resource_concept_id"]]
        evidence = [
            evidence_by_id[evidence_id]
            for evidence_id in resource_concept["evidence_ids"]
        ]
        resource_ids = {item["resource_id"] for item in evidence}
        if len(resource_ids) != 1:
            raise ValueError("RESOURCE_PROMOTION_INPUT_INVALID")
        resource_id = next(iter(resource_ids))
        source = sources_by_id[resource_id]
        formal = target_nodes[0]
        resources = resources_by_formal[formal["formal_concept_id"]]
        existing = resources.get(resource_concept["concept_id"])
        if existing is None:
            existing = {
                "resource_concept_id": resource_concept["concept_id"],
                "resource_id": resource_id,
                "label": resource_concept["label"],
                "title": source["title"],
                "authors": deepcopy(source["authors"]),
                "source_url": source["source_url"],
                "citation": source["citation"],
                "license": source["license"],
                "license_url": source["license_url"],
                "use_boundary": source["use_boundary"],
                "page_numbers": sorted({item["page_number"] for item in evidence}),
                "resource_evidence_ids": sorted(resource_concept["evidence_ids"]),
                "match_ids": [],
                "study_concept_ids": [],
                "match_reason": match["match_reason"],
            }
            resources[resource_concept["concept_id"]] = existing
        existing["match_ids"].append(match["match_id"])
        existing["study_concept_ids"].append(match["study_concept_id"])
        promoted_matches += 1

    for formal in promoted_formal:
        resources = resources_by_formal[formal["formal_concept_id"]]
        for resource in resources.values():
            resource["match_ids"] = sorted(set(resource["match_ids"]))
            resource["study_concept_ids"] = sorted(set(resource["study_concept_ids"]))
            resource["promotion_id"] = (
                "resource-promotion:sha256:" + canonical_sha256(resource)
            )
        formal["supplementary_resources"] = sorted(
            resources.values(), key=lambda resource: resource["resource_concept_id"]
        )
    for decision in decisions:
        decision["decision_id"] = (
            "resource-promotion-decision:sha256:" + canonical_sha256(decision)
        )
    decisions.sort(key=lambda item: item["match_id"])
    promoted_resources = sum(
        len(formal["supplementary_resources"]) for formal in promoted_formal
    )
    return {
        "formal_concepts": promoted_formal,
        "resource_binding": {
            "context_revision": context["context_revision"],
            "library_revision": library["library_revision"],
            "matching_policy": context["matching_policy"],
            "promotion_policy": PROMOTION_POLICY,
        },
        "resource_diagnostics": {
            "matches": len(context["matches"]),
            "promoted_matches": promoted_matches,
            "promoted_resources": promoted_resources,
            "dropped_matches": dropped_matches,
            "split_review_matches": split_review_matches,
        },
        "resource_decisions": decisions,
    }
