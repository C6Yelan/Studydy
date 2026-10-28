"""準備語意請求，並把模型回應投影到已知 Evidence。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass, field
import math
import re
from typing import Any

from content_identity import canonical_sha256
from .source_context import teaching_text
from .claim_literals import (
    LITERAL_PROJECTION, EXCERPT_PROJECTION, EXCERPT_REPAIR, LITERAL_REPAIRS,
    preserves_literals, quoted_sources,
)

from .structure_rules import (
    RELATION_BASIS,
    RELATION_TYPES,
    _GENERIC_REASONS,
    _KEY,
    _text,
)


def build_semantic_bundles(
    context: dict[str, Any],
    *,
    state: SemanticState,
    fits: Callable[[dict[str, Any]], bool],
    minimum_page: int = 1,
    minimum_evidence_index: int = 0,
) -> Iterator[dict[str, Any]]:
    """封裝連續 Evidence；候選目錄可縮小，來源區塊與已保存概念不截斷。"""

    excluded = set(context.get("non_content_evidence_ids", []))
    evidence = [
        item
        for index, item in enumerate(context["evidence"])
        if index >= minimum_evidence_index
        and item["evidence_id"] not in excluded
        and item["page"] >= minimum_page
    ]

    def bundle(start: int, end: int) -> dict[str, Any]:
        items = evidence[start:end]
        ids = {item["evidence_id"] for item in items}
        sections = [
            {
                **section,
                "evidence_ids": [ref for ref in section["evidence_ids"] if ref in ids],
            }
            for section in context["sections"]
            if any(ref in ids for ref in section["evidence_ids"])
        ]
        return {"sections": sections, "evidence": items}

    def fits_bundle(candidate: dict[str, Any]) -> bool:
        candidate["catalog_keys"] = _relevant_catalog_keys(context, candidate, state)
        request = semantic_request(context, candidate, state)
        if fits(request):
            return True
        # 先確保本批來源本身放得下；不能因歷史目錄成長而把新來源越切越碎。
        if not fits({**request, "existing_concepts": []}):
            return False
        candidate["include_claim_details"] = False
        while True:
            if fits(semantic_request(context, candidate, state)):
                return True
            # 名稱／別名命中的候選優先，其次才是同章節。只縮請求，不刪 state。
            keys = candidate["catalog_keys"]
            if not keys:
                return False
            candidate["catalog_keys"] = keys[: len(keys) // 2]

    start = 0
    while start < len(evidence):
        low = start
        high = min(start + 8, len(evidence))
        candidate = bundle(start, high)
        while fits_bundle(candidate):
            low = high
            if high == len(evidence):
                yield candidate
                return
            high = min(start + (high - start) * 2, len(evidence))
            candidate = bundle(start, high)
        while low + 1 < high:
            middle = (low + high) // 2
            if fits_bundle(bundle(start, middle)):
                low = middle
            else:
                high = middle
        if low == start:
            raise ValueError("SEMANTIC_INPUT_TOO_LARGE")
        boundaries = [
            end
            for end in range(start + 1, low + 1)
            if (
                end == len(evidence)
                or evidence[end - 1]["section_id"] != evidence[end]["section_id"]
            )
        ]
        end = boundaries[-1] if boundaries else low
        candidate = bundle(start, end)
        # tokenizer 不保證任意字串前綴的 token 數嚴格單調；最終 bundle 再核對。
        if not fits_bundle(candidate):
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
            "s",
            "t",
            "k",
            "r",
            "e",
            "c",
        ],
        "properties": {
            "s": {"type": "string"},
            "t": {"type": "string"},
            "k": {"type": "string", "enum": sorted(RELATION_TYPES)},
            "r": {"type": "string", "minLength": 1},
            "e": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "integer", "minimum": 0},
            },
            "c": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "concepts",
            "relations",
            *(["review_required"] if incremental else []),
        ],
        "properties": {
            **(
                {
                    "review_required": {
                        "type": "boolean",
                        "description": (
                            "True if source scope or concept grouping needs review. "
                            "This is an advisory flag; keep grounded additions and "
                            "do not overwrite saved Claims."
                        ),
                    },
                }
                if incremental
                else {}
            ),
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

    def catalog(
        self, handles: dict[str, int], keys: list[str], *, details: bool = True
    ) -> list[dict[str, Any]]:
        return [
            {
                "k": key,
                "l": concept["label"],
                "a": concept["aliases"],
                **(
                    {
                        "c": [claim["text"] for claim in concept["claims"]],
                        "e": list(
                            dict.fromkeys(
                                handles[span["evidence_id"]]
                                for claim in concept["claims"]
                                for span in claim["source_spans"]
                            )
                        ),
                    }
                    if details
                    else {}
                ),
            }
            for key in keys
            for concept in [self.concepts[key]]
        ]


def _relevant_catalog_keys(
    context: dict[str, Any],
    bundle: dict[str, Any],
    state: SemanticState,
) -> list[str]:
    if not state.concepts:
        return []
    # 名稱／別名供跨來源辨識；同章節既有內容也保留。這只選擇請求視圖，不刪 canonical state。
    text = "\n".join(
        [section["title"] for section in bundle["sections"]]
        + [item["exact_text"] for item in bundle["evidence"]]
    ).casefold()
    sections = {section["section_id"] for section in bundle["sections"]}
    evidence_sections = {
        item["evidence_id"]: item["section_id"] for item in context["evidence"]
    }
    selected = []
    for key, concept in state.concepts.items():
        same_section = any(
            evidence_sections.get(span["evidence_id"]) in sections
            for claim in concept["claims"]
            for span in claim["source_spans"]
        )
        names = [concept["label"], *concept["aliases"], key.replace("_", " ")]
        mentioned = any(
            re.search(
                r"(?<![a-z0-9])" + re.escape(name.strip().casefold()) + r"(?![a-z0-9])",
                text,
            )
            for name in names
            if len(name.strip()) >= 2
        )
        if same_section or mentioned:
            selected.append((int(mentioned), int(same_section), key))
    return [
        key
        for _, _, key in sorted(
            selected, key=lambda item: (-item[0], -item[1], item[2])
        )
    ]


def semantic_request(
    context: dict[str, Any], bundle: dict[str, Any], state: SemanticState
) -> dict[str, Any]:
    handles = {
        item["evidence_id"]: index for index, item in enumerate(context["evidence"])
    }
    evidence = {item["evidence_id"]: item for item in bundle["evidence"]}
    keys = bundle.get("catalog_keys")
    if keys is None:
        keys = _relevant_catalog_keys(context, bundle, state)
    catalog = state.catalog(
        handles, keys, details=bundle.get("include_claim_details", True)
    )
    source_pages = context.get("source_pages")
    if source_pages:
        all_evidence = {item["evidence_id"]: item for item in context["evidence"]}
        for entry in catalog:
            concept = state.concepts[entry["k"]]
            if "c" not in entry:
                continue
            entry["claim_sources"] = [
                list(
                    dict.fromkeys(
                        source_pages[all_evidence[span["evidence_id"]]["page"] - 1][
                            "source_id"
                        ]
                        for span in claim["source_spans"]
                    )
                )
                for claim in concept["claims"]
            ]
    return {
        **(
            {
                "spoken_sources": {
                    source_id: metadata
                    for source_id, metadata in context["spoken_sources"].items()
                    if source_id in {
                        source_pages[item["page"] - 1]["source_id"]
                        for item in bundle["evidence"]
                    }
                },
            }
            if context.get("spoken_sources")
            else {}
        ),
        "existing_concepts": catalog,
        "catalog_policy": (
            "Only a bounded selection of relevant saved concepts is listed. Omitted concepts and "
            "claim details remain saved, not empty or deleted. Reuse a listed key only for an equivalent "
            "concept; otherwise create a new key. Do not infer missing facts or relations from names alone."
        ),
        **(
            {
                "update_policy": (
                    "Reuse existing keys for equivalent concepts. Add only grounded new Claims. "
                    "Set review_required=true if new sources contradict or revise an existing Claim, "
                    "or require splitting/merging saved concepts. Do not silently overwrite old facts."
                ),
            }
            if context.get("incremental")
            else {}
        ),
        **(
            {
                "source_pages": [
                    # checkpoint JSON 會重排物件欄位；固定提示順序，讓重試的容量估算與批次邊界一致。
                    {
                        key: source_pages[page - 1][key]
                        for key in ("page", "source_id", "normalized_page")
                    }
                    for page in sorted({item["page"] for item in bundle["evidence"]})
                ],
                "source_policy": (
                    "Each source has an independent scope. claim_sources aligns with saved Claims. "
                    "Source order alone does not establish prerequisites."
                    + (
                        " When learning_scopes is supplied, extract teaching content for that source's confirmed goals and topics. "
                        "Adjacent text is context, not permission to add excluded or unrelated topics. Keep necessary definitions and qualifying conditions, "
                        "but do not expand an incidental example, other API option or excluded subject into a new required concept."
                        if context.get("learning_scopes")
                        else ""
                    )
                ),
                **(
                    {
                        "learning_scopes": {
                            identity: scope
                            for identity, scope in context["learning_scopes"].items()
                            if identity
                            in {
                                source_pages[item["page"] - 1]["source_id"]
                                for item in bundle["evidence"]
                            }
                        }
                    }
                    if context.get("learning_scopes")
                    else {}
                ),
            }
            if source_pages
            else {}
        ),
        "sections": [
            {
                "title": teaching_text(section["title"]) or "教材開頭",
                "evidence": [
                    [
                        handles[ref],
                        evidence[ref]["page"],
                        evidence[ref]["kind"],
                        teaching_text(evidence[ref]["exact_text"]),
                    ]
                    for ref in section["evidence_ids"]
                ],
            }
            for section in bundle["sections"]
        ],
    }


def spoken_handles(context):
    pages = context.get("source_pages", [])
    sources = context.get("spoken_sources", {})
    return {
        index for index, item in enumerate(context["evidence"])
        if pages and pages[item["page"] - 1]["source_id"] in sources
    }


def _expand_claim(
    claim: Any, sources: list[dict[str, Any]], *, excerpt_handles=frozenset()
) -> dict[str, Any] | None:
    """一般來源選完整 Evidence；口語來源選逐字節錄，位置由程式核對。"""

    if (
        not isinstance(claim, dict)
        or set(claim) != {"m", "s"}
        or not isinstance(claim["s"], list)
    ):
        return None
    spans = []
    for reference in claim["s"]:
        excerpt = isinstance(reference, dict)
        if excerpt and set(reference) != {"e", "q"}:
            return None
        if excerpt and not isinstance(reference["q"], str):
            return None
        handle = reference["e"] if excerpt else reference
        if type(handle) is not int or not 0 <= handle < len(sources):
            return None
        if excerpt != (handle in excerpt_handles):
            return None
        source = sources[handle]
        spans.append(
            {"evidence_id": source["evidence_id"],
             "quote": reference["q"] if excerpt else source["exact_text"]}
        )
    return {
        "meaning": " ".join(span["quote"] for span in spans)
        if claim["m"] is None
        else claim["m"],
        "source_spans": spans,
    }


def _unsupported_null_meaning(text: str, source_text: str) -> bool:
    """來源可以教 null，但未獲來源支持的空值字串不是學習內容。"""
    return (
        text.casefold() == "null"
        and re.search(
            r"(?<![A-Za-z0-9_])null(?![A-Za-z0-9_])", source_text, re.IGNORECASE
        )
        is None
    )


def _project_claim(
    claim: Any, evidence: dict[str, dict[str, Any]], *, excerpts=False, excerpt_evidence_ids=None
) -> dict[str, Any] | None:
    if not isinstance(claim, dict) or set(claim) != {"meaning", "source_spans"}:
        return None
    try:
        meaning = teaching_text(_text(claim["meaning"]))
        if not meaning:
            return None
    except ValueError:
        return None
    spans = claim["source_spans"]
    if not isinstance(spans, list) or not spans:
        return None
    sources = quoted_sources(spans, evidence, excerpts=excerpts, excerpt_evidence_ids=excerpt_evidence_ids)
    if sources is None:
        return None
    projected = []
    for span in spans:
        if span not in projected:
            projected.append(span)
    source_text = " ".join(span["quote"] for span in projected)
    needs_literal_repair = not preserves_literals(
        meaning,
        sources,
    )
    # 清理後文字不是逐字原文，不能標成原文字面值還原。
    if needs_literal_repair and teaching_text(source_text) != source_text:
        return None
    text = source_text if needs_literal_repair else meaning
    # 不把模型誤填的 JSON 空值字串當教材；來源真的討論 null 時仍可保留。
    if _unsupported_null_meaning(text, source_text):
        return None
    return {
        "text": text,
        "source_spans": projected,
        "projection": (
            EXCERPT_REPAIR if needs_literal_repair else EXCERPT_PROJECTION
        ) if excerpts else (
            "source_literal_repair" if needs_literal_repair else LITERAL_PROJECTION
        ),
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
    if not isinstance(response["concepts"], list) or not isinstance(
        response["relations"], list
    ):
        raise ValueError("SEMANTIC_OUTPUT_INVALID")
    non_content_ids = set(context.get("non_content_evidence_ids", []))
    excerpt_handles = spoken_handles(context)
    excerpt_evidence_ids = {context["evidence"][h]["evidence_id"] for h in excerpt_handles}
    evidence = {
        item["evidence_id"]: item
        for item in bundle["evidence"]
        if item["evidence_id"] not in non_content_ids
    }
    # 在套用新 Claims／aliases 前，重用 request builder 的 catalog 篩選與容量降級契約。
    # 關係可引用本批專屬證據或已傳送的 catalog 證據，不必先成為端點的 Claim。
    request = (
        semantic_request(context, bundle, state) if response["relations"] else None
    )
    selected_keys = bundle.get("catalog_keys")
    visible_keys = set(
        selected_keys
        if selected_keys is not None
        else _relevant_catalog_keys(context, bundle, state)
    )
    allowed_handles = (
        (
            {row[0] for section in request["sections"] for row in section["evidence"]}
            | {
                ref
                for concept in request["existing_concepts"]
                for ref in concept.get("e", [])
            }
        )
        if request is not None
        else set()
    )
    allowed_evidence = {
        item["evidence_id"]
        for index, item in enumerate(context["evidence"])
        if index in allowed_handles and item["evidence_id"] not in non_content_ids
    }
    all_sections = {section["section_id"] for section in context["sections"]}
    response_keys: set[str] = set()
    renamed_keys: dict[str, str] = {}
    for proposal in response["concepts"]:
        if not isinstance(proposal, dict) or set(proposal) != {"k", "l", "a", "c"}:
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        key = proposal["k"]
        if (
            not isinstance(key, str)
            or _KEY.fullmatch(key) is None
            or key in response_keys
        ):
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        response_keys.add(key)
        # 未提供的舊概念不能僅因模型選了同名 key 就被隱性合併。
        if key in state.concepts and key not in visible_keys:
            base = key[:40] + "." + canonical_sha256(proposal)[:16]
            replacement = base
            suffix = 0
            while (
                replacement in state.concepts
                or replacement in response_keys
                or any(
                    item.get("k") == replacement
                    for item in response["concepts"]
                    if isinstance(item, dict)
                )
            ):
                suffix += 1
                replacement = base + "." + str(suffix)
            renamed_keys[key] = replacement
            key = replacement
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
        claim_identities = {canonical_sha256(claim) for claim in current["claims"]}
        for proposed_claim in proposal["c"]:
            expanded = _expand_claim(
                proposed_claim, context["evidence"], excerpt_handles=excerpt_handles
            )
            claim = _project_claim(
                expanded,
                evidence,
                excerpts=expanded is not None and any(
                    isinstance(ref, dict) for ref in proposed_claim["s"]
                ),
                excerpt_evidence_ids=excerpt_evidence_ids,
            )
            if claim is None:
                state.rejected_claims += 1
                continue
            if claim["projection"] in LITERAL_REPAIRS:
                state.literal_repairs += 1
            identity = canonical_sha256(claim)
            if identity not in claim_identities:
                current["claims"].append(claim)
                claim_identities.add(identity)
    known_keys = visible_keys | {renamed_keys.get(key, key) for key in response_keys}
    for relation in response["relations"]:
        if not isinstance(relation, dict) or set(relation) != {
            "s",
            "t",
            "k",
            "r",
            "e",
            "c",
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
            "source_concept": renamed_keys.get(relation["s"], relation["s"]),
            "target_concept": renamed_keys.get(relation["t"], relation["t"]),
            "type": relation["k"],
            "learner_reason": relation["r"],
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
        if (
            relation["source_concept"] not in known_keys
            or relation["target_concept"] not in known_keys
            or relation["source_concept"] == relation["target_concept"]
        ):
            state.rejected_relations += 1
            continue
        if relation_type not in RELATION_TYPES or reason.casefold() in _GENERIC_REASONS:
            state.rejected_relations += 1
            continue
        if (
            not evidence_refs
            or len(evidence_refs) != len(set(evidence_refs))
            or not set(evidence_refs) <= allowed_evidence
        ):
            state.rejected_relations += 1
            continue
        if any(reference not in all_sections for reference in context_refs):
            state.rejected_relations += 1
            continue
        if (
            type(confidence) not in {int, float}
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            state.rejected_relations += 1
            continue
        state.relations.append(deepcopy(relation))
