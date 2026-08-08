from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from pdf_evidence.study_material_output import validate_study_material_output

from .catalog import (
    SUBJECTS,
    _canonical_sha256,
    _failure,
    _nonempty_string,
    _normalized_text,
    _resource_reason,
    _valid_string_list,
    _valid_timestamp,
    validate_controlled_resource_catalog,
)

LEARNING_RESOURCE_SCHEMA = "learning-resource-result/v1"
RESOURCE_MATCH_REVIEW_SCHEMA = "resource-match-review/v1"
_MATCH_FIELDS = {
    "resource_id",
    "concept_id",
    "resource_key",
    "subject",
    "title",
    "source_locator",
    "artifact_ref",
    "artifact_sha256",
    "use_boundary",
    "learning_use",
    "match_basis",
    "matched_terms",
    "processing",
    "quality",
    "decision",
    "reason_code",
}
_RESULT_FIELDS = {
    "schema",
    "result_revision",
    "source_s2_revision",
    "catalog_revision",
    "subject",
    "resources",
    "produced_at",
    "run_id",
    "processing",
    "quality",
    "decision",
    "reason_code",
}
_REVIEW_FIELDS = {
    "schema",
    "review_id",
    "source_s2_revision",
    "catalog_revision",
    "concept_id",
    "resource_key",
    "review_basis",
    "evidence_terms",
    "reviewed_at",
    "review_run_id",
    "processing",
    "quality",
    "decision",
    "reason_code",
}
_MATCH_BASES = {
    "explicit_name",
    "explicit_keyword",
    "approved_summary_review",
}


def _term_occurs(term: str, text: str) -> bool:
    normalized_term = _normalized_text(term)
    normalized_text = _normalized_text(text)
    return normalized_term == normalized_text or (
        len(normalized_term) >= 2 and normalized_term in normalized_text
    )


def _concept_inputs(
    study_material_output: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], dict[str, list[str]]]:
    concepts = {
        concept["concept_id"]: concept
        for concept in study_material_output["concepts"]
    }
    keywords = {concept_id: [] for concept_id in concepts}
    for keyword in study_material_output["keywords"]:
        keywords[keyword["concept_id"]].append(keyword["keyword"])
    summaries = {concept_id: [] for concept_id in concepts}
    for summary in study_material_output["summaries"]:
        for concept_id in summary["source_concept_ids"]:
            summaries[concept_id].append(summary["summary"])
    return concepts, keywords, summaries


def _matching_terms(terms: list[str], texts: list[str]) -> list[str]:
    matched = {
        term
        for term in terms
        if any(_term_occurs(term, text) for text in texts)
    }
    return sorted(matched, key=_normalized_text)


def _explicit_match(
    concept: dict[str, Any],
    concept_keywords: list[str],
    resource: dict[str, Any],
) -> tuple[str, list[str]] | None:
    name_matches = _matching_terms(
        [concept["normalized_name"]],
        [resource["title"], *resource["topics"]],
    )
    if name_matches:
        return "explicit_name", name_matches
    keyword_matches = _matching_terms(concept_keywords, resource["keywords"])
    if keyword_matches:
        return "explicit_keyword", keyword_matches
    return None


def _valid_matched_terms(terms: Any) -> bool:
    return (
        _valid_string_list(terms)
        and terms == sorted(terms, key=_normalized_text)
        and len({_normalized_text(term) for term in terms}) == len(terms)
    )


