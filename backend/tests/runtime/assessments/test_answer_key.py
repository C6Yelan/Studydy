from copy import deepcopy

from fastapi.testclient import TestClient

from assessment_fixtures import concept_fixture, create, finish_set, read, answers_for, send
from product_fixtures import closed_loop, HEADERS, ORIGIN, _app, product_snapshot
from learning_adaptation import answer_events, assessment_state
from runtime.storage.database import database_session
from runtime.storage.tables import Assessment


def test_submitted_feedback_exposes_saved_answer_key_without_changing_public_question(closed_loop,tmp_path,monkeypatch):
    fixture=concept_fixture(closed_loop,2)
    set_id=create(fixture)
    finish_set(fixture)
    group=read(fixture,set_id)
    answers=answers_for(fixture,set_id)
    correct={answer['assessment_revision']:answer['selected_option_id'] for answer in answers}
    original_questions=[deepcopy(item['assessment']) for item in group['items']]
    answers[0]['selected_option_id']=next(option['option_id'] for option in original_questions[0]['options']
        if option['option_id']!=correct[answers[0]['assessment_revision']])
    client=TestClient(_app(fixture['dsn'],tmp_path,monkeypatch),base_url=ORIGIN)
    client.cookies.set('studydy_session',fixture['token'])
    url=f"/v1/study-sessions/{fixture['study'].study_session_id}/assessment-sets/{set_id}"
    response=client.post(url+'/submissions',headers={**HEADERS,'Idempotency-Key':'answer-key-submit'},json={
        'schema':'assessment-set-submission/v1','expected_set_version':group['set_version'],'answers':answers})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['status']=='completed'
    assert [item['assessment'] for item in result['items']]==original_questions
    for item in result['items']:
        feedback=item['feedback']
        assert feedback['schema']=='answer-feedback/v1'
        assert feedback['correct_option_id']==correct[feedback['assessment_revision']]
        assert 'correct_option_id' not in item['assessment'] and 'rationale' not in item['assessment']
    assert result['items'][0]['feedback']['selected_option_id']!=result['items'][0]['feedback']['correct_option_id']


def test_unsubmitted_feedback_hides_even_previously_saved_individual_answers(closed_loop,tmp_path,monkeypatch):
    fixture=concept_fixture(closed_loop,2)
    set_id=create(fixture)
    finish_set(fixture)
    client=TestClient(_app(fixture['dsn'],tmp_path,monkeypatch),base_url=ORIGIN)
    client.cookies.set('studydy_session',fixture['token'])
    sid=fixture['study'].study_session_id
    url=f'/v1/study-sessions/{sid}/assessment-sets/{set_id}'
    before=client.get(url).json()
    assert before['status']=='ready' and all(item['feedback'] is None for item in before['items'])
    assert 'correct_option_id' not in str(before)
    answer=answers_for(fixture,set_id)[0]
    with database_session(fixture['dsn']) as db:
        study,_,_=assessment_state._scope(db,fixture['learner'],sid,lock=True)
        assessment=db.get(Assessment,answer['assessment_revision'])
        answer_events.record_answer(db,study,assessment,answer['selected_option_id'],'historic-single-answer')
    partial=client.get(url).json()
    assert partial['status']=='in_progress' and partial['answered_count']==1
    assert all(item['feedback'] is None for item in partial['items'])
    assert 'correct_option_id' not in str(partial) and 'rationale' not in str(partial)
    workspace=client.get(f"/v1/materials/{fixture['source'].material_id}/knowledge-structures/{fixture['document']['revision']}/study-sessions/{sid}/workspace",
        params={'run_id':fixture['document']['run_id'],'set_id':str(set_id)})
    assert workspace.status_code==200,workspace.text
    assert all(item['feedback'] is None for item in workspace.json()['selected_set']['items'])
    assert 'correct_option_id' not in workspace.text


def test_preexisting_completed_set_projects_private_key_without_regenerating_or_writing(closed_loop,tmp_path,monkeypatch):
    fixture=concept_fixture(closed_loop,2)
    set_id=create(fixture)
    finish_set(fixture)
    answers=answers_for(fixture,set_id)
    send(fixture,set_id,answers)
    # 既有資料只保存題目與私密答案，沒有回饋正解欄位需要回填。
    with database_session(fixture['dsn']) as db:
        expected={answer['assessment_revision']:db.get(Assessment,answer['assessment_revision']).private_answer_document['correct_option_id']
                  for answer in answers}
    before=product_snapshot(fixture['dsn'])
    client=TestClient(_app(fixture['dsn'],tmp_path,monkeypatch),base_url=ORIGIN)
    client.cookies.set('studydy_session',fixture['token'])
    result=client.get(f"/v1/study-sessions/{fixture['study'].study_session_id}/assessment-sets/{set_id}")
    assert result.status_code==200,result.text
    assert result.json()['status']=='completed'
    assert {item['feedback']['assessment_revision']:item['feedback']['correct_option_id'] for item in result.json()['items']}==expected
    assert product_snapshot(fixture['dsn'])==before
