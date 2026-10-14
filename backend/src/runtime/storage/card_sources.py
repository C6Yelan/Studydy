"""卡片專用讀取投影；只在建立／升級時驗證全文，讀取核對本批內容與來源版本。"""

from collections import defaultdict

from sqlalchemy import select

from pdf_evidence.ocr_page_evidence import canonical_sha256
from .knowledge_structures import _binding, _view, _read_verified_document, KnowledgeStructureStoreError
from .tables import CardSourceConcept, CardSourceIndex, KnowledgeStructure, MaterialProcessingRun, MaterialSourceSet


def prepare(session, owner, material_id, revision, *, document=None):
    key = (owner, material_id, revision)
    index = session.get(CardSourceIndex, key)
    if index is not None:
        return index
    # 新卡組沿用建立時已驗證的文件；升級前的卡組只補建一次，不呼叫模型。
    document = document or _read_verified_document(session, owner, material_id, revision=revision)
    view = _view(document, material_id)
    # 與地圖 API 的公開投影一致，不把內部推論／confidence 欄位送到卡片。
    view['concepts'] = [{key: c[key] for key in ('concept_id', 'label', 'aliases', 'claims')} for c in view['concepts']]
    view['relations'] = [{key: r[key] for key in ('relation_id', 'source_concept_id', 'target_concept_id', 'type', 'learner_reason', 'evidence_refs')} for r in view['relations']]
    metadata = {name: view[name] for name in ('source_resolver', 'status', 'excluded_pages')}
    metadata['output_binding'] = _binding(document)
    metadata['source_binding'] = {key: document['input_binding'][key] for key in ('source_set_id', 'source_set_digest', 'bundle_manifest_sha256')}
    metadata['labels'] = {c['concept_id']: c['label'] for c in view['concepts']}
    index = CardSourceIndex(learner_id=owner, material_id=material_id, structure_revision=revision,
                            metadata_=metadata, content_sha256=canonical_sha256(metadata))
    session.add(index)
    session.flush()
    adjacent = defaultdict(list)
    for relation in view['relations']:
        for identity in {relation['source_concept_id'], relation['target_concept_id']}:
            adjacent[identity].append(relation)
    # 與完整前端索引相同：同一 Evidence 的最後一份概念投影為關係來源。
    evidence = {e['evidence_id']: e for concept in view['concepts'] for claim in concept['claims'] for e in claim['evidence']}
    for position, concept in enumerate(view['concepts']):
        relations = adjacent[concept['concept_id']]
        refs = dict.fromkeys(ref for relation in relations for ref in relation['evidence_refs'])
        payload = {'concept': concept, 'relations': relations,
                   'relation_evidence': [evidence[ref] for ref in refs if ref in evidence]}
        session.add(CardSourceConcept(learner_id=owner, material_id=material_id, structure_revision=revision,
            concept_id=concept['concept_id'], position=position, payload=payload,
            content_sha256=canonical_sha256(payload)))
    session.flush()
    return index


def metadata(session, owner, material_id, revision):
    index = session.get(CardSourceIndex, (owner, material_id, revision))
    if index is None:
        index = prepare(session, owner, material_id, revision)
    value = index.metadata_
    binding = session.execute(select(MaterialProcessingRun.output_binding, MaterialProcessingRun.input_source_set_id,
        MaterialSourceSet.digest, MaterialProcessingRun.bundle_manifest_sha256).join(KnowledgeStructure,
        KnowledgeStructure.run_id == MaterialProcessingRun.run_id).join(MaterialSourceSet,
        MaterialSourceSet.source_set_id == MaterialProcessingRun.input_source_set_id).where(
        KnowledgeStructure.learner_id == owner, KnowledgeStructure.material_id == material_id,
        KnowledgeStructure.structure_revision == revision,
        MaterialProcessingRun.status.in_(('succeeded', 'partial')))).one_or_none()
    if (canonical_sha256(value) != index.content_sha256 or binding is None or binding[0] != value['output_binding']
        or dict(zip(('source_set_id', 'source_set_digest', 'bundle_manifest_sha256'), (str(binding[1]), binding[2], binding[3]))) != value['source_binding']):
        raise KnowledgeStructureStoreError('KNOWLEDGE_STRUCTURE_UNAVAILABLE')
    return value


def _scope(owner, material_id, revision):
    return (CardSourceConcept.learner_id == owner, CardSourceConcept.material_id == material_id,
            CardSourceConcept.structure_revision == revision)


def navigation(row, meta):
    labels = meta['labels']
    if not set(row.concept_ids) <= set(labels):
        raise KnowledgeStructureStoreError('KNOWLEDGE_STRUCTURE_UNAVAILABLE')
    return [{'concept_id': identity, 'label': labels[identity], 'page_count': row.page_counts.get(identity)}
            for identity in row.concept_ids]


def batch(session, row, meta, offset, limit):
    from ..card_design import checked_design
    identities = row.concept_ids[offset:offset + limit]
    scope = _scope(row.learner_id, row.material_id, row.knowledge_structure_revision)
    def read(ids):
        rows = session.scalars(select(CardSourceConcept).where(*scope, CardSourceConcept.concept_id.in_(ids))
                               .order_by(CardSourceConcept.position)).all()
        for item in rows:
            if canonical_sha256(item.payload) != item.content_sha256:
                raise KnowledgeStructureStoreError('KNOWLEDGE_STRUCTURE_UNAVAILABLE')
        return rows
    selected = read(identities)
    if len(selected) != len(identities):
        raise KnowledgeStructureStoreError('KNOWLEDGE_STRUCTURE_UNAVAILABLE')
    relations = {r['relation_id']: r for item in selected for r in item.payload['relations']}
    neighbors = {r[key] for r in relations.values() for key in ('source_concept_id', 'target_concept_id')} - set(identities)
    context_rows = sorted([*selected, *read(neighbors)], key=lambda item: item.position)
    concepts = {item.concept_id: item.payload['concept'] for item in context_rows}
    evidence = {e['evidence_id']: e for item in selected for e in item.payload['relation_evidence']}
    results = row.generation.get('results', {}) if row.status == 'ready' else {}
    return {'schema': 'card-batch/v1', 'card_set_id': row.card_set_id, 'version': row.version,
            'offset': offset, 'total': len(row.concept_ids),
            'cards': [{**{key: concepts[identity][key] for key in ('concept_id', 'label', 'claims')},
                       'display_edits': results.get(identity, {}).get('edits', []),
                       'design': checked_design(results.get(identity, {}).get('design'))} for identity in identities],
            'context': {'source_resolver': meta['source_resolver'], 'status': meta['status'],
                        'concepts': list(concepts.values()), 'relations': list(relations.values()),
                        'relation_evidence': list(evidence.values())}}
