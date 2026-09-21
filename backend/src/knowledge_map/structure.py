from __future__ import annotations

from copy import deepcopy
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
import math
import re
from typing import Any
from uuid import UUID

from pdf_evidence.ocr_page_evidence import canonical_bytes, canonical_sha256


STRUCTURE_SCHEMA = "knowledge-structure/v2"
VIEW_SCHEMA = "knowledge-structure-view/v2"
RELATION_TYPES = {"prerequisite", "part_of", "application", "example", "contrast"}
RELATION_BASIS = {
    "prerequisite": "dependency",
    "part_of": "composition",
    "application": "usage",
    "example": "instantiation",
    "contrast": "comparison",
}
RELATION_PRIORITY = {
    "prerequisite": 0,
    "part_of": 1,
    "application": 2,
    "example": 3,
    "contrast": 4,
}
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_TECHNICAL = re.compile(
    r"\\(?:[0abfnrtv\\'\"?]|x[0-9A-Fa-f]+|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8})"
    r"|'[^'\n]{0,80}'|\"[^\"\n]{0,80}\"|(?:==|!=|<=|>=|->|::|&&|\|\||<<|>>)"
    r"|[+\-*/%<>&|!~]"
    r"|(?<!\w)[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s?(?:%|[A-Za-zµμ°][A-Za-z0-9µμ°/^.-]{0,15}))?"
)
_CODE_OR_FORMULA = re.compile(r"[;{}]|\[[^\]]*\]|\([^\n()]*\)|\^|(?<![<>=!])=(?!=)")
_GENERIC_REASONS = {"有關", "內容相似", "同一主題", "一起出現", "related", "similar topic"}


def _id(kind: str, value: Any) -> str:
    return f"{kind}:sha256:{canonical_sha256(value)}"


def _text(value: Any, *, maximum: int = 4096) -> str:
    if not isinstance(value, str):
        raise ValueError("SEMANTIC_OUTPUT_INVALID")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum or "\x00" in cleaned:
        raise ValueError("SEMANTIC_OUTPUT_INVALID")
    return cleaned


def _ordered_pages(pages: Any) -> list[dict[str, Any]]:
    if not isinstance(pages, list) or not pages or any(not isinstance(page, dict) for page in pages):
        raise ValueError("DOCUMENT_EVIDENCE_INVALID")
    ordered = sorted(pages, key=lambda page: page.get("page_number", 0))
    material_ids = {page.get("material_id") for page in ordered}
    page_numbers = [page.get("page_number") for page in ordered]
    if (
        len(material_ids) != 1
        or None in material_ids
        or any(type(number) is not int or number < 1 for number in page_numbers)
        or len(page_numbers) != len(set(page_numbers))
    ):
        raise ValueError("DOCUMENT_EVIDENCE_INVALID")
    material_id = next(iter(material_ids))
    if not isinstance(material_id, str) or re.fullmatch(r"material:sha256:[0-9a-f]{64}", material_id) is None:
        raise ValueError("DOCUMENT_EVIDENCE_INVALID")
    source_sha256 = material_id.removeprefix("material:sha256:")
    evidence_ids: set[str] = set()
    for page in ordered:
        blocks = page.get("evidence_blocks")
        if page.get("schema") != "page-evidence/v4" or not isinstance(blocks, list) or not blocks:
            raise ValueError("DOCUMENT_EVIDENCE_INVALID")
        if page.get("page_ref") != _id(
            "page",
            {"source_sha256": source_sha256, "page_number": page["page_number"]},
        ):
            raise ValueError("DOCUMENT_EVIDENCE_INVALID")
        orders = [block.get("reading_order") for block in blocks if isinstance(block, dict)]
        if len(orders) != len(blocks) or orders != sorted(set(orders)):
            raise ValueError("DOCUMENT_EVIDENCE_INVALID")
        for block in blocks:
            evidence_id = block.get("evidence_id")
            locator = block.get("locator")
            if (
                not isinstance(locator, dict)
                or locator.get("page") != page["page_number"]
                or not isinstance(locator.get("region"), list)
            ):
                raise ValueError("DOCUMENT_EVIDENCE_INVALID")
            block_id = _id(
                "block",
                {
                    "page_ref": page["page_ref"],
                    "reading_order": block["reading_order"],
                    "region": locator["region"],
                },
            )
            expected_evidence_id = _id(
                "evidence",
                {
                    "page_ref": page["page_ref"],
                    "block_id": block_id,
                    "kind": block.get("kind"),
                    "source": block.get("source"),
                    "text": block.get("text"),
                    "reading_order": block.get("reading_order"),
                    "region": locator["region"],
                },
            )
            if (
                block.get("block_id") != block_id
                or locator.get("block_id") != block_id
                or evidence_id != expected_evidence_id
                or evidence_id in evidence_ids
            ):
                raise ValueError("DOCUMENT_EVIDENCE_INVALID")
            evidence_ids.add(evidence_id)
    return ordered


