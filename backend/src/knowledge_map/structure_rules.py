"""Knowledge Structure 的 ID、字面值與關係排序規則。"""
from __future__ import annotations

import re
from typing import Any

from pdf_evidence.ocr_page_evidence import canonical_sha256


STRUCTURE_SCHEMA = "knowledge-structure/v1"
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
