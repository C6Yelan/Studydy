from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from assessment_fixtures import concept_fixture, create, finish_set, answers_for, send
from product_fixtures import closed_loop, ORIGIN, _app
from runtime import assessment_presence, voice
from runtime.errors import SourceError
from runtime.storage.database import database_session
from runtime.storage.tables import AssessmentPresence, VoiceTurn


def test_presence_blocks_all_entries_and_submission_releases(closed_loop, tmp_path, monkeypatch):
    f = concept_fixture(closed_loop, 1)
    set_id = create(f)
    finish_set(f)
    owner, material, dsn = f['learner'].learner_id, f['source'].material_id, f['dsn']
    browser = uuid4()
    conversation = voice.create(owner, material, 'qa', dsn=dsn)['conversation_id']
    failed = voice.add_turn(owner, conversation, 'failed', '問題', dsn=dsn)['turn_id']
    with database_session(dsn) as db:
        db.get(VoiceTurn, failed).status = 'failed'
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', f['token'])
    headers = {'Origin': ORIGIN, 'Idempotency-Key': 'question', 'X-Studydy-Browser-Id': str(browser)}
    presence_url = f'/v1/materials/{material}/assessment-presence'
    body = {'schema': 'assessment-presence/v1', 'assessment_set_id': str(set_id), 'active': True}
    for value in ('', 'invalid'):
        bad = {**headers, 'X-Studydy-Browser-Id': value}
        assert client.put(presence_url, headers=bad, json=body).status_code == 400
        assert client.post(f'/v1/voice-conversations/{conversation}/turns', headers=bad, json={'question': '問題'}).status_code == 400
        assert client.post(f'/v1/voice-conversations/{conversation}/recordings', headers=bad, content=b'audio').status_code == 400
        assert client.post(f'/v1/voice-conversations/{conversation}/turns/{failed}/actions', headers=bad, json={'action': 'retry'}).status_code == 400
    response = client.put(presence_url, headers=headers, json=body)
    assert response.status_code == 200, response.text
    expires = datetime.fromisoformat(response.json()['expires_at'])
    assert 58 < (expires - datetime.now(UTC)).total_seconds() <= 60
    base = f'/v1/voice-conversations/{conversation}'
    for suffix, kwargs in (
        ('/turns', {'json': {'question': '問題'}}),
        ('/recordings', {'content': b'audio'}),
        (f'/turns/{failed}/actions', {'json': {'action': 'retry'}}),
    ):
        response = client.post(base + suffix, headers=headers, **kwargs)
        assert response.status_code == 409, response.text
        assert response.json()['reason_code'] == 'ASSESSMENT_IN_PROGRESS'
    with database_session(dsn) as db:
        assert len(db.scalars(select(VoiceTurn).where(VoiceTurn.conversation_id == conversation)).all()) == 1
        assert db.get(VoiceTurn, failed).status == 'failed'
        assert db.get(AssessmentPresence, (owner, material, browser)).expires_at == expires
    other = {**headers, 'X-Studydy-Browser-Id': str(uuid4())}
    response = client.post(base + '/turns', headers=other, json={'question': '另一個瀏覽器'})
    assert response.status_code == 202, response.text
    pending = response.json()['turn_id']
    # 取消不需瀏覽器 header，作答中也可取消原本已開始的工作。
    assert client.post(base + f'/turns/{pending}/actions', headers={'Origin': ORIGIN}, json={'action': 'cancel'}).status_code == 200
    send(f, set_id, answers_for(f, set_id))
    response = client.post(base + f'/turns/{failed}/actions', headers=headers, json={'action': 'retry'})
    assert response.status_code == 200, response.text
    assert client.put(presence_url, headers=headers, json=body).status_code == 400


def test_presence_scope_expiry_and_explicit_release(closed_loop):
    f = concept_fixture(closed_loop, 1)
    set_id = create(f)
    finish_set(f)
    owner, material, dsn = f['learner'].learner_id, f['source'].material_id, f['dsn']
    browser = uuid4()
    for wrong_owner, wrong_material, wrong_set in ((uuid4(), material, set_id), (owner, uuid4(), set_id), (owner, material, uuid4())):
        with pytest.raises(SourceError, match='REQUEST_INVALID'):
            assessment_presence.update(wrong_owner, wrong_material, browser, wrong_set, True, dsn=dsn)
    assessment_presence.update(owner, material, browser, set_id, True, dsn=dsn)
    with database_session(dsn) as db:
        for account, source, device in ((uuid4(), material, browser), (owner, uuid4(), browser), (owner, material, uuid4())):
            assessment_presence.require_allowed(db, account, source, device)
        with pytest.raises(SourceError, match='ASSESSMENT_IN_PROGRESS'):
            assessment_presence.require_allowed(db, owner, material, browser)
        db.get(AssessmentPresence, (owner, material, browser)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with database_session(dsn) as db:
        assessment_presence.require_allowed(db, owner, material, browser)
    assessment_presence.update(owner, material, browser, set_id, True, dsn=dsn)
    assessment_presence.update(owner, material, browser, uuid4(), False, dsn=dsn)
    with database_session(dsn) as db:
        with pytest.raises(SourceError, match='ASSESSMENT_IN_PROGRESS'):
            assessment_presence.require_allowed(db, owner, material, browser)
    assert assessment_presence.update(owner, material, browser, set_id, False, dsn=dsn)['active'] is False
    with database_session(dsn) as db:
        assessment_presence.require_allowed(db, owner, material, browser)


def test_presence_migration_transaction_rollback(clean_database_dsn, migrations_dir, tmp_path):
    import shutil
    import psycopg
    from runtime.storage.migrations import load_migrations, run_migrations
    migrations = load_migrations(migrations_dir)
    previous = tmp_path / 'previous'
    previous.mkdir()
    for path in sorted(migrations_dir.glob('*.sql'))[:-1]:
        shutil.copyfile(path, previous / path.name)
    run_migrations(clean_database_dsn, migrations_dir=previous)
    with psycopg.connect(clean_database_dsn, autocommit=True) as db:
        with db.transaction(force_rollback=True):
            db.execute(migrations[-1].sql)
            assert db.execute("SELECT to_regclass('assessment_presence')").fetchone() == ('assessment_presence',)
        assert db.execute("SELECT to_regclass('assessment_presence')").fetchone() == (None,)
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (len(migrations),)
