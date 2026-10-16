"""持久教材檢索；每次查詢只取候選原文，所有入口都核對 owner 與固定版本。"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert

from knowledge_map.retrieval import POLICY, distinct_passages, rank_texts, terms
from pdf_evidence.ocr_page_evidence import canonical_sha256
from .source_normalization import SourceError
from .storage.tables import Material, MaterialPassage, MaterialRetrievalIndex


def publish_index(db, owner, material_id, document, passages):
    """與知識結構同一交易发布；索引不完整時不能更新教材 head。"""
    revision = document['revision']
    checksum = canonical_sha256(passages)
    old = db.get(MaterialRetrievalIndex, (owner, material_id, revision))
    if old:
        if old.policy != POLICY or old.content_sha256 != checksum or old.passage_count != len(passages):
            raise SourceError('RETRIEVAL_INDEX_INVALID')
        return
    outline = {}
    for passage in passages:
        item = outline.setdefault(passage['source_id'], {'source': passage['evidence']['source_name'], 'concepts': []})
        item['concepts'] = list(dict.fromkeys([*item['concepts'], *passage['hints']]))
    db.add(MaterialRetrievalIndex(learner_id=owner, material_id=material_id,
        structure_revision=revision, policy=POLICY, passage_count=len(passages),
        content_sha256=checksum, outline=list(outline.values()), created_at=datetime.now(UTC)))
    db.flush()
    # 批次寫入避免每段往返 DB；索引文字與原文 payload 分開，原文不正規化。
    for start in range(0, len(passages), 128):
        values = []
        for passage in passages[start:start + 128]:
            hints = ' '.join(terms(' '.join(passage['hints'])))
            text = ' '.join(terms(passage['text']))
            keywords = func.setweight(func.to_tsvector(literal_column("'simple'::regconfig"), hints), literal_column("'A'")).op('||')(
                func.setweight(func.to_tsvector(literal_column("'simple'::regconfig"), text), literal_column("'D'")))
            values.append({'learner_id': owner, 'material_id': material_id, 'structure_revision': revision,
                'position': passage['position'], 'evidence_id': passage['evidence_id'],
                'source_id': UUID(passage['source_id']), 'page': passage['page'],
                'section_id': passage['section_id'], 'concept_ids': passage['concept_ids'],
                'payload': passage, 'keywords': keywords})
        db.execute(insert(MaterialPassage).values(values))


def _scope(db, owner, material_id, revision):
    material = db.scalar(select(Material).where(Material.learner_id == owner,
        Material.material_id == material_id, Material.discard_requested_at.is_(None)))
    if material is None:
        raise SourceError('RESOURCE_NOT_FOUND')
    index = db.get(MaterialRetrievalIndex, (owner, material_id, revision))
    if index is None:
        raise SourceError('RETRIEVAL_INDEX_REQUIRED')
    if index.policy != POLICY:
        raise SourceError('RETRIEVAL_INDEX_INVALID')
    return (MaterialPassage.learner_id == owner, MaterialPassage.material_id == material_id,
            MaterialPassage.structure_revision == revision)


def search(db, owner, material_id, revision, queries, *, limit=24, neighbors=True):
    scope = _scope(db, owner, material_id, revision)
    selected = {}
    rankings = []
    # 各子問題保留自己的候選名額，避免比較題只剩其中一方。
    for query in queries[:8]:
        words = list(dict.fromkeys(terms(query)))[:80]
        if not words:
            continue
        expression = ' | '.join("'" + word.replace("'", "''") + "'" for word in words)
        tsquery = func.to_tsquery(literal_column("'simple'::regconfig"), expression)
        candidates = list(db.scalars(select(MaterialPassage.payload).where(*scope,
            MaterialPassage.keywords.op('@@')(tsquery))
            .order_by(func.ts_rank_cd(literal_column('ARRAY[0.5, 0.5, 0.5, 1.0]::real[]'), MaterialPassage.keywords, tsquery, 32).desc(), MaterialPassage.position)
            .limit(limit * 4)))
        # DB 索引先縮小候選，再用詞彙稀有度排序；重複的通用章名不應壓過 Q/K/V 等具體術語。
        order = rank_texts(query, [' '.join(p['hints'])+' '+p['text'] for p in candidates], limit=len(candidates))
        ranked, deferred, represented = [], [], set()
        for index, _ in order:
            passage = candidates[index]
            concepts = set(passage['concept_ids'])
            # 同概念的公式碎片與表格格子稍後作上下文補回，不佔滿核心命中名額。
            if concepts and concepts <= represented:
                deferred.append(passage)
            else:
                ranked.append(passage)
                represented.update(concepts)
        ranked = (ranked + deferred)[:limit]
        if ranked:
            rankings.append(ranked)
        for rank, passage in enumerate(ranked):
            key = passage['position']
            if key not in selected:
                selected[key] = [passage, 0.0]
            selected[key][1] += 1 / (20 + rank)
    fused = [p for p, _ in sorted(selected.values(), key=lambda entry: (-entry[1], entry[0]['position']))]
    # 先保留各子問題的前幾名，再以融合分數補滿；共通關鍵字不能吃掉全部名額。
    found = []
    seen = set()
    if rankings:
        quota = max(1, limit // len(rankings))
        for rank in range(quota):
            for ranking in rankings:
                if rank < len(ranking) and ranking[rank]['position'] not in seen:
                    found.append(ranking[rank]);seen.add(ranking[rank]['position'])
    found.extend(p for p in fused if p['position'] not in seen)
    found = found[:limit]
    if neighbors and found:
        positions = {p['position'] + delta for p in found for delta in (-1, 1) if p['position'] + delta >= 0}
        extra = list(db.scalars(select(MaterialPassage.payload).where(*scope, MaterialPassage.position.in_(positions))))
        seen = {p['position'] for p in found}
        # 只補同來源同章節，不能把頁碼相同的另一份文件接成前後文。
        for passage in extra:
            if passage['position'] not in seen and any(
                abs(passage['position'] - hit['position']) == 1 and passage['source_id'] == hit['source_id']
                and passage['section_id'] == hit['section_id'] for hit in found[:limit]):
                found.append(passage)
                seen.add(passage['position'])
        concepts=list(dict.fromkeys(identity for hit in found[:limit] for identity in hit['concept_ids']))[:12]
        if concepts:
            related=db.scalars(select(MaterialPassage.payload).where(*scope,
                MaterialPassage.concept_ids.overlap(concepts)).order_by(MaterialPassage.position).limit(12 * len(concepts)))
            for passage in related:
                if passage['position'] not in seen:
                    found.append(passage);seen.add(passage['position'])
    return distinct_passages(found)


def catalog(db, owner, material_id, revision):
    """只提供查詢線索，不把目錄或概念名稱當作可回答的原文依據。"""
    _scope(db, owner, material_id, revision)
    index = db.get(MaterialRetrievalIndex, (owner, material_id, revision))
    return index.outline


def evidence_context(db, owner, material_id, revision, evidence_ids):
    """Podcast 的確切引用加局部原文；不再為少數引用載入整份教材。"""
    scope = _scope(db, owner, material_id, revision)
    found = list(db.scalars(select(MaterialPassage.payload).where(*scope,
        MaterialPassage.evidence_id.in_(evidence_ids)).order_by(MaterialPassage.position)))
    neighbors = {(p['position'] + delta, p['source_id'], p['page'])
                 for p in found for delta in (-1, 1) if p['position'] + delta >= 0}
    positions = {position for position, _, _ in neighbors}
    extra = list(db.scalars(select(MaterialPassage.payload).where(*scope, MaterialPassage.position.in_(positions)))) if positions else []
    by_position = {p['position']: p for p in found}
    for passage in extra:
        if (passage['position'], passage['source_id'], passage['page']) in neighbors:
            by_position[passage['position']] = passage
    return [by_position[key] for key in sorted(by_position)]
