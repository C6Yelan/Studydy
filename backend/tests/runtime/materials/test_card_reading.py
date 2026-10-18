"""分批讀取仍保留原文、關係與來源；使用隔離資料庫及合成教材。"""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import runtime.api.app as api
from runtime.api.models import project_knowledge_structure
from product_fixtures import closed_loop, seed_run, publish_fixture_structure
from runtime import card_sets
from runtime.learner_session import register_account
from runtime.material_processing import claim_next_material_processing_run, _record_progress
from runtime.storage import knowledge_reads
from runtime.storage.knowledge_structures import read_knowledge_structure, KnowledgeStructureStoreError
from runtime.storage.tables import CardSet, CardSourceConcept
from runtime.storage.database import database_session


def large_deck(fixture):
    owner, material, settings, original, dsn, _ = fixture
    run = seed_run(owner.learner_id, material.material_id, 'batch-source', settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == run.run_id
    for stage in ('evidence', 'semantics', 'publishing'):
        _record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    document = deepcopy(original)
    document.pop('input_binding')
    first = document['concepts'][0]
    document['concepts'] = [{**deepcopy(first), 'concept_id': f'concept:sha256:{i:064x}', 'label': f'Stack variant {i}'} for i in range(19)]
    document['relations'] = [{'relation_id': 'relation:sha256:' + 'e' * 64,
        'source_concept_id': document['concepts'][0]['concept_id'],
        'target_concept_id': document['concepts'][-1]['concept_id'], 'type': 'example',
        'learner_reason': 'The stack example follows last-in first-out order.',
        'evidence_refs': first['claims'][0]['evidence_refs'], 'confidence': 1.0}]
    publish_fixture_structure(owner.learner_id, material.material_id, run.run_id, document, dsn=dsn)
    assert len(document['relations']) == 1
    source, target = (document['relations'][0][key] for key in ('source_concept_id', 'target_concept_id'))
    ids = [source, *[c['concept_id'] for c in document['concepts'] if c['concept_id'] not in (source, target)], target]
    saved = card_sets.create_card_set(owner.learner_id, material.material_id, document['revision'], '分批卡組', ids, 'batch-deck', dsn=dsn)
    with database_session(dsn) as db:
        db.get(CardSet, saved['card_set_id']).status = 'ready'
    return saved['card_set_id'], document, ids


def test_batches_preserve_neighbor_context_without_reloading_document(closed_loop, monkeypatch):
    owner, material, _, _, dsn, _ = closed_loop
    identity, document, ids = large_deck(closed_loop)
    original = read_knowledge_structure(owner.learner_id, material.material_id, revision=document['revision'], dsn=dsn).view
    original = project_knowledge_structure(original).model_dump(by_alias=True, mode='json')
    monkeypatch.setattr(knowledge_reads, '_read_verified_document', lambda *a, **kw: pytest.fail('must use prepared source index'))
    monkeypatch.setattr(card_sets, '_read_verified_document', lambda *a, **kw: pytest.fail('must not read full material'))
    study = card_sets.read_card_study(owner.learner_id, identity, dsn=dsn)
    assert [n['concept_id'] for n in study['navigation']] == ids
    batch = study['batch']
    assert [c['concept_id'] for c in batch['cards']] == ids[:8]
    expected_ids = {*ids[:8], ids[-1]}
    assert batch['context']['concepts'] == [c for c in original['concepts'] if c['concept_id'] in expected_ids]
    assert batch['context']['relations'] == original['relations']
    evidence = {e['evidence_id']: e for c in original['concepts'] for claim in c['claims'] for e in claim['evidence']}
    assert batch['context']['relation_evidence'] == [evidence[ref] for ref in original['relations'][0]['evidence_refs']]
    last = card_sets.read_card_study(owner.learner_id, identity, offset=16, expected_version=1, dsn=dsn)
    assert [c['concept_id'] for c in last['cards']] == ids[16:]
    assert last['total'] == 19
    # 只保存所見概念的張數，不猜測未讀部分；全部補齊才建立統一總張數。
    partial = card_sets.save_card_count(owner.learner_id, identity, None, 1, concept_counts={i: 2 for i in ids[:8]}, dsn=dsn)
    assert partial['card_count'] is None
    complete = card_sets.save_card_count(owner.learner_id, identity, None, 1, concept_counts={i: 2 for i in ids[8:]}, dsn=dsn)
    assert complete['card_count'] == 38
    assert all(n['page_count'] == 2 for n in card_sets.read_card_study(owner.learner_id, identity, dsn=dsn)['navigation'])


def test_batch_api_ownership_version_and_corrupt_projection(closed_loop, monkeypatch):
    owner, _, settings, _, dsn, token = closed_loop
    identity, document, ids = large_deck(closed_loop)
    monkeypatch.setattr(api, 'runtime_binding', lambda _: {})
    origin = 'http://127.0.0.1:4183'
    client = TestClient(api.create_app(api.ApiSettings(profile='local', public_origin=origin, secure_cookie=False,
                                                     local_config=settings, dsn=dsn)), base_url=origin)
    url = f'/v1/card-sets/{identity}'
    assert client.get(url + '/study').status_code == 401
    client.cookies.set('studydy_session', token)
    response = client.get(url + '/study')
    assert response.status_code == 200
    assert len(response.json()['batch']['cards']) == 8
    assert client.get(url + '/cards?offset=16&version=1').status_code == 200
    assert client.get(url + '/cards?offset=8&version=2').status_code == 409
    for query in ('offset=1&version=1', 'offset=24&version=1', 'offset=-8&version=1', 'offset=8&version=1&owner=x'):
        assert client.get(url + '/cards?' + query).status_code == 400
    stranger = register_account('batch-stranger@example.com', 'Synthetic other password 42', dsn=dsn)
    client.cookies.set('studydy_session', stranger.raw_token)
    assert client.get(url + '/study').status_code == 404
    assert client.get(url + '/cards?offset=8&version=1').status_code == 404
    with database_session(dsn) as db:
        item = db.get(CardSourceConcept, (owner.learner_id, closed_loop[1].material_id, document['revision'], ids[0]))
        corrupted = deepcopy(item.payload); corrupted['concept']['claims'][0]['text'] = 'tampered'
        item.payload = corrupted
    with pytest.raises(KnowledgeStructureStoreError, match='KNOWLEDGE_STRUCTURE_UNAVAILABLE'):
        card_sets.read_card_study(owner.learner_id, identity, dsn=dsn)
