from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
import re
from typing import Any
import unicodedata

from pdf_evidence.ocr_page_evidence import canonical_bytes, canonical_sha256


STRUCTURE_SCHEMA = "knowledge-structure/v1"
VIEW_SCHEMA = "knowledge-structure-view/v1"
REQUEST_SCHEMA = "material-semantics-request/v1"
RESPONSE_SCHEMA = "material-semantics-response/v1"
RELATION_TYPES = {"prerequisite", "part_of", "application", "example", "contrast"}
RELATION_BASIS = {
    "prerequisite": "dependency",
    "part_of": "composition",
    "application": "usage",
    "example": "instantiation",
    "contrast": "comparison",
}
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_TECHNICAL = re.compile(
    r"\\(?:[0abfnrtv\\'\"?]|x[0-9A-Fa-f]+|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8})"
    r"|'[^'\n]{0,80}'|\"[^\"\n]{0,80}\"|(?:==|!=|<=|>=|->|::|&&|\|\||<<|>>)"
    r"|(?<!\w)[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s?(?:%|ms|s|kg|g|km|m|cm|mm|Hz|kHz|MHz|GHz|B|KB|MB|GB))?"
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


def _normalized_label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


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


def build_document_context(
    pages: list[dict[str, Any]],
    *,
    page_count: int,
    excluded_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """建立一次性的 material context；不產生逐頁 semantic envelope。"""

    ordered = _ordered_pages(pages)
    if type(page_count) is not int or page_count < len(ordered):
        raise ValueError("DOCUMENT_EVIDENCE_INVALID")
    material_id = ordered[0]["material_id"]
    sections: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for page in ordered:
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
    if not evidence:
        raise ValueError("NO_USABLE_EVIDENCE")
    for index, section in enumerate(sections):
        section["previous_section_title"] = sections[index - 1]["title"] if index else None
        section["next_section_title"] = sections[index + 1]["title"] if index + 1 < len(sections) else None
    excluded = deepcopy(excluded_pages or [])
    return {
        "schema": "document-context/v1",
        "material_id": material_id,
        "page_count": page_count,
        "sections": sections,
        "evidence": evidence,
        "excluded_pages": excluded,
    }


def _request_size(sections: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> int:
    return len(canonical_bytes({"sections": sections, "evidence": evidence}))


def build_semantic_bundles(
    context: dict[str, Any], *, maximum_utf8_bytes: int
) -> list[dict[str, Any]]:
    """依 section 邊界填滿 bundle；超大單一 section 才按 Evidence 切割。"""

    if type(maximum_utf8_bytes) is not int or maximum_utf8_bytes < 4096:
        raise ValueError("SEMANTIC_BUNDLE_INVALID")
    evidence_by_id = {item["evidence_id"]: item for item in context["evidence"]}
    units: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for section in context["sections"]:
        items = [evidence_by_id[evidence_id] for evidence_id in section["evidence_ids"]]
        if _request_size([section], items) <= maximum_utf8_bytes:
            units.append((section, items))
            continue
        chunk: list[dict[str, Any]] = []
        for item in items:
            candidate = [*chunk, item]
            if chunk and _request_size([section], candidate) > maximum_utf8_bytes:
                split_section = deepcopy(section)
                split_section["evidence_ids"] = [entry["evidence_id"] for entry in chunk]
                units.append((split_section, chunk))
                chunk = [item]
            else:
                chunk = candidate
            if _request_size([section], chunk) > maximum_utf8_bytes:
                raise ValueError("SEMANTIC_INPUT_TOO_LARGE")
        split_section = deepcopy(section)
        split_section["evidence_ids"] = [entry["evidence_id"] for entry in chunk]
        units.append((split_section, chunk))
    bundles: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for section, items in units:
        next_sections = [*sections, section]
        next_evidence = [*evidence, *items]
        if evidence and _request_size(next_sections, next_evidence) > maximum_utf8_bytes:
            bundles.append({"sections": sections, "evidence": evidence})
            sections, evidence = [section], list(items)
        else:
            sections, evidence = next_sections, next_evidence
    if evidence:
        bundles.append({"sections": sections, "evidence": evidence})
    return bundles


def semantic_response_schema() -> dict[str, Any]:
    span = {
        "type": "object",
        "additionalProperties": False,
        "required": ["evidence_id", "quote"],
        "properties": {
            "evidence_id": {"type": "string"},
            "quote": {"type": "string", "minLength": 1},
        },
    }
    claim = {
        "type": "object",
        "additionalProperties": False,
        "required": ["meaning", "source_spans"],
        "properties": {
            "meaning": {"type": "string", "minLength": 1},
            "source_spans": {"type": "array", "minItems": 1, "items": span},
        },
    }
    concept = {
        "type": "object",
        "additionalProperties": False,
        "required": ["key", "label", "aliases", "claims"],
        "properties": {
            "key": {"type": "string", "minLength": 1},
            "label": {"type": "string", "minLength": 1},
            "aliases": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "claims": {"type": "array", "items": claim},
        },
    }
    relation = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "source_concept", "target_concept", "type", "learner_reason",
            "evidence_refs", "context_refs", "inference_basis", "confidence",
        ],
        "properties": {
            "source_concept": {"type": "string"},
            "target_concept": {"type": "string"},
            "type": {"type": "string", "enum": sorted(RELATION_TYPES)},
            "learner_reason": {"type": "string", "minLength": 1},
            "evidence_refs": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "context_refs": {"type": "array", "items": {"type": "string"}},
            "inference_basis": {"type": "string", "enum": sorted(set(RELATION_BASIS.values()))},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema", "concepts", "relations"],
        "properties": {
            "schema": {"type": "string", "const": RESPONSE_SCHEMA},
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

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "key": key,
                "label": concept["label"],
                "aliases": concept["aliases"],
                "claims": [claim["text"] for claim in concept["claims"]],
            }
            for key, concept in self.concepts.items()
        ]


def semantic_request(
    context: dict[str, Any], bundle: dict[str, Any], state: SemanticState
) -> dict[str, Any]:
    return {
        "schema": REQUEST_SCHEMA,
        "material_id": context["material_id"],
        "existing_concepts": state.catalog(),
        "sections": deepcopy(bundle["sections"]),
        "evidence": deepcopy(bundle["evidence"]),
    }


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
        if not isinstance(quote, str) or not quote or source is None or quote not in source["exact_text"]:
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

    if not isinstance(response, dict) or set(response) != {"schema", "concepts", "relations"}:
        raise ValueError("SEMANTIC_OUTPUT_INVALID")
    if response["schema"] != RESPONSE_SCHEMA or not isinstance(response["concepts"], list) or not isinstance(response["relations"], list):
        raise ValueError("SEMANTIC_OUTPUT_INVALID")
    evidence = {item["evidence_id"]: item for item in bundle["evidence"]}
    all_evidence = {item["evidence_id"] for item in context["evidence"]}
    all_sections = {section["section_id"] for section in context["sections"]}
    response_keys: set[str] = set()
    for proposal in response["concepts"]:
        if not isinstance(proposal, dict) or set(proposal) != {"key", "label", "aliases", "claims"}:
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        key = proposal["key"]
        if not isinstance(key, str) or _KEY.fullmatch(key) is None or key in response_keys:
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        response_keys.add(key)
        label = _text(proposal["label"], maximum=256)
        aliases = proposal["aliases"]
        if not isinstance(aliases, list):
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        aliases = sorted({_text(alias, maximum=256) for alias in aliases} - {label})
        current = state.concepts.get(key)
        if current is None:
            current = {"label": label, "aliases": aliases, "claims": []}
            state.concepts[key] = current
        else:
            current["aliases"] = sorted(
                set(current["aliases"]) | set(aliases) | ({label} - {current["label"]})
            )
        if not isinstance(proposal["claims"], list):
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        for proposed_claim in proposal["claims"]:
            claim = _project_claim(proposed_claim, evidence)
            if claim is None:
                state.rejected_claims += 1
                continue
            if claim["projection"] == "source_literal_repair":
                state.literal_repairs += 1
            identity = canonical_sha256(claim)
            if all(canonical_sha256(existing) != identity for existing in current["claims"]):
                current["claims"].append(claim)
    known_keys = set(state.concepts)
    for relation in response["relations"]:
        if not isinstance(relation, dict) or set(relation) != {
            "source_concept", "target_concept", "type", "learner_reason",
            "evidence_refs", "context_refs", "inference_basis", "confidence",
        }:
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        reason = _text(relation["learner_reason"], maximum=1024)
        relation_type = relation["type"]
        evidence_refs = relation["evidence_refs"]
        context_refs = relation["context_refs"]
        confidence = relation["confidence"]
        if (
            relation["source_concept"] not in known_keys
            or relation["target_concept"] not in known_keys
            or relation["source_concept"] == relation["target_concept"]
            or relation_type not in RELATION_TYPES
            or relation["inference_basis"] != RELATION_BASIS[relation_type]
            or reason.casefold() in _GENERIC_REASONS
            or not isinstance(evidence_refs, list)
            or not evidence_refs
            or len(evidence_refs) != len(set(evidence_refs))
            or any(reference not in all_evidence for reference in evidence_refs)
            or not isinstance(context_refs, list)
            or len(context_refs) != len(set(context_refs))
            or any(reference not in all_sections for reference in context_refs)
            or type(confidence) not in {int, float}
            or isinstance(confidence, bool)
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
    resource_index: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if context.get("material_id") != f"material:sha256:{source_sha256}":
        raise ValueError("MATERIAL_IDENTITY_INVALID")
    evidence_by_id = {item["evidence_id"]: item for item in context["evidence"]}
    evidence_order = {key: index for index, key in enumerate(evidence_by_id)}
    concepts: list[dict[str, Any]] = []
    key_to_id: dict[str, str] = {}
    for key, item in state.concepts.items():
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
            "aliases": item["aliases"],
            "claim_ids": [claim["claim_id"] for claim in claims],
            "evidence_refs": references,
        }
        concept_id = _id("concept", identity)
        key_to_id[key] = concept_id
        concepts.append(
            {
                "concept_id": concept_id,
                "label": item["label"],
                "aliases": item["aliases"],
                "claims": claims,
                "evidence_refs": references,
                "section_ids": list(dict.fromkeys(evidence_by_id[reference]["section_id"] for reference in references)),
                "source_pages": sorted({evidence_by_id[reference]["page"] for reference in references}),
                "resources": deepcopy((resource_index or {}).get(_normalized_label(item["label"]), [])),
            }
        )
    concepts.sort(key=lambda concept: min(evidence_order[reference] for reference in concept["evidence_refs"]))
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
        if relation_type == "contrast" and source > target:
            source, target = target, source
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
    if not concepts:
        reasons.append("NO_CANONICAL_CONCEPT")
    status = {
        "processing": "partial" if reasons and concepts else ("failed" if not concepts else "succeeded"),
        "quality": "needs_review" if reasons else "accepted",
        "decision": "reject" if not concepts else ("review" if reasons else "retain"),
        "reason_codes": reasons,
    }
    document = {
        "schema": STRUCTURE_SCHEMA,
        "material_id": context["material_id"],
        "source_sha256": source_sha256,
        "run_id": run_id,
        "produced_at": produced_at,
        "provenance": {
            "runtime_lock_sha256": runtime_lock_sha256,
            "model_id": model_id,
            "model_revision": model_revision,
            "semantic_policy": "unified-material-evidence-projection/v1",
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
    document["revision"] = _revision(document)
    if not validate_knowledge_structure(document):
        raise ValueError("KNOWLEDGE_STRUCTURE_INVALID")
    return document


def validate_knowledge_structure(document: Any) -> bool:
    """重驗 final artifact 的 identity、lineage、Relation 與 Path authority。"""

    try:
        fields = {
            "schema", "revision", "material_id", "source_sha256", "run_id", "produced_at",
            "provenance", "page_count", "evidence", "excluded_pages", "document_tree",
            "concepts", "relations", "initial_learning_path", "metrics", "status",
        }
        if (
            not isinstance(document, dict)
            or set(document) != fields
            or document["schema"] != STRUCTURE_SCHEMA
            or document["revision"] != _revision(document)
            or not isinstance(document["source_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", document["source_sha256"]) is None
            or document["material_id"] != f"material:sha256:{document['source_sha256']}"
        ):
            return False
        evidence = document["evidence"]
        concepts = document["concepts"]
        relations = document["relations"]
        if not isinstance(evidence, list) or not isinstance(concepts, list) or not isinstance(relations, list):
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
        if any(
            not isinstance(item, dict)
            or set(item) != {
                "evidence_id", "page_ref", "page", "block_order", "kind", "source",
                "exact_text", "heading", "section_id", "source_locator",
            }
            or item["source"] not in {"native_text", "unlimited_ocr"}
            or type(item["page"]) is not int
            or item["page"] < 1
            or type(item["block_order"]) is not int
            or item["block_order"] < 0
            or not isinstance(item["exact_text"], str)
            or not item["exact_text"]
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
                    "source_sha256": document["source_sha256"],
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
        known_evidence, known_concepts = set(evidence_ids), set(concept_ids)
        evidence_by_id = {item["evidence_id"]: item for item in evidence}
        for concept in concepts:
            if (
                set(concept) != {
                    "concept_id", "label", "aliases", "claims", "evidence_refs",
                    "section_ids", "source_pages", "resources",
                }
                or not concept["claims"]
                or not set(concept["evidence_refs"]) <= known_evidence
                or not isinstance(concept["resources"], list)
            ):
                return False
            expected_sections = list(dict.fromkeys(evidence_by_id[reference]["section_id"] for reference in concept["evidence_refs"]))
            expected_pages = sorted({evidence_by_id[reference]["page"] for reference in concept["evidence_refs"]})
            for claim in concept["claims"]:
                identity = {key: value for key, value in claim.items() if key != "claim_id"}
                if claim["claim_id"] != _id("claim", identity) or not set(claim["evidence_refs"]) <= known_evidence:
                    return False
                if any(
                    not isinstance(span, dict)
                    or set(span) != {"evidence_id", "quote"}
                    or span["evidence_id"] not in known_evidence
                    or span["quote"] not in evidence_by_id[span["evidence_id"]]["exact_text"]
                    for span in claim["source_spans"]
                ):
                    return False
                source_text = " ".join(span["quote"] for span in claim["source_spans"])
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
        tree = document["document_tree"]
        if not isinstance(tree, dict) or set(tree) != {"material_id", "sections"} or tree["material_id"] != document["material_id"]:
            return False
        sections = tree["sections"]
        if not isinstance(sections, list) or [section.get("order") for section in sections] != list(range(len(sections))):
            return False
        tree_concepts = [concept_id for section in sections for concept_id in section.get("concept_ids", [])]
        if len(tree_concepts) != len(set(tree_concepts)) or set(tree_concepts) != known_concepts:
            return False
        edges = []
        relation_ids = set()
        for relation in relations:
            identity = {key: value for key, value in relation.items() if key != "relation_id"}
            if (
                relation["relation_id"] in relation_ids
                or relation["relation_id"] != _id("relation", identity)
                or relation["type"] not in RELATION_TYPES
                or relation["inference_basis"] != RELATION_BASIS[relation["type"]]
                or relation["source_concept_id"] not in known_concepts
                or relation["target_concept_id"] not in known_concepts
                or relation["source_concept_id"] == relation["target_concept_id"]
                or not set(relation["evidence_refs"]) <= known_evidence
            ):
                return False
            relation_ids.add(relation["relation_id"])
            if relation["type"] == "prerequisite":
                if _cycle(edges, (relation["source_concept_id"], relation["target_concept_id"])):
                    return False
                edges.append((relation["source_concept_id"], relation["target_concept_id"]))
        path_ids = [step["concept_id"] for step in document["initial_learning_path"]]
        if len(path_ids) != len(set(path_ids)) or set(path_ids) != known_concepts:
            return False
        positions = {concept_id: index for index, concept_id in enumerate(path_ids)}
        return all(positions[source] < positions[target] for source, target in edges)
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
