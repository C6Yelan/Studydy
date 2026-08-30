from __future__ import annotations

from copy import deepcopy
import math
import unicodedata
from typing import Any

from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.artifact_reason_codes import reason_codes_are_valid
from pdf_evidence.study_material_output import validate_study_material_output
from learning_resources.map_resources import MATCHING_POLICY, PROMOTION_POLICY


RELATION_TYPES = {"prerequisite", "contains", "related"}
SYMMETRIC_RELATION_TYPES = {"related"}


KNOWLEDGE_MAP_SCHEMA = "knowledge-map/v7"
KNOWLEDGE_MAP_VIEW_SCHEMA = "knowledge-map-view/v7"

_RESOURCE_DIAGNOSTIC_FIELDS = {
    "matches",
    "promoted_matches",
    "promoted_resources",
    "dropped_matches",
    "split_review_matches",
}
_SUPPLEMENTARY_RESOURCE_FIELDS = {
    "promotion_id",
    "resource_concept_id",
    "resource_id",
    "label",
    "title",
    "authors",
    "source_url",
    "citation",
    "license",
    "license_url",
    "use_boundary",
    "page_numbers",
    "resource_evidence_ids",
    "match_ids",
    "study_concept_ids",
    "match_reason",
}

_RELATION_DIAGNOSTIC_FIELDS = {
    "possible_pairs",
    "candidate_pairs",
    "selected_pairs",
    "selected_signal_counts",
    "evidence_gated_pairs",
    "rejected_no_evidence",
    "direction_conflicts",
    "verifier_calls",
    "verifier_accepted",
    "verifier_rejected",
    "verifier_unsupported",
    "structural_proposals",
    "contains_proposals",
    "prerequisite_proposals",
    "related_proposals",
    "accepted_relations",
}
_CONCEPT_DIAGNOSTIC_FIELDS = {
    "possible_pairs", "candidate_pairs", "selected_pairs", "pair_ceiling",
    "qwen_same_pairs", "qwen_distinct_pairs", "qwen_uncertain_pairs",
    "verifier_requested_pairs", "verifier_scored_pairs",
    "verifier_allowed_pairs", "verifier_vetoed_pairs",
    "verifier_unsupported_pairs", "verifier_failed_pairs",
    "source_concepts_before", "canonical_concepts_after", "duplicate_delta",
    "coverage_before", "coverage_after",
}


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


