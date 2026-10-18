"""已發布教材的按需讀取：摘要與學習規則分開，完整來源只讀指定概念。"""

from sqlalchemy import insert, select, text

from knowledge_map.source_identity import _claims
from learning_adaptation.learning_states import progress_plan
from learning_adaptation.map_context import _context_from_validated_document
from content_identity import canonical_sha256
from . import card_sources
from .knowledge_structures import KnowledgeStructureStoreError, _read_verified_document
from .tables import (CardSourceConcept, KnowledgeReadIndex, KnowledgeEvidenceRef, KnowledgeStructure, Material,
                     MaterialProcessingRun, MaterialSourceSet,
                     database_session)


def prepare(session, owner, material_id, document):
    """由 publisher 或既有資料升級呼叫；不在一般 GET 交易中重新建索引。"""
    revision = document['revision']
    card_sources.prepare(session, owner, material_id, revision, document=document)
    if session.get(KnowledgeReadIndex, (owner, material_id, revision)) is not None:
        return
    concepts = [{
        'concept_id': c['concept_id'], 'label': c['label'], 'aliases': c['aliases'],
        'claim_count': len(c['claims']),
    } for c in document['concepts']]
    outline = {
        'schema': 'knowledge-outline/v1', 'material_id': str(material_id),
        'knowledge_structure_revision': revision, 'run_id': document['run_id'],
        'source_resolver': f'/v1/materials/{material_id}/knowledge-structures/{revision}/evidence',
        'status': document['status'], 'concepts': concepts,
        'relations': [{key: r[key] for key in ('relation_id', 'source_concept_id', 'target_concept_id', 'type')}
                      for r in document['relations']],
        'section_count': len(document['document_tree']['sections']),
        'initial_learning_path': document['initial_learning_path'],
        'excluded_pages': document['excluded_pages'],
    }
    learning = progress_plan(_context_from_validated_document(material_id, document))
    learning['path_steps'] = document['initial_learning_path']
    learning['claim_signatures'] = dict(_claims(document))
    learning['run_id'] = document['run_id']
    session.add(KnowledgeReadIndex(learner_id=owner, material_id=material_id, structure_revision=revision,
        outline=outline, outline_sha256=canonical_sha256(outline),
        learning=learning, learning_sha256=canonical_sha256(learning)))
    session.flush()
    pages = document['input_binding']['bundle']['pages']
    references = []
    for evidence in document['evidence']:
        location = pages[evidence['page'] - 1]
        payload = {'source_id': location['source_id'], 'normalized_page': location['normalized_page'],
                   'source_locator': evidence['source_locator']}
        references.append({'learner_id': owner, 'material_id': material_id, 'structure_revision': revision,
                           'evidence_id': evidence['evidence_id'], 'payload': payload,
                           'content_sha256': canonical_sha256(payload)})
    if references:
        session.execute(insert(KnowledgeEvidenceRef), references)


def metadata(session, owner, material_id, revision):
    snapshot = session.info.get('read_snapshot')
    cache = snapshot.setdefault('read_metadata', {}) if snapshot is not None else None
    key = (owner, material_id, revision)
    if cache is not None and key in cache:
        return cache[key]
    if session.scalar(select(Material.material_id).where(
        Material.learner_id == owner, Material.material_id == material_id,
        Material.discard_requested_at.is_(None),
    )) is None:
        raise KnowledgeStructureStoreError('KNOWLEDGE_STRUCTURE_UNAVAILABLE')
    # metadata 本身有雜湊，並核對已發布 run、revision 與來源集合的綁定。
    value = card_sources.metadata(session, owner, material_id, revision)
    if cache is not None:
        cache[key] = value
    return value


def _read(session, owner, material_id, revision, name):
    metadata(session, owner, material_id, revision)
    snapshot = session.info.get('read_snapshot')
    cache = snapshot.setdefault('read_indexes', {}) if snapshot is not None else None
    key = (owner, material_id, revision, name)
    if cache is not None and key in cache:
        return cache[key]
    row = session.execute(select(getattr(KnowledgeReadIndex, name),
                                 getattr(KnowledgeReadIndex, name + '_sha256')).where(
        KnowledgeReadIndex.learner_id == owner, KnowledgeReadIndex.material_id == material_id,
        KnowledgeReadIndex.structure_revision == revision,
    )).one_or_none()
    if row is None or canonical_sha256(row[0]) != row[1] or row[0]['knowledge_structure_revision'] != revision:
        raise KnowledgeStructureStoreError('KNOWLEDGE_STRUCTURE_UNAVAILABLE')
    if cache is not None:
        cache[key] = row[0]
    return row[0]


def outline(session, owner, material_id, revision):
    return _read(session, owner, material_id, revision, 'outline')


def learning(session, owner, material_id, revision):
    return _read(session, owner, material_id, revision, 'learning')


