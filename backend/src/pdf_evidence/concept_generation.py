from __future__ import annotations

import json
import re
from typing import Any
import unicodedata

from .ocr_page_evidence import canonical_sha256


SEMANTIC_REQUEST_SCHEMA = "concept-generation-input/v3"
SEMANTIC_ARTIFACT_SCHEMA = "semantic-page-concepts/v2"
PROCESSING_POLICY = "claim-grounded-concept-review/v2"
MAX_MODEL_OUTPUT_BYTES = 65_536

_ENGLISH_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "for", "from", "in",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
}
_BRACKET_INDEXES = re.compile(r"^(?:\[\s*\d+\s*\]\s*)+$")
_SIMPLE_ASSIGNMENTS = re.compile(
    r"^(?:[A-Za-z_]\w*(?:\[[^\]]+\])?\s*=\s*[-+]?[A-Za-z_0-9.]+"
    r"(?:\s*[,，]\s*)?)+$"
)
_PANEL_LABEL = re.compile(r"^\([A-Za-z0-9]+\)\s*(.+)$")
_PAGE_SEQUENCE_SUFFIX = re.compile(
    r"\s*\(\s*\d+\s+of\s+\d+\s*\)\s*$", re.IGNORECASE
)
_FIGURE_CAPTION = re.compile(
    r"^(?:figure|fig\.?|圖)\s*[A-Za-z0-9.-]*\s*[:：-]?\s*(.+)$",
    re.IGNORECASE,
)
_ENGLISH_PREDICATE = re.compile(
    r"\b(?:is|are|was|were|has|have|contains?|includes?|uses?|requires?|"
    r"provides?|describes?|represents?|becomes?|stores?|shows?|allows?|"
    r"supports?|creates?|increases?|decreases?|illustrates?|depicts?|"
    r"demonstrates?|showing|follow)\b",
    re.IGNORECASE,
)
_CHINESE_PREDICATE = re.compile(
    r"(?:是|為|包含|包括|表示|需要|可以|會|由|將|具有|提供|描述|建立|"
    r"增加|減少|刪除|插入|儲存|指向|配置|形成|分為|等於)"
)


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


def _grounding_terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    terms = {
        token[:-1] if len(token) > 3 and token.endswith("s") else token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if token not in _ENGLISH_STOP_WORDS
    }
    chinese = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    terms.update(
        chinese[index : index + 2]
        for index in range(max(0, len(chinese) - 1))
    )
    return terms


def _claim_is_grounded(text: str, evidence_text: str) -> bool:
    claim = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    evidence = " ".join(
        unicodedata.normalize("NFKC", evidence_text).casefold().split()
    )
    if claim in evidence:
        return True
    claim_terms = _grounding_terms(claim)
    if not claim_terms:
        return False
    evidence_terms = _grounding_terms(evidence)
    shared_terms = claim_terms & evidence_terms
    if len(shared_terms) / len(claim_terms) >= 0.5:
        return True
    claim_length = len(claim.replace(" ", ""))
    evidence_length = len(evidence.replace(" ", ""))
    return bool(shared_terms) and evidence_length >= claim_length * 0.6


def _claim_is_fragment(text: str, label: str) -> bool:
    normalized = " ".join(unicodedata.normalize("NFKC", text).split())
    normalized_label = " ".join(
        unicodedata.normalize("NFKC", label).split()
    ).casefold()
    if normalized.casefold() == normalized_label:
        return True
    without_sequence = _PAGE_SEQUENCE_SUFFIX.sub("", normalized).strip()
    if without_sequence.casefold() == normalized_label:
        return True
    if _BRACKET_INDEXES.fullmatch(normalized):
        return True
    if _SIMPLE_ASSIGNMENTS.fullmatch(normalized):
        return True
    panel = _PANEL_LABEL.fullmatch(normalized)
    figure = _FIGURE_CAPTION.fullmatch(normalized)
    caption = panel.group(1) if panel is not None else (
        figure.group(1) if figure is not None else None
    )
    if caption is None:
        return False
    return not bool(
        _ENGLISH_PREDICATE.search(caption)
        or _CHINESE_PREDICATE.search(caption)
    )


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
    evidence_by_alias: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if not isinstance(candidate, dict) or set(candidate) != {
            "label",
            "definition",
            "key_points",
        }:
            raise SemanticOutputError("CANDIDATE_SCHEMA_INVALID")
        label = _normalized_candidate_text(candidate["label"])
        definition = None
        definition_reason = None
        has_rejected_point = False
        try:
            definition = _claim(
                candidate["definition"], evidence_aliases, evidence_by_alias, label
            )
        except SemanticOutputError as error:
            if error.reason_code not in {
                "CLAIM_EVIDENCE_UNSUPPORTED",
                "CLAIM_FRAGMENT_UNUSABLE",
            }:
                raise
            definition_reason = error.reason_code
            has_rejected_point = True
        key_points = candidate["key_points"]
        if not isinstance(key_points, list) or not key_points:
            raise SemanticOutputError("INVALID_KEY_POINTS")
        normalized_points = []
        for point in key_points:
            try:
                normalized_points.append(
                    _claim(point, evidence_aliases, evidence_by_alias, label)
                )
            except SemanticOutputError as error:
                if error.reason_code not in {
                    "CLAIM_EVIDENCE_UNSUPPORTED",
                    "CLAIM_FRAGMENT_UNUSABLE",
                }:
                    raise
                has_rejected_point = True
        if definition is None:
            if len(normalized_points) < 2:
                raise SemanticOutputError(
                    definition_reason or "INVALID_KEY_POINTS"
                )
            definition = normalized_points.pop(0)
        if not normalized_points:
            raise SemanticOutputError("INVALID_KEY_POINTS")
        return {
            "label": label,
            "definition": definition,
            "key_points": normalized_points,
            "is_partial": has_rejected_point,
        }, None
    except SemanticOutputError as error:
        return None, error.reason_code


def _claim(
    value: Any,
    evidence_aliases: dict[str, str],
    evidence_by_alias: dict[str, dict[str, Any]],
    label: str,
) -> dict[str, Any]:
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
    evidence_text = "\n".join(
        evidence_by_alias[reference]["text"] for reference in references
    )
    if not _claim_is_grounded(text, evidence_text):
        raise SemanticOutputError("CLAIM_EVIDENCE_UNSUPPORTED")
    if _claim_is_fragment(text, label):
        raise SemanticOutputError("CLAIM_FRAGMENT_UNUSABLE")
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
        valid, reason = _candidate_reason(
            candidate, evidence_aliases, evidence_by_id
        )
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
        is_partial = valid["is_partial"]
        grounded = {"label": valid["label"], "definition": definition, "key_points": key_points}
        concepts.append(
            {
                "concept_id": concept_id(page_ref, **grounded),
                "page_ref": page_ref,
                **grounded,
                "processing": "partial" if is_partial else "succeeded",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": (
                    ["CONTENT_REVIEW_REQUIRED", "SEMANTIC_REVIEW_REQUIRED"]
                    if is_partial
                    else ["SEMANTIC_REVIEW_REQUIRED"]
                ),
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
        "processing": (
            "partial"
            if rejected or any(concept["processing"] == "partial" for concept in concepts)
            else "succeeded"
        ),
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
        "processing": (
            "partial"
            if rejected or any(concept["processing"] == "partial" for concept in concepts)
            else "succeeded"
        ),
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
