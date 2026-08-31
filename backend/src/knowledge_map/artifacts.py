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


KNOWLEDGE_MAP_SCHEMA = "knowledge-map/v9"
KNOWLEDGE_MAP_VIEW_SCHEMA = "knowledge-map-view/v9"
MAX_FLAT_GROUP_LABEL_LENGTH = 120

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
    "model_calls",
    "model_no_relation_pairs",
    "model_contains_pairs",
    "model_prerequisite_pairs",
    "model_related_pairs",
    "model_review_pairs",
    "unexpected_pairs",
    "invalid_pairs",
    "canonical_rejections",
    "verifier_calls",
    "verifier_accepted",
    "verifier_rejected",
    "verifier_unsupported",
    "verifier_failures",
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


def _cycle_relation_ids(
    relations: list[dict[str, Any]], *, relation_type: str = "prerequisite"
) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    prerequisite = []
    for relation in relations:
        if relation["type"] == relation_type:
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


def _can_reach(
    outgoing: dict[str, set[str]], start: str, target: str
) -> bool:
    pending = [start]
    seen = set()
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        pending.extend(outgoing.get(node, ()))
    return False


def _bounded_group_label(text: str, page_number: int) -> tuple[str, str]:
    """保留既有 heading；沒有 heading 時只說明來源頁，不猜測名稱。"""

    label = " ".join(unicodedata.normalize("NFKC", text).split())
    if not label:
        return f"第 {page_number} 頁未命名段落", "unheaded_fallback"
    if len(label) > MAX_FLAT_GROUP_LABEL_LENGTH:
        label = label[: MAX_FLAT_GROUP_LABEL_LENGTH - 1].rstrip() + "…"
    return label, "heading"


def _build_flat_group_context(
    study_material_output: dict[str, Any],
    formal_concepts: list[dict[str, Any]],
) -> dict[str, Any]:
    """以 Concept 自己的 Claim Evidence 找到平面 section，不使用 section hash 排序。"""

    evidence_by_id = {
        evidence["evidence_id"]: evidence
        for evidence in study_material_output["evidence_index"]
    }
    text_by_evidence = {
        evidence["evidence_id"]: evidence["text"]
        for evidence in study_material_output["evidence_text_index"]
    }
    current_by_evidence = {}
    current_by_section: dict[str, list[dict[str, Any]]] = {}
    for context in study_material_output["document_contexts"]:
        for block in context["current_blocks"]:
            source = {
                "evidence_id": block["evidence_id"],
                "page_ref": context["page_ref"],
                "page_number": context["page_number"],
                "reading_order": block["reading_order"],
                "flat_group_id": block["section_id"],
            }
            current_by_evidence[block["evidence_id"]] = source
            current_by_section.setdefault(block["section_id"], []).append(source)

    anchors = []
    for concept in formal_concepts:
        claim_evidence_ids = {
            evidence_id
            for claim in concept["claims"]
            for evidence_id in claim["evidence_ids"]
        }
        candidates = []
        for evidence_id in claim_evidence_ids:
            source = current_by_evidence.get(evidence_id)
            if source is None or not any(
                member["page_ref"] == source["page_ref"]
                and evidence_id in member["evidence_ids"]
                and source["flat_group_id"] in member["section_ids"]
                for member in concept["source_members"]
            ):
                raise ValueError("KNOWLEDGE_MAP_GROUP_INVALID")
            candidates.append(source)
        if not candidates:
            raise ValueError("KNOWLEDGE_MAP_GROUP_INVALID")
        anchor = min(
            candidates,
            key=lambda item: (
                item["page_number"],
                item["reading_order"],
                item["evidence_id"],
            ),
        )
        anchors.append(
            {
                "formal_concept_id": concept["formal_concept_id"],
                **anchor,
            }
        )

    groups = []
    for flat_group_id in {anchor["flat_group_id"] for anchor in anchors}:
        section_blocks = sorted(
            current_by_section[flat_group_id],
            key=lambda item: (
                item["page_number"],
                item["reading_order"],
                item["evidence_id"],
            ),
        )
        source_order = section_blocks[0]
        headings = [
            block
            for block in section_blocks
            if evidence_by_id[block["evidence_id"]]["kind"] == "heading"
        ]
        heading = headings[0] if headings else None
        label, label_source = _bounded_group_label(
            text_by_evidence[heading["evidence_id"]] if heading else "",
            source_order["page_number"],
        )
        groups.append(
            {
                "flat_group_id": flat_group_id,
                "label": label,
                "label_source": label_source,
                "heading_evidence_id": (
                    heading["evidence_id"] if heading is not None else None
                ),
                "source_order": {
                    key: value
                    for key, value in source_order.items()
                    if key != "flat_group_id"
                },
            }
        )
    return {
        "concept_anchors": sorted(
            anchors, key=lambda item: item["formal_concept_id"]
        ),
        "groups": sorted(
            groups,
            key=lambda item: (
                item["source_order"]["page_number"],
                item["source_order"]["reading_order"],
                item["source_order"]["evidence_id"],
                item["flat_group_id"],
            ),
        ),
    }


