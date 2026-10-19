"""已發布教材的按需讀取：摘要與學習規則分開，完整來源只讀指定概念。"""

from sqlalchemy import insert, select, text

from knowledge_map.source_identity import _claims
from learning_adaptation.learning_states import progress_plan
from learning_adaptation.map_context import _context_from_validated_document
from content_identity import canonical_sha256
from collections import defaultdict
from .knowledge_structures import (
    KnowledgeStructureStoreError,
    _read_verified_document,
    _view,
    _binding,
)
from .tables import (
    CardSourceIndex,
    CardSourceConcept,
    KnowledgeReadIndex,
    KnowledgeEvidenceRef,
    KnowledgeStructure,
    Material,
    MaterialProcessingRun,
    MaterialSourceSet,
)
from .database import database_session


def prepare_concept_index(
    session, owner, material_id, revision, *, document=None, rebuild=False
):
    key = (owner, material_id, revision)
    index = session.get(CardSourceIndex, key)
    if index is not None and not rebuild:
        return index
    # 新卡組沿用建立時已驗證的文件；升級前的卡組只補建一次，不呼叫模型。
    document = document or _read_verified_document(
        session, owner, material_id, revision=revision
    )
    view = _view(document, material_id)
    # 與地圖 API 的公開投影一致，不把內部推論／confidence 欄位送到卡片。
    view["concepts"] = [
        {key: c[key] for key in ("concept_id", "label", "aliases", "claims")}
        for c in view["concepts"]
    ]
    view["relations"] = [
        {
            key: r[key]
            for key in (
                "relation_id",
                "source_concept_id",
                "target_concept_id",
                "type",
                "learner_reason",
                "evidence_refs",
            )
        }
        for r in view["relations"]
    ]
    metadata = {
        name: view[name] for name in ("source_resolver", "status", "excluded_pages")
    }
    metadata["output_binding"] = _binding(document)
    metadata["source_binding"] = {
        key: document["input_binding"][key]
        for key in ("source_set_id", "source_set_digest", "bundle_manifest_sha256")
    }
    metadata["labels"] = {c["concept_id"]: c["label"] for c in view["concepts"]}
    if index is None:
        index = CardSourceIndex(
            learner_id=owner,
            material_id=material_id,
            structure_revision=revision,
        )
        session.add(index)
    index.metadata_ = metadata
    index.content_sha256 = canonical_sha256(metadata)
    session.flush()
    adjacent = defaultdict(list)
    for relation in view["relations"]:
        for identity in {relation["source_concept_id"], relation["target_concept_id"]}:
            adjacent[identity].append(relation)
    # 保留既有 Claim 節錄；關係專屬 Evidence 則使用 canonical 原文與來源位置。
    evidence = {
        e["evidence_id"]: e
        for concept in view["concepts"]
        for claim in concept["claims"]
        for e in claim["evidence"]
    }
    canonical = {item["evidence_id"]: item for item in document["evidence"]}
    sources = {
        item["source_id"]: item for item in document["input_binding"]["manifest"]["items"]
    }
    pages = document["input_binding"]["bundle"]["pages"]
    for relation in view["relations"]:
        for reference in relation["evidence_refs"]:
            if reference in evidence:
                continue
            item = canonical[reference]
            location = pages[item["page"] - 1]
            evidence[reference] = {
                **{
                    key: item[key]
                    for key in (
                        "evidence_id", "page_ref", "page", "block_order",
                        "kind", "source", "source_locator",
                    )
                },
                "quote": item["exact_text"],
                "source_id": location["source_id"],
                "source_name": sources[location["source_id"]]["original_name"],
                "normalized_page": location["normalized_page"],
            }
    stored = {}
    if rebuild:
        stored = {
            row.concept_id: row
            for row in session.scalars(
                select(CardSourceConcept).where(
                    CardSourceConcept.learner_id == owner,
                    CardSourceConcept.material_id == material_id,
                    CardSourceConcept.structure_revision == revision,
                )
            )
        }
    for position, concept in enumerate(view["concepts"]):
        relations = adjacent[concept["concept_id"]]
        refs = dict.fromkeys(
            ref for relation in relations for ref in relation["evidence_refs"]
        )
        payload = {
            "concept": concept,
            "relations": relations,
            "relation_evidence": [evidence[ref] for ref in refs],
        }
        row = stored.get(concept["concept_id"])
        if row is None:
            row = CardSourceConcept(
                learner_id=owner,
                material_id=material_id,
                structure_revision=revision,
                concept_id=concept["concept_id"],
            )
            session.add(row)
        row.position = position
        row.payload = payload
        row.content_sha256 = canonical_sha256(payload)
    session.flush()
    return index