def _review_reason(
    review: Any,
    study_material_output: Any,
    catalog: Any,
    artifact_root: str | Path,
) -> str | None:
    """複核詞必須同時出現在 S2 summary 與資源文字，才接受歧義配對。"""
    if validate_study_material_output(study_material_output) is not None:
        return "RESOURCE_MATCH_REVIEW_SOURCE_INVALID"
    if validate_controlled_resource_catalog(catalog, artifact_root) is not None:
        return "RESOURCE_MATCH_REVIEW_CATALOG_INVALID"
    if not isinstance(review, dict) or set(review) != _REVIEW_FIELDS:
        return "RESOURCE_MATCH_REVIEW_ROOT_INVALID"
    if review["schema"] != RESOURCE_MATCH_REVIEW_SCHEMA:
        return "RESOURCE_MATCH_REVIEW_ROOT_INVALID"
    if (
        review["source_s2_revision"] != study_material_output["output_id"]
        or review["catalog_revision"] != catalog["catalog_revision"]
    ):
        return "RESOURCE_MATCH_REVIEW_BINDING_INVALID"
    if (
        review["review_basis"] != "summary"
        or not _valid_timestamp(review["reviewed_at"])
        or not _nonempty_string(review["review_run_id"])
    ):
        return "RESOURCE_MATCH_REVIEW_ROOT_INVALID"
    concepts, _, summaries = _concept_inputs(study_material_output)
    concept = concepts.get(review["concept_id"])
    resource = next(
        (
            item
            for item in catalog["resources"]
            if item["resource_key"] == review["resource_key"]
        ),
        None,
    )
    if (
        concept is None
        or resource is None
    ):
        return "RESOURCE_MATCH_REVIEW_BINDING_INVALID"
    matched_terms = review["evidence_terms"]
    resource_texts = [resource["title"], *resource["topics"], *resource["keywords"]]
    if (
        not _valid_matched_terms(matched_terms)
        or any(
            not any(
                _term_occurs(term, text)
                for text in summaries[review["concept_id"]]
            )
            or not any(_term_occurs(term, text) for text in resource_texts)
            for term in matched_terms
        )
    ):
        return "RESOURCE_MATCH_REVIEW_TERMS_INVALID"
    status = (
        review["processing"],
        review["quality"],
        review["decision"],
        review["reason_code"],
    )
    if status != (
        "succeeded",
        "accepted",
        "retain",
        "RESOURCE_MATCH_REVIEW_ACCEPTED",
    ):
        return "RESOURCE_MATCH_REVIEW_STATUS_INVALID"
    content = {key: value for key, value in review.items() if key != "review_id"}
    review_sha256 = _canonical_sha256(content)
    if review["review_id"] != f"resource-match-review:sha256:{review_sha256}":
        return "RESOURCE_MATCH_REVIEW_IDENTITY_INVALID"
    return None


def validate_resource_match_review(
    review: Any,
    study_material_output: Any,
    catalog: Any,
    artifact_root: str | Path,
) -> str | None:
    """重驗 summary 歧義複核的 S2、catalog、artifact 與文字綁定。"""
    return _review_reason(review, study_material_output, catalog, artifact_root)


def _review_matches(
    review: dict[str, Any],
    concept_id: str,
    resource_key: str,
) -> bool:
    return (
        review["concept_id"] == concept_id
        and review["resource_key"] == resource_key
    )


def _matched_resource(
    concept_id: str,
    resource: dict[str, Any],
    match_basis: str,
    matched_terms: list[str],
) -> dict[str, Any] | None:
    identity_sha256 = _canonical_sha256(
        {"concept_id": concept_id, "resource_key": resource["resource_key"]}
    )
    if identity_sha256 is None:
        return None
    return {
        "resource_id": f"learning-resource:sha256:{identity_sha256}",
        "concept_id": concept_id,
        "subject": resource["subject"],
        **{
            field: deepcopy(resource[field])
            for field in (
                "resource_key",
                "title",
                "source_locator",
                "artifact_ref",
                "artifact_sha256",
                "use_boundary",
                "learning_use",
            )
        },
        "match_basis": match_basis,
        "matched_terms": deepcopy(matched_terms),
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "RESOURCE_MATCH_ACCEPTED",
    }


