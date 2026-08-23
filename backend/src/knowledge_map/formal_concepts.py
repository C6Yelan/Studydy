from __future__ import annotations

from copy import deepcopy
import unicodedata
from typing import Any

from pdf_evidence.ocr_page_evidence import canonical_sha256


RESOLUTION_REQUEST_SCHEMA = "formal-concept-resolution-input/v1"
RESOLUTION_OUTPUT_SCHEMA = "formal-concept-resolution/v1"
OPERATIONS = {"KEEP", "MERGE", "RENAME", "SPLIT", "DROP"}
MAX_GROUP_CANDIDATES = 32


class FormalConceptError(ValueError):
    """Formal Concept 驗證失敗時只回傳固定原因。"""


def normalized_label(label: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", label).split()).casefold()


def build_resolution_requests(
    source_concepts: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, str], dict[str, str]]]:
    """只把同正規化 label 的來源 Concept 放在同一個判斷群組。"""

    groups: dict[str, list[dict[str, Any]]] = {}
    for concept in source_concepts:
        label = concept.get("label") if isinstance(concept, dict) else None
        if not isinstance(label, str) or not label:
            raise FormalConceptError("RESOLUTION_SOURCE_INVALID")
        groups.setdefault(normalized_label(label), []).append(concept)

    requests = []
    for group_index, label_key in enumerate(sorted(groups), start=1):
        concepts = groups[label_key]
        if len(concepts) > MAX_GROUP_CANDIDATES:
            raise FormalConceptError("RESOLUTION_CEILING_EXCEEDED")
        concept_aliases: dict[str, str] = {}
        claim_aliases: dict[str, str] = {}
        candidates = []
        for concept_index, concept in enumerate(concepts, start=1):
            concept_alias = f"c{concept_index}"
            concept_aliases[concept_alias] = concept["concept_id"]
            claims = []
            for claim_index, claim in enumerate(
                [concept["definition"], *concept["key_points"]], start=1
            ):
                claim_alias = f"{concept_alias}q{claim_index}"
                claim_aliases[claim_alias] = claim["claim_id"]
                claims.append({"id": claim_alias, "text": claim["text"]})
            candidates.append(
                {"id": concept_alias, "label": concept["label"], "claims": claims}
            )
        request = {
            "schema": RESOLUTION_REQUEST_SCHEMA,
            "group_id": f"g{group_index}",
            "candidates": candidates,
        }
        requests.append((request, concept_aliases, claim_aliases))
    return requests


