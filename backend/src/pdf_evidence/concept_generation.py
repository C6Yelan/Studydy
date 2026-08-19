from __future__ import annotations

import json
import math
from typing import Any
import unicodedata

from .ocr_page_evidence import canonical_sha256


SEMANTIC_REQUEST_SCHEMA = "concept-generation-input/v1"
SEMANTIC_ARTIFACT_SCHEMA = "semantic-page-concepts/v1"
PROMPT_TEMPLATE = """You extract study concepts from normalized text evidence.
Use only the supplied evidence. Return JSON only, with exactly this shape:
{"concepts":[{"label":"...","definition":"...","key_points":["..."],"evidence_ids":["..."]}]}
Every central claim and key point must be grounded by its listed Evidence IDs.
Do not return status, paths, coordinates, commentary, markdown, or additional fields."""
PROMPT_SHA256 = "97f14f58b3599f22fcda7921d69fbd64035562c11897a4eadc6aacb355f5ca5c"
PROCESSING_POLICY = "concept-evidence-review/v1"
MAX_MODEL_OUTPUT_BYTES = 65_536


class SemanticOutputError(ValueError):
    """只攜帶固定 reason code，不攜帶模型內容。"""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SemanticOutputError("MODEL_OUTPUT_INVALID_JSON")
        value[key] = item
    return value


def _reject_constant(_: str) -> None:
    raise SemanticOutputError("MODEL_OUTPUT_INVALID_JSON")


def _exact_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise SemanticOutputError("INVALID_TEXT_FIELD")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if normalized != value or any(ord(character) < 32 for character in value):
        raise SemanticOutputError("INVALID_TEXT_FIELD")
    return value


def _exact_evidence_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or "\r" in value:
        raise SemanticOutputError("INVALID_TEXT_FIELD")
    normalized = unicodedata.normalize("NFC", value)
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    if normalized != value or any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise SemanticOutputError("INVALID_TEXT_FIELD")
    return value


def _normalized_candidate_text(value: Any, maximum: int) -> str:
    """正規化模型產生的 Concept 文字，再檢查可用性與安全邊界。"""
    if not isinstance(value, str):
        raise SemanticOutputError("INVALID_TEXT_FIELD")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(character) < 32 for character in normalized)
    ):
        raise SemanticOutputError("INVALID_TEXT_FIELD")
    return normalized


def build_semantic_request(page_evidence: dict[str, Any]) -> dict[str, Any]:
    """保留 material identity 與 Evidence locator。"""
    evidence = []
    for block in page_evidence["evidence_blocks"]:
        evidence.append(
            {
                "evidence_id": block["evidence_id"],
                "text": block["text"],
                "locator": {
                    "page": block["locator"]["page"],
                    "block_id": block["locator"]["block_id"],
                    "region": block["locator"]["region"],
                },
            }
        )
    request = {
        "schema": SEMANTIC_REQUEST_SCHEMA,
        "material_id": page_evidence["material_id"],
        "material_revision": page_evidence["material_revision"],
        "section_id": page_evidence["section_id"],
        "evidence": evidence,
    }
    validate_semantic_request(request)
    return request


def validate_semantic_request(request: Any) -> dict[str, dict[str, Any]]:
    expected = {"schema", "material_id", "material_revision", "section_id", "evidence"}
    if not isinstance(request, dict) or set(request) != expected or request["schema"] != SEMANTIC_REQUEST_SCHEMA:
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    for field in ("material_id", "material_revision", "section_id"):
        _exact_text(request[field], 128)
    evidence_items = request["evidence"]
    if not isinstance(evidence_items, list) or not evidence_items:
        raise SemanticOutputError("INVALID_EVIDENCE_COUNT")
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for evidence in evidence_items:
        if not isinstance(evidence, dict) or set(evidence) != {"evidence_id", "text", "locator"}:
            raise SemanticOutputError("INPUT_SCHEMA_INVALID")
        evidence_id = _exact_text(evidence["evidence_id"], 128)
        if evidence_id in evidence_by_id:
            raise SemanticOutputError("DUPLICATE_EVIDENCE_ID")
        _exact_evidence_text(evidence["text"])
        locator = evidence["locator"]
        if not isinstance(locator, dict) or set(locator) != {"page", "block_id", "region"}:
            raise SemanticOutputError("INVALID_LOCATOR")
        if type(locator["page"]) is not int or locator["page"] < 1:
            raise SemanticOutputError("INVALID_LOCATOR")
        _exact_text(locator["block_id"], 128)
        region = locator["region"]
        if (
            not isinstance(region, list)
            or len(region) != 4
            or any(type(number) not in {int, float} or not math.isfinite(number) for number in region)
            or not (region[0] < region[2] and region[1] < region[3])
        ):
            raise SemanticOutputError("INVALID_LOCATOR")
        evidence_by_id[evidence_id] = evidence
    return evidence_by_id


