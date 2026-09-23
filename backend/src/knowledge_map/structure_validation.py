"""驗證來源綁定、Evidence、概念、關係與正式 v1 地圖。"""
from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any
from uuid import UUID

from .semantic_projection import _unsupported_null_meaning
from .structure_rules import (
    STRUCTURE_SCHEMA, RELATION_BASIS, RELATION_PRIORITY, RELATION_TYPES,
    _CODE_OR_FORMULA, _GENERIC_REASONS, _TECHNICAL, _cycle, _id, _path,
    _revision,
)


_DRAFT_FIELDS = {
    "material_id", "source_sha256", "run_id", "produced_at", "provenance",
    "page_count", "evidence", "excluded_pages", "document_tree", "concepts",
    "relations", "initial_learning_path", "metrics", "status",
}


def _structure_fields(document, required):
    fields = set(required)
    if "source_review_required" in document:
        if document["source_review_required"] is not True:
            return False
        fields.add("source_review_required")
    if "execution_identity" in document:
        execution = document["execution_identity"]
        if (
            not isinstance(execution, dict)
            or set(execution) != {
                "transport", "model_id", "model_revision",
                "config_sha256", "runtime_lock_sha256",
            }
            or execution["transport"] != "command"
            or any(
                not isinstance(execution[k], str)
                or re.fullmatch(r"[0-9a-f]{64}", execution[k]) is None
                for k in ("config_sha256", "runtime_lock_sha256")
            )
        ):
            return False
        fields.add("execution_identity")
    return set(document) == fields


def validate_structure_draft(document: Any) -> bool:
    """未綁定來源的內部分析結果；沒有正式 schema 或 revision，不可直接發布。"""
    return (
        isinstance(document, dict)
        and _structure_fields(document, _DRAFT_FIELDS)
        and _validate_structure_content(document, document["source_sha256"])
    )


def validate_knowledge_structure(document: Any) -> bool:
    """只接受已綁定來源的 knowledge-structure/v1；重驗 revision 與內容。"""
    try:
        fields = (_DRAFT_FIELDS - {"source_sha256"}) | {
            "schema", "revision", "source_set_sha256", "input_binding",
        }
        if not isinstance(document, dict) or not _structure_fields(document, fields):
            return False
        binding = document["input_binding"]
        if (
            document["schema"] != STRUCTURE_SCHEMA
            or document["revision"] != _revision(document)
            or not isinstance(binding, dict)
            or binding.get("schema") != "structure-input-binding/v1"
            or set(binding) != {
                "schema", "source_set_id", "source_set_digest", "bundle_manifest_sha256",
                "manifest", "bundle", "base_revision",
            }
            or not isinstance(binding["manifest"], dict)
            or binding["manifest"].get("schema") != "source-set/v1"
            or not isinstance(binding["bundle"], dict)
            or binding["bundle"].get("schema") != "bundle-manifest/v1"
            or binding["source_set_digest"] != document["source_set_sha256"]
        ):
            return False
        return _validate_structure_content(document, document["source_set_sha256"])
    except (KeyError, TypeError, ValueError):
        return False