def _non_content_evidence_ids(pages: list[dict[str, Any]]) -> set[str]:
    """只排除有邊界位置及頁面角色依據的文字；原始 Evidence 不刪除。"""

    excluded: set[str] = set()
    recurring: dict[tuple[Any, ...], list[tuple[int, str]]] = {}
    for page in pages:
        bounds = page.get("geometry", {}).get("unrotated_points")
        if not bounds:
            continue
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        for block in page["evidence_blocks"]:
            box = block["locator"]["region"]
            edge = "bottom" if box[1] >= bounds[1] + height * 0.9 else "top" if box[3] <= bounds[1] + height * 0.08 else None
            text = " ".join(block["text"].split())
            if edge is None or len(text) > 200:
                continue
            # 程式中的版權字串仍是教材，不因靠近頁尾就移除。
            if re.search(r"[;{}]|(?<![<>=!])=(?!=)", text):
                continue
            if re.search(r"©\s*\d{4}|\bcopyright\s*(?:©|\(c\))?\s*\d{4}|all rights reserved|版權所有", text, re.IGNORECASE):
                excluded.add(block["evidence_id"])
                continue
            if block["kind"] == "heading" or _CODE_OR_FORMULA.search(text):
                continue
            # 頁碼須跨頁同位置且保持頁序差；固定數值不是頁碼。
            number = re.fullmatch(r"(?:(?:page|p\.)\s*)?(?:(\d+)\.)?(\d{1,4})", text, re.IGNORECASE)
            identity = ("page", number[1], int(number[2]) - page["page_number"]) if number else ("running", text)
            key = (edge, round((box[0] - bounds[0]) / width, 1), *identity)
            recurring.setdefault(key, []).append((page["page_number"], block["evidence_id"]))
    for occurrences in recurring.values():
        if len({page for page, _ in occurrences}) >= 2:
            excluded.update(reference for _, reference in occurrences)
    return excluded


def build_document_context(
    pages: list[dict[str, Any]],
    *,
    page_count: int,
    excluded_pages: list[dict[str, Any]] | None = None,
    source_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """建立一次性的 material context；不產生逐頁 semantic envelope。"""

    ordered = _ordered_pages(pages)
    if type(page_count) is not int or page_count < len(ordered):
        raise ValueError("DOCUMENT_EVIDENCE_INVALID")
    material_id = ordered[0]["material_id"]
    non_content_ids = _non_content_evidence_ids(ordered)
    sections: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_included_page: int | None = None
    for page in ordered:
        if source_pages and last_included_page is not None and source_pages[page["page_number"] - 1]["source_id"] != source_pages[last_included_page - 1]["source_id"]:
            current = None
        if last_included_page is not None and page["page_number"] != last_included_page + 1:
            current = None
        for block in page["evidence_blocks"]:
            if current is None or block["kind"] == "heading":
                title = _text(block["text"], maximum=512) if block["kind"] == "heading" else "教材開頭"
                section_id = _id(
                    "section",
                    {
                        "material_id": material_id,
                        "anchor_evidence_id": block["evidence_id"],
                    },
                )
                current = {
                    "section_id": section_id,
                    "title": title,
                    "order": len(sections),
                    "heading_evidence_id": block["evidence_id"] if block["kind"] == "heading" else None,
                    "evidence_ids": [],
                }
                sections.append(current)
            locator = block.get("locator")
            if (
                not isinstance(locator, dict)
                or locator.get("page") != page["page_number"]
                or locator.get("block_id") != block.get("block_id")
                or not isinstance(locator.get("region"), list)
            ):
                raise ValueError("DOCUMENT_EVIDENCE_INVALID")
            item = {
                "evidence_id": block["evidence_id"],
                "page_ref": page["page_ref"],
                "page": page["page_number"],
                "block_order": block["reading_order"],
                "kind": block["kind"],
                "source": block["source"],
                "exact_text": block["text"],
                "heading": current["title"],
                "section_id": current["section_id"],
                "source_locator": {
                    "page": locator["page"],
                    "block_id": locator["block_id"],
                    "region": deepcopy(locator["region"]),
                },
            }
            evidence.append(item)
            current["evidence_ids"].append(item["evidence_id"])
        last_included_page = page["page_number"]
    if not evidence:
        raise ValueError("NO_USABLE_EVIDENCE")
    for index, section in enumerate(sections):
        section["previous_section_title"] = sections[index - 1]["title"] if index else None
        section["next_section_title"] = sections[index + 1]["title"] if index + 1 < len(sections) else None
    excluded = deepcopy(excluded_pages or [])
    return {
        "schema": "document-context/v1",
        **({"source_pages": deepcopy(source_pages)} if source_pages else {}),
        "material_id": material_id,
        "page_count": page_count,
        "sections": sections,
        "evidence": evidence,
        "excluded_pages": excluded,
        "non_content_evidence_ids": sorted(non_content_ids),
    }


def build_semantic_bundles(
    context: dict[str, Any], *, state: SemanticState,
    fits: Callable[[dict[str, Any]], bool],
    minimum_page: int = 1,
    minimum_evidence_index: int = 0,
) -> Iterator[dict[str, Any]]:
    """依語意服務的批量判斷封裝連續 Evidence；每批納入最新 concept catalog。"""

    excluded = set(context.get("non_content_evidence_ids", []))
    evidence = [item for index, item in enumerate(context["evidence"]) if index >= minimum_evidence_index
                and item["evidence_id"] not in excluded and item["page"] >= minimum_page]

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
            if end == len(evidence) or evidence[end - 1]["section_id"] != evidence[end]["section_id"]
        ]
        end = boundaries[-1] if boundaries else low
        candidate = bundle(start, end)
        # tokenizer 不保證任意字串前綴的 token 數嚴格單調；最終 bundle 再核對。
        if not fits(semantic_request(context, candidate, state)):
            raise ValueError("SEMANTIC_INPUT_TOO_LARGE")
        yield candidate
        start = end


