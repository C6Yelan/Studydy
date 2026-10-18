"""實際 DB 的按需讀取：來源一致、權限隔離、不再讀完整教材。"""
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from product_fixtures import ORIGIN, _app, closed_loop
from assessment_fixtures import concept_fixture, create, finish
from runtime.storage import knowledge_reads
from runtime.storage.tables import CardSourceConcept, KnowledgeReadIndex
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