def source_metadata(session, owner, material_id, revision):
    index = session.get(CardSourceIndex, (owner, material_id, revision))
    if index is None:
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    value = index.metadata_
    binding = session.execute(
        select(
            MaterialProcessingRun.output_binding,
            MaterialProcessingRun.input_source_set_id,
            MaterialSourceSet.digest,
            MaterialProcessingRun.bundle_manifest_sha256,
            MaterialProcessingRun.runtime_binding,
            MaterialProcessingRun.runtime_lock_document,
        )
        .join(
            KnowledgeStructure,
            KnowledgeStructure.run_id == MaterialProcessingRun.run_id,
        )
        .join(
            MaterialSourceSet,
            MaterialSourceSet.source_set_id
            == MaterialProcessingRun.input_source_set_id,
        )
        .where(
            KnowledgeStructure.learner_id == owner,
            KnowledgeStructure.material_id == material_id,
            KnowledgeStructure.structure_revision == revision,
            MaterialProcessingRun.status.in_(("succeeded", "partial")),
        )
    ).one_or_none()
    from ..material_runtime import lock_matches_binding

    if (
        canonical_sha256(value) != index.content_sha256
        or binding is None
        or binding[0] != value["output_binding"]
        or dict(
            zip(
                ("source_set_id", "source_set_digest", "bundle_manifest_sha256"),
                (str(binding[1]), binding[2], binding[3]),
            )
        )
        != value["source_binding"]
        or not lock_matches_binding(binding[5], binding[4])
        or binding[4]["runtime_lock_sha256"]
        != value["output_binding"]["runtime_lock_sha256"]
    ):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    return value


def prepare(session, owner, material_id, document):
    """由 publisher 或既有資料升級呼叫；不在一般 GET 交易中重新建索引。"""
    revision = document["revision"]
    prepare_concept_index(session, owner, material_id, revision, document=document)
    if session.get(KnowledgeReadIndex, (owner, material_id, revision)) is not None:
        return
    concepts = [
        {
            "concept_id": c["concept_id"],
            "label": c["label"],
            "aliases": c["aliases"],
            "claim_count": len(c["claims"]),
        }
        for c in document["concepts"]
    ]
    outline = {
        "schema": "knowledge-outline/v1",
        "material_id": str(material_id),
        "knowledge_structure_revision": revision,
        "run_id": document["run_id"],
        "source_resolver": f"/v1/materials/{material_id}/knowledge-structures/{revision}/evidence",
        "status": document["status"],
        "concepts": concepts,
        "relations": [
            {
                key: r[key]
                for key in (
                    "relation_id",
                    "source_concept_id",
                    "target_concept_id",
                    "type",
                )
            }
            for r in document["relations"]
        ],
        "section_count": len(document["document_tree"]["sections"]),
        "initial_learning_path": document["initial_learning_path"],
        "excluded_pages": document["excluded_pages"],
    }
    learning = progress_plan(_context_from_validated_document(material_id, document))
    learning["path_steps"] = document["initial_learning_path"]
    learning["claim_signatures"] = dict(_claims(document))
    learning["run_id"] = document["run_id"]
    session.add(
        KnowledgeReadIndex(
            learner_id=owner,
            material_id=material_id,
            structure_revision=revision,
            outline=outline,
            outline_sha256=canonical_sha256(outline),
            learning=learning,
            learning_sha256=canonical_sha256(learning),
        )
    )
    session.flush()
    pages = document["input_binding"]["bundle"]["pages"]
    references = []
    for evidence in document["evidence"]:
        location = pages[evidence["page"] - 1]
        payload = {
            "source_id": location["source_id"],
            "normalized_page": location["normalized_page"],
            "source_locator": evidence["source_locator"],
        }
        references.append(
            {
                "learner_id": owner,
                "material_id": material_id,
                "structure_revision": revision,
                "evidence_id": evidence["evidence_id"],
                "payload": payload,
                "content_sha256": canonical_sha256(payload),
            }
        )
    if references:
        session.execute(insert(KnowledgeEvidenceRef), references)


