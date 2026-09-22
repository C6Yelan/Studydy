"""以未變的 normalized 來源／區塊辨識知識點，不依賴合併 PDF 的頁碼。"""
from collections import defaultdict
from copy import deepcopy

from pdf_evidence.ocr_page_evidence import canonical_sha256
from .structure import SemanticState


def page_source(document, page):
    binding = document["input_binding"]
    location = binding["bundle"]["pages"][page - 1]
    item = next(item for item in binding["manifest"]["items"] if item["source_id"] == location["source_id"])
    return canonical_sha256({"original": item["original_sha256"], "normalized": item["normalized_sha256"]}), location["normalized_page"]


def evidence_identity(document, evidence):
    digest, page = page_source(document, evidence["page"])
    return canonical_sha256({
        "normalized_sha256": digest, "page": page, "kind": evidence["kind"],
        "text": evidence["exact_text"], "order": evidence["block_order"],
        "region": evidence["source_locator"]["region"],
    })


def _claims(document):
    evidence = {e["evidence_id"]: evidence_identity(document, e) for e in document["evidence"]}
    result = defaultdict(list)
    for concept in document["concepts"]:
        for claim in concept["claims"]:
            identity = canonical_sha256({"text": claim["text"],
                "evidence": sorted(evidence[span["evidence_id"]] for span in claim["source_spans"])})
            result[identity].append((concept["concept_id"], claim["claim_id"]))
    return result


def unchanged_claims(before, after):
    """只承接一對一、文字與來源完全相同的知識點；不猜測拆分／合併。"""
    old, new = _claims(before), _claims(after)
    return {items[0]: new[key][0] for key, items in old.items()
            if len(items) == 1 and len(new.get(key, [])) == 1}


def seed_incremental_state(before, context, binding):
    current = {"input_binding": binding}
    by_identity = {evidence_identity(current, item): item for item in context["evidence"]}
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
    keys = {concept["concept_id"]: f"saved_{index}" for index, concept in enumerate(before["concepts"])}
    for concept in before["concepts"]:
        claims = []
        for claim in concept["claims"]:
            claims.append({"text": claim["text"], "projection": claim["projection"], "source_spans": [
                {"evidence_id": references[span["evidence_id"]]["evidence_id"], "quote": span["quote"]}
                for span in claim["source_spans"]]})
        state.concepts[keys[concept["concept_id"]]] = {
            "label": concept["label"], "aliases": deepcopy(concept["aliases"]), "claims": claims}
    for relation in before["relations"]:
        refs = [references[ref] for ref in relation["evidence_refs"]]
        state.relations.append({
            "source_concept": keys[relation["source_concept_id"]], "target_concept": keys[relation["target_concept_id"]],
            **{key: deepcopy(relation[key]) for key in ("type", "learner_reason", "inference_basis", "confidence")},
            "evidence_refs": [item["evidence_id"] for item in refs],
            "context_refs": list(dict.fromkeys(item["section_id"] for item in refs)),
        })
    return state