def _validate_evidence_and_pages(document, source_digest):
    """確認 Evidence 身分、來源位置與每一頁的處理結果。"""
    evidence = document["evidence"]
    excluded_pages = document["excluded_pages"]
    evidence_ids = [item["evidence_id"] for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        return False
    evidence_positions = [(item["page"], item["block_order"]) for item in evidence]
    if (
        evidence_positions != sorted(evidence_positions)
        or len(evidence_positions) != len(set(evidence_positions))
    ):
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
            or any(
                type(number) not in {int, float} or not math.isfinite(number)
                for number in locator["region"]
            )
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
    return True


def _validate_concepts(document):
    """確認 Claim 與概念只引用原始 Evidence，且內容 ID 可重算。"""
    evidence = document["evidence"]
    concepts = document["concepts"]
    concept_ids = [concept["concept_id"] for concept in concepts]
    if len(concept_ids) != len(set(concept_ids)):
        return False
    evidence_ids = [item["evidence_id"] for item in evidence]
    known_evidence = set(evidence_ids)
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
        expected_sections = list(
            dict.fromkeys(
                evidence_by_id[reference]["section_id"]
                for reference in concept["evidence_refs"]
            )
        )
        expected_pages = sorted({
            evidence_by_id[reference]["page"]
            for reference in concept["evidence_refs"]
        })
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
            if (
                claim["claim_id"] != _id("claim", identity)
                or not set(claim["evidence_refs"]) <= known_evidence
            ):
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
                    any(
                        literal not in source_text
                        for literal in _TECHNICAL.findall(claim["text"])
                    )
                    or any(
                        literal not in claim["text"]
                        for literal in _TECHNICAL.findall(source_text)
                    )
                    or (
                        _CODE_OR_FORMULA.search(source_text)
                        and claim["text"] not in source_text
                    )
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
    return True


def _validate_document_tree(document):
    """確認章節與概念歸屬能由有序 Evidence 重建。"""
    evidence = document["evidence"]
    concepts = document["concepts"]
    known_concepts = {concept["concept_id"] for concept in concepts}
    tree = document["document_tree"]
    if (
        not isinstance(tree, dict)
        or set(tree) != {"material_id", "sections"}
        or tree["material_id"] != document["material_id"]
    ):
        return False
    sections = tree["sections"]
    if (
        not isinstance(sections, list)
        or [section.get("order") for section in sections] != list(range(len(sections)))
    ):
        return False
    tree_concepts = [
        concept_id
        for section in sections
        for concept_id in section.get("concept_ids", [])
    ]
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
    return True


def _validate_relations_and_path(document):
    """確認關係的端點、來源與方向，以及完整的學習路徑。"""
    evidence = document["evidence"]
    concepts = document["concepts"]
    relations = document["relations"]
    sections = document["document_tree"]["sections"]
    known_evidence = {item["evidence_id"] for item in evidence}
    known_concepts = {concept["concept_id"] for concept in concepts}
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
    return True


def _validate_status(document):
    """由排除頁、修復紀錄與概念數重算產品狀態。"""
    excluded_pages = document["excluded_pages"]
    metrics = document["metrics"]
    concepts = document["concepts"]
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
        "processing": (
            "partial" if reasons and concepts else ("failed" if not concepts else "succeeded")
        ),
        "quality": "needs_review" if reasons else "accepted",
        "decision": "reject" if not concepts else ("review" if reasons else "retain"),
        "reason_codes": reasons,
    }
    return document["status"] == expected_status


def _validate_structure_content(document, source_digest):
    try:
        if (
            not isinstance(source_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_digest) is None
            or document["material_id"] != f"material:sha256:{source_digest}"
        ):
            return False
        execution = document.get("execution_identity")
        provenance = document["provenance"]
        try:
            produced_at = datetime.fromisoformat(document["produced_at"])
            UUID(document["run_id"])
        except (AttributeError, TypeError, ValueError):
            return False
        if (
            produced_at.tzinfo is None
            or not isinstance(provenance, dict)
            or set(provenance) != {
                "runtime_lock_sha256", "model_id", "model_revision", "semantic_policy"
            }
            or re.fullmatch(r"[0-9a-f]{64}", provenance["runtime_lock_sha256"]) is None
            or not isinstance(provenance["model_id"], str) or not provenance["model_id"]
            or not isinstance(provenance["model_revision"], str) or not provenance["model_revision"]
            or (
                execution is None
                and (
                    provenance["model_id"] != "google/gemma-4-31B-it-qat-w4a16-ct"
                    or provenance["model_revision"] != "52f3f65bc7a02d555763bc923bd1d9094898219d"
                )
            )
            or (
                execution is not None
                and any(
                    execution[k] != provenance[k]
                    for k in ("model_id", "model_revision", "runtime_lock_sha256")
                )
            )
            or provenance["semantic_policy"] != "unified-material-evidence-projection/v1"
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
        if not _validate_evidence_and_pages(document, source_digest):
            return False
        if not _validate_concepts(document):
            return False
        if not _validate_document_tree(document):
            return False
        if not _validate_relations_and_path(document):
            return False
        return _validate_status(document)
    except (KeyError, TypeError, ValueError):
        return False
