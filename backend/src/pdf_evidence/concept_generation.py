from __future__ import annotations

import json
import re
from typing import Any
import unicodedata

from .ocr_page_evidence import canonical_sha256
from .document_context import serialize_document_context


SEMANTIC_REQUEST_SCHEMA = "concept-generation-input/v7"
SEMANTIC_ARTIFACT_SCHEMA = "semantic-page-concepts/v4"
PROCESSING_POLICY = "claim-grounded-concept-review/v8"
MAX_MODEL_OUTPUT_BYTES = 65_536

_BRACKET_INDEXES = re.compile(r"^(?:\[\s*\d+\s*\]\s*)+$")
_ISOLATED_CONNECTOR = re.compile(
    r"^(?:and|or|but|because|therefore|however|then|以及|與|和|或|但|因為|"
    r"所以|因此|然後)[,，;；:]?$",
    re.IGNORECASE,
)
_TECHNICAL_TOKEN = re.compile(
    r"\\(?:[0abfnrtv'\"?\\]|x[0-9A-Fa-f]{1,8}|u[0-9A-Fa-f]{4,8}|[0-7]{1,3})"
    r"|===|!==|==|!=|<=|>=|->|=>|\+\+|--|&&|\|\|"
    r"|(?<![\\\w])(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?(?:%|[A-Za-z]+)?)(?!\w)"
)

