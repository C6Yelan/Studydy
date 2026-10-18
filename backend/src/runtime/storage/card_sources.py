"""卡組選取與顯示投影；共同教材來源由 knowledge_reads 讀取。"""

from .knowledge_structures import KnowledgeStructureStoreError
from .knowledge_reads import read_concepts


def navigation(row, meta):
    labels = meta["labels"]
    if not set(row.concept_ids) <= set(labels):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    return [
        {
            "concept_id": identity,
            "label": labels[identity],
            "page_count": row.page_counts.get(identity),
        }
        for identity in row.concept_ids
    ]


def batch(session, row, meta, offset, limit):
    from ..card_design import checked_design

    identities = row.concept_ids[offset : offset + limit]
    selected = read_concepts(
        session,
        row.learner_id,
        row.material_id,
        row.knowledge_structure_revision,
        identities,
    )
    relations = {
        r["relation_id"]: r for item in selected for r in item.payload["relations"]
    }
    neighbors = {
        r[key]
        for r in relations.values()
        for key in ("source_concept_id", "target_concept_id")
    } - set(identities)
    neighbor_rows = read_concepts(
        session,
        row.learner_id,
        row.material_id,
        row.knowledge_structure_revision,
        neighbors,
        allow_missing=True,
    )
    context_rows = sorted([*selected, *neighbor_rows], key=lambda item: item.position)
    concepts = {item.concept_id: item.payload["concept"] for item in context_rows}
    evidence = {
        e["evidence_id"]: e
        for item in selected
        for e in item.payload["relation_evidence"]
    }
    results = row.generation.get("results", {}) if row.status == "ready" else {}
    return {
        "schema": "card-batch/v1",
        "card_set_id": row.card_set_id,
        "version": row.version,
        "offset": offset,
        "total": len(row.concept_ids),
        "cards": [
            {
                **{
                    key: concepts[identity][key]
                    for key in ("concept_id", "label", "claims")
                },
                "display_edits": results.get(identity, {}).get("edits", []),
                "design": checked_design(results.get(identity, {}).get("design")),
            }
            for identity in identities
        ],
        "context": {
            "source_resolver": meta["source_resolver"],
            "status": meta["status"],
            "concepts": list(concepts.values()),
            "relations": list(relations.values()),
            "relation_evidence": list(evidence.values()),
        },
    }
