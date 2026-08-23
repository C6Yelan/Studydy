from __future__ import annotations

from typing import Any

from pdf_evidence.ocr_page_evidence import canonical_sha256


RELATION_REQUEST_SCHEMA = "formal-relation-input/v1"
RELATION_OUTPUT_SCHEMA = "formal-relations/v1"
RELATION_TYPES = {
    "prerequisite",
    "contains",
    "similar",
    "confusing",
    "application",
    "example",
}
SYMMETRIC_TYPES = {"similar", "confusing"}
MAX_RELATION_PAIRS = 128
PAIR_BATCH_SIZE = 16


class RelationError(ValueError):
    """Relation candidate 不安全時只回傳固定 reason code。"""


def select_relation_pairs(
    formal_concepts: list[dict[str, Any]],
    page_numbers: dict[str, int],
    *,
    ceiling: int = MAX_RELATION_PAIRS,
) -> tuple[list[list[tuple[str, str]]], dict[str, Any]]:
    """依同頁、首次相鄰與同 resolution group 建立固定順序 pair。"""

    ordered = sorted(
        formal_concepts,
        key=lambda concept: (
            min(page_numbers[page] for page in concept["source_page_refs"]),
            concept["resolution_order"],
            concept["formal_concept_id"],
        ),
    )
    pairs: set[tuple[str, str]] = set()
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if set(left["source_page_refs"]) & set(right["source_page_refs"]):
                pairs.add((left["formal_concept_id"], right["formal_concept_id"]))
            if left["group_id"] == right["group_id"]:
                pairs.add((left["formal_concept_id"], right["formal_concept_id"]))
    for left, right in zip(ordered, ordered[1:]):
        pairs.add((left["formal_concept_id"], right["formal_concept_id"]))
    position = {concept["formal_concept_id"]: index for index, concept in enumerate(ordered)}
    selected = sorted(pairs, key=lambda pair: (position[pair[0]], position[pair[1]]))
    is_partial = len(selected) > ceiling
    selected = selected[:ceiling]
    batches = [selected[index : index + PAIR_BATCH_SIZE] for index in range(0, len(selected), PAIR_BATCH_SIZE)]
    return batches, {
        "processing": "partial" if is_partial else "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["RELATION_PAIR_CEILING_EXCEEDED"] if is_partial else ["RELATION_REVIEW_REQUIRED"],
    }


def build_relation_request(
    pairs: list[tuple[str, str]],
    formal_concepts: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, str], dict[str, tuple[str, str]]]:
    """Model 只看短 Concept／Evidence alias，不取得正式 identity。"""

    concepts_by_id = {concept["formal_concept_id"]: concept for concept in formal_concepts}
    concept_aliases: dict[str, str] = {}
    evidence_aliases: dict[str, tuple[str, str]] = {}
    nodes = []
    needed = []
    for pair in pairs:
        needed.extend(pair)
    for concept_id in dict.fromkeys(needed):
        concept = concepts_by_id.get(concept_id)
        if concept is None:
            raise RelationError("RELATION_ENDPOINT_INVALID")
        concept_alias = f"n{len(concept_aliases) + 1}"
        concept_aliases[concept_alias] = concept_id
        evidence = []
        seen_evidence: set[str] = set()
        for claim in concept["claims"]:
            for evidence_id in claim["evidence_ids"]:
                if evidence_id in seen_evidence:
                    continue
                seen_evidence.add(evidence_id)
                alias = f"{concept_alias}e{len(evidence) + 1}"
                evidence_aliases[alias] = (concept_id, evidence_id)
                evidence.append({"id": alias, "claim_text": claim["text"]})
        nodes.append({"id": concept_alias, "label": concept["label"], "evidence": evidence})
    alias_by_id = {concept_id: alias for alias, concept_id in concept_aliases.items()}
    request = {
        "schema": RELATION_REQUEST_SCHEMA,
        "nodes": nodes,
        "pairs": [
            {"id": f"p{index}", "left": alias_by_id[left], "right": alias_by_id[right]}
            for index, (left, right) in enumerate(pairs, start=1)
        ],
    }
    return request, concept_aliases, evidence_aliases