def build_learning_resource_result(
    study_material_output: Any,
    catalog: Any,
    artifact_root: str | Path,
    subject: Any,
    review_artifacts: Any = None,
    *,
    produced_at: Any = None,
    run_id: Any = None,
) -> dict[str, Any]:
    """依 subject、明確文字與已驗證複核，建立 0..N 筆學習資源。"""
    if validate_study_material_output(study_material_output) is not None:
        return _failure("LEARNING_RESOURCE_SOURCE_INVALID", LEARNING_RESOURCE_SCHEMA)
    if subject not in SUBJECTS:
        return _failure("LEARNING_RESOURCE_SUBJECT_INVALID", LEARNING_RESOURCE_SCHEMA)
    if not _valid_timestamp(produced_at) or not _nonempty_string(run_id):
        return _failure("LEARNING_RESOURCE_RUN_INVALID", LEARNING_RESOURCE_SCHEMA)
    if validate_controlled_resource_catalog(catalog, artifact_root) is not None:
        return _failure("LEARNING_RESOURCE_CATALOG_INVALID", LEARNING_RESOURCE_SCHEMA)
    if review_artifacts is None:
        checked_reviews = []
    elif not isinstance(review_artifacts, list):
        return _failure("RESOURCE_MATCH_REVIEW_INVALID", LEARNING_RESOURCE_SCHEMA)
    else:
        checked_reviews = review_artifacts
    review_keys = set()
    for review in checked_reviews:
        if (
            _review_reason(review, study_material_output, catalog, artifact_root)
            is not None
        ):
            return _failure("RESOURCE_MATCH_REVIEW_INVALID", LEARNING_RESOURCE_SCHEMA)
        review_key = (review["concept_id"], review["resource_key"])
        if review_key in review_keys:
            return _failure("RESOURCE_MATCH_REVIEW_INVALID", LEARNING_RESOURCE_SCHEMA)
        review_keys.add(review_key)

    concepts, keywords, _ = _concept_inputs(study_material_output)
    resources = []
    for concept_id, concept in concepts.items():
        for catalog_resource in catalog["resources"]:
            if catalog_resource["subject"] != subject:
                continue
            explicit = _explicit_match(concept, keywords[concept_id], catalog_resource)
            if explicit is not None:
                match_basis, matched_terms = explicit
            else:
                review = next(
                    (
                        item
                        for item in checked_reviews
                        if _review_matches(
                            item, concept_id, catalog_resource["resource_key"]
                        )
                    ),
                    None,
                )
                if review is None:
                    continue
                match_basis = "approved_summary_review"
                matched_terms = review["evidence_terms"]
            if (
                _resource_reason(catalog_resource, Path(artifact_root).resolve())
                is not None
            ):
                return _failure("LEARNING_RESOURCE_GATE_INVALID", LEARNING_RESOURCE_SCHEMA)
            matched_resource = _matched_resource(
                concept_id, catalog_resource, match_basis, matched_terms
            )
            if matched_resource is None:
                return _failure(
                    "LEARNING_RESOURCE_IDENTITY_INVALID",
                    LEARNING_RESOURCE_SCHEMA,
                )
            resources.append(matched_resource)

    resources.sort(key=lambda resource: resource["resource_id"])
    if resources:
        status = (
            "succeeded",
            "accepted",
            "retain",
            "CONTROLLED_RESOURCE_MATCHES_FOUND",
        )
    else:
        status = (
            "succeeded",
            "accepted",
            "retain",
            "NO_CONTROLLED_RESOURCE_MATCH",
        )
    processing, quality, decision, reason_code = status
    content = {
        "schema": LEARNING_RESOURCE_SCHEMA,
        "source_s2_revision": study_material_output["output_id"],
        "catalog_revision": catalog["catalog_revision"],
        "subject": subject,
        "resources": resources,
        "produced_at": produced_at,
        "run_id": run_id,
        "processing": processing,
        "quality": quality,
        "decision": decision,
        "reason_code": reason_code,
    }
    semantic_content = {
        key: value for key, value in content.items() if key not in {"produced_at", "run_id"}
    }
    revision_sha256 = _canonical_sha256(semantic_content)
    if revision_sha256 is None:
        return _failure("LEARNING_RESOURCE_IDENTITY_INVALID", LEARNING_RESOURCE_SCHEMA)
    result = {
        "result_revision": f"learning-resource-result:sha256:{revision_sha256}",
        **content,
    }
    reason = validate_learning_resource_result(
        result,
        study_material_output,
        catalog,
        artifact_root,
        checked_reviews,
    )
    return result if reason is None else _failure(reason, LEARNING_RESOURCE_SCHEMA)