def _topology_and_learning_path(
    formal_concepts: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    flat_group_context: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    """用已發布關係與平面 section 建立唯一的地圖拓撲及學習順序。"""

    nodes = {
        concept["formal_concept_id"]: concept
        for concept in formal_concepts
        if concept["decision"] != "reject"
    }
    source_page = {
        concept_id: min(concept["source_page_numbers"])
        for concept_id, concept in nodes.items()
    }
    if (
        not isinstance(flat_group_context, dict)
        or set(flat_group_context) != {"concept_anchors", "groups"}
        or not isinstance(flat_group_context["concept_anchors"], list)
        or not isinstance(flat_group_context["groups"], list)
    ):
        raise ValueError("KNOWLEDGE_MAP_GROUP_INVALID")
    anchor_by_node = {
        anchor.get("formal_concept_id"): anchor
        for anchor in flat_group_context["concept_anchors"]
        if isinstance(anchor, dict)
    }
    groups_by_id = {
        group.get("flat_group_id"): group
        for group in flat_group_context["groups"]
        if isinstance(group, dict)
    }
    if (
        set(anchor_by_node) != set(nodes)
        or len(anchor_by_node) != len(flat_group_context["concept_anchors"])
        or len(groups_by_id) != len(flat_group_context["groups"])
    ):
        raise ValueError("KNOWLEDGE_MAP_GROUP_INVALID")
    anchor_fields = {
        "formal_concept_id", "flat_group_id", "evidence_id", "page_ref",
        "page_number", "reading_order",
    }
    for node, anchor in anchor_by_node.items():
        concept = nodes[node]
        if (
            set(anchor) != anchor_fields
            or anchor["evidence_id"] not in {
                evidence_id
                for claim in concept["claims"]
                for evidence_id in claim["evidence_ids"]
            }
            or type(anchor["page_number"]) is not int
            or anchor["page_number"] < 1
            or type(anchor["reading_order"]) is not int
            or anchor["reading_order"] < 0
            or not any(
                member["page_ref"] == anchor["page_ref"]
                and anchor["evidence_id"] in member["evidence_ids"]
                and anchor["flat_group_id"] in member["section_ids"]
                for member in concept["source_members"]
            )
            or anchor["page_number"] not in concept["source_page_numbers"]
        ):
            raise ValueError("KNOWLEDGE_MAP_GROUP_INVALID")
    group_fields = {
        "flat_group_id", "label", "label_source", "heading_evidence_id",
        "source_order",
    }
    source_order_fields = {
        "evidence_id", "page_ref", "page_number", "reading_order",
    }
    if set(groups_by_id) != {
        anchor["flat_group_id"] for anchor in anchor_by_node.values()
    }:
        raise ValueError("KNOWLEDGE_MAP_GROUP_INVALID")
    for group in groups_by_id.values():
        source_order = group.get("source_order")
        if (
            set(group) != group_fields
            or not isinstance(group["label"], str)
            or not 1 <= len(group["label"]) <= MAX_FLAT_GROUP_LABEL_LENGTH
            or group["label_source"] not in {"heading", "unheaded_fallback"}
            or not isinstance(source_order, dict)
            or set(source_order) != source_order_fields
            or not isinstance(source_order["evidence_id"], str)
            or not isinstance(source_order["page_ref"], str)
            or type(source_order["page_number"]) is not int
            or source_order["page_number"] < 1
            or type(source_order["reading_order"]) is not int
            or source_order["reading_order"] < 0
            or (
                group["label_source"] == "heading"
                and not isinstance(group["heading_evidence_id"], str)
            )
            or (
                group["label_source"] == "unheaded_fallback"
                and (
                    group["heading_evidence_id"] is not None
                    or group["label"]
                    != f"第 {source_order['page_number']} 頁未命名段落"
                )
            )
        ):
            raise ValueError("KNOWLEDGE_MAP_GROUP_INVALID")
    flat_group_by_node = {
        node: anchor["flat_group_id"] for node, anchor in anchor_by_node.items()
    }
    flat_group_ids = [group["flat_group_id"] for group in flat_group_context["groups"]]
    if flat_group_context["concept_anchors"] != sorted(
        flat_group_context["concept_anchors"],
        key=lambda item: item["formal_concept_id"],
    ) or flat_group_context["groups"] != sorted(
        flat_group_context["groups"],
        key=lambda item: (
            item["source_order"]["page_number"],
            item["source_order"]["reading_order"],
            item["source_order"]["evidence_id"],
            item["flat_group_id"],
        ),
    ):
        raise ValueError("KNOWLEDGE_MAP_GROUP_INVALID")
    flat_group_rank = {
        group_id: index for index, group_id in enumerate(flat_group_ids)
    }

    contains_outgoing = {node: set() for node in nodes}
    contains_incoming = {node: set() for node in nodes}
    for relation in relations:
        if relation["type"] != "contains":
            continue
        source = relation["source_formal_concept_id"]
        target = relation["target_formal_concept_id"]
        if source in nodes and target in nodes:
            contains_outgoing[source].add(target)
            contains_incoming[target].add(source)

    contains_degree = {
        node: len(contains_incoming[node]) for node in nodes
    }
    hierarchy_ready = sorted(
        (node for node, count in contains_degree.items() if count == 0),
        key=lambda node: (source_page[node], node),
    )
    hierarchy_order = []
    depth = {node: 0 for node in nodes}
    while hierarchy_ready:
        node = hierarchy_ready.pop(0)
        hierarchy_order.append(node)
        for child in sorted(contains_outgoing[node], key=lambda item: (source_page[item], item)):
            depth[child] = max(depth[child], depth[node] + 1)
            contains_degree[child] -= 1
            if contains_degree[child] == 0:
                hierarchy_ready.append(child)
                hierarchy_ready.sort(key=lambda item: (source_page[item], item))
    if len(hierarchy_order) != len(nodes):
        raise ValueError("KNOWLEDGE_MAP_CYCLE_INVALID")

    primary_parent = {}
    for node in hierarchy_order:
        parents = contains_incoming[node]
        primary_parent[node] = (
            min(
                parents,
                key=lambda parent: (-depth[parent], source_page[parent], parent),
            )
            if parents
            else None
        )

    topology_order = sorted(
        nodes,
        key=lambda node: (
            flat_group_rank[flat_group_by_node[node]],
            depth[node],
            source_page[node],
            node,
        ),
    )
    roots = [node for node in topology_order if primary_parent[node] is None]
    topology = {
        "roots": roots,
        "nodes": [
            {
                "formal_concept_id": node,
                "depth": depth[node],
                "primary_parent_formal_concept_id": primary_parent[node],
                "flat_group_id": flat_group_by_node[node],
                "flat_group_anchor": {
                    key: value
                    for key, value in anchor_by_node[node].items()
                    if key not in {"formal_concept_id", "flat_group_id"}
                },
            }
            for node in topology_order
        ],
        "flat_groups": [
            {
                "flat_group_id": group_id,
                "label": groups_by_id[group_id]["label"],
                "label_source": groups_by_id[group_id]["label_source"],
                "heading_evidence_id": groups_by_id[group_id]["heading_evidence_id"],
                "source_order": deepcopy(groups_by_id[group_id]["source_order"]),
                "formal_concept_ids": [
                    node
                    for node in topology_order
                    if flat_group_by_node[node] == group_id
                ],
            }
            for group_id in flat_group_ids
        ],
    }

    constraint_outgoing = {node: set() for node in nodes}
    prerequisite_incoming = {node: set() for node in nodes}
    teachable_parent_incoming = {node: set() for node in nodes}
    for relation in relations:
        if relation["type"] != "prerequisite" or relation["is_in_prerequisite_cycle"]:
            continue
        source = relation["source_formal_concept_id"]
        target = relation["target_formal_concept_id"]
        if source in nodes and target in nodes:
            constraint_outgoing[source].add(target)
            prerequisite_incoming[target].add(source)

    skipped_parent_before_child_count = 0
    for relation in sorted(relations, key=lambda item: item["relation_id"]):
        if relation["type"] != "contains":
            continue
        source = relation["source_formal_concept_id"]
        target = relation["target_formal_concept_id"]
        if target in constraint_outgoing[source]:
            teachable_parent_incoming[target].add(source)
        elif _can_reach(constraint_outgoing, target, source):
            skipped_parent_before_child_count += 1
        else:
            constraint_outgoing[source].add(target)
            teachable_parent_incoming[target].add(source)

    incoming_count = {node: 0 for node in nodes}
    for targets in constraint_outgoing.values():
        for target in targets:
            incoming_count[target] += 1

    def order(node_id: str) -> tuple[Any, ...]:
        return (
            flat_group_rank[flat_group_by_node[node_id]],
            depth[node_id],
            source_page[node_id],
            node_id,
        )

    ready = sorted((node for node, count in incoming_count.items() if count == 0), key=order)
    path_ids = []
    while ready:
        node = ready.pop(0)
        path_ids.append(node)
        for target in sorted(constraint_outgoing[node], key=order):
            incoming_count[target] -= 1
            if incoming_count[target] == 0:
                ready.append(target)
                ready.sort(key=order)
    if len(path_ids) != len(nodes):
        raise ValueError("KNOWLEDGE_MAP_CYCLE_INVALID")

    labels = {node: concept["label"] for node, concept in nodes.items()}
    path = []
    previous_group: str | None = None
    for step_number, node in enumerate(path_ids, start=1):
        prerequisites = sorted(prerequisite_incoming[node])
        parents = sorted(teachable_parent_incoming[node])
        group_id = flat_group_by_node[node]
        if prerequisites:
            reason = (
                f"先理解「{labels[prerequisites[0]]}」，再進入這個概念。"
                if len(prerequisites) == 1
                else f"先完成 {len(prerequisites)} 個先備概念，再進入這個概念。"
            )
        elif parents:
            reason_parent = (
                primary_parent[node]
                if primary_parent[node] in parents
                else parents[0]
            )
            reason = f"先建立上層概念「{labels[reason_parent]}」，再學習這個子概念。"
        elif previous_group == group_id:
            reason = "接續教材同一節的平面概念順序。"
        elif contains_incoming[node]:
            reason = "此步先遵守先備關係，避免形成相互等待的學習順序。"
        else:
            reason = f"依教材第 {source_page[node]} 頁的首次出現位置安排。"
        path.append(
            {
                "step_number": step_number,
                "formal_concept_id": node,
                "placement_reason": reason,
                "order_basis": {
                    "prerequisite_formal_concept_ids": prerequisites,
                    "parent_formal_concept_ids": parents,
                    "flat_group_id": group_id,
                    "hierarchy_depth": depth[node],
                    "source_page_number": source_page[node],
                },
            }
        )
        previous_group = group_id

    undirected = {node: set() for node in nodes}
    for source, targets in contains_outgoing.items():
        for target in targets:
            undirected[source].add(target)
            undirected[target].add(source)
    component_count = 0
    remaining = set(nodes)
    while remaining:
        component_count += 1
        pending = [min(remaining)]
        while pending:
            node = pending.pop()
            if node not in remaining:
                continue
            remaining.remove(node)
            pending.extend(undirected[node])
    diagnostics = {
        "component_count": component_count,
        "orphan_concept_count": sum(not neighbors for neighbors in undirected.values()),
        "secondary_parent_count": sum(
            max(0, len(parents) - 1) for parents in contains_incoming.values()
        ),
        "skipped_parent_before_child_count": skipped_parent_before_child_count,
    }
    return topology, path, diagnostics


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
        or len(resource_promotion["formal_concepts"]) != len(resolved_formal_concepts)
        or len({
            concept.get("formal_concept_id")
            for concept in resolved_formal_concepts
            if isinstance(concept, dict)
        }) != len(resolved_formal_concepts)
        or {
            concept.get("formal_concept_id"): {
                key: value
                for key, value in concept.items()
                if key != "supplementary_resources"
            }
            for concept in resource_promotion["formal_concepts"]
            if isinstance(concept, dict)
        }
        != {
            concept.get("formal_concept_id"): concept
            for concept in resolved_formal_concepts
            if isinstance(concept, dict)
        }
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
    contains_cycle_ids = _cycle_relation_ids(relations, relation_type="contains")
    if cycle_ids or contains_cycle_ids:
        raise ValueError("KNOWLEDGE_MAP_RELATION_INVALID")
    for relation in relations:
        if (
            relation["source_formal_concept_id"] not in formal_ids
            or relation["target_formal_concept_id"] not in formal_ids
            or relation["source_formal_concept_id"] == relation["target_formal_concept_id"]
        ):
            raise ValueError("KNOWLEDGE_MAP_RELATION_INVALID")
        relation["is_in_prerequisite_cycle"] = False

    formal_concepts.sort(
        key=lambda concept: (
            min(concept["source_page_numbers"]),
            concept["formal_concept_id"],
        )
    )
    relations.sort(key=lambda relation: relation["relation_id"])
    flat_group_context = _build_flat_group_context(
        study_material_output, formal_concepts
    )
    topology, path, topology_diagnostics = _topology_and_learning_path(
        formal_concepts, relations, flat_group_context
    )
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
        "flat_group_context": flat_group_context,
        "topology": topology,
        "topology_diagnostics": topology_diagnostics,
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
        "flat_group_context", "topology", "topology_diagnostics",
        "initial_learning_path",
        "evidence_index", "excluded_pages", "processing",
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
                    "same_context",
                    "same_section",
                    "explicit_relation",
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
            or diagnostics["selected_pairs"]
            != diagnostics["model_no_relation_pairs"]
            + diagnostics["model_contains_pairs"]
            + diagnostics["model_prerequisite_pairs"]
            + diagnostics["model_related_pairs"]
            + diagnostics["invalid_pairs"]
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
        evidence_kinds: dict[str, str] = {}
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
            evidence_kinds[evidence["evidence_id"]] = evidence["kind"]
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
        contexts_by_formal = {
            concept["formal_concept_id"]: {
                member["document_context_id"]: member
                for member in concept["source_members"]
            }
            for concept in formal
        }
        for relation in knowledge_map["relations"]:
            identity = {
                "type": relation.get("type"),
                "source_formal_concept_id": relation.get("source_formal_concept_id"),
                "target_formal_concept_id": relation.get("target_formal_concept_id"),
                "reason": relation.get("reason"),
                "inference_basis": relation.get("inference_basis"),
                "relation_evidence": relation.get("relation_evidence"),
                "relation_context": relation.get("relation_context"),
            }
            relation_key = (
                relation.get("type"),
                relation.get("source_formal_concept_id"),
                relation.get("target_formal_concept_id"),
            )
            if (
                set(relation) != {
                    "relation_id", "type", "source_formal_concept_id",
                    "target_formal_concept_id", "reason", "inference_basis",
                    "relation_evidence", "relation_context", "needs_review",
                    "quality", "decision", "reason_codes",
                    "is_in_prerequisite_cycle",
                }
                or relation["relation_id"] in relation_ids
                or relation["source_formal_concept_id"] not in formal_ids
                or relation["target_formal_concept_id"] not in formal_ids
                or relation["source_formal_concept_id"] == relation["target_formal_concept_id"]
                or relation["type"] not in RELATION_TYPES
                or not isinstance(relation["reason"], str)
                or not relation["reason"]
                or relation["inference_basis"]
                not in {"claim_semantics", "document_structure", "combined"}
                or type(relation["needs_review"]) is not bool
                or relation["quality"] != "needs_review"
                or relation["decision"] != "review"
                or not reason_codes_are_valid(relation["reason_codes"], formal=True)
                or relation["reason_codes"] != sorted(set(relation["reason_codes"]))
                or type(relation["is_in_prerequisite_cycle"]) is not bool
                or relation["is_in_prerequisite_cycle"]
                or relation["relation_id"]
                != "formal-relation:sha256:" + canonical_sha256(identity)
                or relation_key in relation_keys
                or not isinstance(relation["relation_evidence"], list)
                or not relation["relation_evidence"]
                or not isinstance(relation["relation_context"], list)
                or (
                    relation["inference_basis"]
                    in {"document_structure", "combined"}
                    and not relation["relation_context"]
                )
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
            context_keys = []
            for item in relation["relation_context"]:
                if (
                    not isinstance(item, dict)
                    or set(item) != {
                        "owner_formal_concept_id", "document_context_id",
                        "page_ref", "section_ids",
                    }
                    or item["owner_formal_concept_id"] not in {
                        relation["source_formal_concept_id"],
                        relation["target_formal_concept_id"],
                    }
                ):
                    return "KNOWLEDGE_MAP_INVALID"
                owner = item["owner_formal_concept_id"]
                member = contexts_by_formal[owner].get(
                    item["document_context_id"]
                )
                if (
                    member is None
                    or item["page_ref"] != member["page_ref"]
                    or item["section_ids"] != member["section_ids"]
                ):
                    return "KNOWLEDGE_MAP_INVALID"
                context_keys.append((owner, item["document_context_id"]))
            if context_keys != sorted(set(context_keys)):
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
        if _cycle_relation_ids(knowledge_map["relations"]) or _cycle_relation_ids(
            knowledge_map["relations"], relation_type="contains"
        ):
            return "KNOWLEDGE_MAP_INVALID"
        group_evidence = [
            *knowledge_map["flat_group_context"]["concept_anchors"],
            *(
                group["source_order"]
                for group in knowledge_map["flat_group_context"]["groups"]
            ),
        ]
        if any(
            evidence_pages.get(item["evidence_id"]) != item["page_ref"]
            or page_numbers.get(item["page_ref"]) != item["page_number"]
            for item in group_evidence
        ) or any(
            group["heading_evidence_id"] is not None
            and evidence_kinds.get(group["heading_evidence_id"]) != "heading"
            for group in knowledge_map["flat_group_context"]["groups"]
        ):
            return "KNOWLEDGE_MAP_INVALID"
        expected_topology, expected_path, expected_topology_diagnostics = (
            _topology_and_learning_path(
                formal,
                knowledge_map["relations"],
                knowledge_map["flat_group_context"],
            )
        )
        if (
            knowledge_map["topology"] != expected_topology
            or knowledge_map["topology_diagnostics"]
            != expected_topology_diagnostics
            or knowledge_map["initial_learning_path"] != expected_path
        ):
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
        "topology": deepcopy(knowledge_map["topology"]),
        "topology_diagnostics": deepcopy(knowledge_map["topology_diagnostics"]),
        "initial_learning_path": deepcopy(knowledge_map["initial_learning_path"]),
        "excluded_pages": deepcopy(knowledge_map["excluded_pages"]),
    }
