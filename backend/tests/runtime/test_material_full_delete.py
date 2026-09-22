from hashlib import sha256

import psycopg
from fastapi.testclient import TestClient

from test_learning_resume import learning_records, library_materials, closed_loop
from test_accounts import _app, ORIGIN, HEADERS
from runtime.storage.artifacts import _root


def test_full_learned_material_delete_preserves_every_other_material(learning_records, tmp_path, monkeypatch):
    f = learning_records
    material_id = f['first'].material_id
    artifact_id = f['first'].artifact_id
    with psycopg.connect(f['dsn']) as db:
        study_ids = [row[0] for row in db.execute('SELECT study_session_id FROM study_sessions WHERE material_id=%s', (material_id,))]
        assessment_ids = [row[0] for row in db.execute('SELECT assessment_revision FROM assessments WHERE study_session_id=ANY(%s)', (study_ids,))]
        assert assessment_ids
        assert db.execute('SELECT count(*) FROM answer_events WHERE material_id=%s', (material_id,)).fetchone()[0] > 0
    def others():
        snapshot = {}
        with psycopg.connect(f['dsn']) as db:
            for table in ['materials', 'artifacts', 'material_processing_runs', 'knowledge_structures', 'study_sessions', 'answer_events', 'assessments']:
                condition, value = ('NOT (study_session_id=ANY(%s))', study_ids) if table == 'assessments' else ('material_id<>%s', material_id)
                rows = db.execute(f'SELECT row_to_json(t)::text FROM {table} t WHERE {condition} ORDER BY 1', (value,)).fetchall()
                snapshot[table] = sha256(repr(rows).encode()).hexdigest()
        return snapshot
    before = others()
    client = TestClient(_app(f['dsn'], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', f['foreign'].raw_token)
    assert client.delete(f'/v1/materials/{material_id}', headers=HEADERS).status_code == 404
    assert others() == before
    client.cookies.set('studydy_session', f['token'])
    response = client.delete(f'/v1/materials/{material_id}', headers=HEADERS)
    assert response.status_code == 202 and response.json()['state'] == 'removed'
    with psycopg.connect(f['dsn']) as db:
        for table in ['materials', 'artifacts', 'material_processing_runs', 'knowledge_structures', 'study_sessions', 'answer_events']:
            assert db.execute(f'SELECT count(*) FROM {table} WHERE material_id=%s', (material_id,)).fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM assessments WHERE study_session_id=ANY(%s)', (study_ids,)).fetchone() == (0,)
    assert not (_root() / 'objects' / artifact_id.hex).exists()
    assert not (_root() / '.trash' / artifact_id.hex).exists()
    assert others() == before
    paths = [f'/v1/artifacts/{artifact_id}', f'/v1/materials/{material_id}',
        f'/v1/material-processing-runs/{f["structure"]["run_id"]}',
        f'/v1/materials/{material_id}/knowledge-structures/{f["structure"]["revision"]}',
        f'/v1/study-sessions/{f["active"].study_session_id}',
        f'/v1/materials/{material_id}/knowledge-structures/{f["structure"]["revision"]}/study-sessions/{f["active"].study_session_id}/resume?run_id={f["structure"]["run_id"]}',
        f'/v1/study-sessions/{f["active"].study_session_id}/assessment-sets/{f["set_id"]}']
    for path in paths:
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()['reason_code'] == 'RESOURCE_NOT_FOUND'


def test_full_delete_rollback_restores_learning_rows_and_quarantined_pdf(learning_records, monkeypatch):
    from contextlib import contextmanager
    import pytest
    import runtime.material_discard as discard
    from runtime.storage.tables import database_session
    from test_material_library import product_snapshot
    f = learning_records
    before = product_snapshot(f['dsn'])
    @contextmanager
    def fail_after_children(dsn):
        with database_session(dsn) as session:
            execute = session.execute
            def checked(statement, *args, **kwargs):
                if str(statement).startswith('DELETE FROM artifacts'):
                    raise RuntimeError('synthetic commit-stage failure')
                return execute(statement, *args, **kwargs)
            session.execute = checked
            yield session
    with monkeypatch.context() as patch:
        patch.setattr(discard, 'database_session', fail_after_children)
        with pytest.raises(discard.MaterialDiscardError, match='MATERIAL_DISCARD_STORAGE_FAILED'):
            discard.request_material_discard(f['learner'].learner_id, f['first'].material_id, dsn=f['dsn'])
    after = product_snapshot(f['dsn'])
    assert {k:v for k,v in before.items() if k != 'materials'} == {k:v for k,v in after.items() if k != 'materials'}
    assert (_root() / 'objects' / f['first'].artifact_id.hex).exists()
    assert not (_root() / '.trash' / f['first'].artifact_id.hex).exists()
    discard.finish_material_discards(dsn=f['dsn'])
    assert not (_root() / 'objects' / f['first'].artifact_id.hex).exists()
