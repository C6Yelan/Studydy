"""準備語意請求，並把模型回應投影到已知 Evidence。"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass, field
import math
import re
from typing import Any

from pdf_evidence.ocr_page_evidence import canonical_sha256

from .structure_rules import (
    RELATION_BASIS, RELATION_TYPES, _CODE_OR_FORMULA, _GENERIC_REASONS,
    _KEY, _TECHNICAL, _text,
)


def build_semantic_bundles(
    context: dict[str, Any], *, state: SemanticState,
    fits: Callable[[dict[str, Any]], bool],
    minimum_page: int = 1,
    minimum_evidence_index: int = 0,
) -> Iterator[dict[str, Any]]:
    """依語意服務的批量判斷封裝連續 Evidence；每批納入最新 concept catalog。"""

    excluded = set(context.get("non_content_evidence_ids", []))
    evidence = [
        item for index, item in enumerate(context["evidence"])
        if index >= minimum_evidence_index
        and item["evidence_id"] not in excluded
        and item["page"] >= minimum_page
    ]

    def bundle(start: int, end: int) -> dict[str, Any]:
        items = evidence[start:end]
        ids = {item["evidence_id"] for item in items}
        sections = [
            {**section, "evidence_ids": [ref for ref in section["evidence_ids"] if ref in ids]}
            for section in context["sections"]
            if any(ref in ids for ref in section["evidence_ids"])
        ]
        return {"sections": sections, "evidence": items}

    start = 0
    while start < len(evidence):
        candidate = bundle(start, len(evidence))
        if fits(semantic_request(context, candidate, state)):
            yield candidate
            return
        low, high = start, len(evidence)
        while low + 1 < high:
            middle = (low + high) // 2
            if fits(semantic_request(context, bundle(start, middle), state)):
                low = middle
            else:
                high = middle
        if low == start:
            raise ValueError("SEMANTIC_INPUT_TOO_LARGE")
        boundaries = [
            end for end in range(start + 1, low + 1)
            if (
                end == len(evidence)
                or evidence[end - 1]["section_id"] != evidence[end]["section_id"]
            )
        ]
        end = boundaries[-1] if boundaries else low
        candidate = bundle(start, end)
        # tokenizer 不保證任意字串前綴的 token 數嚴格單調；最終 bundle 再核對。
        if not fits(semantic_request(context, candidate, state)):
            raise ValueError("SEMANTIC_INPUT_TOO_LARGE")
        yield candidate
        start = end


def semantic_response_schema(
    evidence_handles: list[int], *, incremental: bool = False
) -> dict[str, Any]:
    span = {"type": "integer", "enum": evidence_handles}
    claim = {
        "type": "object",
        "additionalProperties": False,
        "required": ["m", "s"],
        "properties": {
            "m": {"type": ["string", "null"], "minLength": 1},
            "s": {"type": "array", "minItems": 1, "items": span},
        },
    }
    concept = {
        "type": "object",
        "additionalProperties": False,
        "required": ["k", "l", "a", "c"],
        "properties": {
            "k": {"type": "string", "minLength": 1},
            "l": {"type": "string", "minLength": 1},
            "a": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "c": {"type": "array", "items": claim},
        },
    }
    relation = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "s", "t", "k", "r", "e", "c",
        ],
        "properties": {
            "s": {"type": "string"},
            "t": {"type": "string"},
            "k": {"type": "string", "enum": sorted(RELATION_TYPES)},
            "r": {"type": "string", "minLength": 1},
            "e": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 0}},
            "c": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["concepts", "relations", *(["review_required"] if incremental else [])],
        "properties": {
            **({
                "review_required": {
                    "type": "boolean",
                    "description": (
                        "True if source scope or concept grouping needs review. "
                        "This is an advisory flag; keep grounded additions and "
                        "do not overwrite saved Claims."
                    ),
                },
            } if incremental else {}),
            "concepts": {"type": "array", "items": concept},
            "relations": {"type": "array", "items": relation},
        },
    }


@dataclass
class SemanticState:
    concepts: dict[str, dict[str, Any]] = field(default_factory=dict)
    relations: list[dict[str, Any]] = field(default_factory=list)
    rejected_claims: int = 0
    rejected_relations: int = 0
    literal_repairs: int = 0
    source_review_required: bool = False

    def catalog(self, handles: dict[str, int]) -> list[dict[str, Any]]:
        return [
            {
                "k": key,
                "l": concept["label"],
                "a": concept["aliases"],
                "c": [claim["text"] for claim in concept["claims"]],
                "e": list(
                    dict.fromkeys(
                        handles[span["evidence_id"]]
                        for claim in concept["claims"]
                        for span in claim["source_spans"]
                    )
                ),
            }
            for key, concept in self.concepts.items()
        ]


def semantic_request(
    context: dict[str, Any], bundle: dict[str, Any], state: SemanticState
) -> dict[str, Any]:
    handles = {item["evidence_id"]: index for index, item in enumerate(context["evidence"])}
    evidence = {item["evidence_id"]: item for item in bundle["evidence"]}
    catalog = state.catalog(handles)
    source_pages = context.get("source_pages")
    if source_pages:
        all_evidence = {item["evidence_id"]: item for item in context["evidence"]}
        for entry, concept in zip(catalog, state.concepts.values()):
            entry["claim_sources"] = [list(dict.fromkeys(
                source_pages[all_evidence[span["evidence_id"]]["page"] - 1]["source_id"]
                for span in claim["source_spans"])) for claim in concept["claims"]]
    return {
        "existing_concepts": catalog,
        **({
            "update_policy": (
                "Reuse existing keys for equivalent concepts. Add only grounded new Claims. "
                "Set review_required=true if new sources contradict or revise an existing Claim, "
                "or require splitting/merging saved concepts. Do not silently overwrite old facts."
            ),
        } if context.get("incremental") else {}),
        **({
            "source_pages": [
                source_pages[page - 1]
                for page in sorted({item['page'] for item in bundle['evidence']})
            ],
            "source_policy": (
                "Each source has an independent scope. claim_sources aligns with saved Claims. "
                "Source order alone does not establish prerequisites."
            ),
        } if source_pages else {}),
        "sections": [
            {
                "title": section["title"],
                "evidence": [
                    [
                        handles[ref], evidence[ref]["page"],
                        evidence[ref]["kind"], evidence[ref]["exact_text"],
                    ]
                    for ref in section["evidence_ids"]
                ],
            }
            for section in bundle["sections"]
        ],
    }


def _expand_claim(claim: Any, sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    """模型只選完整 Evidence ID；引用文字由程式展開，不接受手算字元範圍。"""

    if not isinstance(claim, dict) or set(claim) != {"m", "s"} or not isinstance(claim["s"], list):
        return None
    spans = []
    for handle in claim["s"]:
        if type(handle) is not int or not 0 <= handle < len(sources):
            return None
        source = sources[handle]
        spans.append({"evidence_id": source["evidence_id"], "quote": source["exact_text"]})
    return {
        "meaning": " ".join(span["quote"] for span in spans) if claim["m"] is None else claim["m"],
        "source_spans": spans,
    }


def _unsupported_null_meaning(text: str, source_text: str) -> bool:
    """來源可以教 null，但未獲來源支持的空值字串不是學習內容。"""
    return text.casefold() == "null" and re.search(
        r"(?<![A-Za-z0-9_])null(?![A-Za-z0-9_])", source_text, re.IGNORECASE
    ) is None


def _project_claim(claim: Any, evidence: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(claim, dict) or set(claim) != {"meaning", "source_spans"}:
        return None
    try:
        meaning = _text(claim["meaning"])
    except ValueError:
        return None
    spans = claim["source_spans"]
    if not isinstance(spans, list) or not spans:
        return None
    projected: list[dict[str, str]] = []
    for span in spans:
        if not isinstance(span, dict) or set(span) != {"evidence_id", "quote"}:
            return None
        source = evidence.get(span["evidence_id"])
        quote = span["quote"]
        if (
            not isinstance(quote, str) or not quote
            or source is None or quote != source["exact_text"]
        ):
            return None
        item = {"evidence_id": span["evidence_id"], "quote": quote}
        if item not in projected:
            projected.append(item)
    source_text = " ".join(span["quote"] for span in projected)
    meaning_literals = _TECHNICAL.findall(meaning)
    source_literals = _TECHNICAL.findall(source_text)
    needs_literal_repair = (
        any(literal not in source_text for literal in meaning_literals)
        or any(literal not in meaning for literal in source_literals)
    )
    if (
        (_CODE_OR_FORMULA.search(meaning) or _CODE_OR_FORMULA.search(source_text))
        and meaning not in source_text
    ):
        needs_literal_repair = True
    text = source_text if needs_literal_repair else meaning
    # 不把模型誤填的 JSON 空值字串當教材；來源真的討論 null 時仍可保留。
    if _unsupported_null_meaning(text, source_text):
        return None
    return {
        "text": text,
        "source_spans": projected,
        "projection": "source_literal_repair" if needs_literal_repair else "semantic_meaning",
    }


def apply_semantic_response(
    response: Any,
    *,
    context: dict[str, Any],
    bundle: dict[str, Any],
    state: SemanticState,
) -> None:
    """只投影有效的 Claim/Relation；一筆錯誤不刪除 sibling Claims。"""

    if not isinstance(response, dict) or set(response) != {"concepts", "relations"}:
        raise ValueError("SEMANTIC_OUTPUT_INVALID")
    if not isinstance(response["concepts"], list) or not isinstance(response["relations"], list):
        raise ValueError("SEMANTIC_OUTPUT_INVALID")
    non_content_ids = set(context.get("non_content_evidence_ids", []))
    evidence = {
        item["evidence_id"]: item
        for item in bundle["evidence"] if item["evidence_id"] not in non_content_ids
    }
    all_evidence = {item["evidence_id"] for item in context["evidence"]}
    all_sections = {section["section_id"] for section in context["sections"]}
    response_keys: set[str] = set()
    for proposal in response["concepts"]:
        if not isinstance(proposal, dict) or set(proposal) != {"k", "l", "a", "c"}:
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        key = proposal["k"]
        if not isinstance(key, str) or _KEY.fullmatch(key) is None or key in response_keys:
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        response_keys.add(key)
        label = _text(proposal["l"], maximum=256)
        aliases = proposal["a"]
        if not isinstance(aliases, list):
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        aliases = sorted({_text(alias, maximum=256) for alias in aliases} - {label})
        current = state.concepts.get(key)
        if current is None:
            current = {"label": label, "aliases": aliases, "claims": []}
            state.concepts[key] = current
        else:
            current["aliases"] = sorted(
                (set(current["aliases"]) | set(aliases) | {label}) - {current["label"]}
            )
        if not isinstance(proposal["c"], list):
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        for proposed_claim in proposal["c"]:
            claim = _project_claim(_expand_claim(proposed_claim, context["evidence"]), evidence)
            if claim is None:
                state.rejected_claims += 1
                continue
            if claim["projection"] == "source_literal_repair":
                state.literal_repairs += 1
            identity = canonical_sha256(claim)
            if all(canonical_sha256(existing) != identity for existing in current["claims"]):
                current["claims"].append(claim)
    known_keys = set(state.concepts)
    context_evidence = {
        item["evidence_id"]: item for item in context["evidence"]
    }
    for relation in response["relations"]:
        if not isinstance(relation, dict) or set(relation) != {
            "s", "t", "k", "r", "e", "c",
        }:
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        if (
            any(not isinstance(relation[key], str) for key in ("s", "t", "k"))
            or not isinstance(relation["e"], list)
            or any(
                type(ref) is not int or not 0 <= ref < len(context["evidence"])
                for ref in relation["e"]
            )
        ):
            state.rejected_relations += 1
            continue
        refs = [context["evidence"][ref] for ref in relation["e"]]
        relation = {
            "source_concept": relation["s"], "target_concept": relation["t"],
            "type": relation["k"], "learner_reason": relation["r"],
            "evidence_refs": [item["evidence_id"] for item in refs],
            "context_refs": list(dict.fromkeys(item["section_id"] for item in refs)),
            "inference_basis": RELATION_BASIS.get(relation["k"]),
            "confidence": relation["c"],
        }
        reason = _text(relation["learner_reason"], maximum=1024)
        relation_type = relation["type"]
        evidence_refs = relation["evidence_refs"]
        context_refs = relation["context_refs"]
        confidence = relation["confidence"]
        endpoint_evidence = {
            span["evidence_id"]
            for key in (relation["source_concept"], relation["target_concept"])
            for claim in state.concepts.get(key, {}).get("claims", [])
            for span in claim["source_spans"]
        }
        endpoint_sections = {
            context_evidence[reference]["section_id"]
            for reference in endpoint_evidence
        }
        if (
            relation["source_concept"] not in known_keys
            or relation["target_concept"] not in known_keys
            or relation["source_concept"] == relation["target_concept"]
        ):
            state.rejected_relations += 1
            continue
        if (
            relation_type not in RELATION_TYPES
            or relation["inference_basis"] != RELATION_BASIS[relation_type]
            or reason.casefold() in _GENERIC_REASONS
        ):
            state.rejected_relations += 1
            continue
        if (
            not isinstance(evidence_refs, list)
            or not evidence_refs
            or len(evidence_refs) != len(set(evidence_refs))
            or any(reference not in all_evidence for reference in evidence_refs)
            or not set(evidence_refs) <= endpoint_evidence
        ):
            state.rejected_relations += 1
            continue
        if (
            not isinstance(context_refs, list)
            or len(context_refs) != len(set(context_refs))
            or any(reference not in all_sections for reference in context_refs)
            or not set(context_refs) <= endpoint_sections
        ):
            state.rejected_relations += 1
            continue
        if (
            type(confidence) not in {int, float}
            or isinstance(confidence, bool)
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            state.rejected_relations += 1
            continue
        state.relations.append(deepcopy(relation))
