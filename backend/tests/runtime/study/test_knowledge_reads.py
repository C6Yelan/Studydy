"""實際 DB 的按需讀取：來源一致、權限隔離、不再讀完整教材。"""
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from product_fixtures import ORIGIN, _app, closed_loop
from assessment_fixtures import answer, concept_fixture, create, finish
from runtime.storage import knowledge_reads
from runtime.storage.tables import CardSet, CardSourceConcept, CardSourceIndex, KnowledgeReadIndex, KnowledgeStructure
from runtime.storage.database import database_session
from runtime.api.models import project_knowledge_structure
from learning_adaptation.learner_progress import derive_learner_progress


def test_outline_detail_search_and_workspace_do_not_read_the_full_document(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 3)
    root = create(fixture)
    finish(fixture)
    owner = fixture['learner'].learner_id
    mid, rev = fixture['source'].material_id, fixture['document']['revision']
    from runtime.storage.knowledge_structures import _view
    full = project_knowledge_structure(_view(fixture['document'], mid)).model_dump(mode='json', by_alias=True)
    client = TestClient(_app(fixture['dsn'], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', fixture['token'])
    base = f'/v1/materials/{mid}/knowledge-structures/{rev}'
    import runtime.storage.knowledge_structures as storage
    def expensive(*args, **kwargs):
        raise AssertionError('Page reads must use the published indexes')
    monkeypatch.setattr(storage, '_read_verified_document', expensive)
    monkeypatch.setattr(storage, 'validate_knowledge_structure', expensive)
    outline = client.get(base + '/outline')
    assert outline.status_code == 200, outline.json()
    assert len(outline.json()['concepts']) == len(full['concepts'])
    assert all('claims' not in c for c in outline.json()['concepts'])
    identity = full['concepts'][0]['concept_id']
    detail = client.get(base + '/concepts', params={'ids': identity})
    assert detail.status_code == 200, detail.json()
    assert detail.json()['concepts'] == [full['concepts'][0]]
    assert detail.json()['requested_concept_ids'] == [identity]
    evidence = full['concepts'][0]['claims'][0]['evidence'][0]
    resolved = client.get(base + f"/evidence/{evidence['evidence_id']}/source")
    assert resolved.status_code == 200, resolved.json()
    assert resolved.json()['normalized_page'] == evidence['normalized_page']
    query = full['concepts'][0]['claims'][0]['text'][:80]
    found = client.get(base + '/search', params={'q': query})
    assert found.status_code == 200 and identity in found.json()['concept_ids']
    assert client.get(base + '/search', params={'q': '%_'}).json()['concept_ids'] == []
    assert client.get(base + '/concepts', params={'ids': identity + ',' + identity}).status_code == 400
    assert client.get(base + '/concepts', params={'ids': 'not-a-concept'}).status_code == 400
    workspace = client.get(base + f"/study-sessions/{fixture['study'].study_session_id}/workspace",
                           params={'run_id': fixture['document']['run_id']})
    assert workspace.status_code == 200, workspace.json()
    body = workspace.json()
    assert body['schema'] == 'study-workspace/v1' and 'knowledge_structure' not in body
    assert body['selected_set_id'] == str(root)
    assert len(body['context']['requested_concept_ids']) <= 3
    assert len(body['progress']['concept_states']) <= 3
    assert 'correct_option_id' not in workspace.text
    summary = client.get(f"/v1/study-sessions/{fixture['study'].study_session_id}/map-progress")
    assert summary.status_code == 200, summary.json()
    assert all('assessable_claim_ids' not in state for state in summary.json()['concept_states'])
    assert client.get(base.replace(str(mid), str(uuid4())) + '/outline').status_code == 404
    client.cookies.clear()
    assert client.get(base + '/outline').status_code == 401


@pytest.mark.parametrize('part', ['outline', 'learning', 'concept'])
def test_corrupted_read_projection_is_rejected(closed_loop, part):
    owner, source, _, document, dsn, _ = closed_loop
    mid, rev = source.material_id, document['revision']
    with database_session(dsn) as db:
        if part == 'concept':
            row = db.get(CardSourceConcept, (owner.learner_id, mid, rev, document['concepts'][0]['concept_id']))
            changed = deepcopy(row.payload)
            changed['concept']['claims'][0]['text'] = 'tampered'
            row.payload = changed
        else:
            row = db.get(KnowledgeReadIndex, (owner.learner_id, mid, rev))
            changed = deepcopy(getattr(row, part)); changed['run_id'] = str(uuid4())
            setattr(row, part, changed)
    from runtime.storage.knowledge_structures import KnowledgeStructureStoreError
    with database_session(dsn) as db, pytest.raises(KnowledgeStructureStoreError):
        if part == 'concept':
            knowledge_reads.concept_context(db, owner.learner_id, mid, rev, [document['concepts'][0]['concept_id']])
        else:
            getattr(knowledge_reads, part)(db, owner.learner_id, mid, rev)


def test_backfill_recreates_only_missing_projection_without_changing_canonical_data(closed_loop):
    owner, source, _, document, dsn, _ = closed_loop
    with database_session(dsn) as db:
        before = knowledge_reads.outline(db, owner.learner_id, source.material_id, document['revision'])
        db.execute(delete(KnowledgeReadIndex))
    assert knowledge_reads.backfill(dsn=dsn) == 1
    assert knowledge_reads.backfill(dsn=dsn) == 0
    with database_session(dsn) as db:
        assert knowledge_reads.outline(db, owner.learner_id, source.material_id, document['revision']) == before


@pytest.mark.parametrize('missing', [(), ('concept',), ('learning',), ('concept', 'learning')])
def test_readonly_index_lookup_never_repairs_missing_indexes(closed_loop, missing):
    from runtime.storage.knowledge_structures import KnowledgeStructureStoreError
    owner, source, _, document, dsn, _ = closed_loop
    key = (owner.learner_id, source.material_id, document['revision'])
    with database_session(dsn) as db:
        before = deepcopy(knowledge_reads.outline(db, *key))
        for name in missing:
            db.execute(delete(CardSourceIndex if name == 'concept' else KnowledgeReadIndex))
    with database_session(dsn) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        if missing:
            with pytest.raises(KnowledgeStructureStoreError, match='^KNOWLEDGE_STRUCTURE_UNAVAILABLE$'):
                knowledge_reads.outline(db, *key)
        else:
            assert knowledge_reads.outline(db, *key) == before
    with database_session(dsn) as db:
        assert (db.get(CardSourceIndex, key) is None) == ('concept' in missing)
        assert (db.get(KnowledgeReadIndex, key) is None) == ('learning' in missing)
    assert knowledge_reads.backfill(dsn=dsn) == int(bool(missing))
    assert knowledge_reads.backfill(dsn=dsn) == 0
    with database_session(dsn) as db:
        assert knowledge_reads.outline(db, *key) == before
        assert db.get(KnowledgeStructure, key).document == document


def test_relation_only_evidence_survives_read_projection_and_existing_index_repair(closed_loop):
    from io import BytesIO
    import pymupdf
    from content_identity import canonical_sha256
    from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context
    from product_fixtures import seed_pdf, seed_run, publish_fixture_structure
    from runtime import card_sets
    from runtime.material_processing import claim_next_material_processing_run, _record_progress
    from runtime.storage.knowledge_structures import KnowledgeStructureStoreError
    from structure_fixtures import build_knowledge_structure
    from test_knowledge_structure_v1 import _block, _page

    owner, _, settings, _, dsn, _ = closed_loop
    passages = ['Queues remove the first item.', 'Stacks remove the last item.',
                'Queues and stacks have contrasting removal orders.']
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for index, passage in enumerate(passages):
            page.insert_text((72, 72 + index * 30), passage)
        source = seed_pdf(owner.learner_id, BytesIO(pdf.tobytes()), 'relation-only', dsn=dsn)
    run = seed_run(owner.learner_id, source.material_id, 'relation-only-run', settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == run.run_id
    for stage in ('evidence', 'semantics', 'publishing'):
        _record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    context = build_document_context([_page(1, [_block(1, i, 'paragraph', value)
                                              for i, value in enumerate(passages)])], page_count=1)
    state = SemanticState()
    apply_semantic_response({
        'concepts': [{'k': key, 'l': key, 'a': [], 'c': [{'m': None, 's': [index]}]}
                     for index, key in enumerate(('Queue', 'Stack'))],
        'relations': [{'s': 'Queue', 't': 'Stack', 'k': 'contrast', 'r': passages[2], 'e': [2], 'c': 1.0}],
    }, context=context, bundle={'sections': context['sections'], 'evidence': context['evidence']}, state=state)
    lock = settings['runtime_lock']
    document = build_knowledge_structure(
        context, state, source_sha256=context['material_id'].rsplit(':', 1)[-1], run_id=str(run.run_id),
        produced_at='2026-10-19T00:00:00+00:00', runtime_lock_sha256=canonical_sha256(lock),
        model_id=lock['semantic_service']['model_id'], model_revision=lock['semantic_service']['revision'],
        semantic_calls=1, ocr_calls=0,
    )
    publish_fixture_structure(owner.learner_id, source.material_id, run.run_id, document, dsn=dsn)
    assert len(document['relations']) == 1
    relation = document['relations'][0]
    ref = relation['evidence_refs'][0]
    assert all(ref not in claim['evidence_refs'] for concept in document['concepts'] for claim in concept['claims'])
    evidence = next(item for item in document['evidence'] if item['evidence_id'] == ref)
    ids = [concept['concept_id'] for concept in document['concepts']]
    key = (owner.learner_id, source.material_id, document['revision'])
    with database_session(dsn) as db:
        detail = knowledge_reads.concept_context(db, *key, ids)
    quote = detail['relation_evidence'][0]
    assert quote['evidence_id'] == ref and quote['quote'] == passages[2]
    assert quote['source_locator'] == evidence['source_locator']
    assert quote['normalized_page'] == quote['page'] == 1
    assert quote['source_id'] == document['input_binding']['manifest']['items'][0]['source_id']
    deck = card_sets.create_card_set(owner.learner_id, source.material_id, document['revision'],
                                    'Relation sources', ids, 'relation-only-deck', dsn=dsn)
    with database_session(dsn) as db:
        db.get(CardSet, deck['card_set_id']).status = 'ready'
    batch = card_sets.read_card_study(owner.learner_id, deck['card_set_id'], dsn=dsn)['batch']
    assert batch['context']['relation_evidence'] == [quote]
    with database_session(dsn) as db, pytest.raises(KnowledgeStructureStoreError):
        knowledge_reads.concept_context(db, uuid4(), source.material_id, document['revision'], ids)

    # 重現舊索引合法雜湊下的漏引用，不改 canonical KnowledgeStructure。
    with database_session(dsn) as db:
        rows = db.scalars(select(CardSourceConcept).where(CardSourceConcept.material_id == source.material_id)).all()
        for row in rows:
            row.payload = {**row.payload, 'relation_evidence': []}
            row.content_sha256 = canonical_sha256(row.payload)
    assert knowledge_reads.backfill(dsn=dsn) == 1
    assert knowledge_reads.backfill(dsn=dsn) == 0
    with database_session(dsn) as db:
        assert db.get(KnowledgeStructure, key).document == document
        assert knowledge_reads.concept_context(db, *key, ids) == detail


def test_progress_summary_returns_counts_without_outline_or_concept_states(closed_loop, monkeypatch, tmp_path):
    fixture = concept_fixture(closed_loop, 3)
    set_id = create(fixture)
    finish(fixture)
    answer(fixture, set_id)
    mid, rev = fixture['source'].material_id, fixture['document']['revision']
    study = fixture['study'].study_session_id
    client = TestClient(_app(fixture['dsn'], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', fixture['token'])
    base = f'/v1/materials/{mid}/knowledge-structures/{rev}/progress-summary'
    progress = client.get(f'/v1/study-sessions/{study}/map-progress').json()
    labels = {c['concept_id']: c['label'] for c in fixture['document']['concepts']}

    plain = client.get(base)
    assert plain.status_code == 200, plain.json()
    assert plain.json() == {
        'schema': 'progress-summary/v1', 'material_id': str(mid), 'knowledge_structure_revision': rev,
        'study_session_id': None, 'concept_count': len(labels), 'completed_count': 0,
        'current_concept_id': None, 'current_concept_label': None,
    }
    session = client.get(base, params={'study_session_id': str(study)})
    assert session.status_code == 200, session.json()
    body = session.json()
    assert body['concept_count'] == len(labels)
    assert body['completed_count'] == sum(s['status'] == 'completed' for s in progress['concept_states'])
    assert body['completed_count'] >= 1
    assert body['current_concept_id'] == progress['current_concept_id']
    assert body['current_concept_label'] == labels.get(progress['current_concept_id'])
    assert 'concept_states' not in body and 'concepts' not in body

    # 他份教材路徑、未知版本、不存在的工作階段與多餘參數都不可讀到進度。
    other = base.replace(str(mid), str(uuid4()))
    assert client.get(other, params={'study_session_id': str(study)}).status_code == 404
    assert client.get(other).status_code == 404
    assert client.get(base, params={'study_session_id': str(uuid4())}).status_code == 404
    assert client.get(base, params={'extra': '1'}).status_code == 400
    client.cookies.clear()
    assert client.get(base).status_code == 401


def test_regions_reuse_source_statistics_of_the_same_revision(closed_loop):
    from sqlalchemy import event

    owner, source, _, document, dsn, _ = closed_loop
    key = (owner.learner_id, source.material_id, document['revision'])
    expansions = []

    def count(_conn, _cursor, statement, *_):
        if 'card_source_concepts c' in statement:
            expansions.append(statement)

    with database_session(dsn) as db:
        event.listen(db.get_bind(), 'before_cursor_execute', count)
        try:
            first = knowledge_reads.regions(db, *key)
            second = knowledge_reads.regions(db, *key)
        finally:
            event.remove(db.get_bind(), 'before_cursor_execute', count)
    assert first == second and len(expansions) == 1
    # 權限仍由 outline 核對：他人讀同一版本照樣被拒。
    from runtime.storage.knowledge_structures import KnowledgeStructureStoreError
    with database_session(dsn) as db, pytest.raises(KnowledgeStructureStoreError):
        knowledge_reads.regions(db, uuid4(), source.material_id, document['revision'])