def metadata(session, owner, material_id, revision):
    snapshot = session.info.get("read_snapshot")
    cache = snapshot.setdefault("read_metadata", {}) if snapshot is not None else None
    key = (owner, material_id, revision)
    if cache is not None and key in cache:
        return cache[key]
    if (
        session.scalar(
            select(Material.material_id).where(
                Material.learner_id == owner,
                Material.material_id == material_id,
                Material.discard_requested_at.is_(None),
            )
        )
        is None
    ):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    # metadata 本身有雜湊，並核對已發布 run、revision 與來源集合的綁定。
    value = source_metadata(session, owner, material_id, revision)
    if cache is not None:
        cache[key] = value
    return value


def _read(session, owner, material_id, revision, name):
    metadata(session, owner, material_id, revision)
    snapshot = session.info.get("read_snapshot")
    cache = snapshot.setdefault("read_indexes", {}) if snapshot is not None else None
    key = (owner, material_id, revision, name)
    if cache is not None and key in cache:
        return cache[key]
    row = session.execute(
        select(
            getattr(KnowledgeReadIndex, name),
            getattr(KnowledgeReadIndex, name + "_sha256"),
        ).where(
            KnowledgeReadIndex.learner_id == owner,
            KnowledgeReadIndex.material_id == material_id,
            KnowledgeReadIndex.structure_revision == revision,
        )
    ).one_or_none()
    if (
        row is None
        or canonical_sha256(row[0]) != row[1]
        or row[0]["knowledge_structure_revision"] != revision
    ):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    if cache is not None:
        cache[key] = row[0]
    return row[0]


def outline(session, owner, material_id, revision):
    return _read(session, owner, material_id, revision, "outline")


def learning(session, owner, material_id, revision):
    return _read(session, owner, material_id, revision, "learning")


def concept_context(
    session, owner, material_id, revision, identities, *, neighbors=False
):
    meta = metadata(session, owner, material_id, revision)
    if (
        not identities
        or len(identities) > 32
        or len(set(identities)) != len(identities)
    ):
        raise ValueError("REQUEST_INVALID")

    def read(ids):
        return read_concepts(session, owner, material_id, revision, ids)

    selected = read(identities)
    relations = {
        r["relation_id"]: r for row in selected for r in row.payload["relations"]
    }
    related = {
        r[k]
        for r in relations.values()
        for k in ("source_concept_id", "target_concept_id")
    } - set(identities)
    rows = sorted(
        [*selected, *(read(related) if neighbors else [])], key=lambda row: row.position
    )
    evidence = {
        e["evidence_id"]: e
        for row in selected
        for e in row.payload["relation_evidence"]
    }
    return {
        "schema": "knowledge-concepts/v1",
        "material_id": str(material_id),
        "knowledge_structure_revision": revision,
        "requested_concept_ids": identities,
        "source_resolver": meta["source_resolver"],
        "status": meta["status"],
        "concepts": [row.payload["concept"] for row in rows],
        "relations": list(relations.values()),
        "relation_evidence": list(evidence.values()),
    }


