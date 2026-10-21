"""以原檔與 normalized 來源辨識跨版本不變的 Evidence 與 Claim。"""

from collections import defaultdict
from copy import deepcopy
import unicodedata

from content_identity import canonical_sha256

from .semantic_projection import SemanticState


def page_source(document, page):
    """將集合閱讀頁碼對回原來源與該來源的 normalized 頁碼。"""
    binding = document["input_binding"]
    location = binding["bundle"]["pages"][page - 1]
    source = next(
        item
        for item in binding["manifest"]["items"]
        if item["source_id"] == location["source_id"]
    )
    source_digest = canonical_sha256(
        {
            "original": source["original_sha256"],
            "normalized": source["normalized_sha256"],
        }
    )
    return source_digest, location["normalized_page"]


def evidence_identity(document, evidence):
    source_digest, source_page = page_source(document, evidence["page"])
    return canonical_sha256(
        {
            "normalized_sha256": source_digest,
            "page": source_page,
            "kind": evidence["kind"],
            "text": evidence["exact_text"],
            "order": evidence["block_order"],
            "region": evidence["source_locator"]["region"],
        }
    )


def _claims(document):
    evidence = {
        item["evidence_id"]: evidence_identity(document, item)
        for item in document["evidence"]
    }
    result = defaultdict(list)
    for concept in document["concepts"]:
        for claim in concept["claims"]:
            identity = canonical_sha256(
                {
                    "text": claim["text"],
                    "evidence": sorted(
                        evidence[span["evidence_id"]] for span in claim["source_spans"]
                    ),
                }
            )
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


def _concept_name(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def map_concepts(before, after):
    """「移到新版」的概念對應；回傳 {舊概念: {to, basis, reason, shared, total}}。

    1. 來源 Evidence（basis=evidence）：舊概念各重點引用的原文區塊，以與 unchanged_claims
       相同的來源身分（原檔／normalized hash、來源頁碼、區塊順序、範圍與原文）比對。
       引用最多這些區塊的新概念若唯一，且涵蓋舊概念至少一半的區塊，即為對應概念。
       重新分析會改寫重點文字，但教材原文區塊不變，因此以原文區塊為主要依據。
    2. 名稱（basis=label）：沒有符合第 1 項的新概念時，舊概念名稱或別名（NFKC、不分大小寫、
       合併空白）與唯一一個新概念的名稱或別名相同。
    3. 其餘不承接：區塊分散到多個新概念為 split，名稱對到多個新概念為 ambiguous，
       都找不到為 not_found。不猜測語意相近的名稱，也不拆分舊概念。
    多個舊概念可以對應同一個新概念（新版合併）。
    """

    def blocks(document):
        identities = {
            item["evidence_id"]: evidence_identity(document, item)
            for item in document["evidence"]
        }
        return {
            concept["concept_id"]: {
                identities[span["evidence_id"]]
                for claim in concept["claims"]
                for span in claim["source_spans"]
            }
            for concept in document["concepts"]
        }

    def names(concept):
        return {_concept_name(concept["label"])} | {
            _concept_name(alias) for alias in concept.get("aliases", [])
        }

    new_blocks = blocks(after)
    citing = defaultdict(set)
    for identity, items in new_blocks.items():
        for item in items:
            citing[item].add(identity)
    by_name = defaultdict(set)
    for concept in after["concepts"]:
        for name in names(concept):
            by_name[name].add(concept["concept_id"])
    originals = {concept["concept_id"]: concept for concept in before["concepts"]}
    result = {}
    for concept, items in blocks(before).items():
        counts = defaultdict(int)
        for item in items:
            for identity in citing.get(item, ()):
                counts[identity] += 1
        best = max(counts.values(), default=0)
        leaders = [identity for identity, count in counts.items() if count == best]
        mapped = {"to": None, "basis": None, "reason": None, "shared": best, "total": len(items)}
        if best and len(leaders) == 1 and best * 2 >= len(items):
            result[concept] = {**mapped, "to": leaders[0], "basis": "evidence"}
            continue
        candidates = set().union(
            *(by_name.get(name, set()) for name in names(originals[concept]))
        )
        if len(candidates) == 1:
            result[concept] = {**mapped, "to": next(iter(candidates)), "basis": "label"}
        else:
            reason = "ambiguous" if candidates else "split" if best else "not_found"
            result[concept] = {**mapped, "reason": reason}
    return result


def unchanged_concepts(before, after):
    """增補時只略過內容、章節脈絡及相連關係皆不變的概念；不是新的品質驗收。"""

    def identities(document):
        evidence = {
            item["evidence_id"]: evidence_identity(document, item)
            for item in document["evidence"]
        }
        titles = {
            section["section_id"]: section["title"]
            for section in document["document_tree"]["sections"]
        }
        concepts = {}
        for concept in document["concepts"]:
            concepts[concept["concept_id"]] = canonical_sha256(
                {
                    "label": concept["label"],
                    "aliases": concept["aliases"],
                    "sections": [
                        titles[identity] for identity in concept["section_ids"]
                    ],
                    "claims": [
                        {
                            "text": claim["text"],
                            "projection": claim["projection"],
                            "sources": [
                                (evidence[span["evidence_id"]], span["quote"])
                                for span in claim["source_spans"]
                            ],
                        }
                        for claim in concept["claims"]
                    ],
                }
            )
        relations = defaultdict(list)
        for relation in document["relations"]:
            signature = canonical_sha256(
                {
                    "source": concepts[relation["source_concept_id"]],
                    "target": concepts[relation["target_concept_id"]],
                    **{
                        key: relation[key]
                        for key in (
                            "type",
                            "learner_reason",
                            "inference_basis",
                            "confidence",
                        )
                    },
                    "evidence": [
                        evidence[identity] for identity in relation["evidence_refs"]
                    ],
                }
            )
            for endpoint in ("source_concept_id", "target_concept_id"):
                relations[relation[endpoint]].append(signature)
        return {
            identity: canonical_sha256(
                {"content": signature, "relations": sorted(relations[identity])}
            )
            for identity, signature in concepts.items()
        }

    old = defaultdict(list)
    for identity, signature in identities(before).items():
        old[signature].append(identity)
    new = identities(after)
    counts = defaultdict(int)
    for signature in new.values():
        counts[signature] += 1
    return {
        identity
        for identity, signature in new.items()
        if len(old[signature]) == counts[signature] == 1
    }


def seed_incremental_state(before, context, binding):
    """依來源綁定重建語意狀態與集合頁碼。"""
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
            claims.append(
                {
                    "text": claim["text"],
                    "projection": claim["projection"],
                    "source_spans": source_spans,
                }
            )
        state.concepts[keys[concept["concept_id"]]] = {
            "label": concept["label"],
            "aliases": deepcopy(concept["aliases"]),
            "claims": claims,
        }

    for relation in before["relations"]:
        matched = [references[reference] for reference in relation["evidence_refs"]]
        state.relations.append(
            {
                "source_concept": keys[relation["source_concept_id"]],
                "target_concept": keys[relation["target_concept_id"]],
                **{
                    key: deepcopy(relation[key])
                    for key in (
                        "type",
                        "learner_reason",
                        "inference_basis",
                        "confidence",
                    )
                },
                "evidence_refs": [item["evidence_id"] for item in matched],
                "context_refs": list(
                    dict.fromkeys(item["section_id"] for item in matched)
                ),
            }
        )
    return state