def _relation_diagnostics(
    relation_pair_status: dict[str, Any],
    relation_artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    selector = relation_pair_status.get("diagnostics", {})
    diagnostics = {
        "possible_pairs": selector.get("possible_pairs", 0),
        "candidate_pairs": selector.get("candidate_pairs", 0),
        "selected_pairs": selector.get("selected_pairs", 0),
        "selected_signal_counts": deepcopy(
            selector.get("selected_signal_counts", {})
        ),
    }
    for field in _RELATION_DIAGNOSTIC_FIELDS - set(diagnostics):
        diagnostics[field] = sum(
            artifact.get("diagnostics", {}).get(field, 0)
            for artifact in relation_artifacts
        )
    return diagnostics


def _concept_diagnostics(
    resolution_artifacts: list[dict[str, Any]],
) -> dict[str, int]:
    diagnostics = {field: 0 for field in _CONCEPT_DIAGNOSTIC_FIELDS}
    for artifact in resolution_artifacts:
        source = artifact.get("diagnostics", {})
        for field in _CONCEPT_DIAGNOSTIC_FIELDS:
            value = source.get(field, 0)
            if type(value) is not int or value < 0:
                raise ValueError("KNOWLEDGE_MAP_CONCEPT_INVALID")
            if field == "pair_ceiling":
                diagnostics[field] = max(diagnostics[field], value)
            else:
                diagnostics[field] += value
    return diagnostics


def build_knowledge_map(
    study_material_output: dict[str, Any],
    resolution_artifacts: list[dict[str, Any]],
    relation_artifacts: list[dict[str, Any]],
    *,
    relation_pair_status: dict[str, Any],
    resource_promotion: dict[str, Any],
    material_runtime_binding_sha256: str,
) -> dict[str, Any]:
    """只使用通過 deterministic validation 的 Formal Concept 與 Relation。"""

    if validate_study_material_output(study_material_output) is not None:
        raise ValueError("KNOWLEDGE_MAP_SOURCE_INVALID")
    page_numbers = {
        page["page_ref"]: page["page_number"] for page in study_material_output["pages"]
    }
    resolved_formal_concepts = []
    for artifact in resolution_artifacts:
        for source in artifact.get("formal_concepts", []):
            resolved_formal_concepts.append(deepcopy(source))
    source_concept_ids = {
        concept["concept_id"] for concept in study_material_output["concepts"]
    }
    covered_source_ids = [
        source_id
        for concept in resolved_formal_concepts
        for source_id in concept.get("source_concept_ids", [])
    ]
    if (
        set(covered_source_ids) != source_concept_ids
        or len(covered_source_ids) != len(set(covered_source_ids))
    ):
        raise ValueError("KNOWLEDGE_MAP_CONCEPT_INVALID")
    contexts_by_page = {
        context["page_ref"]: context
        for context in study_material_output["document_contexts"]
    }
    expected_members = {}
    for source in study_material_output["concepts"]:
        claims = [source["definition"], *source["key_points"]]
        context = contexts_by_page[source["page_ref"]]
        expected_members[source["concept_id"]] = {
            "source_concept_id": source["concept_id"],
            "label": " ".join(
                unicodedata.normalize("NFKC", source["label"]).split()
            ),
            "claim_ids": sorted(claim["claim_id"] for claim in claims),
            "evidence_ids": sorted({
                evidence_id
                for claim in claims
                for evidence_id in claim["evidence_ids"]
            }),
            "page_ref": source["page_ref"],
            "document_context_id": context["context_id"],
            "section_ids": sorted(context["section_ids"]),
        }
    actual_members = {
        member.get("source_concept_id"): member
        for concept in resolved_formal_concepts
        for member in concept.get("source_members", [])
        if isinstance(member, dict)
    }
    if actual_members != expected_members:
        raise ValueError("KNOWLEDGE_MAP_CONCEPT_INVALID")
    if (
        not isinstance(resource_promotion, dict)
        or set(resource_promotion) != {
            "formal_concepts", "resource_binding", "resource_diagnostics",
            "resource_decisions",
        }
        or not isinstance(resource_promotion["formal_concepts"], list)
        or [
            {key: value for key, value in concept.items() if key != "supplementary_resources"}
            for concept in resource_promotion["formal_concepts"]
        ]
        != resolved_formal_concepts
    ):
        raise ValueError("KNOWLEDGE_MAP_RESOURCE_INVALID")
    formal_concepts = deepcopy(resource_promotion["formal_concepts"])
    for concept in formal_concepts:
        try:
            concept["source_page_numbers"] = sorted(
                {page_numbers[page] for page in concept["source_page_refs"]}
            )
        except KeyError:
            raise ValueError("KNOWLEDGE_MAP_SOURCE_INVALID") from None
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
        or resource_promotion["resource_diagnostics"]["split_review_matches"] > 0
    )
    reasons = {
        "KNOWLEDGE_MAP_REVIEW_REQUIRED",
        *study_material_output["reason_codes"],
        *(
            reason
            for artifact in resolution_artifacts
            for reason in artifact.get("reason_codes", [])
        ),
        *relation_pair_status.get("reason_codes", []),
        *(
            reason
            for artifact in relation_artifacts
            for reason in artifact.get("reason_codes", [])
        ),
    }
    if cycle_ids:
        reasons.add("PREREQUISITE_CYCLE")
    if has_no_formal_concept:
        reasons.add("NO_FORMAL_CONCEPT")
    if resource_promotion["resource_diagnostics"]["split_review_matches"]:
        reasons.add("RESOURCE_SPLIT_REVIEW_REQUIRED")
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
        "concept_diagnostics": _concept_diagnostics(resolution_artifacts),
        "relations": relations,
        "relation_diagnostics": _relation_diagnostics(
            relation_pair_status, relation_artifacts
        ),
        "resource_binding": deepcopy(resource_promotion["resource_binding"]),
        "resource_diagnostics": deepcopy(resource_promotion["resource_diagnostics"]),
        "resource_decisions": deepcopy(resource_promotion["resource_decisions"]),
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
        "schema", "source_output_id", "source_binding", "material_ref", "formal_concepts", "concept_diagnostics", "relations", "relation_diagnostics",
        "resource_binding", "resource_diagnostics", "resource_decisions",
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
        diagnostics = knowledge_map["relation_diagnostics"]
        if (
            not isinstance(diagnostics, dict)
            or set(diagnostics) != _RELATION_DIAGNOSTIC_FIELDS
            or any(
                type(diagnostics[field]) is not int or diagnostics[field] < 0
                for field in _RELATION_DIAGNOSTIC_FIELDS
                - {"selected_signal_counts"}
            )
            or not isinstance(diagnostics["selected_signal_counts"], dict)
            or any(
                signal
                not in {
                    "adjacent",
                    "same_group",
                    "same_page",
                    "explicit_relation",
                    "cross_reference",
                    "label_mention",
                    "shared_evidence",
                    "shared_formula",
                }
                or type(count) is not int
                or count < 0
                for signal, count in diagnostics["selected_signal_counts"].items()
            )
            or diagnostics["selected_pairs"] > diagnostics["candidate_pairs"]
            or diagnostics["candidate_pairs"] > diagnostics["possible_pairs"]
            or diagnostics["verifier_accepted"] + diagnostics["verifier_rejected"]
            > diagnostics["verifier_calls"]
            or diagnostics["structural_proposals"]
            != diagnostics["contains_proposals"]
            + diagnostics["prerequisite_proposals"]
        ):
            return "KNOWLEDGE_MAP_INVALID"
        concept_diagnostics = knowledge_map["concept_diagnostics"]
        if (
            not isinstance(concept_diagnostics, dict)
            or set(concept_diagnostics) != _CONCEPT_DIAGNOSTIC_FIELDS
            or any(
                type(value) is not int or value < 0
                for value in concept_diagnostics.values()
            )
            or concept_diagnostics["selected_pairs"]
            > concept_diagnostics["candidate_pairs"]
            or concept_diagnostics["candidate_pairs"]
            > concept_diagnostics["possible_pairs"]
            or concept_diagnostics["selected_pairs"]
            != concept_diagnostics["qwen_same_pairs"]
            + concept_diagnostics["qwen_distinct_pairs"]
            + concept_diagnostics["qwen_uncertain_pairs"]
            or concept_diagnostics["verifier_requested_pairs"]
            != concept_diagnostics["qwen_same_pairs"]
            or concept_diagnostics["verifier_requested_pairs"]
            != concept_diagnostics["verifier_scored_pairs"]
            + concept_diagnostics["verifier_unsupported_pairs"]
            + concept_diagnostics["verifier_failed_pairs"]
            or concept_diagnostics["verifier_scored_pairs"]
            != concept_diagnostics["verifier_allowed_pairs"]
            + concept_diagnostics["verifier_vetoed_pairs"]
            or concept_diagnostics["duplicate_delta"]
            != concept_diagnostics["source_concepts_before"]
            - concept_diagnostics["canonical_concepts_after"]
            or concept_diagnostics["coverage_before"]
            != concept_diagnostics["coverage_after"]
        ):
            return "KNOWLEDGE_MAP_INVALID"
        resource_binding = knowledge_map["resource_binding"]
        resource_diagnostics = knowledge_map["resource_diagnostics"]
        resource_decisions = knowledge_map["resource_decisions"]
        if (
            not isinstance(resource_binding, dict)
            or set(resource_binding) != {
                "context_revision", "library_revision", "matching_policy", "promotion_policy"
            }
            or not isinstance(resource_binding["context_revision"], str)
            or not resource_binding["context_revision"].startswith("map-resource-context:sha256:")
            or not isinstance(resource_binding["library_revision"], str)
            or not resource_binding["library_revision"].startswith("resource-library:sha256:")
            or resource_binding["matching_policy"] != MATCHING_POLICY
            or resource_binding["promotion_policy"] != PROMOTION_POLICY
            or not isinstance(resource_diagnostics, dict)
            or set(resource_diagnostics) != _RESOURCE_DIAGNOSTIC_FIELDS
            or any(type(value) is not int or value < 0 for value in resource_diagnostics.values())
            or resource_diagnostics["matches"]
            != resource_diagnostics["promoted_matches"]
            + resource_diagnostics["dropped_matches"]
            + resource_diagnostics["split_review_matches"]
            or not isinstance(resource_decisions, list)
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
        claims_by_formal: dict[str, dict[str, dict[str, Any]]] = {}
        formal_claims: dict[str, dict[str, Any]] = {}
        promoted_match_ids: set[str] = set()
        promoted_resource_count = 0
        for concept in formal:
            claims = concept.get("claims") if isinstance(concept, dict) else None
            if (
                set(concept) != {
                    "formal_concept_id", "group_id", "operation", "source_concept_ids",
                    "label", "aliases", "claims", "source_members",
                    "source_page_refs", "source_page_numbers",
                    "quality", "decision", "reason_codes", "resolution_order",
                    "supplementary_resources",
                }
                or not concept["source_concept_ids"]
                or len(concept["source_concept_ids"])
                != len(set(concept["source_concept_ids"]))
                or not concept["source_page_refs"]
                or len(concept["source_page_refs"])
                != len(set(concept["source_page_refs"]))
                or concept["operation"] not in {"KEEP", "MERGE"}
                or (
                    concept["operation"] == "MERGE"
                    and len(concept["source_concept_ids"]) < 2
                )
                or (
                    concept["operation"] == "KEEP"
                    and len(concept["source_concept_ids"]) != 1
                )
                or not isinstance(concept["label"], str)
                or not concept["label"]
                or not isinstance(concept["aliases"], list)
                or concept["aliases"] != sorted(set(concept["aliases"]))
                or concept["label"] in concept["aliases"]
                or any(not isinstance(alias, str) or not alias for alias in concept["aliases"])
                or concept["quality"] != "needs_review"
                or concept["decision"] != "review"
                or not reason_codes_are_valid(concept["reason_codes"], formal=True)
                or concept["reason_codes"] != sorted(set(concept["reason_codes"]))
                or not isinstance(concept["resolution_order"], list)
                or len(concept["resolution_order"]) != 2
                or any(type(item) is not int or item < 0 for item in concept["resolution_order"])
                or not claims
                or not isinstance(concept["supplementary_resources"], list)
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
                        "aliases": concept["aliases"],
                        "claims": claims,
                        "source_members": concept["source_members"],
                    }
                )
            ):
                return "KNOWLEDGE_MAP_INVALID"
            members = concept["source_members"]
            if (
                not isinstance(members, list)
                or not members
                or {member.get("source_concept_id") for member in members}
                != set(concept["source_concept_ids"])
                or len({member.get("source_concept_id") for member in members})
                != len(members)
                or any(
                    not isinstance(member, dict)
                    or set(member) != {
                        "source_concept_id", "label", "claim_ids", "evidence_ids",
                        "page_ref", "document_context_id", "section_ids",
                    }
                    or not isinstance(member["label"], str)
                    or not member["label"]
                    or member["label"] not in {concept["label"], *concept["aliases"]}
                    or member["claim_ids"] != sorted(set(member["claim_ids"]))
                    or not member["claim_ids"]
                    or member["evidence_ids"] != sorted(set(member["evidence_ids"]))
                    or not member["evidence_ids"]
                    or member["page_ref"] not in concept["source_page_refs"]
                    or not isinstance(member["document_context_id"], str)
                    or not member["document_context_id"].startswith("document-context:sha256:")
                    or member["section_ids"] != sorted(set(member["section_ids"]))
                    or not member["section_ids"]
                    for member in members
                )
                or {claim_id for member in members for claim_id in member["claim_ids"]}
                != {claim["claim_id"] for claim in claims}
                or any(
                    set(member["evidence_ids"])
                    != {
                        evidence_id
                        for claim in claims
                        if claim["claim_id"] in member["claim_ids"]
                        for evidence_id in claim["evidence_ids"]
                    }
                    or any(
                        evidence_pages.get(evidence_id) != member["page_ref"]
                        for evidence_id in member["evidence_ids"]
                    )
                    for member in members
                )
                or {member["page_ref"] for member in members}
                != set(concept["source_page_refs"])
            ):
                return "KNOWLEDGE_MAP_INVALID"
            resource_concept_ids: set[str] = set()
            for resource in concept["supplementary_resources"]:
                if (
                    not isinstance(resource, dict)
                    or set(resource) != _SUPPLEMENTARY_RESOURCE_FIELDS
                    or resource["promotion_id"]
                    != "resource-promotion:sha256:" + canonical_sha256(
                        {key: value for key, value in resource.items() if key != "promotion_id"}
                    )
                    or not isinstance(resource["resource_concept_id"], str)
                    or not resource["resource_concept_id"].startswith("resource-concept:sha256:")
                    or resource["resource_concept_id"] in resource_concept_ids
                    or not isinstance(resource["resource_id"], str)
                    or not resource["resource_id"].startswith("resource:sha256:")
                    or any(
                        not isinstance(resource[field], str) or not resource[field]
                        for field in (
                            "label", "title", "source_url", "citation", "license",
                            "license_url", "use_boundary"
                        )
                    )
                    or not isinstance(resource["authors"], list)
                    or not resource["authors"]
                    or any(not isinstance(author, str) or not author for author in resource["authors"])
                    or not isinstance(resource["page_numbers"], list)
                    or not resource["page_numbers"]
                    or resource["page_numbers"] != sorted(set(resource["page_numbers"]))
                    or any(type(page) is not int or page < 1 for page in resource["page_numbers"])
                    or not isinstance(resource["resource_evidence_ids"], list)
                    or not resource["resource_evidence_ids"]
                    or resource["resource_evidence_ids"] != sorted(set(resource["resource_evidence_ids"]))
                    or any(
                        not isinstance(item, str)
                        or not item.startswith("resource-evidence:sha256:")
                        for item in resource["resource_evidence_ids"]
                    )
                    or not isinstance(resource["match_ids"], list)
                    or not resource["match_ids"]
                    or resource["match_ids"] != sorted(set(resource["match_ids"]))
                    or any(
                        not isinstance(item, str)
                        or not item.startswith("resource-match:sha256:")
                        or item in promoted_match_ids
                        for item in resource["match_ids"]
                    )
                    or not isinstance(resource["study_concept_ids"], list)
                    or not resource["study_concept_ids"]
                    or resource["study_concept_ids"] != sorted(set(resource["study_concept_ids"]))
                    or not set(resource["study_concept_ids"]) <= set(concept["source_concept_ids"])
                    or resource["match_reason"] != "EXACT_NORMALIZED_LABEL"
                ):
                    return "KNOWLEDGE_MAP_INVALID"
                resource_concept_ids.add(resource["resource_concept_id"])
                promoted_match_ids.update(resource["match_ids"])
                promoted_resource_count += 1
            concept_claim_ids = {claim["claim_id"] for claim in claims}
            if len(concept_claim_ids) != len(claims):
                return "KNOWLEDGE_MAP_INVALID"
            for claim in claims:
                known_claim = formal_claims.get(claim["claim_id"])
                if known_claim is not None and known_claim != claim:
                    return "KNOWLEDGE_MAP_INVALID"
                formal_claims[claim["claim_id"]] = claim
            claims_by_formal[concept["formal_concept_id"]] = {
                claim["claim_id"]: claim for claim in claims
            }
        decision_match_ids: set[str] = set()
        split_reviews = 0
        dropped = 0
        for item in resource_decisions:
            if (
                not isinstance(item, dict)
                or set(item) != {
                    "decision_id",
                    "match_id", "study_concept_id", "resource_concept_id",
                    "formal_concept_ids", "decision", "reason_code",
                }
                or item["decision_id"]
                != "resource-promotion-decision:sha256:" + canonical_sha256(
                    {key: value for key, value in item.items() if key != "decision_id"}
                )
                or not isinstance(item["match_id"], str)
                or not item["match_id"].startswith("resource-match:sha256:")
                or item["match_id"] in promoted_match_ids
                or item["match_id"] in decision_match_ids
                or not isinstance(item["study_concept_id"], str)
                or not isinstance(item["resource_concept_id"], str)
                or not item["resource_concept_id"].startswith("resource-concept:sha256:")
                or not isinstance(item["formal_concept_ids"], list)
                or item["formal_concept_ids"] != sorted(set(item["formal_concept_ids"]))
                or not set(item["formal_concept_ids"]) <= formal_ids
            ):
                return "KNOWLEDGE_MAP_INVALID"
            if item["decision"] == "reject":
                if item["reason_code"] != "RESOURCE_SOURCE_CONCEPT_DROPPED" or item["formal_concept_ids"]:
                    return "KNOWLEDGE_MAP_INVALID"
                dropped += 1
            elif item["decision"] == "review":
                if (
                    item["reason_code"] != "RESOURCE_SPLIT_REVIEW_REQUIRED"
                    or len(item["formal_concept_ids"]) < 2
                ):
                    return "KNOWLEDGE_MAP_INVALID"
                split_reviews += 1
            else:
                return "KNOWLEDGE_MAP_INVALID"
            decision_match_ids.add(item["match_id"])
        if (
            len(promoted_match_ids) != resource_diagnostics["promoted_matches"]
            or promoted_resource_count != resource_diagnostics["promoted_resources"]
            or dropped != resource_diagnostics["dropped_matches"]
            or split_reviews != resource_diagnostics["split_review_matches"]
            or len(promoted_match_ids | decision_match_ids) != resource_diagnostics["matches"]
            or (split_reviews > 0) != ("RESOURCE_SPLIT_REVIEW_REQUIRED" in knowledge_map["reason_codes"])
            or (split_reviews > 0 and knowledge_map["processing"] != "partial")
        ):
            return "KNOWLEDGE_MAP_INVALID"
        relation_ids = set()
        relation_keys: set[tuple[str, str, str]] = set()
        directed_pairs: set[tuple[str, str]] = set()
        for relation in knowledge_map["relations"]:
            identity = {
                "type": relation.get("type"),
                "source_formal_concept_id": relation.get("source_formal_concept_id"),
                "target_formal_concept_id": relation.get("target_formal_concept_id"),
                "relation_evidence": relation.get("relation_evidence"),
            }
            relation_key = (
                relation.get("type"),
                relation.get("source_formal_concept_id"),
                relation.get("target_formal_concept_id"),
            )
            if (
                set(relation) != {
                    "relation_id", "type", "source_formal_concept_id",
                    "target_formal_concept_id", "relation_evidence",
                    "quality", "decision", "reason_codes",
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
                or not isinstance(relation["relation_evidence"], list)
                or not relation["relation_evidence"]
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
            relation_evidence_keys = []
            for item in relation["relation_evidence"]:
                if (
                    not isinstance(item, dict)
                    or set(item) != {
                        "owner_formal_concept_id", "claim_id", "evidence_ids"
                    }
                    or item["owner_formal_concept_id"] not in {
                        relation["source_formal_concept_id"],
                        relation["target_formal_concept_id"],
                    }
                    or not isinstance(item["claim_id"], str)
                    or not isinstance(item["evidence_ids"], list)
                    or not item["evidence_ids"]
                    or item["evidence_ids"] != sorted(set(item["evidence_ids"]))
                ):
                    return "KNOWLEDGE_MAP_INVALID"
                owner = item["owner_formal_concept_id"]
                claim = claims_by_formal[owner].get(item["claim_id"])
                if claim is None or not set(item["evidence_ids"]) <= set(claim["evidence_ids"]):
                    return "KNOWLEDGE_MAP_INVALID"
                relation_evidence_keys.append((owner, item["claim_id"]))
            if relation_evidence_keys != sorted(set(relation_evidence_keys)):
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
                "aliases": deepcopy(concept["aliases"]),
                "claims": claims,
                "source_concept_ids": deepcopy(concept["source_concept_ids"]),
                "source_page_numbers": deepcopy(concept["source_page_numbers"]),
                "supplementary_resources": deepcopy(concept["supplementary_resources"]),
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
        "concept_diagnostics": deepcopy(knowledge_map["concept_diagnostics"]),
        "relations": deepcopy(knowledge_map["relations"]),
        "relation_diagnostics": deepcopy(knowledge_map["relation_diagnostics"]),
        "resource_binding": deepcopy(knowledge_map["resource_binding"]),
        "resource_diagnostics": deepcopy(knowledge_map["resource_diagnostics"]),
        "resource_decisions": deepcopy(knowledge_map["resource_decisions"]),
        "initial_learning_path": deepcopy(knowledge_map["initial_learning_path"]),
        "excluded_pages": deepcopy(knowledge_map["excluded_pages"]),
    }