def evidence_source(session, owner, material_id, revision, evidence_id):
    from ..errors import SourceError

    metadata(session, owner, material_id, revision)
    reference = session.get(
        KnowledgeEvidenceRef, (owner, material_id, revision, evidence_id)
    )
    if reference is None:
        raise SourceError("RESOURCE_NOT_FOUND")
    if canonical_sha256(reference.payload) != reference.content_sha256:
        raise SourceError("SOURCE_BINDING_INVALID")
    row = session.execute(
        select(MaterialSourceSet.manifest, MaterialSourceSet.digest)
        .join(
            MaterialProcessingRun,
            MaterialProcessingRun.input_source_set_id
            == MaterialSourceSet.source_set_id,
        )
        .join(
            KnowledgeStructure,
            KnowledgeStructure.run_id == MaterialProcessingRun.run_id,
        )
        .where(
            KnowledgeStructure.learner_id == owner,
            KnowledgeStructure.material_id == material_id,
            KnowledgeStructure.structure_revision == revision,
        )
    ).one_or_none()
    if row is None or canonical_sha256(row[0]) != row[1]:
        raise SourceError("SOURCE_BINDING_INVALID")
    source = next(
        (
            item
            for item in row[0]["items"]
            if item["source_id"] == reference.payload["source_id"]
        ),
        None,
    )
    if source is None:
        raise SourceError("SOURCE_BINDING_INVALID")
    return source, reference.payload


def search(session, owner, material_id, revision, query):
    metadata(session, owner, material_id, revision)
    # 搜尋全部重點文字，但只回傳命中的 ID；不把全部原文送到瀏覽器。
    pattern = (
        "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )
    statement = text("""
        SELECT concept_id FROM card_source_concepts
        WHERE learner_id=:owner AND material_id=:material AND structure_revision=:revision
          AND ((payload->'concept'->>'label') ILIKE :pattern
            OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(payload->'concept'->'aliases') a WHERE a ILIKE :pattern)
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'concept'->'claims') c WHERE c->>'text' ILIKE :pattern))
        ORDER BY position
    """)
    return {
        "schema": "knowledge-search/v1",
        "material_id": str(material_id),
        "knowledge_structure_revision": revision,
        "concept_ids": list(
            session.scalars(
                statement,
                {
                    "owner": owner,
                    "material": material_id,
                    "revision": revision,
                    "pattern": pattern,
                },
            )
        ),
    }


def backfill(*, dsn=None):
    """明確補建缺失索引／關係引用；只更新衍生投影，不改 canonical 資料。"""
    with database_session(dsn) as session:
        rows = session.execute(text("""
            SELECT k.learner_id, k.material_id, k.structure_revision
            FROM knowledge_structures k
            LEFT JOIN card_source_indexes c USING (learner_id, material_id, structure_revision)
            LEFT JOIN knowledge_read_indexes r USING (learner_id, material_id, structure_revision)
            WHERE c.structure_revision IS NULL OR r.structure_revision IS NULL
               OR EXISTS (
                   SELECT 1 FROM card_source_concepts p
                   WHERE p.learner_id=k.learner_id AND p.material_id=k.material_id
                     AND p.structure_revision=k.structure_revision
                     AND NOT (
                         jsonb_path_query_array(p.payload, '$.relation_evidence[*].evidence_id')
                         @> jsonb_path_query_array(p.payload, '$.relations[*].evidence_refs[*]')
                     )
               )
        """)).all()
    for owner, material_id, revision in rows:
        with database_session(dsn) as session:
            session.scalar(
                select(Material)
                .where(
                    Material.learner_id == owner, Material.material_id == material_id
                )
                .with_for_update()
            )
            document = _read_verified_document(
                session, owner, material_id, revision=revision
            )
            prepare_concept_index(
                session, owner, material_id, revision, document=document, rebuild=True
            )
            prepare(session, owner, material_id, document)
    return len(rows)


def read_concepts(
    session, owner, material_id, revision, identities, *, allow_missing=False
):
    """同一版本的共同來源讀取；卡片鄰居維持原本允許缺項的契約。"""
    if not identities:
        return []
    rows = session.scalars(
        select(CardSourceConcept)
        .where(
            CardSourceConcept.learner_id == owner,
            CardSourceConcept.material_id == material_id,
            CardSourceConcept.structure_revision == revision,
            CardSourceConcept.concept_id.in_(identities),
        )
        .order_by(CardSourceConcept.position)
    ).all()
    if (not allow_missing and len(rows) != len(identities)) or any(
        canonical_sha256(row.payload) != row.content_sha256 for row in rows
    ):
        raise KnowledgeStructureStoreError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    return rows
