"""進度只驗證一次結構，且並行交卷不會混入同次讀取。"""

from concurrent.futures import ThreadPoolExecutor

from learning_adaptation.assessment_state import _list_sets
from learning_adaptation.learner_progress import derive_learner_progress, progress_snapshot
from product_fixtures import ORIGIN, _app, closed_loop
from assessment_fixtures import answer, finish, concept_fixture, create, read


def test_set_and_progress_query_counts_do_not_grow_per_question(closed_loop, record_property):
    from sqlalchemy import event
    from runtime.storage.database import _database_engine

    counts = []
    for size in (1, 10, 100):
        fixture = concept_fixture(closed_loop, size)
        root = create(fixture)
        finish(fixture)
        statements = []
        engine = _database_engine(fixture['dsn'])

        def count_query(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('SELECT'):
                statements.append(statement)

        event.listen(engine, 'before_cursor_execute', count_query)
        try:
            ready = read(fixture, root)
        finally:
            event.remove(engine, 'before_cursor_execute', count_query)
        assert len(ready['items']) == size and all(item['can_submit'] for item in ready['items'])
        assert all('correct_option_id' not in item['assessment'] for item in ready['items'])
        set_count = len(statements)
        answer(fixture, root)
        statements.clear()
        event.listen(engine, 'before_cursor_execute', count_query)
        try:
            progress = derive_learner_progress(fixture['learner'], fixture['study'].study_session_id, dsn=fixture['dsn'])
        finally:
            event.remove(engine, 'before_cursor_execute', count_query)
        assert progress.event_watermark == size
        assert progress.assessment_cycles[0]['outcome'] == 'passed'
        counts.append((size, set_count, len(statements)))
    record_property('question_count_set_queries_progress_queries', counts)
    assert len({(set_count, progress_count) for _, set_count, progress_count in counts}) == 1, counts


def test_set_list_query_count_does_not_grow_per_set(closed_loop, record_property):
    from sqlalchemy import event
    from learning_adaptation.assessment_state import list_sets
    from runtime.storage.database import _database_engine

    fixture = concept_fixture(closed_loop, 1)
    engine = _database_engine(fixture['dsn'])
    counts = []
    created = 0
    for size in (1, 10, 100):
        while created < size:
            create(fixture, key=f'failed-attempt-{created}')
            finish(fixture, fail={0})
            created += 1
        statements = []

        def count_query(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('SELECT'):
                statements.append(statement)

        event.listen(engine, 'before_cursor_execute', count_query)
        try:
            result = list_sets(fixture['learner'], fixture['study'].study_session_id, dsn=fixture['dsn'])
        finally:
            event.remove(engine, 'before_cursor_execute', count_query)
        assert len(result['sets']) == size
        assert result['active_set_ids'] == []
        assert all(group['published_count'] == group['answered_count'] == group['passed_count'] == 0
                   for group in result['sets'])
        counts.append((size, len(statements)))
    record_property('set_count_list_queries', counts)
    assert len({queries for _, queries in counts}) == 1, counts


def test_progress_validates_structure_once_and_keeps_a_consistent_snapshot(closed_loop, monkeypatch):
    import runtime.storage.knowledge_structures as storage
    import knowledge_map.structure as structure

    fixture = concept_fixture(closed_loop, 2)
    root = create(fixture)
    finish(fixture)
    checks = []
    validate = structure.validate_knowledge_structure

    def counted(document):
        checks.append(document["revision"])
        return validate(document)

    for module in (storage, structure):
        monkeypatch.setattr(module, "validate_knowledge_structure", counted)
    with progress_snapshot(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    ) as (db, study, document, before):
        assert checks == []
        assert before.event_watermark == 0
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(answer, fixture, root, {1}).result(timeout=15)
        # 新答案已 commit，舊讀取仍維持相同時點，不再靠完整重算兩次來猜是否一致。
        assert _list_sets(db, study)["sets"][0]["answered_count"] == 0
        assert before.assessment_cycles[0]["outcome"] == "in_progress"

    after = derive_learner_progress(
        fixture["learner"], fixture["study"].study_session_id, dsn=fixture["dsn"],
    )
    assert after.event_watermark == 2
    assert after.assessment_cycles[0]["pending_count"] == 1
    assert after.guidance_revision != before.guidance_revision
    assert read(fixture, root)["answered_count"] == 2


def test_resume_projects_from_one_verified_document(closed_loop, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import runtime.storage.knowledge_structures as storage

    fixture = concept_fixture(closed_loop, 2)
    checks = []
    validate = storage.validate_knowledge_structure

    def counted(document):
        checks.append(document["revision"])
        return validate(document)

    monkeypatch.setattr(storage, "validate_knowledge_structure", counted)
    client = TestClient(_app(fixture["dsn"], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    path = (
        f"/v1/materials/{fixture['source'].material_id}"
        f"/knowledge-structures/{fixture['document']['revision']}"
        f"/study-sessions/{fixture['study'].study_session_id}/resume"
    )
    response = client.get(path, params={"run_id": fixture["document"]["run_id"]})
    assert response.status_code == 200, response.json()
    assert checks == [fixture["document"]["revision"]]
    assert response.json()["progress"]["event_watermark"] == 0


def test_resume_includes_public_selected_set_from_same_snapshot(closed_loop, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import runtime.storage.knowledge_structures as storage
    fixture = concept_fixture(closed_loop, 2)
    root = create(fixture)
    finish(fixture)
    expected = read(fixture, root)
    checks = []
    validate = storage.validate_knowledge_structure
    def counted(document):
        checks.append(document['revision'])
        return validate(document)
    monkeypatch.setattr(storage, 'validate_knowledge_structure', counted)
    client = TestClient(_app(fixture['dsn'], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', fixture['token'])
    response = client.get(
        f"/v1/materials/{fixture['source'].material_id}/knowledge-structures/{fixture['document']['revision']}"
        f"/study-sessions/{fixture['study'].study_session_id}/resume",
        params={'run_id': fixture['document']['run_id']},
    )
    assert response.status_code == 200, response.json()
    result = response.json()
    assert result['selected_set_id'] == str(root)
    assert checks == [fixture['document']['revision']]
    assert result['selected_set']['items'][0]['assessment'] == expected['items'][0]['assessment']
    from runtime.api.models import AssessmentSetView
    assert result['selected_set'] == AssessmentSetView.model_validate(expected).model_dump(mode='json', by_alias=True)
    assert all(item['feedback'] is None for item in result['selected_set']['items'])
    assert 'correct_option_id' not in response.text and 'private' not in response.text