def concept_context(session, owner, material_id, revision, identities, *, neighbors=False):
    meta = metadata(session, owner, material_id, revision)
    if not identities or len(identities) > 32 or len(set(identities)) != len(identities):
        raise ValueError('REQUEST_INVALID')
    scope = (CardSourceConcept.learner_id == owner, CardSourceConcept.material_id == material_id,
             CardSourceConcept.structure_revision == revision)
    def read(ids):
        if not ids:
            return []
        rows = session.scalars(select(CardSourceConcept).where(
            *scope, CardSourceConcept.concept_id.in_(ids)).order_by(CardSourceConcept.position)).all()
        if len(rows) != len(ids) or any(canonical_sha256(row.payload) != row.content_sha256 for row in rows):
            raise KnowledgeStructureStoreError('KNOWLEDGE_STRUCTURE_UNAVAILABLE')
        return rows
    selected = read(identities)
    relations = {r['relation_id']: r for row in selected for r in row.payload['relations']}
    related = {r[k] for r in relations.values() for k in ('source_concept_id', 'target_concept_id')} - set(identities)
    rows = sorted([*selected, *(read(related) if neighbors else [])], key=lambda row: row.position)
    evidence = {e['evidence_id']: e for row in selected for e in row.payload['relation_evidence']}
    return {'schema': 'knowledge-concepts/v1', 'material_id': str(material_id),
        'knowledge_structure_revision': revision, 'requested_concept_ids': identities,
        'source_resolver': meta['source_resolver'], 'status': meta['status'],
        'concepts': [row.payload['concept'] for row in rows],
        'relations': list(relations.values()), 'relation_evidence': list(evidence.values())}


def evidence_source(session, owner, material_id, revision, evidence_id):
    from ..errors import SourceError
    metadata(session, owner, material_id, revision)
    reference = session.get(KnowledgeEvidenceRef, (owner, material_id, revision, evidence_id))
    if reference is None:
        raise SourceError('RESOURCE_NOT_FOUND')
    if canonical_sha256(reference.payload) != reference.content_sha256:
        raise SourceError('SOURCE_BINDING_INVALID')
    row = session.execute(select(MaterialSourceSet.manifest, MaterialSourceSet.digest).join(
        MaterialProcessingRun, MaterialProcessingRun.input_source_set_id == MaterialSourceSet.source_set_id
    ).join(KnowledgeStructure, KnowledgeStructure.run_id == MaterialProcessingRun.run_id).where(
        KnowledgeStructure.learner_id == owner, KnowledgeStructure.material_id == material_id,
        KnowledgeStructure.structure_revision == revision,
    )).one_or_none()
    if row is None or canonical_sha256(row[0]) != row[1]:
        raise SourceError('SOURCE_BINDING_INVALID')
    source = next((item for item in row[0]['items'] if item['source_id'] == reference.payload['source_id']), None)
    if source is None:
        raise SourceError('SOURCE_BINDING_INVALID')
    return source, reference.payload


def search(session, owner, material_id, revision, query):
    metadata(session, owner, material_id, revision)
    # 搜尋全部重點文字，但只回傳命中的 ID；不把全部原文送到瀏覽器。
    pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    statement = text("""
        SELECT concept_id FROM card_source_concepts
        WHERE learner_id=:owner AND material_id=:material AND structure_revision=:revision
          AND ((payload->'concept'->>'label') ILIKE :pattern
            OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(payload->'concept'->'aliases') a WHERE a ILIKE :pattern)
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'concept'->'claims') c WHERE c->>'text' ILIKE :pattern))
        ORDER BY position
    """)
    return {'schema': 'knowledge-search/v1', 'material_id': str(material_id),
            'knowledge_structure_revision': revision, 'concept_ids': list(session.scalars(statement,
                {'owner': owner, 'material': material_id, 'revision': revision, 'pattern': pattern}))}


def backfill(*, dsn=None):
    """部署時補建既有發布版本，不改原教材、revision、答案或來源。"""
    with database_session(dsn) as session:
        rows = session.execute(select(KnowledgeStructure.learner_id, KnowledgeStructure.material_id,
            KnowledgeStructure.structure_revision).outerjoin(KnowledgeReadIndex,
            (KnowledgeReadIndex.learner_id == KnowledgeStructure.learner_id) &
            (KnowledgeReadIndex.material_id == KnowledgeStructure.material_id) &
            (KnowledgeReadIndex.structure_revision == KnowledgeStructure.structure_revision))
            .where(KnowledgeReadIndex.structure_revision.is_(None))).all()
    for owner, material_id, revision in rows:
        with database_session(dsn) as session:
            session.scalar(select(Material).where(Material.learner_id == owner,
                Material.material_id == material_id).with_for_update())
            document = _read_verified_document(session, owner, material_id, revision=revision)
            prepare(session, owner, material_id, document)
    return len(rows)
