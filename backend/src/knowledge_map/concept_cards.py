"""已授權、已驗證 published KS 的唯讀圖卡投影；不產生新知識。"""

from copy import deepcopy


def project_concept_cards(
    view: dict, material_id: str, selected_ids: list[str], canonical_evidence: list[dict],
) -> dict:
    concepts = {item["concept_id"]: item for item in view["concepts"]}
    selected = set(selected_ids)
    if not selected or not selected <= concepts.keys():
        raise ValueError("REQUEST_INVALID")
    # 順序由 published KS 決定，不受點選次序、重複 ID 或 renderer 排版影響。
    cards = [
        {key: deepcopy(item[key]) for key in ("concept_id", "label", "aliases", "claims")}
        for item in view["concepts"] if item["concept_id"] in selected
    ]
    evidence = {
        item["evidence_id"]: item
        for concept in view["concepts"] for claim in concept["claims"] for item in claim["evidence"]
    }
    exact_text = {item["evidence_id"]: item["exact_text"] for item in canonical_evidence}
    relations = []
    for relation in view["relations"]:
        if not selected.intersection((relation["source_concept_id"], relation["target_concept_id"])):
            continue
        if (
            relation["source_concept_id"] not in concepts
            or relation["target_concept_id"] not in concepts
            or not set(relation["evidence_refs"]) <= evidence.keys()
            or not set(relation["evidence_refs"]) <= exact_text.keys()
        ):
            raise ValueError("KNOWLEDGE_STRUCTURE_INVALID")
        relations.append({
            **{key: deepcopy(relation[key]) for key in (
                "relation_id", "source_concept_id", "target_concept_id", "type",
                "learner_reason", "evidence_refs",
            )},
            "source_label": concepts[relation["source_concept_id"]]["label"],
            "target_label": concepts[relation["target_concept_id"]]["label"],
            # relation 引用整個 Evidence 區塊，不以同區塊最後一條 claim 的節錄冒充。
            "evidence": [
                {**deepcopy(evidence[ref]), "quote": exact_text[ref]}
                for ref in relation["evidence_refs"]
            ],
        })
    return {
        "schema": "concept-cards/v1",
        "selection": {
            "material_id": material_id,
            "content_material_id": view["material_id"],
            "knowledge_structure_revision": view["knowledge_structure_revision"],
            "concept_ids": [card["concept_id"] for card in cards],
            "claim_ids": list(dict.fromkeys(claim["claim_id"] for card in cards for claim in card["claims"])),
            "relation_ids": [item["relation_id"] for item in relations],
            "policy": "manual-published-order/v1",
        },
        "source_resolver": view["source_resolver"],
        "status": deepcopy(view["status"]),
        "excluded_pages": deepcopy(view["excluded_pages"]),
        "cards": cards,
        "relations": relations,
    }