def semantic_response_schema(evidence_handles: list[int], *, incremental: bool = False) -> dict[str, Any]:
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
            **({"review_required": {"type": "boolean", "description": "True if source scope or concept grouping needs review. This is an advisory flag; keep grounded additions and do not overwrite saved Claims."}} if incremental else {}),
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
        **({"update_policy": "Reuse existing keys for equivalent concepts. Add only grounded new Claims. Set review_required=true if new sources contradict or revise an existing Claim, or require splitting/merging saved concepts. Do not silently overwrite old facts."} if context.get("incremental") else {}),
        **({"source_pages": [source_pages[page - 1] for page in sorted({item['page'] for item in bundle['evidence']})], "source_policy": "Each source has an independent scope. claim_sources aligns with saved Claims. Source order alone does not establish prerequisites."} if source_pages else {}),
        "sections": [
            {
                "title": section["title"],
                "evidence": [
                    [handles[ref], evidence[ref]["page"], evidence[ref]["kind"], evidence[ref]["exact_text"]]
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
        if not isinstance(quote, str) or not quote or source is None or quote != source["exact_text"]:
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
    if (_CODE_OR_FORMULA.search(meaning) or _CODE_OR_FORMULA.search(source_text)) and meaning not in source_text:
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
    evidence = {item["evidence_id"]: item for item in bundle["evidence"] if item["evidence_id"] not in non_content_ids}
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
            or any(type(ref) is not int or not 0 <= ref < len(context["evidence"]) for ref in relation["e"])
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


def _cycle(edges: list[tuple[str, str]], candidate: tuple[str, str]) -> bool:
    graph: dict[str, set[str]] = {}
    for source, target in [*edges, candidate]:
        graph.setdefault(source, set()).add(target)
    pending = [candidate[1]]
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node == candidate[0]:
            return True
        if node not in visited:
            visited.add(node)
            pending.extend(graph.get(node, ()))
    return False


def _path(concepts: list[dict[str, Any]], relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline = {concept["concept_id"]: index for index, concept in enumerate(concepts)}
    outgoing = {concept_id: set() for concept_id in baseline}
    incoming = {concept_id: set() for concept_id in baseline}
    for relation in relations:
        if relation["type"] == "prerequisite":
            source, target = relation["source_concept_id"], relation["target_concept_id"]
            outgoing[source].add(target)
            incoming[target].add(source)
    ready = sorted((key for key, values in incoming.items() if not values), key=baseline.get)
    ordered: list[str] = []
    while ready:
        source = ready.pop(0)
        ordered.append(source)
        for target in sorted(outgoing[source], key=baseline.get):
            incoming[target].discard(source)
            if not incoming[target] and target not in ordered and target not in ready:
                ready.append(target)
                ready.sort(key=baseline.get)
    if len(ordered) != len(concepts):
        raise ValueError("PREREQUISITE_CYCLE")
    return [
        {
            "position": index,
            "concept_id": concept_id,
            "reason": "prerequisite" if any(
                relation["type"] == "prerequisite" and relation["target_concept_id"] == concept_id
                for relation in relations
            ) else "document_order",
        }
        for index, concept_id in enumerate(ordered, start=1)
    ]


def _revision(document: dict[str, Any]) -> str:
    identity = {
        key: value
        for key, value in document.items()
        if key not in {"revision", "run_id", "produced_at", "metrics"}
    }
    return _id("knowledge-structure", identity)


def build_knowledge_structure(
    context: dict[str, Any],
    state: SemanticState,
    *,
    source_sha256: str,
    run_id: str,
    produced_at: str,
    runtime_lock_sha256: str,
    model_id: str,
    model_revision: str,
    semantic_calls: int,
    ocr_calls: int,
    evidence_duration_ms: int = 0,
    semantic_duration_ms: int = 0,
    execution_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        parsed_time = datetime.fromisoformat(produced_at)
        UUID(run_id)
    except (TypeError, ValueError):
        raise ValueError("MATERIAL_IDENTITY_INVALID") from None
    if (
        context.get("material_id") != f"material:sha256:{source_sha256}"
        or parsed_time.tzinfo is None
        or re.fullmatch(r"[0-9a-f]{64}", runtime_lock_sha256) is None
        or not isinstance(model_id,str) or not model_id
        or not isinstance(model_revision,str) or not model_revision
        or (execution_identity is None and (model_id!="google/gemma-4-31B-it-qat-w4a16-ct" or model_revision!="52f3f65bc7a02d555763bc923bd1d9094898219d"))
    ):
        raise ValueError("MATERIAL_IDENTITY_INVALID")
    evidence_by_id = {item["evidence_id"]: item for item in context["evidence"]}
    evidence_order = {key: index for index, key in enumerate(evidence_by_id)}
    concepts: list[dict[str, Any]] = []
    canonical_concept_ids: set[str] = set()
    key_to_id: dict[str, str] = {}
    for key, item in state.concepts.items():
        aliases = sorted(set(item["aliases"]) - {item["label"]})
        claims = []
        for claim in item["claims"]:
            claim = deepcopy(claim)
            claim["evidence_refs"] = list(dict.fromkeys(span["evidence_id"] for span in claim["source_spans"]))
            claim["claim_id"] = _id("claim", claim)
            claims.append(claim)
        if not claims:
            continue
        references = list(dict.fromkeys(reference for claim in claims for reference in claim["evidence_refs"]))
        identity = {
            "label": item["label"],
            "aliases": aliases,
            "claim_ids": [claim["claim_id"] for claim in claims],
            "evidence_refs": references,
        }
        concept_id = _id("concept", identity)
        key_to_id[key] = concept_id
        # 不同模型 key 若產生完全相同的 canonical 內容，只發布一個節點。
        if concept_id in canonical_concept_ids:
            continue
        canonical_concept_ids.add(concept_id)
        concepts.append(
            {
                "concept_id": concept_id,
                "label": item["label"],
                "aliases": aliases,
                "claims": claims,
                "evidence_refs": references,
                "section_ids": list(dict.fromkeys(evidence_by_id[reference]["section_id"] for reference in references)),
                "source_pages": sorted({evidence_by_id[reference]["page"] for reference in references}),
            }
        )
    concepts.sort(
        key=lambda concept: (
            min(evidence_order[reference] for reference in concept["evidence_refs"]),
            concept["concept_id"],
        )
    )
    relations: list[dict[str, Any]] = []
    directed_relations: set[tuple[str, str, str]] = set()
    prerequisite_edges: list[tuple[str, str]] = []
    rejected_relations = 0
    for proposal in state.relations:
        source = key_to_id.get(proposal["source_concept"])
        target = key_to_id.get(proposal["target_concept"])
        if source is None or target is None or source == target:
            rejected_relations += 1
            continue
        relation_type = proposal["type"]
        # 比較兩端不排序，避免反轉理由中的前者／後者；下方仍做雙向去重。
        identity = (source, target, relation_type)
        reverse = (target, source, relation_type)
        if identity in directed_relations or reverse in directed_relations:
            rejected_relations += 1
            continue
        if relation_type == "prerequisite" and _cycle(prerequisite_edges, (source, target)):
            rejected_relations += 1
            continue
        relation = {
            "source_concept_id": source,
            "target_concept_id": target,
            "type": relation_type,
            "learner_reason": proposal["learner_reason"],
            "evidence_refs": deepcopy(proposal["evidence_refs"]),
            "context_refs": deepcopy(proposal["context_refs"]),
            "inference_basis": proposal["inference_basis"],
            "confidence": float(proposal["confidence"]),
        }
        relation["relation_id"] = _id("relation", relation)
        relations.append(relation)
        directed_relations.add(identity)
        if relation_type == "prerequisite":
            prerequisite_edges.append((source, target))
    relations.sort(
        key=lambda relation: (
            RELATION_PRIORITY[relation["type"]],
            relation["source_concept_id"],
            relation["target_concept_id"],
            relation["relation_id"],
        )
    )
    section_nodes = []
    for section in context["sections"]:
        concept_ids = [
            concept["concept_id"]
            for concept in concepts
            if concept["section_ids"][0] == section["section_id"]
        ]
        section_nodes.append(
            {
                "section_id": section["section_id"],
                "title": section["title"],
                "order": section["order"],
                "heading_evidence_id": section["heading_evidence_id"],
                "concept_ids": concept_ids,
            }
        )
    reasons = []
    if context["excluded_pages"]:
        reasons.append("PAGES_EXCLUDED")
    if state.rejected_claims:
        reasons.append("CLAIMS_REJECTED")
    if state.literal_repairs:
        reasons.append("LITERALS_RESTORED_FROM_SOURCE")
    rejected_relations += state.rejected_relations
    if rejected_relations:
        reasons.append("RELATIONS_REJECTED")
    if state.source_review_required:
        reasons.append("SOURCE_REVIEW_SUGGESTED")
    if not concepts:
        reasons.append("NO_CANONICAL_CONCEPT")
    status = {
        "processing": "partial" if reasons and concepts else ("failed" if not concepts else "succeeded"),
        "quality": "needs_review" if reasons else "accepted",
        "decision": "reject" if not concepts else ("review" if reasons else "retain"),
        "reason_codes": reasons,
    }
    document = {
        "schema": STRUCTURE_SCHEMA if execution_identity is None else "knowledge-structure/v3",
        "material_id": context["material_id"],
        "source_sha256": source_sha256,
        "run_id": run_id,
        "produced_at": produced_at,
        "provenance": {
            "runtime_lock_sha256": runtime_lock_sha256,
            "model_id": model_id,
            "model_revision": model_revision,
            "semantic_policy": "unified-material-evidence-projection/v3",
        },
        "page_count": context["page_count"],
        "evidence": deepcopy(context["evidence"]),
        "excluded_pages": deepcopy(context["excluded_pages"]),
        "document_tree": {"material_id": context["material_id"], "sections": section_nodes},
        "concepts": concepts,
        "relations": relations,
        "initial_learning_path": _path(concepts, relations),
        "metrics": {
            "semantic_calls": semantic_calls,
            "ocr_calls": ocr_calls,
            "evidence_duration_ms": evidence_duration_ms,
            "semantic_duration_ms": semantic_duration_ms,
            "literal_repairs": state.literal_repairs,
            "rejected_claims": state.rejected_claims,
            "rejected_relations": rejected_relations,
        },
        "status": status,
    }
    if execution_identity is not None:document["execution_identity"]=deepcopy(execution_identity)
    if state.source_review_required:document["source_review_required"]=True
    document["revision"] = _revision(document)
    if not validate_knowledge_structure(document):
        raise ValueError("KNOWLEDGE_STRUCTURE_INVALID")
    return document


def validate_knowledge_structure(document: Any) -> bool:
    """重驗 final artifact 的 identity、lineage、Relation 與 Path authority。"""

    try:
        digest_field = "source_set_sha256" if isinstance(document, dict) and document.get("schema") == "knowledge-structure/v4" else "source_sha256"
        source_digest = document[digest_field]
        fields = {
            "schema", "revision", "material_id", "source_sha256", "run_id", "produced_at",
            "provenance", "page_count", "evidence", "excluded_pages", "document_tree",
            "concepts", "relations", "initial_learning_path", "metrics", "status",
        }
        if isinstance(document, dict) and "source_review_required" in document:
            if document["source_review_required"] is not True:return False
            fields.add("source_review_required")
        if digest_field == "source_set_sha256":
            fields.remove("source_sha256")
            fields.add(digest_field)
            binding = document.get("input_binding")
            if not isinstance(binding, dict) or binding.get("source_set_digest") != source_digest:
                return False
        if isinstance(document,dict) and document.get("schema") in {"knowledge-structure/v3", "knowledge-structure/v4"} and "input_binding" in document:
            fields.add("input_binding")
        execution=document.get("execution_identity") if isinstance(document,dict) else None
        if execution is not None:
            fields.add("execution_identity")
            if document.get("schema") not in {"knowledge-structure/v3", "knowledge-structure/v4"} or not isinstance(execution,dict) or set(execution)!={"transport","model_id","model_revision","config_sha256","runtime_lock_sha256"}:return False
            if execution["transport"]!="command" or any(not isinstance(execution[k],str) or re.fullmatch(r"[0-9a-f]{64}",execution[k]) is None for k in ("config_sha256","runtime_lock_sha256")):return False
        if (
            not isinstance(document, dict)
            or set(document) != fields
            or document["schema"] not in {STRUCTURE_SCHEMA,"knowledge-structure/v3","knowledge-structure/v4"}
            or document["revision"] != _revision(document)
            or not isinstance(source_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_digest) is None
            or document["material_id"] != f"material:sha256:{source_digest}"
        ):
            return False
        provenance = document["provenance"]
        try:
            produced_at = datetime.fromisoformat(document["produced_at"])
            UUID(document["run_id"])
        except (TypeError, ValueError):
            return False
        if (
            produced_at.tzinfo is None
            or not isinstance(provenance, dict)
            or set(provenance) != {
                "runtime_lock_sha256", "model_id", "model_revision", "semantic_policy"
            }
            or re.fullmatch(r"[0-9a-f]{64}", provenance["runtime_lock_sha256"]) is None
            or not isinstance(provenance["model_id"],str) or not provenance["model_id"]
            or not isinstance(provenance["model_revision"],str) or not provenance["model_revision"]
            or (execution is None and (provenance["model_id"]!="google/gemma-4-31B-it-qat-w4a16-ct" or provenance["model_revision"]!="52f3f65bc7a02d555763bc923bd1d9094898219d"))
            or (execution is not None and any(execution[k]!=provenance[k] for k in ("model_id","model_revision","runtime_lock_sha256")))
            or provenance["semantic_policy"] != "unified-material-evidence-projection/v3"
        ):
            return False
        evidence = document["evidence"]
        excluded_pages = document["excluded_pages"]
        concepts = document["concepts"]
        relations = document["relations"]
        if (
            type(document["page_count"]) is not int
            or document["page_count"] < 1
            or not isinstance(evidence, list)
            or not isinstance(excluded_pages, list)
            or not isinstance(concepts, list)
            or not isinstance(relations, list)
        ):
            return False
        metrics = document["metrics"]
        if (
            not isinstance(metrics, dict)
            or set(metrics) != {
                "semantic_calls", "ocr_calls", "evidence_duration_ms",
                "semantic_duration_ms", "literal_repairs", "rejected_claims",
                "rejected_relations",
            }
            or any(type(value) is not int or value < 0 for value in metrics.values())
            or metrics["semantic_calls"] < 1
            or metrics["ocr_calls"] > document["page_count"]
        ):
            return False
        evidence_ids = [item["evidence_id"] for item in evidence]
        concept_ids = [item["concept_id"] for item in concepts]
        if len(evidence_ids) != len(set(evidence_ids)) or len(concept_ids) != len(set(concept_ids)):
            return False
        evidence_positions = [(item["page"], item["block_order"]) for item in evidence]
        if evidence_positions != sorted(evidence_positions) or len(evidence_positions) != len(set(evidence_positions)):
            return False
        if any(
            not isinstance(item, dict)
            or set(item) != {
                "evidence_id", "page_ref", "page", "block_order", "kind", "source",
                "exact_text", "heading", "section_id", "source_locator",
            }
            or item["source"] not in {"native_text", "unlimited_ocr"}
            or not isinstance(item["kind"], str)
            or not item["kind"]
            or type(item["page"]) is not int
            or item["page"] < 1
            or type(item["block_order"]) is not int
            or item["block_order"] < 0
            or not isinstance(item["exact_text"], str)
            or not item["exact_text"]
            or not isinstance(item["heading"], str)
            or not item["heading"]
            or not isinstance(item["section_id"], str)
            for item in evidence
        ):
            return False
        for item in evidence:
            locator = item["source_locator"]
            if (
                not isinstance(locator, dict)
                or set(locator) != {"page", "block_id", "region"}
                or not isinstance(locator["region"], list)
                or len(locator["region"]) != 4
                or any(type(number) not in {int, float} or not math.isfinite(number) for number in locator["region"])
                or locator["region"][0] >= locator["region"][2]
                or locator["region"][1] >= locator["region"][3]
            ):
                return False
            page_ref = _id(
                "page",
                {
                    "source_sha256": source_digest,
                    "page_number": item["page"],
                },
            )
            block_id = _id(
                "block",
                {
                    "page_ref": page_ref,
                    "reading_order": item["block_order"],
                    "region": locator["region"],
                },
            )
            evidence_id = _id(
                "evidence",
                {
                    "page_ref": page_ref,
                    "block_id": block_id,
                    "kind": item["kind"],
                    "source": item["source"],
                    "text": item["exact_text"],
                    "reading_order": item["block_order"],
                    "region": locator["region"],
                },
            )
            if (
                item["page_ref"] != page_ref
                or locator["page"] != item["page"]
                or locator["block_id"] != block_id
                or item["evidence_id"] != evidence_id
            ):
                return False
        included_page_numbers = {item["page"] for item in evidence}
        excluded_page_numbers: set[int] = set()
        for excluded in excluded_pages:
            if (
                not isinstance(excluded, dict)
                or set(excluded) != {"page_ref", "page", "stage", "reason_code"}
                or type(excluded["page"]) is not int
                or excluded["page"] < 1
                or excluded["page"] > document["page_count"]
                or excluded["page"] in excluded_page_numbers
                or excluded["stage"] != "evidence"
                or not isinstance(excluded["reason_code"], str)
                or re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", excluded["reason_code"]) is None
                or excluded["page_ref"] != _id(
                    "page",
                    {
                        "source_sha256": source_digest,
                        "page_number": excluded["page"],
                    },
                )
            ):
                return False
            excluded_page_numbers.add(excluded["page"])
        if (
            included_page_numbers & excluded_page_numbers
            or included_page_numbers | excluded_page_numbers
            != set(range(1, document["page_count"] + 1))
        ):
            return False
        known_evidence, known_concepts = set(evidence_ids), set(concept_ids)
        evidence_by_id = {item["evidence_id"]: item for item in evidence}
        evidence_order = {evidence_id: index for index, evidence_id in enumerate(evidence_ids)}
        for concept in concepts:
            if (
                set(concept) != {
                    "concept_id", "label", "aliases", "claims", "evidence_refs",
                    "section_ids", "source_pages",
                }
                or not concept["claims"]
                or not isinstance(concept["label"], str)
                or not concept["label"].strip()
                or not isinstance(concept["aliases"], list)
                or not isinstance(concept["evidence_refs"], list)
                or not concept["evidence_refs"]
                or len(concept["evidence_refs"]) != len(set(concept["evidence_refs"]))
                or not set(concept["evidence_refs"]) <= known_evidence
                or not isinstance(concept["section_ids"], list)
                or not concept["section_ids"]
                or not isinstance(concept["source_pages"], list)
            ):
                return False
            expected_sections = list(dict.fromkeys(evidence_by_id[reference]["section_id"] for reference in concept["evidence_refs"]))
            expected_pages = sorted({evidence_by_id[reference]["page"] for reference in concept["evidence_refs"]})
            claim_ids: set[str] = set()
            for claim in concept["claims"]:
                if (
                    not isinstance(claim, dict)
                    or set(claim) != {
                        "claim_id", "text", "source_spans", "projection", "evidence_refs"
                    }
                    or claim["claim_id"] in claim_ids
                    or not isinstance(claim["text"], str)
                    or not claim["text"].strip()
                    or not isinstance(claim["source_spans"], list)
                    or not claim["source_spans"]
                    or not isinstance(claim["evidence_refs"], list)
                    or not claim["evidence_refs"]
                ):
                    return False
                identity = {key: value for key, value in claim.items() if key != "claim_id"}
                if claim["claim_id"] != _id("claim", identity) or not set(claim["evidence_refs"]) <= known_evidence:
                    return False
                if any(
                    not isinstance(span, dict)
                    or set(span) != {"evidence_id", "quote"}
                    or span["evidence_id"] not in known_evidence
                    or span["quote"] != evidence_by_id[span["evidence_id"]]["exact_text"]
                    for span in claim["source_spans"]
                ):
                    return False
                if claim["evidence_refs"] != list(
                    dict.fromkeys(span["evidence_id"] for span in claim["source_spans"])
                ):
                    return False
                claim_ids.add(claim["claim_id"])
                source_text = " ".join(span["quote"] for span in claim["source_spans"])
                if _unsupported_null_meaning(claim["text"], source_text):
                    return False
                if claim["projection"] == "source_literal_repair":
                    if claim["text"] != source_text:
                        return False
                elif claim["projection"] == "semantic_meaning":
                    if (
                        any(literal not in source_text for literal in _TECHNICAL.findall(claim["text"]))
                        or any(literal not in claim["text"] for literal in _TECHNICAL.findall(source_text))
                        or (_CODE_OR_FORMULA.search(source_text) and claim["text"] not in source_text)
                    ):
                        return False
                else:
                    return False
            expected_evidence_refs = list(
                dict.fromkeys(
                    reference
                    for claim in concept["claims"]
                    for reference in claim["evidence_refs"]
                )
            )
            if concept["evidence_refs"] != expected_evidence_refs:
                return False
            concept_identity = {
                "label": concept["label"],
                "aliases": concept["aliases"],
                "claim_ids": [claim["claim_id"] for claim in concept["claims"]],
                "evidence_refs": concept["evidence_refs"],
            }
            if (
                concept["concept_id"] != _id("concept", concept_identity)
                or concept["aliases"] != sorted(set(concept["aliases"]))
                or concept["label"] in concept["aliases"]
                or concept["section_ids"] != expected_sections
                or concept["source_pages"] != expected_pages
            ):
                return False
        if concepts != sorted(
            concepts,
            key=lambda concept: (
                min(evidence_order[reference] for reference in concept["evidence_refs"]),
                concept["concept_id"],
            ),
        ):
            return False
        tree = document["document_tree"]
        if not isinstance(tree, dict) or set(tree) != {"material_id", "sections"} or tree["material_id"] != document["material_id"]:
            return False
        sections = tree["sections"]
        if not isinstance(sections, list) or [section.get("order") for section in sections] != list(range(len(sections))):
            return False
        tree_concepts = [concept_id for section in sections for concept_id in section.get("concept_ids", [])]
        if len(tree_concepts) != len(set(tree_concepts)) or set(tree_concepts) != known_concepts:
            return False
        expected_sections = []
        for item in evidence:
            if not expected_sections or expected_sections[-1]["section_id"] != item["section_id"]:
                if item["section_id"] != _id(
                    "section",
                    {
                        "material_id": document["material_id"],
                        "anchor_evidence_id": item["evidence_id"],
                    },
                ):
                    return False
                expected_sections.append(
                    {
                        "section_id": item["section_id"],
                        "title": item["heading"],
                        "order": len(expected_sections),
                        "heading_evidence_id": (
                            item["evidence_id"] if item["kind"] == "heading" else None
                        ),
                        "concept_ids": [
                            concept["concept_id"]
                            for concept in concepts
                            if concept["section_ids"][0] == item["section_id"]
                        ],
                    }
                )
            elif item["heading"] != expected_sections[-1]["title"]:
                return False
        if sections != expected_sections:
            return False
        edges = []
        relation_ids = set()
        relation_directions: set[tuple[str, str, str]] = set()
        known_sections = {section["section_id"] for section in sections}
        concept_by_id = {concept["concept_id"]: concept for concept in concepts}
        for relation in relations:
            if not isinstance(relation, dict) or set(relation) != {
                "relation_id", "source_concept_id", "target_concept_id", "type",
                "learner_reason", "evidence_refs", "context_refs", "inference_basis",
                "confidence",
            }:
                return False
            identity = {key: value for key, value in relation.items() if key != "relation_id"}
            relation_type = relation["type"]
            source_id = relation["source_concept_id"]
            target_id = relation["target_concept_id"]
            if (
                relation["relation_id"] in relation_ids
                or relation["relation_id"] != _id("relation", identity)
                or relation_type not in RELATION_TYPES
                or relation["inference_basis"] != RELATION_BASIS[relation_type]
                or source_id not in known_concepts
                or target_id not in known_concepts
                or source_id == target_id
            ):
                return False
            direction = (
                source_id,
                target_id,
                relation_type,
            )
            reverse = (direction[1], direction[0], direction[2])
            if (
                direction in relation_directions
                or reverse in relation_directions
            ):
                return False
            reason = relation["learner_reason"]
            if (
                not isinstance(reason, str)
                or not reason.strip()
                or reason.casefold() in _GENERIC_REASONS
            ):
                return False
            endpoint_evidence = set(concept_by_id[source_id]["evidence_refs"]) | set(
                concept_by_id[target_id]["evidence_refs"]
            )
            evidence_refs = relation["evidence_refs"]
            if (
                not isinstance(evidence_refs, list)
                or not evidence_refs
                or len(evidence_refs) != len(set(evidence_refs))
                or not set(evidence_refs) <= known_evidence
                or not set(evidence_refs) <= endpoint_evidence
            ):
                return False
            endpoint_sections = set(concept_by_id[source_id]["section_ids"]) | set(
                concept_by_id[target_id]["section_ids"]
            )
            context_refs = relation["context_refs"]
            if (
                not isinstance(context_refs, list)
                or len(context_refs) != len(set(context_refs))
                or not set(context_refs) <= known_sections
                or not set(context_refs) <= endpoint_sections
            ):
                return False
            confidence = relation["confidence"]
            if (
                type(confidence) not in {int, float}
                or isinstance(confidence, bool)
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
            ):
                return False
            relation_ids.add(relation["relation_id"])
            relation_directions.add(direction)
            if relation_type == "prerequisite":
                if _cycle(edges, (source_id, target_id)):
                    return False
                edges.append((source_id, target_id))
        if relations != sorted(
            relations,
            key=lambda relation: (
                RELATION_PRIORITY[relation["type"]],
                relation["source_concept_id"],
                relation["target_concept_id"],
                relation["relation_id"],
            ),
        ):
            return False
        path_ids = [step["concept_id"] for step in document["initial_learning_path"]]
        if len(path_ids) != len(set(path_ids)) or set(path_ids) != known_concepts:
            return False
        if document["initial_learning_path"] != _path(concepts, relations):
            return False
        reasons = []
        if excluded_pages:
            reasons.append("PAGES_EXCLUDED")
        if metrics["rejected_claims"]:
            reasons.append("CLAIMS_REJECTED")
        if metrics["literal_repairs"]:
            reasons.append("LITERALS_RESTORED_FROM_SOURCE")
        if metrics["rejected_relations"]:
            reasons.append("RELATIONS_REJECTED")
        if document.get("source_review_required", False):
            reasons.append("SOURCE_REVIEW_SUGGESTED")
        if not concepts:
            reasons.append("NO_CANONICAL_CONCEPT")
        expected_status = {
            "processing": "partial" if reasons and concepts else ("failed" if not concepts else "succeeded"),
            "quality": "needs_review" if reasons else "accepted",
            "decision": "reject" if not concepts else ("review" if reasons else "retain"),
            "reason_codes": reasons,
        }
        return document["status"] == expected_status
    except (KeyError, TypeError, ValueError):
        return False


def build_knowledge_structure_view(document: dict[str, Any]) -> dict[str, Any]:
    if not validate_knowledge_structure(document):
        raise ValueError("KNOWLEDGE_STRUCTURE_INVALID")
    evidence = {item["evidence_id"]: item for item in document["evidence"]}
    concepts = []
    for concept in document["concepts"]:
        public = {key: deepcopy(value) for key, value in concept.items() if key != "evidence_refs"}
        for claim in public["claims"]:
            claim["evidence"] = [
                {
                    "evidence_id": reference,
                    "page_ref": evidence[reference]["page_ref"],
                    "page": evidence[reference]["page"],
                    "block_order": evidence[reference]["block_order"],
                    "kind": evidence[reference]["kind"],
                    "source": evidence[reference]["source"],
                    "source_locator": deepcopy(evidence[reference]["source_locator"]),
                    "quote": " ".join(
                        span["quote"]
                        for span in claim["source_spans"]
                        if span["evidence_id"] == reference
                    ),
                }
                for reference in claim["evidence_refs"]
            ]
            del claim["source_spans"]
            del claim["evidence_refs"]
            del claim["projection"]
        concepts.append(public)
    return {
        "schema": VIEW_SCHEMA,
        "material_id": document["material_id"],
        "knowledge_structure_revision": document["revision"],
        "status": deepcopy(document["status"]),
        "document_tree": deepcopy(document["document_tree"]),
        "concepts": concepts,
        "relations": deepcopy(document["relations"]),
        "initial_learning_path": deepcopy(document["initial_learning_path"]),
        "excluded_pages": deepcopy(document["excluded_pages"]),
    }