def _decode_complete_output(model_text: Any) -> dict[str, Any]:
    if not isinstance(model_text, str):
        raise SemanticOutputError("MODEL_OUTPUT_INVALID_JSON")
    if len(model_text.encode("utf-8")) > MAX_MODEL_OUTPUT_BYTES:
        raise SemanticOutputError("MODEL_OUTPUT_TOO_LARGE")
    try:
        output = json.loads(
            model_text,
            object_pairs_hook=_without_duplicates,
            parse_constant=_reject_constant,
        )
    except SemanticOutputError:
        raise
    except (RecursionError, ValueError) as error:
        raise SemanticOutputError("MODEL_OUTPUT_INVALID_JSON") from error
    if not isinstance(output, dict):
        raise SemanticOutputError("CANDIDATE_SCHEMA_INVALID")
    return output


def _candidate_reason(candidate: Any, evidence_ids: set[str]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if not isinstance(candidate, dict) or set(candidate) != {
            "label",
            "definition",
            "key_points",
            "evidence_ids",
        }:
            raise SemanticOutputError("CANDIDATE_SCHEMA_INVALID")
        label = _normalized_candidate_text(candidate["label"], 120)
        definition = _normalized_candidate_text(candidate["definition"], 1_000)
        key_points = candidate["key_points"]
        if not isinstance(key_points, list) or not 1 <= len(key_points) <= 10:
            raise SemanticOutputError("INVALID_KEY_POINTS")
        normalized_points = [_normalized_candidate_text(point, 300) for point in key_points]
        references = candidate["evidence_ids"]
        if (
            not isinstance(references, list)
            or not 1 <= len(references) <= 16
            or any(not isinstance(reference, str) for reference in references)
        ):
            raise SemanticOutputError("INVALID_EVIDENCE_REFERENCES")
        if len(set(references)) != len(references):
            raise SemanticOutputError("DUPLICATE_EVIDENCE_REFERENCE")
        if not set(references) <= evidence_ids:
            raise SemanticOutputError("UNKNOWN_EVIDENCE_ID")
        return {
            "label": label,
            "definition": definition,
            "key_points": normalized_points,
            "evidence_ids": references,
        }, None
    except SemanticOutputError as error:
        return None, error.reason_code


def validate_concepts(
    model_text: Any,
    *,
    semantic_request: dict[str, Any],
    page_ref: str,
    input_binding: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    """只信任 model 的 Concept fields；狀態與決策由 backend 建立。"""
    evidence_by_id = validate_semantic_request(semantic_request)
    output = _decode_complete_output(model_text)
    if set(output) != {"concepts"}:
        raise SemanticOutputError("CANDIDATE_SCHEMA_INVALID")
    candidates = output["concepts"]
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 24:
        raise SemanticOutputError("INVALID_CONCEPT_COUNT")
    concepts: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    evidence_ids = set(evidence_by_id)
    for index, candidate in enumerate(candidates):
        valid, reason = _candidate_reason(candidate, evidence_ids)
        if valid is None:
            rejected.append(
                {
                    "candidate_index": index,
                    "processing": "failed",
                    "quality": "needs_review",
                    "decision": "reject",
                    "reason_codes": [reason],
                }
            )
            continue
        identity = {"page_ref": page_ref, **valid}
        concepts.append(
            {
                "concept_id": f"concept:sha256:{canonical_sha256(identity)}",
                "page_ref": page_ref,
                **valid,
                "processing": "succeeded",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": ["SEMANTIC_REVIEW_REQUIRED"],
            }
        )
    if not concepts:
        raise SemanticOutputError("NO_USABLE_CONCEPT")
    return {
        "schema": SEMANTIC_ARTIFACT_SCHEMA,
        "page_ref": page_ref,
        "concepts": concepts,
        "rejected_candidates": rejected,
        "input_binding": input_binding,
        "attempt": attempt,
        "processing_policy": PROCESSING_POLICY,
        "processing": "partial" if rejected else "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["SEMANTIC_REVIEW_REQUIRED"],
    }
