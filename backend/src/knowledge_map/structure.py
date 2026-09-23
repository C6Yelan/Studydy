"""建構正式知識地圖，並提供現行流程使用的結構入口。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
from typing import Any
from uuid import UUID

from .source_context import build_document_context
from .semantic_projection import (
    SemanticState, apply_semantic_response, build_semantic_bundles,
    semantic_request, semantic_response_schema,
)
from .structure_rules import (
    STRUCTURE_SCHEMA, RELATION_PRIORITY, _cycle, _id, _path, _revision,
)
from .structure_validation import validate_knowledge_structure, validate_structure_draft


def build_structure_draft(
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
        or not isinstance(model_id, str) or not model_id
        or not isinstance(model_revision, str) or not model_revision
        or (
            execution_identity is None
            and (
                model_id != "google/gemma-4-31B-it-qat-w4a16-ct"
                or model_revision != "52f3f65bc7a02d555763bc923bd1d9094898219d"
            )
        )
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
            claim["evidence_refs"] = list(dict.fromkeys(
                span["evidence_id"] for span in claim["source_spans"]
            ))
            claim["claim_id"] = _id("claim", claim)
            claims.append(claim)
        if not claims:
            continue
        references = list(dict.fromkeys(
            reference for claim in claims for reference in claim["evidence_refs"]
        ))
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
                "section_ids": list(dict.fromkeys(
                    evidence_by_id[reference]["section_id"] for reference in references
                )),
                "source_pages": sorted({
                    evidence_by_id[reference]["page"] for reference in references
                }),
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
        "processing": (
            "partial" if reasons and concepts else ("failed" if not concepts else "succeeded")
        ),
        "quality": "needs_review" if reasons else "accepted",
        "decision": "reject" if not concepts else ("review" if reasons else "retain"),
        "reason_codes": reasons,
    }
    document = {
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
    if execution_identity is not None:
        document["execution_identity"] = deepcopy(execution_identity)
    if state.source_review_required:
        document["source_review_required"] = True
    if not validate_structure_draft(document):
        raise ValueError("KNOWLEDGE_STRUCTURE_INVALID")
    return document


def finalize_knowledge_structure(
    draft: dict[str, Any], input_binding: dict[str, Any]
) -> dict[str, Any]:
    """完成來源集合綁定後，才建立唯一正式契約及其內容雜湊。"""
    if not validate_structure_draft(draft):
        raise ValueError("KNOWLEDGE_STRUCTURE_DRAFT_INVALID")
    document = deepcopy(draft)
    document["source_set_sha256"] = document.pop("source_sha256")
    document["input_binding"] = deepcopy(input_binding)
    document["schema"] = STRUCTURE_SCHEMA
    document["revision"] = _revision(document)
    if not validate_knowledge_structure(document):
        raise ValueError("KNOWLEDGE_STRUCTURE_INVALID")
    return document


def build_knowledge_structure_view(document: dict[str, Any]) -> dict[str, Any]:
    if not validate_knowledge_structure(document):
        raise ValueError("KNOWLEDGE_STRUCTURE_INVALID")
    return _view_from_validated_document(document)


def _view_from_validated_document(document: dict[str, Any]) -> dict[str, Any]:
    """僅供已完成完整結構驗證的讀取／發布流程共用。"""
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
        "material_id": document["material_id"],
        "knowledge_structure_revision": document["revision"],
        "status": deepcopy(document["status"]),
        "document_tree": deepcopy(document["document_tree"]),
        "concepts": concepts,
        "relations": deepcopy(document["relations"]),
        "initial_learning_path": deepcopy(document["initial_learning_path"]),
        "excluded_pages": deepcopy(document["excluded_pages"]),
    }