def validate_relations(
    candidate: Any,
    *,
    request: dict[str, Any],
    concept_aliases: dict[str, str],
    evidence_aliases: dict[str, tuple[str, str]],
    formal_concepts: list[dict[str, Any]],
    evidence_pages: dict[str, str],
) -> dict[str, Any]:
    """驗證 endpoint、雙側 Evidence ownership、方向與重覆衝突。"""

    if (
        not isinstance(candidate, dict)
        or set(candidate) != {"schema", "pairs"}
        or candidate["schema"] != RELATION_OUTPUT_SCHEMA
        or not isinstance(candidate["pairs"], list)
    ):
        raise RelationError("RELATION_SCHEMA_INVALID")
    expected = {pair["id"]: pair for pair in request["pairs"]}
    if len(candidate["pairs"]) != len(expected):
        raise RelationError("RELATION_PAIR_MISSING")
    formal_by_id = {concept["formal_concept_id"]: concept for concept in formal_concepts}
    seen_pairs: set[str] = set()
    relations = []
    relation_keys: set[tuple[str, str, str]] = set()
    directed_pairs: dict[tuple[str, str], str] = {}
    for answer in candidate["pairs"]:
        if (
            not isinstance(answer, dict)
            or set(answer) != {"id", "outcome", "relations"}
            or answer["id"] not in expected
            or answer["id"] in seen_pairs
            or answer["outcome"] not in {"relations", "no_relation", "uncertain"}
            or not isinstance(answer["relations"], list)
            or (answer["outcome"] != "relations" and answer["relations"])
            or (answer["outcome"] == "relations" and not answer["relations"])
        ):
            raise RelationError("RELATION_SCHEMA_INVALID")
        seen_pairs.add(answer["id"])
        allowed_endpoints = {expected[answer["id"]]["left"], expected[answer["id"]]["right"]}
        for source_relation in answer["relations"]:
            if (
                not isinstance(source_relation, dict)
                or set(source_relation) != {
                    "type", "source", "target", "source_evidence_ids", "target_evidence_ids"
                }
                or source_relation["type"] not in RELATION_TYPES
                or {source_relation["source"], source_relation["target"]} != allowed_endpoints
                or source_relation["source"] == source_relation["target"]
            ):
                raise RelationError("RELATION_ENDPOINT_INVALID")
            source_id = concept_aliases.get(source_relation["source"])
            target_id = concept_aliases.get(source_relation["target"])
            if source_id is None or target_id is None:
                raise RelationError("RELATION_ENDPOINT_INVALID")
            source_evidence = _owned_evidence(
                source_relation["source_evidence_ids"], source_id, evidence_aliases
            )
            target_evidence = _owned_evidence(
                source_relation["target_evidence_ids"], target_id, evidence_aliases
            )
            source_pages = set(formal_by_id[source_id]["source_page_refs"])
            target_pages = set(formal_by_id[target_id]["source_page_refs"])
            if (
                any(evidence_pages.get(item) not in source_pages for item in source_evidence)
                or any(evidence_pages.get(item) not in target_pages for item in target_evidence)
            ):
                raise RelationError("RELATION_EVIDENCE_INVALID")
            relation_type = source_relation["type"]
            if relation_type in SYMMETRIC_TYPES and target_id < source_id:
                source_id, target_id = target_id, source_id
                source_evidence, target_evidence = target_evidence, source_evidence
            key = (relation_type, source_id, target_id)
            if key in relation_keys:
                raise RelationError("RELATION_DUPLICATE")
            opposite = (target_id, source_id)
            if relation_type not in SYMMETRIC_TYPES and opposite in directed_pairs:
                raise RelationError("RELATION_CONFLICT")
            relation_keys.add(key)
            directed_pairs[(source_id, target_id)] = relation_type
            identity = {
                "type": relation_type,
                "source_formal_concept_id": source_id,
                "target_formal_concept_id": target_id,
                "source_evidence_ids": source_evidence,
                "target_evidence_ids": target_evidence,
            }
            relations.append(
                {
                    "relation_id": "formal-relation:sha256:" + canonical_sha256(identity),
                    **identity,
                    "quality": "needs_review",
                    "decision": "review",
                    "reason_codes": ["RELATION_REVIEW_REQUIRED"],
                }
            )
    if seen_pairs != set(expected):
        raise RelationError("RELATION_PAIR_MISSING")
    return {
        "schema": RELATION_OUTPUT_SCHEMA,
        "relations": sorted(relations, key=lambda item: item["relation_id"]),
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["RELATION_REVIEW_REQUIRED"],
    }


def _owned_evidence(
    aliases: Any,
    owner_id: str,
    evidence_aliases: dict[str, tuple[str, str]],
) -> list[str]:
    if (
        not isinstance(aliases, list)
        or not aliases
        or len(aliases) != len(set(aliases))
        or any(evidence_aliases.get(alias, (None,))[0] != owner_id for alias in aliases)
    ):
        raise RelationError("RELATION_EVIDENCE_INVALID")
    return sorted(evidence_aliases[alias][1] for alias in aliases)
