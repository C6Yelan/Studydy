from __future__ import annotations

from copy import deepcopy
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from pdf_evidence.artifact_reason_codes import reason_codes_are_valid
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.study_material_output import validate_study_material_output

from .prerequisites import prerequisite_constraints_are_valid


KNOWLEDGE_MAP_SCHEMA = "knowledge-map/v10"
KNOWLEDGE_MAP_VIEW_SCHEMA = "knowledge-map-view/v10"
MAX_SECTION_LABEL_LENGTH = 120

_CONCEPT_DIAGNOSTIC_FIELDS = {
    "possible_pairs", "candidate_pairs", "selected_pairs", "pair_ceiling",
    "qwen_same_pairs", "qwen_distinct_pairs", "qwen_uncertain_pairs",
    "verifier_requested_pairs", "verifier_scored_pairs",
    "verifier_allowed_pairs", "verifier_vetoed_pairs",
    "verifier_unsupported_pairs", "verifier_failed_pairs",
    "source_concepts_before", "canonical_concepts_after", "duplicate_delta",
    "coverage_before", "coverage_after",
}
_RESOURCE_DIAGNOSTIC_FIELDS = {
    "matches", "promoted_matches", "promoted_resources", "dropped_matches",
    "split_review_matches",
}
_SUPPLEMENTARY_RESOURCE_FIELDS = {
    "promotion_id", "resource_concept_id", "resource_id", "label", "title",
    "authors", "source_url", "citation", "license", "license_url",
    "use_boundary", "page_numbers", "resource_evidence_ids", "match_ids",
    "study_concept_ids", "match_reason",
}


def _revision(document: dict[str, Any]) -> str:
    content = {key: value for key, value in document.items() if key != "revision"}
    return "knowledge-map:sha256:" + canonical_sha256(content)


def _clean_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _section_label(text: str, page_number: int) -> tuple[str, str]:
    """有 grounded heading 就沿用，否則只標明來源頁面。"""

    label = _clean_text(text)
    if not label:
        return f"第 {page_number} 頁未命名段落", "unheaded_fallback"
    if len(label) > MAX_SECTION_LABEL_LENGTH:
        label = label[: MAX_SECTION_LABEL_LENGTH - 1].rstrip() + "…"
    return label, "heading"


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