_CLAIM_FRAGMENT_REASONS = {
    "CLAIM_ISOLATED_CONNECTOR",
    "CLAIM_SYNTAX_TAIL",
}
_EVIDENCE_KINDS = {
    "heading",
    "paragraph",
    "list",
    "code",
    "caption",
    "image_text",
    "other",
}
_ASSESSMENT_STEM_END = re.compile(
    r"[?？]\s*(?:[(（][^()（）\n]{1,32}[)）])?\s*$"
)
_PARENTHESIZED_OPTION = re.compile(
    r"^\s*[(（](?P<label>[A-Za-z])[)）]\s+\S"
)
_PLAIN_OPTION = re.compile(
    r"^\s*(?P<label>[A-Za-z])(?P<suffix>[.)：:、])\s+\S"
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


def _semantic_evidence_text(value: Any) -> str:
    """只在模型輸入副本以空白分隔無語意 C0 control。"""

    if not isinstance(value, str):
        raise SemanticOutputError("INVALID_TEXT_FIELD")
    separated = "".join(
        " " if ord(character) < 32 and character not in "\n\t" else character
        for character in value
    )
    normalized = unicodedata.normalize("NFC", separated)
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    return _exact_evidence_text(normalized)


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


def _claim_has_safe_technical_tokens(text: str, evidence_text: str) -> bool:
    """模型不得在 Claim 憑空加入精確技術符號或數值。"""

    claim_tokens = set(_TECHNICAL_TOKEN.findall(text))
    evidence_tokens = set(_TECHNICAL_TOKEN.findall(evidence_text))
    return claim_tokens <= evidence_tokens


def _claim_fragment_reason(text: str, label: str) -> str | None:
    """只拒絕客觀上不是內容的連接詞或純符號殘片。"""

    normalized = " ".join(unicodedata.normalize("NFKC", text).split())
    if _ISOLATED_CONNECTOR.fullmatch(normalized):
        return "CLAIM_ISOLATED_CONNECTOR"
    if _BRACKET_INDEXES.fullmatch(normalized):
        return "CLAIM_SYNTAX_TAIL"
    if (
        normalized.startswith((")", "]", "}", ",", "，", ";", "；"))
        or all(
            character in ")]},，;；:+-*/=" or character.isspace()
            for character in normalized
        )
    ):
        return "CLAIM_SYNTAX_TAIL"
    return None


def _option_marker(text: str) -> tuple[str, int] | None:
    """只辨識有一致前綴且可驗證順序的短選項標記。"""

    parenthesized = _PARENTHESIZED_OPTION.match(text)
    plain = _PLAIN_OPTION.match(text)
    match = parenthesized or plain
    if match is None:
        return None
    label = match.group("label")
    marker_value = ord(label.casefold()) - ord("a") + 1
    marker_style = (
        "parenthesized"
        if parenthesized is not None
        else f"suffix:{match.group('suffix')}"
    )
    return marker_style, marker_value


def _assessment_id(
    source_context_id: str,
    question_evidence_id: str,
    option_evidence_ids: list[str],
) -> str:
    identity = {
        "source_context_id": source_context_id,
        "question_evidence_id": question_evidence_id,
        "option_evidence_ids": option_evidence_ids,
    }
    return "assessment-context:sha256:" + canonical_sha256(identity)


def _find_unkeyed_assessments(
    blocks: list[dict[str, Any]], source_context_id: str
) -> list[dict[str, Any]]:
    """以相鄰 block 的問句與連續標記保留無答案鍵題組。"""

    groups = []
    block_index = 0
    while block_index < len(blocks):
        question = blocks[block_index]
        if (
            question.get("kind") != "paragraph"
            or _ASSESSMENT_STEM_END.search(question.get("text", "")) is None
        ):
            block_index += 1
            continue

        options = []
        option_markers = []
        for block in blocks[block_index + 1 :]:
            if block.get("kind") not in {"paragraph", "list"}:
                break
            marker = _option_marker(block.get("text", ""))
            if marker is None:
                break
            options.append(block)
            option_markers.append(marker)
        if (
            len(options) < 3
            or len({marker[0] for marker in option_markers}) != 1
            or any(
                marker[1] != option_markers[0][1] + index
                for index, marker in enumerate(option_markers)
            )
        ):
            block_index += 1
            continue

        question_evidence_id = question["id"]
        option_evidence_ids = [option["id"] for option in options]
        groups.append(
            {
                "assessment_id": _assessment_id(
                    source_context_id,
                    question_evidence_id,
                    option_evidence_ids,
                ),
                "question_evidence_id": question_evidence_id,
                "option_evidence_ids": option_evidence_ids,
                "has_reliable_answer": False,
            }
        )
        block_index += len(options) + 1
    return groups


def build_semantic_request(
    page_evidence: dict[str, Any],
    document_context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Evidence 使用短 alias；文脈保留可重算的 block grounding。"""
    if (
        document_context.get("page_ref") != page_evidence.get("page_ref")
        or document_context.get("page_evidence_id")
        != page_evidence.get("page_evidence_id")
        or {
            block.get("evidence_id")
            for block in document_context.get("current_blocks", [])
        }
        != {
            block.get("evidence_id")
            for block in page_evidence.get("evidence_blocks", [])
        }
    ):
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    evidence = []
    evidence_aliases = {}
    evidence_kinds = {}
    for index, block in enumerate(page_evidence["evidence_blocks"], start=1):
        alias = f"e{index}"
        evidence.append(
            {"id": alias, "text": _semantic_evidence_text(block["text"])}
        )
        evidence_aliases[alias] = block["evidence_id"]
        evidence_kinds[alias] = block["kind"]
    document_context_envelope = serialize_document_context(
        document_context, evidence_aliases, evidence_kinds
    )
    request = {
        "schema": SEMANTIC_REQUEST_SCHEMA,
        "evidence": evidence,
        "document_context": document_context_envelope,
        "assessment_groups": _find_unkeyed_assessments(
            [
                {
                    "id": item["id"],
                    "text": item["text"],
                    "kind": evidence_kinds[item["id"]],
                }
                for item in evidence
            ],
            document_context_envelope["source_context_id"],
        ),
    }
    validate_semantic_request(request)
    return request, evidence_aliases


def validate_semantic_request(request: Any) -> dict[str, dict[str, Any]]:
    expected = {
        "schema",
        "evidence",
        "document_context",
        "assessment_groups",
    }
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
    current_evidence_kinds = _validate_document_context_envelope(
        request["document_context"]
    )
    if set(current_evidence_kinds) != set(evidence_by_id):
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    for evidence_id, kind in current_evidence_kinds.items():
        evidence_by_id[evidence_id] = {
            **evidence_by_id[evidence_id],
            "kind": kind,
        }
    expected_assessments = _find_unkeyed_assessments(
        list(evidence_by_id.values()),
        request["document_context"]["source_context_id"],
    )
    if request["assessment_groups"] != expected_assessments:
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    assessment_member_ids = {
        evidence_id
        for group in expected_assessments
        for evidence_id in (
            group["question_evidence_id"],
            *group["option_evidence_ids"],
        )
    }
    for evidence_id in assessment_member_ids:
        evidence_by_id[evidence_id] = {
            **evidence_by_id[evidence_id],
            "is_unkeyed_assessment_member": True,
        }
    return evidence_by_id


def _validate_document_context_envelope(context: Any) -> dict[str, str]:
    fields = {
        "schema",
        "source_context_id",
        "document_context_id",
        "current_blocks",
        "context_blocks",
    }
    if (
        not isinstance(context, dict)
        or set(context) != fields
        or context["schema"] != "concept-context-envelope/v3"
        or not isinstance(context["source_context_id"], str)
        or re.fullmatch(
            r"document-context:sha256:[0-9a-f]{64}",
            context["source_context_id"],
        )
        is None
        or not isinstance(context["document_context_id"], str)
        or re.fullmatch(
            r"concept-context:sha256:[0-9a-f]{64}",
            context["document_context_id"],
        )
        is None
        or not isinstance(context["current_blocks"], list)
        or not context["current_blocks"]
        or not isinstance(context["context_blocks"], list)
    ):
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    current_fields = {
        "evidence_id",
        "kind",
        "heading_ancestry_ids",
        "previous_evidence_id",
        "next_evidence_id",
        "continuation_ids",
    }
    context_fields = {
        "id",
        "role",
        "text",
    }
    if any(
        not isinstance(block, dict)
        or set(block) != current_fields
        or re.fullmatch(r"e[1-9][0-9]*", str(block["evidence_id"])) is None
        or block["kind"] not in _EVIDENCE_KINDS
        or not isinstance(block["heading_ancestry_ids"], list)
        or not isinstance(block["continuation_ids"], list)
        for block in context["current_blocks"]
    ):
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    if any(
        not isinstance(block, dict)
        or set(block) != context_fields
        or re.fullmatch(r"c[1-9][0-9]*", str(block["id"])) is None
        or block["role"]
        not in {
            "heading_ancestry",
            "continuation",
            "previous_page",
            "next_page",
        }
        or not isinstance(block["text"], str)
        or not block["text"]
        for block in context["context_blocks"]
    ):
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    current_ids = {
        block["evidence_id"] for block in context["current_blocks"]
    }
    context_ids = {block["id"] for block in context["context_blocks"]}
    if (
        len(current_ids) != len(context["current_blocks"])
        or len(context_ids) != len(context["context_blocks"])
    ):
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    allowed_ids = current_ids | context_ids
    references = [
        reference
        for block in context["current_blocks"]
        for reference in [
            *block["heading_ancestry_ids"],
            block["previous_evidence_id"],
            block["next_evidence_id"],
            *block["continuation_ids"],
        ]
        if reference is not None
    ]
    if any(reference not in allowed_ids for reference in references):
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    context_tokens = sum(
        len(block["text"].encode("utf-8"))
        for block in context["context_blocks"]
    )
    if context_tokens > 1_024:
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    identity = {
        key: value
        for key, value in context.items()
        if key != "document_context_id"
    }
    if context["document_context_id"] != (
        "concept-context:sha256:" + canonical_sha256(identity)
    ):
        raise SemanticOutputError("INPUT_SCHEMA_INVALID")
    return {
        block["evidence_id"]: block["kind"]
        for block in context["current_blocks"]
    }


def split_semantic_request(
    request: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """依原頁面順序切半，題幹與無答案鍵選項不可拆散。"""

    checked_evidence = validate_semantic_request(request)
    evidence = [
        {"id": item["id"], "text": item["text"]}
        for item in checked_evidence.values()
    ]
    assessment_by_question = {
        group["question_evidence_id"]: group
        for group in request["assessment_groups"]
    }
    evidence_by_id = {item["id"]: item for item in evidence}
    units = []
    used_ids = set()
    for item in evidence:
        if item["id"] in used_ids:
            continue
        assessment = assessment_by_question.get(item["id"])
        if assessment is None:
            units.append([item])
            used_ids.add(item["id"])
            continue
        assessment_ids = [
            assessment["question_evidence_id"],
            *assessment["option_evidence_ids"],
        ]
        units.append(
            [evidence_by_id[evidence_id] for evidence_id in assessment_ids]
        )
        used_ids.update(assessment_ids)

    if len(units) > 1:
        middle = len(units) // 2
        groups = (
            [item for unit in units[:middle] for item in unit],
            [item for unit in units[middle:] for item in unit],
        )
    else:
        if request["assessment_groups"]:
            raise SemanticOutputError("MODEL_INPUT_TOO_LARGE")
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
        {
            "schema": SEMANTIC_REQUEST_SCHEMA,
            "evidence": group,
            "document_context": _context_for_evidence(
                request["document_context"],
                {item["id"] for item in group},
            ),
            "assessment_groups": [
                assessment
                for assessment in request["assessment_groups"]
                if {
                    assessment["question_evidence_id"],
                    *assessment["option_evidence_ids"],
                }
                <= {item["id"] for item in group}
            ],
        }
        for group in groups
    )
    for child in requests:
        validate_semantic_request(child)
    return requests


def _context_for_evidence(
    context: dict[str, Any], evidence_ids: set[str]
) -> dict[str, Any]:
    """切 request 時只保留該批 current Evidence 的關係。"""

    context_ids = {block["id"] for block in context["context_blocks"]}
    allowed_ids = evidence_ids | context_ids

    def kept(reference: str | None) -> str | None:
        return reference if reference in allowed_ids else None

    child = {
        **context,
        "current_blocks": [
            {
                **block,
                "heading_ancestry_ids": [
                    reference
                    for reference in block["heading_ancestry_ids"]
                    if reference in allowed_ids
                ],
                "previous_evidence_id": kept(block["previous_evidence_id"]),
                "next_evidence_id": kept(block["next_evidence_id"]),
                "continuation_ids": [
                    reference
                    for reference in block["continuation_ids"]
                    if reference in allowed_ids
                ],
            }
            for block in context["current_blocks"]
            if block["evidence_id"] in evidence_ids
        ],
    }
    identity = {
        key: value
        for key, value in child.items()
        if key != "document_context_id"
    }
    child["document_context_id"] = (
        "concept-context:sha256:" + canonical_sha256(identity)
    )
    return child


def fitted_semantic_request_matches_source(
    fitted_request: dict[str, Any],
    source_request: dict[str, Any],
) -> bool:
    """Fitted request 只能保留 source Evidence slice 與 optional context 子集。"""

    source_evidence = {
        evidence["id"]: evidence for evidence in source_request["evidence"]
    }
    if (
        not fitted_request["evidence"]
        or any(
            source_evidence.get(evidence["id"]) != evidence
            and not _single_evidence_slice_matches(
                evidence, fitted_request, source_request
            )
            for evidence in fitted_request["evidence"]
        )
    ):
        return False
    fitted_context = fitted_request["document_context"]
    source_context = source_request["document_context"]
    if fitted_context["source_context_id"] != source_context["source_context_id"]:
        return False
    fitted_evidence_ids = {
        evidence["id"] for evidence in fitted_request["evidence"]
    }
    expected_assessments = []
    for assessment in source_request["assessment_groups"]:
        assessment_ids = {
            assessment["question_evidence_id"],
            *assessment["option_evidence_ids"],
        }
        if assessment_ids & fitted_evidence_ids:
            if not assessment_ids <= fitted_evidence_ids:
                return False
            expected_assessments.append(assessment)
    if fitted_request["assessment_groups"] != expected_assessments:
        return False
    source_current = {
        block["evidence_id"]: block
        for block in source_context["current_blocks"]
    }
    if len(fitted_context["current_blocks"]) != len(fitted_request["evidence"]):
        return False
    source_optional = {
        block["id"]: block for block in source_context["context_blocks"]
    }
    if any(
        source_optional.get(block["id"]) != block
        for block in fitted_context["context_blocks"]
    ):
        return False
    for fitted in fitted_context["current_blocks"]:
        source = source_current.get(fitted["evidence_id"])
        if source is None:
            return False
        for field in ("evidence_id", "kind"):
            if fitted[field] != source[field]:
                return False
        for field in ("previous_evidence_id", "next_evidence_id"):
            if fitted[field] not in {None, source[field]}:
                return False
        if (
            not set(fitted["heading_ancestry_ids"])
            <= set(source["heading_ancestry_ids"])
            or not set(fitted["continuation_ids"])
            <= set(source["continuation_ids"])
        ):
            return False
    return True


def _single_evidence_slice_matches(
    evidence: dict[str, Any],
    fitted_request: dict[str, Any],
    source_request: dict[str, Any],
) -> bool:
    if len(fitted_request["evidence"]) != 1:
        return False
    source = next(
        (
            source_evidence
            for source_evidence in source_request["evidence"]
            if source_evidence["id"] == evidence["id"]
        ),
        None,
    )
    if source is None or len(source["text"]) < 2:
        return False
    candidate_text = evidence["text"]
    frontier = [source["text"]]
    for _ in range(32):
        children = []
        for text in frontier:
            if candidate_text == text:
                return True
            if len(text) < 2:
                continue
            middle = len(text) // 2
            for child in (text[:middle].strip(), text[middle:].strip()):
                if child and candidate_text in child:
                    children.append(child)
        if not children:
            return False
        frontier = children
    return candidate_text in frontier


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
        if not isinstance(candidate, dict) or set(candidate) != {"label", "claims"}:
            raise SemanticOutputError("CANDIDATE_SCHEMA_INVALID")
        label = _normalized_candidate_text(candidate["label"])
        rejected_claim_reasons = []
        source_claims = candidate["claims"]
        if not isinstance(source_claims, list) or not source_claims:
            raise SemanticOutputError("INVALID_CLAIMS")
        claims = []
        for source_claim in source_claims:
            try:
                claims.append(
                    _claim(source_claim, evidence_aliases, evidence_by_alias, label)
                )
            except SemanticOutputError as error:
                if error.reason_code not in {
                    "CLAIM_EVIDENCE_UNSUPPORTED",
                    "CLAIM_UNKEYED_ASSESSMENT_OPTION",
                    *_CLAIM_FRAGMENT_REASONS,
                }:
                    raise
                rejected_claim_reasons.append(error.reason_code)
        if not claims:
            raise SemanticOutputError(
                rejected_claim_reasons[0] if rejected_claim_reasons else "INVALID_CLAIMS"
            )
        return {
            "label": label,
            "claims": claims,
            "rejected_claim_reasons": sorted(set(rejected_claim_reasons)),
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
    if any(
        evidence_by_alias[reference].get("is_unkeyed_assessment_member") is True
        for reference in references
    ):
        raise SemanticOutputError("CLAIM_UNKEYED_ASSESSMENT_OPTION")
    evidence_text = "\n".join(
        evidence_by_alias[reference]["text"] for reference in references
    )
    fragment_reason = _claim_fragment_reason(text, label)
    if fragment_reason is not None:
        raise SemanticOutputError(fragment_reason)
    if not _claim_has_safe_technical_tokens(text, evidence_text):
        raise SemanticOutputError("CLAIM_EVIDENCE_UNSUPPORTED")
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
        claims = [
            {
                "claim_id": claim_id(page_ref, claim, index=index),
                **claim,
            }
            for index, claim in enumerate(valid["claims"])
        ]
        rejected_claim_reasons = valid["rejected_claim_reasons"]
        is_partial = bool(rejected_claim_reasons)
        grounded = {"label": valid["label"], "claims": claims}
        concepts.append(
            {
                "concept_id": concept_id(page_ref, **grounded),
                "page_ref": page_ref,
                **grounded,
                "processing": "partial" if is_partial else "succeeded",
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": sorted(
                    set(
                        rejected_claim_reasons
                        + ["SEMANTIC_REVIEW_REQUIRED"]
                    )
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


def failed_semantic_page(
    *,
    page_ref: str,
    input_binding: dict[str, Any],
    reason_code: str,
) -> dict[str, Any]:
    """保留來源頁面並明確記錄本頁 Concept 階段失敗。"""

    return {
        "schema": SEMANTIC_ARTIFACT_SCHEMA,
        "page_ref": page_ref,
        "concepts": [],
        "rejected_candidates": [],
        "input_binding": input_binding,
        "attempt": 1,
        "processing_policy": PROCESSING_POLICY,
        "processing": "failed",
        "quality": "needs_review",
        "decision": "reject",
        "reason_codes": [reason_code],
    }


def claim_id(
    page_ref: str,
    claim: dict[str, Any],
    *,
    index: int,
) -> str:
    """以頁面、順序與內容建立不可混頁的穩定 Claim ID。"""

    identity = {"page_ref": page_ref, "index": index}
    identity.update(claim)
    return f"claim:sha256:{canonical_sha256(identity)}"


def concept_id(
    page_ref: str,
    label: str,
    claims: list[dict[str, Any]],
) -> str:
    """以完整 claim-level 內容建立 Concept 穩定 ID。"""

    identity = {
        "page_ref": page_ref,
        "label": label,
        "claims": claims,
    }
    return f"concept:sha256:{canonical_sha256(identity)}"
