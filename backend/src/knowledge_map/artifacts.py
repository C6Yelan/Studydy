from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.artifact_reason_codes import reason_codes_are_valid
from pdf_evidence.study_material_output import validate_study_material_output


RELATION_TYPES = {
    "prerequisite", "contains", "similar", "confusing", "application", "example"
}
SYMMETRIC_RELATION_TYPES = {"similar", "confusing"}


KNOWLEDGE_MAP_SCHEMA = "knowledge-map/v3"
KNOWLEDGE_MAP_VIEW_SCHEMA = "knowledge-map-view/v3"


def _revision(document: dict[str, Any]) -> str:
    content = {key: value for key, value in document.items() if key != "revision"}
    return "knowledge-map:sha256:" + canonical_sha256(content)


def _cycle_relation_ids(relations: list[dict[str, Any]]) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    prerequisite = []
    for relation in relations:
        if relation["type"] == "prerequisite":
            source = relation["source_formal_concept_id"]
            target = relation["target_formal_concept_id"]
            adjacency.setdefault(source, set()).add(target)
            prerequisite.append(relation)

    def can_reach(start: str, target: str) -> bool:
        pending = [start]
        seen = set()
        while pending:
            node = pending.pop()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            pending.extend(adjacency.get(node, ()))
        return False

    return {
        relation["relation_id"]
        for relation in prerequisite
        if can_reach(
            relation["target_formal_concept_id"],
            relation["source_formal_concept_id"],
        )
    }


