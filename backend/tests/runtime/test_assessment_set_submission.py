"""整組交卷的原子保存、重播、範圍及舊作答保護。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from learning_adaptation import answer_events, assessment_sets as sets
from runtime.storage.tables import Assessment, AnswerEvent, StudySession, database_session
from test_assessment_sets import closed_loop, concept_fixture, create, finish, read
from test_accounts import _app, ORIGIN, HEADERS


def answers_for(f, identity):
    answers=[]
    for item in read(f,identity)['items']:
        if not item['assessment']:continue
        public=item['assessment']
        with database_session(f['dsn']) as session:
            selected=session.get(Assessment,public['assessment_revision']).private_answer_document['correct_option_id']
        answers.append({'assessment_revision':public['assessment_revision'],'question_id':public['question_id'],'selected_option_id':selected})
    return answers


def send(f, identity, answers, key='submit', version=None):
    sets.submit_set_answers(f['learner'],f['study'].study_session_id,identity,answers,
        read(f,identity)['set_version'] if version is None else version,key,dsn=f['dsn'])


def test_whole_set_submission_publishes_all_feedback_and_replays_once(closed_loop,tmp_path,monkeypatch):
    f=concept_fixture(closed_loop,3);identity=create(f);finish(f)
    client=TestClient(_app(f['dsn'],tmp_path,monkeypatch),base_url=ORIGIN);client.cookies.set('studydy_session',f['token'])
    sid=f['study'].study_session_id;base=f'/v1/study-sessions/{sid}/assessment-sets/{identity}'
    group=read(f,identity);answers=answers_for(f,identity)
    one=answers[0]
    rejected=client.post(f'/v1/study-sessions/{sid}/assessments/{one["assessment_revision"]}/submissions',
        headers={**HEADERS,'Idempotency-Key':'individual'},json={'schema':'answer-submission-create/v2',
        'question_id':one['question_id'],'selected_option_id':one['selected_option_id']})
    assert rejected.status_code==404
    assert read(f,identity)['answered_count']==0
    assert all(item['feedback'] is None for item in client.get(base).json()['items'])
    body={'schema':'assessment-set-submission/v1','expected_set_version':group['set_version'],'answers':answers}
    headers={**HEADERS,'Idempotency-Key':'whole-set'}
    result=client.post(base+'/submissions',headers=headers,json=body)
    assert result.status_code==200,result.json()
    completed=result.json()
    assert completed['status']=='completed' and completed['answered_count']==3 and completed['cycle']['outcome']=='passed'
    assert all(item['feedback'] is not None and not item['can_submit'] for item in completed['items'])
    assert client.post(base+'/submissions',headers=headers,json={**body,'answers':list(reversed(answers))}).json()==completed
    assert len(answer_events.read_answer_events(f['learner'],sid,dsn=f['dsn']))==3
    other={**HEADERS,'Idempotency-Key':'different-intent'}
    assert client.post(base+'/submissions',headers=other,json=body).status_code==409
    assert client.get(base).json()==completed


@pytest.mark.parametrize('corrupt',['missing','duplicate','wrong-question','wrong-option','foreign-assessment'])
def test_invalid_member_rejects_every_answer_without_partial_scoring(closed_loop,corrupt):
    f=concept_fixture(closed_loop,3);identity=create(f);finish(f);before=read(f,identity)
    answers=answers_for(f,identity)
    if corrupt=='missing':answers.pop()
    elif corrupt=='duplicate':answers[-1]=deepcopy(answers[0])
    elif corrupt=='wrong-question':answers[-1]['question_id']=answers[0]['question_id']
    elif corrupt=='wrong-option':answers[-1]['selected_option_id']='option:sha256:'+'f'*64
    else:answers[-1]['assessment_revision']='assessment:sha256:'+'f'*64
    with pytest.raises(sets.AssessmentSetError,match='REQUEST_INVALID'):send(f,identity,answers)
    assert read(f,identity)==before
    assert answer_events.read_answer_events(f['learner'],f['study'].study_session_id,dsn=f['dsn'])==()


def test_mid_write_failure_rolls_back_all_answers_and_versions(closed_loop,monkeypatch):
    f=concept_fixture(closed_loop,3);identity=create(f);finish(f);before=read(f,identity)
    original=answer_events.record_answer;calls=[]
    def fail(*args,**kwargs):
        calls.append(True)
        if len(calls)==2:raise RuntimeError('SIMULATED_STORAGE_FAILURE')
        return original(*args,**kwargs)
    monkeypatch.setattr(answer_events,'record_answer',fail)
    with pytest.raises(RuntimeError,match='SIMULATED_STORAGE_FAILURE'):send(f,identity,answers_for(f,identity))
    assert len(calls)==2 and read(f,identity)==before
    with database_session(f['dsn']) as session:
        assert session.get(StudySession,f['study'].study_session_id).last_event_number==0
        assert list(session.scalars(select(AnswerEvent).where(AnswerEvent.study_session_id==f['study'].study_session_id)))==[]


@pytest.mark.parametrize('same_key',[True,False])
def test_concurrent_set_submissions_cannot_double_score(closed_loop,same_key):
    f=concept_fixture(closed_loop,3);identity=create(f);finish(f)
    answers=answers_for(f,identity);version=read(f,identity)['set_version'];barrier=Barrier(2)
    def submit(index):
        barrier.wait(timeout=5)
        try:send(f,identity,answers,key='one-intent' if same_key else f'intent-{index}',version=version);return 'saved'
        except sets.AssessmentSetError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,range(2)))
    assert results.count('saved')==(2 if same_key else 1)
    assert read(f,identity)['answered_count']==3
    events=answer_events.read_answer_events(f['learner'],f['study'].study_session_id,dsn=f['dsn'])
    assert [event.event_number for event in events]==[1,2,3]


def test_previously_saved_individual_answer_is_preserved_when_finishing_set(closed_loop):
    f=concept_fixture(closed_loop,3);identity=create(f);finish(f);answers=answers_for(f,identity)
    # 建立切換前已保存一題的 fixture，公開單題 API 現在禁止新增題組答案。
    with database_session(f['dsn']) as session:
        study,_,_=sets._scope(session,f['learner'],f['study'].study_session_id,lock=True)
        row=session.get(Assessment,answers[0]['assessment_revision'])
        saved=answer_events.record_answer(session,study,row,answers[0]['selected_option_id'],'pre-existing-answer')
    bad=deepcopy(answers);bad[0]['selected_option_id']=next(o['option_id'] for o in read(f,identity)['items'][0]['assessment']['options'] if o['option_id']!=answers[0]['selected_option_id'])
    with pytest.raises(sets.AssessmentSetError,match='CONFLICT'):send(f,identity,bad)
    assert read(f,identity)['answered_count']==1
    send(f,identity,answers)
    result=read(f,identity)
    assert result['answered_count']==3 and result['items'][0]['feedback']['answer_event_id']==saved.event.answer_event_id
    assert result['status']=='completed'