def validate_resolution(
    candidate: Any,
    *,
    request: dict[str, Any],
    concept_aliases: dict[str, str],
    claim_aliases: dict[str, str],
    source_concepts: list[dict[str, Any]],
) -> dict[str, Any]:
    """每個 source 恰好一次，且 Formal node 只能重組既有 claims。"""

    if (
        not isinstance(candidate, dict)
        or set(candidate) != {"schema", "group_id", "resolutions"}
        or candidate["schema"] != RESOLUTION_OUTPUT_SCHEMA
        or candidate["group_id"] != request.get("group_id")
        or not isinstance(candidate["resolutions"], list)
    ):
        raise FormalConceptError("RESOLUTION_SCHEMA_INVALID")
    source_by_id = {concept["concept_id"]: concept for concept in source_concepts}
    expected_sources = set(concept_aliases)
    seen_sources: set[str] = set()
    used_claims: set[str] = set()
    formal_concepts = []
    for resolution_index, resolution in enumerate(candidate["resolutions"]):
        if (
            not isinstance(resolution, dict)
            or set(resolution) != {"operation", "source_ids", "nodes"}
            or resolution["operation"] not in OPERATIONS
            or not isinstance(resolution["source_ids"], list)
            or not resolution["source_ids"]
            or len(resolution["source_ids"]) != len(set(resolution["source_ids"]))
            or not set(resolution["source_ids"]) <= expected_sources
            or not isinstance(resolution["nodes"], list)
        ):
            raise FormalConceptError("RESOLUTION_SCHEMA_INVALID")
        if seen_sources & set(resolution["source_ids"]):
            raise FormalConceptError("RESOLUTION_SOURCE_DUPLICATE")
        seen_sources.update(resolution["source_ids"])
        operation = resolution["operation"]
        node_count = len(resolution["nodes"])
        if (
            (operation in {"KEEP", "RENAME"} and (len(resolution["source_ids"]), node_count) != (1, 1))
            or (operation == "MERGE" and (len(resolution["source_ids"]) < 2 or node_count != 1))
            or (operation == "SPLIT" and (len(resolution["source_ids"]), node_count) != (1, 2))
            or (operation == "DROP" and (len(resolution["source_ids"]), node_count) != (1, 0))
        ):
            raise FormalConceptError("RESOLUTION_SHAPE_INVALID")

        source_ids = sorted(concept_aliases[alias] for alias in resolution["source_ids"])
        available_claims = {
            claim["claim_id"]
            for source_id in source_ids
            for claim in [
                source_by_id[source_id]["definition"],
                *source_by_id[source_id]["key_points"],
            ]
        }
        resolution_claims: set[str] = set()
        for node_index, node in enumerate(resolution["nodes"]):
            if (
                not isinstance(node, dict)
                or set(node) != {"label", "claim_ids"}
                or not isinstance(node["label"], str)
                or not node["label"].strip()
                or not isinstance(node["claim_ids"], list)
                or not node["claim_ids"]
                or len(node["claim_ids"]) != len(set(node["claim_ids"]))
                or not set(node["claim_ids"]) <= set(claim_aliases)
            ):
                raise FormalConceptError("RESOLUTION_SHAPE_INVALID")
            claim_ids = [claim_aliases[alias] for alias in node["claim_ids"]]
            if not set(claim_ids) <= available_claims:
                raise FormalConceptError("RESOLUTION_CLAIM_INVALID")
            if resolution_claims & set(claim_ids) or used_claims & set(claim_ids):
                raise FormalConceptError("RESOLUTION_CLAIM_DUPLICATE")
            resolution_claims.update(claim_ids)
            source_claims = {
                claim["claim_id"]: claim
                for source_id in source_ids
                for claim in [
                    source_by_id[source_id]["definition"],
                    *source_by_id[source_id]["key_points"],
                ]
            }
            ordered_claims = [
                deepcopy(source_claims[claim_id])
                for claim_id in source_claims
                if claim_id in claim_ids
            ]
            label = " ".join(unicodedata.normalize("NFKC", node["label"]).split())
            source_label = " ".join(
                unicodedata.normalize("NFKC", source_by_id[source_ids[0]]["label"]).split()
            )
            if (
                operation == "KEEP" and label != source_label
                or operation == "RENAME" and label == source_label
            ):
                raise FormalConceptError("RESOLUTION_SHAPE_INVALID")
            identity = {
                "group_id": request["group_id"],
                "operation": operation,
                "source_concept_ids": source_ids,
                "label": label,
                "claims": ordered_claims,
            }
            formal_concepts.append(
                {
                    "formal_concept_id": "formal-concept:sha256:" + canonical_sha256(identity),
                    **identity,
                    "source_page_refs": sorted(
                        {source_by_id[source_id]["page_ref"] for source_id in source_ids}
                    ),
                    "quality": "needs_review",
                    "decision": "review",
                    "reason_codes": ["FORMAL_CONCEPT_REVIEW_REQUIRED"],
                    "resolution_order": [resolution_index, node_index],
                }
            )
        if operation != "DROP" and resolution_claims != available_claims:
            raise FormalConceptError("RESOLUTION_CLAIM_MISSING")
        used_claims.update(resolution_claims)
    if seen_sources != expected_sources:
        raise FormalConceptError("RESOLUTION_SOURCE_MISSING")
    formal_concepts.sort(
        key=lambda concept: (
            min(concept["source_concept_ids"]),
            concept["formal_concept_id"],
        )
    )
    for resolution_order, concept in enumerate(formal_concepts):
        concept["resolution_order"] = [resolution_order, 0]
    return {
        "schema": RESOLUTION_OUTPUT_SCHEMA,
        "group_id": request["group_id"],
        "formal_concepts": formal_concepts,
        "dropped_source_concept_ids": sorted(
            concept_aliases[resolution["source_ids"][0]]
            for resolution in candidate["resolutions"]
            if resolution["operation"] == "DROP"
        ),
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["FORMAL_CONCEPT_REVIEW_REQUIRED"],
    }
