from __future__ import annotations

import json
from typing import Any
import unicodedata

from .ocr_page_evidence import canonical_sha256


SEMANTIC_REQUEST_SCHEMA = "concept-generation-input/v3"
SEMANTIC_ARTIFACT_SCHEMA = "semantic-page-concepts/v2"
PROCESSING_POLICY = "claim-grounded-concept-review/v1"
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


def _normalized_candidate_text(value: Any) -> str:
    """正規化模型產生的 Concept 文字，再檢查可用性與安全邊界。"""
    if not isinstance(value, str):
        raise SemanticOutputError("INVALID_TEXT_FIELD")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if (
        not normalized
        or any(ord(character) < 32 for character in normalized)
    ):
        raise SemanticOutputError("INVALID_TEXT_FIELD")
    return normalized


def build_semantic_request(
    page_evidence: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """模型只取得短 alias 與文字；正式 Evidence identity 留在後端。"""
    evidence = []
    evidence_aliases = {}
    for index, block in enumerate(page_evidence["evidence_blocks"], start=1):
        alias = f"e{index}"
        evidence.append({"id": alias, "text": block["text"]})
        evidence_aliases[alias] = block["evidence_id"]
    request = {
        "schema": SEMANTIC_REQUEST_SCHEMA,
        "evidence": evidence,
    }
    validate_semantic_request(request)
    return request, evidence_aliases


def validate_semantic_request(request: Any) -> dict[str, dict[str, Any]]:
    expected = {"schema", "evidence"}
    if not isinstance(request, dict) or set(request) != expected or request["schema"] != SEMANTIC_REQUEST_SCHEMA:
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    evidence_items = request["evidence"]
    if not isinstance(evidence_items, list) or not evidence_items:
        raise SemanticOutputError("INVALID_EVIDENCE_COUNT")
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for evidence in evidence_items:
        if not isinstance(evidence, dict) or set(evidence) != {"id", "text"}:
            raise SemanticOutputError("INPUT_SCHEMA_INVALID")
        evidence_id = _exact_text(evidence["id"], 16)
        if evidence_id in evidence_by_id:
            raise SemanticOutputError("DUPLICATE_EVIDENCE_ID")
        _exact_evidence_text(evidence["text"])
        evidence_by_id[evidence_id] = evidence
    return evidence_by_id


def split_semantic_request(
    request: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """依原頁面順序切半；單一超長 Evidence 則只切它的文字。"""

    evidence = list(validate_semantic_request(request).values())
    if len(evidence) > 1:
        middle = len(evidence) // 2
        groups = (evidence[:middle], evidence[middle:])
    else:
        item = evidence[0]
        text = item["text"]
        if len(text) < 2:
            raise SemanticOutputError("MODEL_INPUT_TOO_LARGE")
        middle = len(text) // 2
        left, right = text[:middle].strip(), text[middle:].strip()
        if not left or not right:
            raise SemanticOutputError("MODEL_INPUT_TOO_LARGE")
        groups = ([{"id": item["id"], "text": left}], [{"id": item["id"], "text": right}])
    requests = tuple(
        {"schema": SEMANTIC_REQUEST_SCHEMA, "evidence": group}
        for group in groups
    )
    for child in requests:
        validate_semantic_request(child)
    return requests


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


def _candidate_reason(
    candidate: Any,
    evidence_aliases: dict[str, str],
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if not isinstance(candidate, dict) or set(candidate) != {
            "label",
            "definition",
            "key_points",
        }:
            raise SemanticOutputError("CANDIDATE_SCHEMA_INVALID")
        label = _normalized_candidate_text(candidate["label"])
        definition = _claim(candidate["definition"], evidence_aliases)
        key_points = candidate["key_points"]
        if not isinstance(key_points, list) or not key_points:
            raise SemanticOutputError("INVALID_KEY_POINTS")
        normalized_points = [_claim(point, evidence_aliases) for point in key_points]
        return {
            "label": label,
            "definition": definition,
            "key_points": normalized_points,
        }, None
    except SemanticOutputError as error:
        return None, error.reason_code


def _claim(value: Any, evidence_aliases: dict[str, str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"text", "evidence_ids"}:
        raise SemanticOutputError("CANDIDATE_SCHEMA_INVALID")
    text = _normalized_candidate_text(value["text"])
    references = value["evidence_ids"]
    if (
        not isinstance(references, list)
        or not references
        or any(not isinstance(reference, str) for reference in references)
    ):
        raise SemanticOutputError("INVALID_EVIDENCE_REFERENCES")
    if len(set(references)) != len(references):
        raise SemanticOutputError("DUPLICATE_EVIDENCE_REFERENCE")
    if not set(references) <= set(evidence_aliases):
        raise SemanticOutputError("UNKNOWN_EVIDENCE_ID")
    return {
        "text": text,
        "evidence_ids": [evidence_aliases[reference] for reference in references],
    }


def validate_concepts(
    model_text: Any,
    *,
    semantic_request: dict[str, Any],
    evidence_aliases: dict[str, str],
    page_ref: str,
    input_binding: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    """只信任 model 的 Concept fields；狀態與決策由 backend 建立。"""
    evidence_by_id = validate_semantic_request(semantic_request)
    if set(evidence_by_id) != set(evidence_aliases):
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    output = _decode_complete_output(model_text)
    if set(output) != {"concepts"}:
        raise SemanticOutputError("CANDIDATE_SCHEMA_INVALID")
    candidates = output["concepts"]
    if not isinstance(candidates, list):
        raise SemanticOutputError("INVALID_CONCEPT_COUNT")
    concepts: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        valid, reason = _candidate_reason(candidate, evidence_aliases)
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
        definition = {
            "claim_id": claim_id(page_ref, "definition", valid["definition"]),
            **valid["definition"],
        }
        key_points = [
            {
                "claim_id": claim_id(page_ref, "key_point", point, index=index),
                **point,
            }
            for index, point in enumerate(valid["key_points"])
        ]
        grounded = {"label": valid["label"], "definition": definition, "key_points": key_points}
        concepts.append(
            {
                "concept_id": concept_id(page_ref, **grounded),
                "page_ref": page_ref,
                **grounded,
                "processing": "succeeded",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": ["SEMANTIC_REVIEW_REQUIRED"],
            }
        )
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


def combine_semantic_batches(
    batches: list[dict[str, Any]],
    *,
    page_ref: str,
    input_binding: dict[str, Any],
) -> dict[str, Any]:
    """把同頁各批已驗證結果合回一個 page artifact。"""

    if not batches:
        raise SemanticOutputError("CANDIDATE_SCHEMA_INVALID")
    concepts_by_id: dict[str, dict[str, Any]] = {}
    rejected = []
    for batch in batches:
        if batch.get("page_ref") != page_ref:
            raise SemanticOutputError("INPUT_SCHEMA_INVALID")
        for concept in batch["concepts"]:
            previous = concepts_by_id.get(concept["concept_id"])
            if previous is not None and previous != concept:
                raise SemanticOutputError("CANDIDATE_SCHEMA_INVALID")
            concepts_by_id[concept["concept_id"]] = concept
        for candidate in batch["rejected_candidates"]:
            rejected.append(
                {
                    **candidate,
                    "candidate_index": len(rejected),
                }
            )
    concepts = sorted(concepts_by_id.values(), key=lambda item: item["concept_id"])
    return {
        "schema": SEMANTIC_ARTIFACT_SCHEMA,
        "page_ref": page_ref,
        "concepts": concepts,
        "rejected_candidates": rejected,
        "input_binding": input_binding,
        "attempt": max(batch["attempt"] for batch in batches),
        "processing_policy": PROCESSING_POLICY,
        "processing": "partial" if rejected else "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["SEMANTIC_REVIEW_REQUIRED"],
    }


def claim_id(
    page_ref: str,
    kind: str,
    claim: dict[str, Any],
    *,
    index: int | None = None,
) -> str:
    """以頁面、claim 類型與內容建立不可混頁的穩定 ID。"""

    identity = {"page_ref": page_ref, "kind": kind}
    if index is not None:
        identity["index"] = index
    identity.update(claim)
    return f"claim:sha256:{canonical_sha256(identity)}"


def concept_id(
    page_ref: str,
    label: str,
    definition: dict[str, Any],
    key_points: list[dict[str, Any]],
) -> str:
    """以完整 claim-level 內容建立 Concept 穩定 ID。"""

    identity = {
        "page_ref": page_ref,
        "label": label,
        "definition": definition,
        "key_points": key_points,
    }
    return f"concept:sha256:{canonical_sha256(identity)}"