def _learning_path(
    formal_concepts: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> list[str]:
    nodes = {
        concept["formal_concept_id"]: concept
        for concept in formal_concepts
        if concept["decision"] != "reject"
    }
    outgoing = {node: set() for node in nodes}
    incoming = {node: 0 for node in nodes}
    for relation in relations:
        if relation["type"] != "prerequisite" or relation["is_in_prerequisite_cycle"]:
            continue
        source = relation["source_formal_concept_id"]
        target = relation["target_formal_concept_id"]
        if source in nodes and target in nodes and target not in outgoing[source]:
            outgoing[source].add(target)
            incoming[target] += 1

    def order(node_id: str) -> tuple[Any, ...]:
        concept = nodes[node_id]
        return (
            min(concept["source_page_numbers"]),
            concept["resolution_order"],
            node_id,
        )

    ready = sorted((node for node, count in incoming.items() if count == 0), key=order)
    path = []
    while ready:
        node = ready.pop(0)
        path.append(node)
        for target in sorted(outgoing[node], key=order):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort(key=order)
    if len(path) != len(nodes):
        raise ValueError("KNOWLEDGE_MAP_CYCLE_INVALID")
    return path


def build_knowledge_map(
    study_material_output: dict[str, Any],
    resolution_artifacts: list[dict[str, Any]],
    relation_artifacts: list[dict[str, Any]],
    *,
    relation_pair_status: dict[str, Any],
    material_runtime_binding_sha256: str,
) -> dict[str, Any]:
    """只使用通過 deterministic validation 的 Formal Concept 與 Relation。"""

    if validate_study_material_output(study_material_output) is not None:
        raise ValueError("KNOWLEDGE_MAP_SOURCE_INVALID")
    page_numbers = {
        page["page_ref"]: page["page_number"] for page in study_material_output["pages"]
    }
    formal_concepts = []
    for artifact in resolution_artifacts:
        for source in artifact.get("formal_concepts", []):
            concept = deepcopy(source)
            try:
                concept["source_page_numbers"] = sorted(
                    {page_numbers[page] for page in concept["source_page_refs"]}
                )
            except KeyError:
                raise ValueError("KNOWLEDGE_MAP_SOURCE_INVALID") from None
            formal_concepts.append(concept)
    formal_ids = [concept["formal_concept_id"] for concept in formal_concepts]
    if len(formal_ids) != len(set(formal_ids)):
        raise ValueError("KNOWLEDGE_MAP_CONCEPT_INVALID")

    relations = [
        deepcopy(relation)
        for artifact in relation_artifacts
        for relation in artifact.get("relations", [])
    ]
    relation_ids = [relation["relation_id"] for relation in relations]
    if len(relation_ids) != len(set(relation_ids)):
        raise ValueError("KNOWLEDGE_MAP_RELATION_INVALID")
    cycle_ids = _cycle_relation_ids(relations)
    for relation in relations:
        if (
            relation["source_formal_concept_id"] not in formal_ids
            or relation["target_formal_concept_id"] not in formal_ids
            or relation["source_formal_concept_id"] == relation["target_formal_concept_id"]
        ):
            raise ValueError("KNOWLEDGE_MAP_RELATION_INVALID")
        relation["is_in_prerequisite_cycle"] = relation["relation_id"] in cycle_ids
        if relation["is_in_prerequisite_cycle"]:
            relation["reason_codes"] = sorted(
                set(relation["reason_codes"]) | {"PREREQUISITE_CYCLE"}
            )

    formal_concepts.sort(
        key=lambda concept: (
            min(concept["source_page_numbers"]),
            concept["resolution_order"],
            concept["formal_concept_id"],
        )
    )
    relations.sort(key=lambda relation: relation["relation_id"])
    path = _learning_path(formal_concepts, relations)
    has_no_formal_concept = not formal_concepts
    is_partial = (
        has_no_formal_concept
        or study_material_output["processing"] == "partial"
        or any(artifact.get("processing") == "partial" for artifact in resolution_artifacts)
        or relation_pair_status.get("processing") == "partial"
        or any(artifact.get("processing") == "partial" for artifact in relation_artifacts)
    )
    reasons = {
        "KNOWLEDGE_MAP_REVIEW_REQUIRED",
        *study_material_output["reason_codes"],
        *relation_pair_status.get("reason_codes", []),
    }
    if cycle_ids:
        reasons.add("PREREQUISITE_CYCLE")
    if has_no_formal_concept:
        reasons.add("NO_FORMAL_CONCEPT")
    document = {
        "schema": KNOWLEDGE_MAP_SCHEMA,
        "source_output_id": study_material_output["output_id"],
        "source_binding": {
            "study_material_output_id": study_material_output["output_id"],
            "producer_output_id": study_material_output["source_binding"]["producer_output_id"],
            "producer_runtime_lock_sha256": study_material_output["source_binding"]["runtime_binding_sha256"],
            "material_runtime_binding_sha256": material_runtime_binding_sha256,
        },
        "material_ref": study_material_output["material_ref"],
        "formal_concepts": formal_concepts,
        "relations": relations,
        "initial_learning_path": path,
        "evidence_index": deepcopy(study_material_output["evidence_index"]),
        "excluded_pages": deepcopy(study_material_output["excluded_pages"]),
        "processing": "partial" if is_partial else "succeeded",
        "quality": "needs_review",
        "decision": "reject" if has_no_formal_concept else "review",
        "reason_codes": sorted(reasons),
    }
    document["revision"] = _revision(document)
    if validate_knowledge_map(document) is not None:
        raise ValueError("KNOWLEDGE_MAP_INVALID")
    return document


def validate_knowledge_map(knowledge_map: Any) -> str | None:
    fields = {
        "schema", "source_output_id", "source_binding", "material_ref", "formal_concepts", "relations",
        "initial_learning_path", "evidence_index", "excluded_pages", "processing",
        "quality", "decision", "reason_codes", "revision",
    }
    try:
        if (
            not isinstance(knowledge_map, dict)
            or set(knowledge_map) != fields
            or knowledge_map["schema"] != KNOWLEDGE_MAP_SCHEMA
            or knowledge_map["revision"] != _revision(knowledge_map)
            or set(knowledge_map["source_binding"]) != {
                "study_material_output_id", "producer_output_id",
                "producer_runtime_lock_sha256", "material_runtime_binding_sha256"
            }
            or knowledge_map["processing"] not in {"succeeded", "partial"}
            or knowledge_map["quality"] != "needs_review"
            or knowledge_map["decision"] not in {"review", "reject"}
            or knowledge_map["source_output_id"]
            != knowledge_map["source_binding"]["study_material_output_id"]
            or any(
                not isinstance(knowledge_map["source_binding"][field], str)
                or len(knowledge_map["source_binding"][field]) != 64
                or any(character not in "0123456789abcdef" for character in knowledge_map["source_binding"][field])
                for field in (
                    "producer_runtime_lock_sha256",
                    "material_runtime_binding_sha256",
                )
            )
            or not reason_codes_are_valid(knowledge_map["reason_codes"], formal=True)
            or knowledge_map["reason_codes"] != sorted(set(knowledge_map["reason_codes"]))
        ):
            return "KNOWLEDGE_MAP_INVALID"
        formal = knowledge_map["formal_concepts"]
        formal_ids = {concept["formal_concept_id"] for concept in formal}
        if len(formal_ids) != len(formal):
            return "KNOWLEDGE_MAP_INVALID"
        evidence_pages: dict[str, str] = {}
        page_numbers: dict[str, int] = {}
        for evidence in knowledge_map["evidence_index"]:
            region = evidence.get("region") if isinstance(evidence, dict) else None
            bbox = region.get("bbox") if isinstance(region, dict) else None
            if (
                not isinstance(evidence, dict)
                or set(evidence) != {
                    "evidence_id", "page_ref", "page_number", "kind", "region"
                }
                or evidence["evidence_id"] in evidence_pages
                or not isinstance(evidence["evidence_id"], str)
                or not isinstance(evidence["page_ref"], str)
                or type(evidence["page_number"]) is not int
                or evidence["page_number"] < 1
                or not isinstance(evidence["kind"], str)
                or not evidence["kind"]
                or not isinstance(region, dict)
                or set(region) != {"coordinate_space", "bbox"}
                or region["coordinate_space"] != "unrotated_pdf_points"
                or not isinstance(bbox, list)
                or len(bbox) != 4
                or any(
                    type(number) not in {int, float} or not math.isfinite(number)
                    for number in bbox
                )
                or bbox[0] >= bbox[2]
                or bbox[1] >= bbox[3]
                or (
                    evidence["page_ref"] in page_numbers
                    and page_numbers[evidence["page_ref"]] != evidence["page_number"]
                )
            ):
                return "KNOWLEDGE_MAP_INVALID"
            evidence_pages[evidence["evidence_id"]] = evidence["page_ref"]
            page_numbers[evidence["page_ref"]] = evidence["page_number"]
        formal_evidence: dict[str, set[str]] = {}
        formal_claim_ids: set[str] = set()
        for concept in formal:
            claims = concept.get("claims") if isinstance(concept, dict) else None
            if (
                set(concept) != {
                    "formal_concept_id", "group_id", "operation", "source_concept_ids",
                    "label", "claims", "source_page_refs", "source_page_numbers",
                    "quality", "decision", "reason_codes", "resolution_order",
                }
                or not concept["source_concept_ids"]
                or len(concept["source_concept_ids"])
                != len(set(concept["source_concept_ids"]))
                or not concept["source_page_refs"]
                or len(concept["source_page_refs"])
                != len(set(concept["source_page_refs"]))
                or concept["operation"] not in {"KEEP", "MERGE", "RENAME", "SPLIT"}
                or (
                    concept["operation"] == "MERGE"
                    and len(concept["source_concept_ids"]) < 2
                )
                or (
                    concept["operation"] in {"KEEP", "RENAME", "SPLIT"}
                    and len(concept["source_concept_ids"]) != 1
                )
                or not isinstance(concept["label"], str)
                or not concept["label"]
                or concept["quality"] != "needs_review"
                or concept["decision"] != "review"
                or not reason_codes_are_valid(concept["reason_codes"], formal=True)
                or concept["reason_codes"] != sorted(set(concept["reason_codes"]))
                or not isinstance(concept["resolution_order"], list)
                or len(concept["resolution_order"]) != 2
                or any(type(item) is not int or item < 0 for item in concept["resolution_order"])
                or not claims
                or any(
                    set(claim) != {"claim_id", "text", "evidence_ids"}
                    or not isinstance(claim["text"], str)
                    or not claim["text"]
                    or not isinstance(claim["evidence_ids"], list)
                    or not claim["evidence_ids"]
                    or len(claim["evidence_ids"]) != len(set(claim["evidence_ids"]))
                    for claim in claims
                )
                or any(
                evidence_pages.get(evidence_id) not in concept["source_page_refs"]
                for claim in claims
                for evidence_id in claim["evidence_ids"]
                )
                or concept["source_page_numbers"]
                != sorted({page_numbers[page] for page in concept["source_page_refs"]})
                or concept["formal_concept_id"] != "formal-concept:sha256:" + canonical_sha256(
                    {
                        "group_id": concept["group_id"],
                        "operation": concept["operation"],
                        "source_concept_ids": concept["source_concept_ids"],
                        "label": concept["label"],
                        "claims": claims,
                    }
                )
            ):
                return "KNOWLEDGE_MAP_INVALID"
            concept_claim_ids = {claim["claim_id"] for claim in claims}
            if (
                len(concept_claim_ids) != len(claims)
                or formal_claim_ids & concept_claim_ids
            ):
                return "KNOWLEDGE_MAP_INVALID"
            formal_claim_ids.update(concept_claim_ids)
            formal_evidence[concept["formal_concept_id"]] = {
                evidence_id
                for claim in claims
                for evidence_id in claim["evidence_ids"]
            }
        relation_ids = set()
        relation_keys: set[tuple[str, str, str]] = set()
        directed_pairs: set[tuple[str, str]] = set()
        for relation in knowledge_map["relations"]:
            identity = {
                "type": relation.get("type"),
                "source_formal_concept_id": relation.get("source_formal_concept_id"),
                "target_formal_concept_id": relation.get("target_formal_concept_id"),
                "source_evidence_ids": relation.get("source_evidence_ids"),
                "target_evidence_ids": relation.get("target_evidence_ids"),
            }
            relation_key = (
                relation.get("type"),
                relation.get("source_formal_concept_id"),
                relation.get("target_formal_concept_id"),
            )
            if (
                set(relation) != {
                    "relation_id", "type", "source_formal_concept_id",
                    "target_formal_concept_id", "source_evidence_ids",
                    "target_evidence_ids", "quality", "decision", "reason_codes",
                    "is_in_prerequisite_cycle",
                }
                or relation["relation_id"] in relation_ids
                or relation["source_formal_concept_id"] not in formal_ids
                or relation["target_formal_concept_id"] not in formal_ids
                or relation["source_formal_concept_id"] == relation["target_formal_concept_id"]
                or relation["type"] not in RELATION_TYPES
                or relation["quality"] != "needs_review"
                or relation["decision"] != "review"
                or not reason_codes_are_valid(relation["reason_codes"], formal=True)
                or relation["reason_codes"] != sorted(set(relation["reason_codes"]))
                or type(relation["is_in_prerequisite_cycle"]) is not bool
                or relation["relation_id"]
                != "formal-relation:sha256:" + canonical_sha256(identity)
                or relation_key in relation_keys
                or not relation["source_evidence_ids"]
                or not relation["target_evidence_ids"]
                or len(relation["source_evidence_ids"])
                != len(set(relation["source_evidence_ids"]))
                or len(relation["target_evidence_ids"])
                != len(set(relation["target_evidence_ids"]))
                or not set(relation["source_evidence_ids"])
                <= formal_evidence[relation["source_formal_concept_id"]]
                or not set(relation["target_evidence_ids"])
                <= formal_evidence[relation["target_formal_concept_id"]]
                or (
                    relation["type"] in SYMMETRIC_RELATION_TYPES
                    and relation["target_formal_concept_id"]
                    < relation["source_formal_concept_id"]
                )
                or (
                    relation["type"] not in SYMMETRIC_RELATION_TYPES
                    and (
                        relation["target_formal_concept_id"],
                        relation["source_formal_concept_id"],
                    ) in directed_pairs
                )
            ):
                return "KNOWLEDGE_MAP_INVALID"
            relation_ids.add(relation["relation_id"])
            relation_keys.add(relation_key)
            directed_pairs.add(
                (
                    relation["source_formal_concept_id"],
                    relation["target_formal_concept_id"],
                )
            )
        if (knowledge_map["decision"] == "reject") != (not formal):
            return "KNOWLEDGE_MAP_INVALID"
        if set(knowledge_map["initial_learning_path"]) != formal_ids:
            return "KNOWLEDGE_MAP_INVALID"
        expected_cycles = _cycle_relation_ids(knowledge_map["relations"])
        if any(
            relation["is_in_prerequisite_cycle"]
            != (relation["relation_id"] in expected_cycles)
            for relation in knowledge_map["relations"]
        ):
            return "KNOWLEDGE_MAP_INVALID"
        if knowledge_map["initial_learning_path"] != _learning_path(formal, knowledge_map["relations"]):
            return "KNOWLEDGE_MAP_INVALID"
    except (KeyError, TypeError, ValueError):
        return "KNOWLEDGE_MAP_INVALID"
    return None


def build_knowledge_map_view(knowledge_map: dict[str, Any]) -> dict[str, Any]:
    """公開 view 只提供 claim locator，不含教材全文或 runtime 設定。"""

    if validate_knowledge_map(knowledge_map) is not None:
        raise ValueError("KNOWLEDGE_MAP_INVALID")
    evidence_by_id = {
        evidence["evidence_id"]: evidence for evidence in knowledge_map["evidence_index"]
    }
    concepts = []
    for concept in knowledge_map["formal_concepts"]:
        claims = []
        for claim in concept["claims"]:
            claims.append(
                {
                    "claim_id": claim["claim_id"],
                    "text": claim["text"],
                    "evidence": [deepcopy(evidence_by_id[item]) for item in claim["evidence_ids"]],
                }
            )
        concepts.append(
            {
                "formal_concept_id": concept["formal_concept_id"],
                "label": concept["label"],
                "claims": claims,
                "source_concept_ids": deepcopy(concept["source_concept_ids"]),
                "source_page_numbers": deepcopy(concept["source_page_numbers"]),
                "quality": concept["quality"],
                "decision": concept["decision"],
                "reason_codes": deepcopy(concept["reason_codes"]),
            }
        )
    return {
        "schema": KNOWLEDGE_MAP_VIEW_SCHEMA,
        "material_ref": knowledge_map["material_ref"],
        "knowledge_map_revision": knowledge_map["revision"],
        "source_output_id": knowledge_map["source_output_id"],
        "status": {
            "processing": knowledge_map["processing"],
            "quality": knowledge_map["quality"],
            "decision": knowledge_map["decision"],
            "reason_codes": deepcopy(knowledge_map["reason_codes"]),
        },
        "concepts": concepts,
        "relations": deepcopy(knowledge_map["relations"]),
        "initial_learning_path": deepcopy(knowledge_map["initial_learning_path"]),
        "excluded_pages": deepcopy(knowledge_map["excluded_pages"]),
    }