def _document_tree_and_learning_path(
    study_material_output: dict[str, Any],
    formal_concepts: list[dict[str, Any]],
    prerequisite_constraints: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """以 Claim Evidence 的首次位置建立 flat Section 與獨立 Path。"""

    evidence_by_id = {
        evidence["evidence_id"]: evidence
        for evidence in study_material_output["evidence_index"]
    }
    text_by_id = {
        evidence["evidence_id"]: evidence["text"]
        for evidence in study_material_output["evidence_text_index"]
    }
    current_by_evidence: dict[str, dict[str, Any]] = {}
    blocks_by_section: dict[str, list[dict[str, Any]]] = {}
    for context in study_material_output["document_contexts"]:
        for block in context["current_blocks"]:
            source = {
                "evidence_id": block["evidence_id"],
                "page_ref": context["page_ref"],
                "page_number": context["page_number"],
                "reading_order": block["reading_order"],
                "section_id": block["section_id"],
                "document_context_id": context["context_id"],
            }
            current_by_evidence[block["evidence_id"]] = source
            blocks_by_section.setdefault(block["section_id"], []).append(source)

    concept_anchors = []
    for concept in formal_concepts:
        candidates = []
        for claim in concept["claims"]:
            for evidence_id in claim["evidence_ids"]:
                source = current_by_evidence.get(evidence_id)
                if source is None or not any(
                    member["page_ref"] == source["page_ref"]
                    and member["document_context_id"]
                    == source["document_context_id"]
                    and evidence_id in member["evidence_ids"]
                    and source["section_id"] in member["section_ids"]
                    for member in concept["source_members"]
                ):
                    raise ValueError("KNOWLEDGE_MAP_TREE_INVALID")
                candidates.append(source)
        if not candidates:
            raise ValueError("KNOWLEDGE_MAP_TREE_INVALID")
        anchor = min(
            candidates,
            key=lambda item: (
                item["page_number"], item["reading_order"], item["evidence_id"]
            ),
        )
        concept_anchors.append(
            {"formal_concept_id": concept["formal_concept_id"], **anchor}
        )

    sections = []
    for section_id in {anchor["section_id"] for anchor in concept_anchors}:
        section_blocks = sorted(
            blocks_by_section[section_id],
            key=lambda item: (
                item["page_number"], item["reading_order"], item["evidence_id"]
            ),
        )
        source_order = section_blocks[0]
        heading = next(
            (
                block for block in section_blocks
                if evidence_by_id[block["evidence_id"]]["kind"] == "heading"
            ),
            None,
        )
        label, label_source = _section_label(
            text_by_id[heading["evidence_id"]] if heading else "",
            source_order["page_number"],
        )
        ordered_concepts = sorted(
            (
                anchor for anchor in concept_anchors
                if anchor["section_id"] == section_id
            ),
            key=lambda item: (
                item["page_number"], item["reading_order"],
                item["evidence_id"], item["formal_concept_id"],
            ),
        )
        sections.append(
            {
                "section_id": section_id,
                "label": label,
                "label_source": label_source,
                "heading_evidence_id": (
                    heading["evidence_id"] if heading is not None else None
                ),
                "source_order": {
                    "page_ref": source_order["page_ref"],
                    "page_number": source_order["page_number"],
                    "reading_order": source_order["reading_order"],
                    "evidence_id": source_order["evidence_id"],
                },
                "concept_ids": [
                    anchor["formal_concept_id"] for anchor in ordered_concepts
                ],
            }
        )
    sections.sort(
        key=lambda section: (
            section["source_order"]["page_number"],
            section["source_order"]["reading_order"],
            section["source_order"]["evidence_id"], section["section_id"],
        )
    )
    document_tree = {
        "root": {
            "material_ref": study_material_output["material_ref"],
            "section_ids": [section["section_id"] for section in sections],
        },
        "sections": sections,
    }
    anchor_by_concept = {
        anchor["formal_concept_id"]: anchor for anchor in concept_anchors
    }
    baseline_ids = [
        concept_id for section in sections for concept_id in section["concept_ids"]
    ]
    baseline_index = {
        concept_id: index for index, concept_id in enumerate(baseline_ids)
    }
    incoming = {concept_id: 0 for concept_id in baseline_ids}
    outgoing = {concept_id: set() for concept_id in baseline_ids}
    constraints_by_target: dict[str, list[dict[str, Any]]] = {
        concept_id: [] for concept_id in baseline_ids
    }
    for constraint in prerequisite_constraints:
        source = constraint["source_formal_concept_id"]
        target = constraint["target_formal_concept_id"]
        outgoing[source].add(target)
        incoming[target] += 1
        constraints_by_target[target].append(constraint)
    ready = sorted(
        (concept_id for concept_id, count in incoming.items() if count == 0),
        key=baseline_index.__getitem__,
    )
    ordered_ids = []
    while ready:
        concept_id = ready.pop(0)
        ordered_ids.append(concept_id)
        for target in sorted(outgoing[concept_id], key=baseline_index.__getitem__):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort(key=baseline_index.__getitem__)
    if len(ordered_ids) != len(baseline_ids):
        raise ValueError("KNOWLEDGE_MAP_PATH_INVALID")
    path = []
    for step_number, formal_concept_id in enumerate(ordered_ids, start=1):
        anchor = anchor_by_concept[formal_concept_id]
        constraint_ids = sorted(
            constraint["prerequisite_constraint_id"]
            for constraint in constraints_by_target[formal_concept_id]
        )
        path.append(
            {
                "step_number": step_number,
                "formal_concept_id": formal_concept_id,
                "placement_reason": (
                    "依已正向驗證的先備條件調整學習順序。"
                    if constraint_ids
                    else f"依教材第 {anchor['page_number']} 頁的首次 Claim Evidence 安排。"
                ),
                "order_basis": {
                    "prerequisite_constraint_ids": constraint_ids,
                    "section_id": anchor["section_id"],
                    "page_ref": anchor["page_ref"],
                    "page_number": anchor["page_number"],
                    "reading_order": anchor["reading_order"],
                    "evidence_id": anchor["evidence_id"],
                },
            }
        )
    return document_tree, path


def _empty_resources() -> dict[str, Any]:
    return {
        "processing": "partial",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["SUPPLEMENTARY_RESOURCES_UNAVAILABLE"],
        "binding": None,
        "diagnostics": {field: 0 for field in _RESOURCE_DIAGNOSTIC_FIELDS},
        "decisions": [],
    }


def _attach_resources(
    formal_concepts: list[dict[str, Any]],
    resource_promotion: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if resource_promotion is None:
        concepts = deepcopy(formal_concepts)
        for concept in concepts:
            concept["supplementary_resources"] = []
        return concepts, _empty_resources()
    expected_fields = {
        "formal_concepts", "resource_binding", "resource_diagnostics",
        "resource_decisions",
    }
    if not isinstance(resource_promotion, dict) or set(resource_promotion) != expected_fields:
        raise ValueError("KNOWLEDGE_MAP_RESOURCE_INVALID")
    promoted = resource_promotion["formal_concepts"]
    if not isinstance(promoted, list):
        raise ValueError("KNOWLEDGE_MAP_RESOURCE_INVALID")
    without_resources = {
        concept.get("formal_concept_id"): {
            key: value for key, value in concept.items()
            if key != "supplementary_resources"
        }
        for concept in promoted if isinstance(concept, dict)
    }
    if without_resources != {
        concept["formal_concept_id"]: concept for concept in formal_concepts
    }:
        raise ValueError("KNOWLEDGE_MAP_RESOURCE_INVALID")
    diagnostics = resource_promotion["resource_diagnostics"]
    if (
        not isinstance(diagnostics, dict)
        or set(diagnostics) != _RESOURCE_DIAGNOSTIC_FIELDS
        or any(type(value) is not int or value < 0 for value in diagnostics.values())
    ):
        raise ValueError("KNOWLEDGE_MAP_RESOURCE_INVALID")
    has_review = diagnostics["split_review_matches"] > 0
    return deepcopy(promoted), {
        "processing": "partial" if has_review else "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["RESOURCE_SPLIT_REVIEW_REQUIRED"] if has_review else [],
        "binding": deepcopy(resource_promotion["resource_binding"]),
        "diagnostics": deepcopy(diagnostics),
        "decisions": deepcopy(resource_promotion["resource_decisions"]),
    }


def build_knowledge_map(
    study_material_output: dict[str, Any],
    resolution_artifacts: list[dict[str, Any]],
    *,
    material_runtime_binding_sha256: str,
    resource_promotion: dict[str, Any] | None = None,
    prerequisite_constraints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """先建立完整 Concepts、Document Tree 與 Path，再附加 optional resources。"""

    if validate_study_material_output(study_material_output) is not None:
        raise ValueError("KNOWLEDGE_MAP_SOURCE_INVALID")
    resolved = [
        deepcopy(concept)
        for artifact in resolution_artifacts
        for concept in artifact.get("formal_concepts", [])
    ]
    source_ids = {concept["concept_id"] for concept in study_material_output["concepts"]}
    covered_ids = [
        source_id for concept in resolved
        for source_id in concept.get("source_concept_ids", [])
    ]
    if set(covered_ids) != source_ids or len(covered_ids) != len(set(covered_ids)):
        raise ValueError("KNOWLEDGE_MAP_CONCEPT_INVALID")
    page_numbers = {
        page["page_ref"]: page["page_number"]
        for page in study_material_output["pages"]
    }
    resolved.sort(
        key=lambda concept: (
            min(page_numbers[page_ref] for page_ref in concept["source_page_refs"]),
            concept["formal_concept_id"],
        )
    )
    formal_concepts, supplementary_resources = _attach_resources(
        resolved, resource_promotion
    )
    for concept in formal_concepts:
        concept["source_page_numbers"] = sorted(
            {page_numbers[page_ref] for page_ref in concept["source_page_refs"]}
        )
    constraints = deepcopy(prerequisite_constraints or [])
    if not prerequisite_constraints_are_valid(constraints, formal_concepts):
        raise ValueError("KNOWLEDGE_MAP_PREREQUISITE_INVALID")
    document_tree, initial_learning_path = _document_tree_and_learning_path(
        study_material_output, formal_concepts, constraints
    )
    has_no_concept = not formal_concepts
    reasons = {
        "KNOWLEDGE_MAP_REVIEW_REQUIRED", *study_material_output["reason_codes"],
        *(
            reason for artifact in resolution_artifacts
            for reason in artifact.get("reason_codes", [])
        ),
    }
    if has_no_concept:
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
        "concept_diagnostics": _concept_diagnostics(resolution_artifacts),
        "document_tree": document_tree,
        "prerequisite_constraints": constraints,
        "initial_learning_path": initial_learning_path,
        "supplementary_resources": supplementary_resources,
        "evidence_index": deepcopy(study_material_output["evidence_index"]),
        "excluded_pages": deepcopy(study_material_output["excluded_pages"]),
        "processing": "partial" if (
            has_no_concept
            or study_material_output["processing"] == "partial"
            or any(artifact.get("processing") == "partial" for artifact in resolution_artifacts)
        ) else "succeeded",
        "quality": "needs_review",
        "decision": "reject" if has_no_concept else "review",
        "reason_codes": sorted(reasons),
    }
    document["revision"] = _revision(document)
    if validate_knowledge_map(document, study_material_output) is not None:
        raise ValueError("KNOWLEDGE_MAP_INVALID")
    return document


def _valid_resource(resource: Any) -> bool:
    if not isinstance(resource, dict) or set(resource) != _SUPPLEMENTARY_RESOURCE_FIELDS:
        return False
    source_url = urlsplit(resource["source_url"])
    license_url = urlsplit(resource["license_url"])
    return (
        source_url.scheme in {"http", "https"} and bool(source_url.netloc)
        and license_url.scheme in {"http", "https"} and bool(license_url.netloc)
        and all(
            isinstance(resource[field], str) and bool(resource[field])
            for field in (
                "promotion_id", "resource_concept_id", "resource_id", "label",
                "title", "citation", "license", "use_boundary",
                "match_reason",
            )
        )
        and all(
            isinstance(resource[field], list)
            for field in (
                "page_numbers", "resource_evidence_ids", "match_ids",
                "study_concept_ids",
            )
        )
        and isinstance(resource["authors"], list)
        and bool(resource["authors"])
        and all(
            isinstance(author, str) and bool(author)
            for author in resource["authors"]
        )
    )


def _formal_concepts_are_valid(
    formal_concepts: Any, study_material_output: dict[str, Any]
) -> bool:
    if not isinstance(formal_concepts, list):
        return False
    source_by_id = {
        concept["concept_id"]: concept for concept in study_material_output["concepts"]
    }
    contexts_by_page = {
        context["page_ref"]: context
        for context in study_material_output["document_contexts"]
    }
    page_numbers = {
        page["page_ref"]: page["page_number"]
        for page in study_material_output["pages"]
    }
    formal_ids = set()
    covered_sources = []
    for concept in formal_concepts:
        expected_fields = {
            "formal_concept_id", "operation", "source_concept_ids", "label",
            "aliases", "claims", "source_members", "source_page_refs",
            "source_page_numbers", "quality", "decision", "reason_codes",
            "supplementary_resources",
        }
        if not isinstance(concept, dict) or set(concept) != expected_fields:
            return False
        source_ids = concept["source_concept_ids"]
        if (
            not isinstance(source_ids, list) or not source_ids
            or any(source_id not in source_by_id for source_id in source_ids)
            or concept["operation"] != ("MERGE" if len(source_ids) > 1 else "KEEP")
            or concept["formal_concept_id"] in formal_ids
            or concept["quality"] != "needs_review" or concept["decision"] != "review"
            or not reason_codes_are_valid(concept["reason_codes"], formal=True)
            or not isinstance(concept["supplementary_resources"], list)
            or any(not _valid_resource(item) for item in concept["supplementary_resources"])
        ):
            return False
        ordered_sources = sorted(
            (source_by_id[source_id] for source_id in source_ids),
            key=lambda source: (page_numbers[source["page_ref"]], source["concept_id"]),
        )
        claims_by_id = {}
        expected_members = []
        for source in ordered_sources:
            for claim in source["claims"]:
                previous = claims_by_id.get(claim["claim_id"])
                if previous is not None and previous != claim:
                    return False
                claims_by_id.setdefault(claim["claim_id"], claim)
            context = contexts_by_page[source["page_ref"]]
            expected_members.append(
                {
                    "source_concept_id": source["concept_id"],
                    "label": _clean_text(source["label"]),
                    "claim_ids": sorted(claim["claim_id"] for claim in source["claims"]),
                    "evidence_ids": sorted({
                        evidence_id for claim in source["claims"]
                        for evidence_id in claim["evidence_ids"]
                    }),
                    "page_ref": source["page_ref"],
                    "document_context_id": context["context_id"],
                    "section_ids": sorted(context["section_ids"]),
                }
            )
        expected_claims = list(claims_by_id.values())
        label = _clean_text(ordered_sources[0]["label"])
        aliases = sorted({
            _clean_text(source["label"]) for source in ordered_sources
            if _clean_text(source["label"]) != label
        })
        identity = {
            "operation": concept["operation"], "source_concept_ids": source_ids,
            "label": label, "aliases": aliases, "claims": expected_claims,
            "source_members": expected_members,
        }
        if (
            concept["formal_concept_id"]
            != "formal-concept:sha256:" + canonical_sha256(identity)
            or concept["label"] != label or concept["aliases"] != aliases
            or concept["claims"] != expected_claims
            or concept["source_members"] != expected_members
            or concept["source_page_refs"]
            != sorted({source["page_ref"] for source in ordered_sources})
            or concept["source_page_numbers"]
            != sorted({page_numbers[source["page_ref"]] for source in ordered_sources})
        ):
            return False
        formal_ids.add(concept["formal_concept_id"])
        covered_sources.extend(source_ids)
    return set(covered_sources) == set(source_by_id) and len(covered_sources) == len(set(covered_sources))


def validate_knowledge_map(
    knowledge_map: Any, study_material_output: Any
) -> str | None:
    fields = {
        "schema", "source_output_id", "source_binding", "material_ref",
        "formal_concepts", "concept_diagnostics", "document_tree",
        "prerequisite_constraints", "initial_learning_path",
        "supplementary_resources", "evidence_index",
        "excluded_pages", "processing", "quality", "decision", "reason_codes",
        "revision",
    }
    try:
        if (
            validate_study_material_output(study_material_output) is not None
            or not isinstance(knowledge_map, dict) or set(knowledge_map) != fields
            or knowledge_map["schema"] != KNOWLEDGE_MAP_SCHEMA
            or knowledge_map["revision"] != _revision(knowledge_map)
            or knowledge_map["source_output_id"] != study_material_output["output_id"]
            or knowledge_map["material_ref"] != study_material_output["material_ref"]
            or knowledge_map["evidence_index"] != study_material_output["evidence_index"]
            or knowledge_map["excluded_pages"] != study_material_output["excluded_pages"]
            or knowledge_map["processing"] not in {"succeeded", "partial"}
            or knowledge_map["quality"] != "needs_review"
            or knowledge_map["decision"] not in {"review", "reject"}
            or not reason_codes_are_valid(knowledge_map["reason_codes"], formal=True)
            or knowledge_map["reason_codes"] != sorted(set(knowledge_map["reason_codes"]))
        ):
            return "KNOWLEDGE_MAP_INVALID"
        binding = knowledge_map["source_binding"]
        if (
            not isinstance(binding, dict)
            or set(binding) != {
                "study_material_output_id", "producer_output_id",
                "producer_runtime_lock_sha256", "material_runtime_binding_sha256",
            }
            or binding["study_material_output_id"] != study_material_output["output_id"]
            or binding["producer_output_id"]
            != study_material_output["source_binding"]["producer_output_id"]
            or binding["producer_runtime_lock_sha256"]
            != study_material_output["source_binding"]["runtime_binding_sha256"]
            or any(
                not isinstance(binding[field], str) or len(binding[field]) != 64
                or any(character not in "0123456789abcdef" for character in binding[field])
                for field in ("producer_runtime_lock_sha256", "material_runtime_binding_sha256")
            )
        ):
            return "KNOWLEDGE_MAP_INVALID"
        diagnostics = knowledge_map["concept_diagnostics"]
        if (
            not isinstance(diagnostics, dict) or set(diagnostics) != _CONCEPT_DIAGNOSTIC_FIELDS
            or any(type(value) is not int or value < 0 for value in diagnostics.values())
            or diagnostics["selected_pairs"]
            != diagnostics["qwen_same_pairs"] + diagnostics["qwen_distinct_pairs"]
            + diagnostics["qwen_uncertain_pairs"]
            or diagnostics["coverage_before"] != diagnostics["coverage_after"]
            or diagnostics["duplicate_delta"]
            != diagnostics["source_concepts_before"] - diagnostics["canonical_concepts_after"]
        ):
            return "KNOWLEDGE_MAP_INVALID"
        if not _formal_concepts_are_valid(knowledge_map["formal_concepts"], study_material_output):
            return "KNOWLEDGE_MAP_INVALID"
        if not prerequisite_constraints_are_valid(
            knowledge_map["prerequisite_constraints"],
            knowledge_map["formal_concepts"],
        ):
            return "KNOWLEDGE_MAP_INVALID"
        expected_tree, expected_path = _document_tree_and_learning_path(
            study_material_output,
            knowledge_map["formal_concepts"],
            knowledge_map["prerequisite_constraints"],
        )
        if knowledge_map["document_tree"] != expected_tree or knowledge_map["initial_learning_path"] != expected_path:
            return "KNOWLEDGE_MAP_INVALID"
        sidecar = knowledge_map["supplementary_resources"]
        if (
            not isinstance(sidecar, dict)
            or set(sidecar) != {
                "processing", "quality", "decision", "reason_codes", "binding",
                "diagnostics", "decisions",
            }
            or sidecar["processing"] not in {"succeeded", "partial"}
            or sidecar["quality"] != "needs_review" or sidecar["decision"] != "review"
            or (
                bool(sidecar["reason_codes"])
                and not reason_codes_are_valid(sidecar["reason_codes"], formal=True)
            )
            or sidecar["reason_codes"] != sorted(set(sidecar["reason_codes"]))
            or not isinstance(sidecar["diagnostics"], dict)
            or set(sidecar["diagnostics"]) != _RESOURCE_DIAGNOSTIC_FIELDS
            or any(type(value) is not int or value < 0 for value in sidecar["diagnostics"].values())
            or not isinstance(sidecar["decisions"], list)
        ):
            return "KNOWLEDGE_MAP_INVALID"
        has_no_concept = not knowledge_map["formal_concepts"]
        if (knowledge_map["decision"] == "reject") != has_no_concept:
            return "KNOWLEDGE_MAP_INVALID"
    except (KeyError, TypeError, ValueError):
        return "KNOWLEDGE_MAP_INVALID"
    return None


def build_knowledge_map_view(
    knowledge_map: dict[str, Any], study_material_output: dict[str, Any]
) -> dict[str, Any]:
    """公開 view 只提供 Claims、locator、Tree、Path 與 optional resources。"""

    if validate_knowledge_map(knowledge_map, study_material_output) is not None:
        raise ValueError("KNOWLEDGE_MAP_INVALID")
    evidence_by_id = {
        evidence["evidence_id"]: evidence for evidence in knowledge_map["evidence_index"]
    }
    concepts = []
    for concept in knowledge_map["formal_concepts"]:
        concepts.append(
            {
                "formal_concept_id": concept["formal_concept_id"],
                "label": concept["label"],
                "aliases": deepcopy(concept["aliases"]),
                "claims": [
                    {
                        "claim_id": claim["claim_id"], "text": claim["text"],
                        "evidence": [
                            deepcopy(evidence_by_id[evidence_id])
                            for evidence_id in claim["evidence_ids"]
                        ],
                    }
                    for claim in concept["claims"]
                ],
                "source_concept_ids": deepcopy(concept["source_concept_ids"]),
                "source_page_numbers": deepcopy(concept["source_page_numbers"]),
                "supplementary_resources": deepcopy(concept["supplementary_resources"]),
                "quality": concept["quality"], "decision": concept["decision"],
                "reason_codes": deepcopy(concept["reason_codes"]),
            }
        )
    return {
        "schema": KNOWLEDGE_MAP_VIEW_SCHEMA,
        "material_ref": knowledge_map["material_ref"],
        "knowledge_map_revision": knowledge_map["revision"],
        "source_output_id": knowledge_map["source_output_id"],
        "status": {
            "processing": knowledge_map["processing"], "quality": knowledge_map["quality"],
            "decision": knowledge_map["decision"],
            "reason_codes": deepcopy(knowledge_map["reason_codes"]),
        },
        "concepts": concepts,
        "concept_diagnostics": deepcopy(knowledge_map["concept_diagnostics"]),
        "document_tree": deepcopy(knowledge_map["document_tree"]),
        "initial_learning_path": deepcopy(knowledge_map["initial_learning_path"]),
        "supplementary_resources": deepcopy(knowledge_map["supplementary_resources"]),
        "excluded_pages": deepcopy(knowledge_map["excluded_pages"]),
    }
