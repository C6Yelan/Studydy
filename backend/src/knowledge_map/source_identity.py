"""以原檔與 normalized 來源辨識跨版本不變的 Evidence 與 Claim。"""

from collections import defaultdict
from copy import deepcopy

from pdf_evidence.ocr_page_evidence import canonical_sha256

from .semantic_projection import SemanticState


def page_source(document, page):
    """將集合閱讀頁碼對回原來源與該來源的 normalized 頁碼。"""
    binding = document["input_binding"]
    location = binding["bundle"]["pages"][page - 1]
    source = next(
        item for item in binding["manifest"]["items"]
        if item["source_id"] == location["source_id"]
    )
    source_digest = canonical_sha256({
        "original": source["original_sha256"],
        "normalized": source["normalized_sha256"],
    })
    return source_digest, location["normalized_page"]


def evidence_identity(document, evidence):
    source_digest, source_page = page_source(document, evidence["page"])
    return canonical_sha256({
        "normalized_sha256": source_digest,
        "page": source_page,
        "kind": evidence["kind"],
        "text": evidence["exact_text"],
        "order": evidence["block_order"],
        "region": evidence["source_locator"]["region"],
    })


def _claims(document):
    evidence = {
        item["evidence_id"]: evidence_identity(document, item)
        for item in document["evidence"]
    }
    result = defaultdict(list)
    for concept in document["concepts"]:
        for claim in concept["claims"]:
            identity = canonical_sha256({
                "text": claim["text"],
                "evidence": sorted(
                    evidence[span["evidence_id"]] for span in claim["source_spans"]
                ),
            })
            result[identity].append((concept["concept_id"], claim["claim_id"]))
    return result


def unchanged_claims(before, after):
    """只承接一對一、文字與來源完全相同的知識點；不猜測拆分／合併。"""
    old_claims = _claims(before)
    new_claims = _claims(after)
    matches = {}
    for identity, old_items in old_claims.items():
        new_items = new_claims.get(identity, [])
        if len(old_items) == 1 and len(new_items) == 1:
            matches[old_items[0]] = new_items[0]
    return matches


def seed_incremental_state(before, context, binding):
    """用現行來源綁定重建可接續的語意狀態，不沿用舊閱讀頁碼。"""
    current = {"input_binding": binding}
    by_identity = {
        evidence_identity(current, item): item for item in context["evidence"]
    }
    if len(by_identity) != len(context["evidence"]):
        raise ValueError("INCREMENTAL_SOURCE_AMBIGUOUS")

    references = {}
    for item in before["evidence"]:
        matched = by_identity.get(evidence_identity(before, item))
        if matched is None:
            raise ValueError("INCREMENTAL_SOURCE_CHANGED")
        references[item["evidence_id"]] = matched

    state = SemanticState()
    state.source_review_required = before.get("source_review_required", False)
    keys = {
        concept["concept_id"]: f"saved_{index}"
        for index, concept in enumerate(before["concepts"])
    }
    for concept in before["concepts"]:
        claims = []
        for claim in concept["claims"]:
            source_spans = [
                {
                    "evidence_id": references[span["evidence_id"]]["evidence_id"],
                    "quote": span["quote"],
                }
                for span in claim["source_spans"]
            ]
            claims.append({
                "text": claim["text"],
                "projection": claim["projection"],
                "source_spans": source_spans,
            })
        state.concepts[keys[concept["concept_id"]]] = {
            "label": concept["label"],
            "aliases": deepcopy(concept["aliases"]),
            "claims": claims,
        }

    for relation in before["relations"]:
        matched = [references[reference] for reference in relation["evidence_refs"]]
        state.relations.append({
            "source_concept": keys[relation["source_concept_id"]],
            "target_concept": keys[relation["target_concept_id"]],
            **{
                key: deepcopy(relation[key])
                for key in ("type", "learner_reason", "inference_basis", "confidence")
            },
            "evidence_refs": [item["evidence_id"] for item in matched],
            "context_refs": list(dict.fromkeys(
                item["section_id"] for item in matched
            )),
        })
    return state
