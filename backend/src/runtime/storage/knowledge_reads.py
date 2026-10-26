"""已發布教材的按需讀取：摘要與學習規則分開，完整來源只讀指定概念。"""

import json
from uuid import UUID

from sqlalchemy import select, text

from content_identity import canonical_sha256
from collections import OrderedDict, defaultdict
from threading import Lock
from .knowledge_structures import (
    KnowledgeStructureStoreError,
    _read_verified_document,
    _view,
    _binding,
)
from .tables import (
    CardSourceIndex,
    CardSourceConcept,
    KnowledgeChapters,
    KnowledgeReadIndex,
    KnowledgeEvidenceRef,
    KnowledgeStructure,
    Material,
    MaterialProcessingRun,
    MaterialSourceSet,
)
from .database import database_session
from .artifacts import open_verified_artifact
from ..errors import SourceError


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
    # 關係引用完整區塊，不能被同區塊最後一個 Claim 的節錄覆蓋。
    evidence = {}
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


def _source_label(name):
    stem, dot, suffix = name.rpartition(".")
    return stem if dot and stem and suffix.isascii() and suffix.isalnum() and len(suffix) <= 5 else name


def group_regions(path, primary_sources):
    """依學習順序排列的概念分區。

    多來源教材以每個概念的主要來源檔分區；只有一個主要來源時改依學習順序
    切成至多四段，並以 basis 標示為推導分段，不冒充教材章節。
    """
    sources = {}
    for concept_id in path:
        source = primary_sources.get(concept_id)
        if source is not None:
            sources.setdefault(source[0], source[1])
    if len(sources) >= 2:
        assigned, previous = {}, None
        # 沒有來源的概念跟隨學習順序上的前一個概念；開頭缺來源者歸到第一個來源區。
        for concept_id in path:
            source = primary_sources.get(concept_id)
            previous = source[0] if source is not None else previous
            assigned[concept_id] = previous
        first = next(iter(sources))
        regions = {source_id: [] for source_id in sources}
        for concept_id in path:
            regions[assigned[concept_id] or first].append(concept_id)
        return "source", [
            {
                "region_id": f"region:{index}",
                "label": _source_label(sources[source_id]),
                "source_id": source_id,
                "concept_ids": concept_ids,
            }
            for index, (source_id, concept_ids) in enumerate(regions.items(), 1)
        ]
    count = min(4, max(1, len(path) // 4))
    size, extra = divmod(len(path), count)
    regions, start = [], 0
    for index in range(count):
        end = start + size + (index < extra)
        regions.append(
            {
                "region_id": f"region:{index + 1}",
                "label": f"第 {start + 1}–{end} 個概念" if end - start > 1 else f"第 {end} 個概念",
                "source_id": None,
                "concept_ids": path[start:end],
            }
        )
        start = end
    return "learning_order", regions


def learning_path(view):
    """建議學習順序；不在路徑上的概念依概念順序接在後面。"""
    path = [
        step["concept_id"]
        for step in sorted(view["initial_learning_path"], key=lambda s: s["position"])
    ]
    seen = set(path)
    return path + [c["concept_id"] for c in view["concepts"] if c["concept_id"] not in seen]


# 已發布版本的概念內容唯讀，來源統計不會改變；記住最近幾份，千頁教材的地圖分區
# 不必每次重新展開全部 Evidence。呼叫端都先經 outline 核對權限與完整性才會用到。
_SOURCE_ROWS: OrderedDict = OrderedDict()
_SOURCE_ROWS_LIMIT = 8
_SOURCE_ROWS_LOCK = Lock()


def concept_sources(session, owner, material_id, revision, path, *, with_pages=False):
    """每個概念的主要來源檔（重點引用最多者），with_pages 時另回傳引用頁碼。

    只取每個概念重點 Evidence 的來源欄位，不把整份概念內容解碼。
    """
    key = (owner, material_id, revision, with_pages)
    with _SOURCE_ROWS_LOCK:
        rows = _SOURCE_ROWS.get(key)
        if rows is not None:
            _SOURCE_ROWS.move_to_end(key)
    if rows is None:
        page_column = ", (e->>'page')::int" if with_pages else ", NULL::int"
        rows = session.execute(
            text(
                f"""
                SELECT c.concept_id, e->>'source_id', e->>'source_name'{page_column}, count(*)
                FROM card_source_concepts c
                CROSS JOIN LATERAL jsonb_array_elements(c.payload->'concept'->'claims') claim
                CROSS JOIN LATERAL jsonb_array_elements(claim->'evidence') e
                WHERE c.learner_id = :owner AND c.material_id = :material
                  AND c.structure_revision = :revision
                GROUP BY 1, 2, 3, 4
                """
            ),
            {"owner": owner, "material": material_id, "revision": revision},
        ).all()
        with _SOURCE_ROWS_LOCK:
            _SOURCE_ROWS[key] = rows
            while len(_SOURCE_ROWS) > _SOURCE_ROWS_LIMIT:
                _SOURCE_ROWS.popitem(last=False)
    known = set(path)
    counts, pages = {}, {}
    for concept_id, source_id, source_name, page, count in rows:
        if concept_id not in known:
            continue
        if page is not None:
            pages.setdefault(concept_id, set()).add(page)
        if source_id is None or source_name is None:
            continue
        key = (concept_id, source_id, source_name)
        counts[key] = counts.get(key, 0) + count
    best = {}
    for (concept_id, source_id, source_name), count in counts.items():
        # 取重點引用最多的來源；次數相同時依檔名決定，不受查詢順序影響。
        rank = (-count, source_name)
        if concept_id not in best or rank < best[concept_id][0]:
            best[concept_id] = (rank, (source_id, source_name))
    primary = {k: v[1] for k, v in best.items()}
    if with_pages:
        return primary, {k: sorted(v) for k, v in pages.items()}
    return primary


def chapter_regions(path, source_regions, chapters):
    """把已保存的章節轉成地圖分區；與目前概念全集不一致時回傳 None。

    多來源教材另回傳島群（來源檔分區），每章歸入成員最多的來源區，
    區域依島群、再依學習順序排列。
    """
    flat = [c for chapter in chapters for c in chapter["concept_ids"]]
    if len(flat) != len(set(flat)) or set(flat) != set(path):
        return None
    group_of = {c: r["region_id"] for r in source_regions for c in r["concept_ids"]}
    order = {r["region_id"]: i for i, r in enumerate(source_regions)}
    regions = []
    for index, chapter in enumerate(chapters, 1):
        votes = {}
        for concept_id in chapter["concept_ids"]:
            group = group_of.get(concept_id)
            votes[group] = votes.get(group, 0) + 1
        group = min(votes, key=lambda g: (-votes[g], order.get(g, len(order))))
        regions.append(
            {
                "region_id": f"chapter:{index}",
                "label": chapter["title"],
                "source_id": None,
                "concept_ids": list(chapter["concept_ids"]),
                "group_id": group,
            }
        )
    used = {r["group_id"] for r in regions}
    groups = [
        {"group_id": r["region_id"], "label": r["label"], "source_id": r["source_id"]}
        for r in source_regions
        if r["region_id"] in used
    ]
    if len(groups) < 2:
        groups = []
        for region in regions:
            region["group_id"] = None
    else:
        regions.sort(key=lambda r: order[r["group_id"]])
    return regions, groups


def regions(session, owner, material_id, revision):
    view = outline(session, owner, material_id, revision)
    path = learning_path(view)
    basis, grouped = group_regions(
        path, concept_sources(session, owner, material_id, revision, path)
    )
    result = {
        "schema": "knowledge-regions/v1",
        "material_id": str(material_id),
        "knowledge_structure_revision": revision,
        "basis": basis,
        "regions": [{**region, "group_id": None} for region in grouped],
        "groups": [],
        "partial": False,
    }
    stored = session.execute(
        select(KnowledgeChapters.status, KnowledgeChapters.chapters, KnowledgeChapters.error_code).where(
            KnowledgeChapters.learner_id == owner,
            KnowledgeChapters.material_id == material_id,
            KnowledgeChapters.structure_revision == revision,
        )
    ).one_or_none()
    if stored is not None and stored.error_code == "LUNA_CALL_LIMIT_EXCEEDED":
        result["error_code"] = stored.error_code
    if stored is None or (stored.status not in ("ready", "fallback") and not result.get("error_code")):
        return result
    if not stored.chapters:
        return result
    chapters = chapter_regions(
        path, grouped if basis == "source" else [], stored.chapters
    )
    if chapters is None:
        return result
    result.update(
        basis="chapters",
        regions=chapters[0],
        groups=chapters[1],
        partial=stored.status == "fallback",
    )
    return result


LIBRARY_SEARCH_CONCEPTS = 30
LIBRARY_SEARCH_MATERIALS = 10


def search_library(session, owner, query):
    """跨教材查教材名稱與各教材目前版本的概念名稱／別名。

    只讀每份教材一筆的 outline 索引，不掃描重點全文，千頁教材也能即時回應；
    概念的完整內容與來源仍由各教材的讀取 API 核對後提供。
    """
    query = query.strip()
    if not query:
        raise ValueError("REQUEST_INVALID")
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    values = {
        "owner": owner,
        "exact": query.lower(),
        "prefix": escaped + "%",
        "pattern": "%" + escaped + "%",
    }
    materials = session.execute(
        text(
            """
            SELECT m.material_id, m.display_name, k.run_id, k.structure_revision
            FROM materials m
            LEFT JOIN knowledge_structures k ON k.learner_id = m.learner_id
              AND k.material_id = m.material_id AND k.structure_revision = m.head_revision
            WHERE m.learner_id = :owner AND m.discard_requested_at IS NULL
              AND m.display_name ILIKE :pattern
            ORDER BY m.created_at DESC
            LIMIT :limit
            """
        ),
        {**values, "limit": LIBRARY_SEARCH_MATERIALS},
    ).all()
    concepts = session.execute(
        text(
            """
            SELECT m.material_id, m.display_name, k.run_id, k.structure_revision,
                   c.value->>'concept_id', c.value->>'label', (c.value->>'claim_count')::int,
                   CASE
                     WHEN lower(c.value->>'label') = :exact THEN 0
                     WHEN c.value->>'label' ILIKE :prefix THEN 1
                     WHEN c.value->>'label' ILIKE :pattern THEN 2
                     ELSE 3
                   END AS rank
            FROM materials m
            JOIN knowledge_structures k ON k.learner_id = m.learner_id
              AND k.material_id = m.material_id AND k.structure_revision = m.head_revision
            JOIN knowledge_read_indexes r ON r.learner_id = m.learner_id
              AND r.material_id = m.material_id AND r.structure_revision = m.head_revision
            CROSS JOIN LATERAL jsonb_array_elements(r.outline->'concepts') WITH ORDINALITY AS c(value, position)
            WHERE m.learner_id = :owner AND m.discard_requested_at IS NULL
              AND (c.value->>'label' ILIKE :pattern OR EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(c.value->'aliases') a WHERE a ILIKE :pattern))
            ORDER BY rank, m.created_at DESC, c.position
            LIMIT :limit
            """
        ),
        {**values, "limit": LIBRARY_SEARCH_CONCEPTS + 1},
    ).all()
    return {
        "schema": "library-search/v1",
        "query": query,
        "materials": [
            {
                "material_id": material_id,
                "display_name": name,
                "run_id": run_id,
                "knowledge_structure_revision": revision,
            }
            for material_id, name, run_id, revision in materials
        ],
        "concepts": [
            {
                "material_id": row[0],
                "material_name": row[1],
                "run_id": row[2],
                "knowledge_structure_revision": row[3],
                "concept_id": row[4],
                "label": row[5],
                "claim_count": row[6],
            }
            for row in concepts[:LIBRARY_SEARCH_CONCEPTS]
        ],
        "concepts_truncated": len(concepts) > LIBRARY_SEARCH_CONCEPTS,
    }


def _clock(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return (f"{hours}:" if hours else "") + f"{rest // 60:02d}:{rest % 60:02d}"


def resolve_evidence_source(owner, material_id, revision, evidence_id, *, dsn=None):
    with database_session(dsn) as session:
        source, evidence = evidence_source(
            session, owner, material_id, revision, evidence_id
        )
    normalized_page = evidence["normalized_page"]
    with open_verified_artifact(
        owner, UUID(source["mapping_artifact_id"]), dsn=dsn
    ) as opened:
        mapping = json.loads(opened.file.read())
    if (
        mapping["original_sha256"] != source["original_sha256"]
        or mapping["normalized_sha256"] != source["normalized_sha256"]
    ):
        raise SourceError("SOURCE_BINDING_INVALID")

    region = evidence["source_locator"]["region"]

    def overlaps(candidate):
        box = candidate.get("region")
        return box is None or (
            min(box[2], region[2]) > max(box[0], region[0])
            and min(box[3], region[3]) > max(box[1], region[1])
        )

    records = [
        record
        for record in mapping["records"]
        if (record.get("normalized_page") == normalized_page and overlaps(record))
        or any(
            candidate["normalized_page"] == normalized_page and overlaps(candidate)
            for candidate in record.get("candidates", [])
        )
    ]
    if not records:
        accuracy = "unavailable"
    elif all(record["accuracy"] == "exact" for record in records):
        accuracy = "exact"
    else:
        accuracy = "ambiguous"
    locators = [record["origin_locator"] for record in records]
    label = f"轉換後第 {normalized_page} 頁"
    if mapping["format"] == "pdf":
        label = f"原始教材第 {normalized_page} 頁"
    elif mapping["format"] == "pptx" and locators:
        label = f"原教材第 {locators[0]['original_slide_number']} 張投影片（轉換後第 {normalized_page} 頁）"
    elif mapping["format"] in ("txt", "md") and locators:
        first_line = min(locator["line_start"] for locator in locators)
        last_line = max(locator["line_end"] for locator in locators)
        label = f"原文第 {first_line}–{last_line} 行（轉換後第 {normalized_page} 頁）"
    elif mapping["format"] == "images" and locators:
        label = f"第 {locators[0]['image_number']} 張圖片"
    playback = None
    if mapping["format"] in ("audio", "video", "youtube") and locators:
        # 每頁是約 2 分鐘的逐字稿時間段；回查時從該段開頭播放。
        start = locators[0]["start_seconds"]
        end = locators[0]["end_seconds"]
        label = f"{_clock(start)}–{_clock(end)}（第 {normalized_page} 段逐字稿）"
        if mapping["format"] == "youtube":
            playback = {
                "kind": "youtube",
                "video_id": mapping["media"]["video_id"],
                "start_seconds": start,
                "end_seconds": end,
            }
        else:
            playback = {
                "kind": "audio",
                "url": f"/v1/artifacts/{source['original_artifact_id']}/media",
                "start_seconds": start,
                "end_seconds": end,
            }
    return {
        **({"playback": playback} if playback else {}),
        "schema": "evidence-source/v1",
        "format": mapping["format"],
        "original_name": source["original_name"],
        "original_url": f"/v1/artifacts/{source['original_artifact_id']}/download",
        "preview_url": f"/v1/artifacts/{source['normalized_artifact_id']}#page={normalized_page}",
        "normalized_page": normalized_page,
        "accuracy": accuracy,
        "origin_locators": locators,
        "label": label,
    }