def validate_learning_resource_result(
    result: Any,
    study_material_output: Any,
    catalog: Any,
    artifact_root: str | Path,
    review_artifacts: Any = None,
) -> str | None:
    """重驗結果 revision、輸入 references、配對文字與每筆來源 Gate。"""
    if validate_study_material_output(study_material_output) is not None:
        return "LEARNING_RESOURCE_SOURCE_INVALID"
    if validate_controlled_resource_catalog(catalog, artifact_root) is not None:
        return "LEARNING_RESOURCE_CATALOG_INVALID"
    if not isinstance(result, dict) or set(result) != _RESULT_FIELDS:
        return "LEARNING_RESOURCE_ROOT_INVALID"
    if (
        result["schema"] != LEARNING_RESOURCE_SCHEMA
        or result["subject"] not in SUBJECTS
        or result["source_s2_revision"] != study_material_output["output_id"]
        or result["catalog_revision"] != catalog["catalog_revision"]
    ):
        return "LEARNING_RESOURCE_BINDING_INVALID"
    if not _valid_timestamp(result["produced_at"]) or not _nonempty_string(
        result["run_id"]
    ):
        return "LEARNING_RESOURCE_RUN_INVALID"
    content = {
        key: value
        for key, value in result.items()
        if key not in {"result_revision", "produced_at", "run_id"}
    }
    revision_sha256 = _canonical_sha256(content)
    if (
        result["result_revision"]
        != f"learning-resource-result:sha256:{revision_sha256}"
    ):
        return "LEARNING_RESOURCE_REVISION_INVALID"

    if review_artifacts is None:
        checked_reviews = []
    elif not isinstance(review_artifacts, list):
        return "RESOURCE_MATCH_REVIEW_INVALID"
    else:
        checked_reviews = review_artifacts
    review_keys = set()
    for review in checked_reviews:
        if (
            _review_reason(review, study_material_output, catalog, artifact_root)
            is not None
        ):
            return "RESOURCE_MATCH_REVIEW_INVALID"
        review_key = (review["concept_id"], review["resource_key"])
        if review_key in review_keys:
            return "RESOURCE_MATCH_REVIEW_INVALID"
        review_keys.add(review_key)

    resources = result["resources"]
    if not isinstance(resources, list) or any(
        not isinstance(resource, dict) for resource in resources
    ):
        return "LEARNING_RESOURCE_ROOT_INVALID"
    if resources != sorted(
        resources,
        key=lambda resource: resource.get("resource_id", ""),
    ):
        return "LEARNING_RESOURCE_ORDER_INVALID"
    concepts, keywords, _ = _concept_inputs(study_material_output)
    catalog_resources = {
        resource["resource_key"]: resource for resource in catalog["resources"]
    }
    resource_ids = set()
    pairs = set()
    for resource in resources:
        if set(resource) != _MATCH_FIELDS:
            return "LEARNING_RESOURCE_ITEM_INVALID"
        concept = concepts.get(resource["concept_id"])
        catalog_resource = catalog_resources.get(resource["resource_key"])
        if (
            concept is None
            or catalog_resource is None
            or catalog_resource["subject"] != result["subject"]
            or resource["subject"] != result["subject"]
            or _resource_reason(
                catalog_resource,
                Path(artifact_root).resolve(),
            )
            is not None
        ):
            return "LEARNING_RESOURCE_GATE_INVALID"
        copied_fields = (
            "title",
            "source_locator",
            "artifact_ref",
            "artifact_sha256",
            "use_boundary",
            "learning_use",
        )
        if any(resource[field] != catalog_resource[field] for field in copied_fields):
            return "LEARNING_RESOURCE_BINDING_INVALID"
        identity_sha256 = _canonical_sha256(
            {
                "concept_id": resource["concept_id"],
                "resource_key": resource["resource_key"],
            }
        )
        if resource["resource_id"] != f"learning-resource:sha256:{identity_sha256}":
            return "LEARNING_RESOURCE_IDENTITY_INVALID"
        pair = (resource["concept_id"], resource["resource_key"])
        if resource["resource_id"] in resource_ids or pair in pairs:
            return "LEARNING_RESOURCE_DUPLICATE"
        resource_ids.add(resource["resource_id"])
        pairs.add(pair)
        if resource["match_basis"] not in _MATCH_BASES or not _valid_matched_terms(
            resource["matched_terms"]
        ):
            return "LEARNING_RESOURCE_MATCH_INVALID"
        explicit = _explicit_match(
            concept,
            keywords[resource["concept_id"]],
            catalog_resource,
        )
        if resource["match_basis"] == "approved_summary_review":
            matching_reviews = [
                review
                for review in checked_reviews
                if _review_matches(
                    review,
                    resource["concept_id"],
                    resource["resource_key"],
                )
            ]
            if (
                explicit is not None
                or len(matching_reviews) != 1
                or matching_reviews[0]["evidence_terms"] != resource["matched_terms"]
            ):
                return "LEARNING_RESOURCE_MATCH_INVALID"
        elif explicit != (resource["match_basis"], resource["matched_terms"]):
            return "LEARNING_RESOURCE_MATCH_INVALID"
        status = (
            resource["processing"],
            resource["quality"],
            resource["decision"],
            resource["reason_code"],
        )
        if status != (
            "succeeded",
            "accepted",
            "retain",
            "RESOURCE_MATCH_ACCEPTED",
        ):
            return "LEARNING_RESOURCE_STATUS_INVALID"

    expected_pairs = set()
    for concept_id, concept in concepts.items():
        for catalog_resource in catalog["resources"]:
            if catalog_resource["subject"] != result["subject"]:
                continue
            explicit = _explicit_match(
                concept,
                keywords[concept_id],
                catalog_resource,
            )
            has_review = any(
                _review_matches(
                    review,
                    concept_id,
                    catalog_resource["resource_key"],
                )
                for review in checked_reviews
            )
            if explicit is not None or has_review:
                expected_pairs.add((concept_id, catalog_resource["resource_key"]))
    if pairs != expected_pairs:
        return "LEARNING_RESOURCE_MATCH_INVALID"

    root_status = (
        result["processing"],
        result["quality"],
        result["decision"],
        result["reason_code"],
    )
    expected_status = (
        (
            "succeeded",
            "accepted",
            "retain",
            "CONTROLLED_RESOURCE_MATCHES_FOUND",
        )
        if resources
        else (
            "succeeded",
            "accepted",
            "retain",
            "NO_CONTROLLED_RESOURCE_MATCH",
        )
    )
    return (
        None
        if root_status == expected_status
        else "LEARNING_RESOURCE_STATUS_INVALID"
    )
