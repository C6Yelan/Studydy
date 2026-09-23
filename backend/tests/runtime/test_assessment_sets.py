
"""單一觀念、多個重點的真 PostgreSQL／API 題組；模型使用受控回應。"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from learning_adaptation import assessment_sets as sets
from learning_adaptation.answer_events import read_answer_events, AnswerSubmissionError
from runtime.learner_session import TrustedLearner
from runtime.storage.tables import Assessment, AssessmentSet, AssessmentSetItem, StudySession, database_session
from test_closed_loop_v1 import closed_loop
from assessment_fixtures import concept_fixture, model_for, finish, create, read
from test_accounts import _app, ORIGIN, HEADERS


@pytest.mark.parametrize('count',[1,3,7])
def test_dynamic_set_covers_multiple_points_of_one_concept_and_read_never_generates(closed_loop,count):
    f=concept_fixture(closed_loop,count)
    plan=sets.read_plan(f['learner'],f['study'].study_session_id,f['concept']['concept_id'],dsn=f['dsn'])
    assert plan['requested_count']==count
    identity=create(f)
    assert create(f)==identity
    assert read(f,identity)['requested_count']==count
    assert all(item['assessment'] is None for item in read(f,identity)['items'])
    calls=[];finish(f,calls=calls)
    result=read(f,identity)
    assert result['status']=='ready' and result['published_count']==count
    assert len(calls)==2*count
    assert {item['assessment']['target_concept_id'] for item in result['items']}=={f['concept']['concept_id']}
    assert len({item['target_claim_id'] for item in result['items']})==count
    before=deepcopy(result)
    assert read(f,identity)==before and len(calls)==2*count
    for item in result['items']:
        assert 'correct_option_id' not in item['assessment'] and 'generation_provenance' not in item['assessment']
        assert item['can_submit'] and item['feedback'] is None


def test_partial_publish_keeps_verified_questions_and_failure_is_not_wrong(closed_loop):
    f=concept_fixture(closed_loop)
    identity=create(f);calls=[];finish(f,fail={1},calls=calls)
    result=read(f,identity)
    assert result['status']=='partial_ready' and result['verified_count']==2 and result['published_count']==0
    assert all(item['assessment'] is None for item in result['items'])
    sets.change_set(f['learner'],f['study'].study_session_id,identity,'publish-partial',result['set_version'],'partial',dsn=f['dsn'])
    ready=read(f,identity)
    assert ready['published_count']==2 and ready['requested_count']==3
    assert sum(item['state']=='omitted' for item in ready['items'])==1
    assert read_answer_events(f['learner'],f['study'].study_session_id,dsn=f['dsn'])==()
    sets.change_set(f['learner'],f['study'].study_session_id,identity,'publish-partial',result['set_version'],'partial',dsn=f['dsn'])
    assert read(f,identity)==ready
    assert sets.claim_set_work(dsn=f['dsn']) is None



def test_expired_lease_and_late_result_do_not_publish_or_retry_implicitly(closed_loop):
    f=concept_fixture(closed_loop,1);identity=create(f)
    work=sets.claim_set_work(dsn=f['dsn'])
    with database_session(f['dsn']) as session:
        group=session.get(AssessmentSet,identity)
        group.lease_expires_at=datetime.now(UTC)-timedelta(seconds=1)
    assert sets.claim_set_work(dsn=f['dsn']) is None
    assert read(f,identity)['status']=='failed'
    calls=[]
    sets.execute_set_work(work,dsn=f['dsn'],semantic_call=model_for(f,calls=calls))
    assert calls==[] and read(f,identity)['published_count']==0
    result=read(f,identity)
    sets.change_set(f['learner'],f['study'].study_session_id,identity,'retry',result['set_version'],'retry',dsn=f['dsn'])
    finish(f)
    assert read(f,identity)['status']=='ready'


def test_api_preserves_scope_private_preparation_and_read_only_resume(closed_loop,tmp_path,monkeypatch):
    f=concept_fixture(closed_loop)
    client=TestClient(_app(f['dsn'],tmp_path,monkeypatch),base_url=ORIGIN)
    client.cookies.set('studydy_session',f['token'])
    sid=f['study'].study_session_id;base=f'/v1/study-sessions/{sid}/assessment-sets'
    created=client.post(base,headers={**HEADERS,'Idempotency-Key':'http-round'},json={
        'schema':'assessment-set-create/v1','target_concept_id':f['concept']['concept_id']})
    assert created.status_code==202,created.json()
    identity=created.json()['set_id']
    assert created.json()['requested_count']==3
    work=sets.claim_set_work(dsn=f['dsn']);calls=[]
    sets.execute_set_work(work,dsn=f['dsn'],semantic_call=model_for(f,calls=calls))
    with database_session(f['dsn']) as session:
        item=session.get(AssessmentSetItem,(work.set_id,work.ordinal))
        hidden=item.prepared_document['public']['assessment_revision']
    result=client.get(f'{base}/{identity}')
    assert result.status_code==200 and result.json()['verified_count']==1
    assert all(item['assessment'] is None for item in result.json()['items'])
    assert client.get(f'/v1/study-sessions/{sid}/assessments/{hidden}').status_code==404
    route=f'/v1/materials/{f["source"].material_id}/knowledge-structures/{f["document"]["revision"]}/study-sessions/{sid}/resume'
    restored=client.get(route,params={'run_id':str(f['run'].run_id),'set_id':identity})
    assert restored.status_code==200,restored.json()
    assert restored.json()['schema']=='study-resume/v5'
    assert restored.json()['selected_set_id']==identity and 'assessments' not in restored.json()
    assert len(calls)==2
    conflict=client.post(base,headers={**HEADERS,'Idempotency-Key':'another-round'},json={
        'schema':'assessment-set-create/v1','target_concept_id':f['concept']['concept_id']})
    assert conflict.status_code==409
    assert client.post(base,headers={**HEADERS,'Idempotency-Key':'fixed-count'},json={
        'schema':'assessment-set-create/v1','target_concept_id':f['concept']['concept_id'],'requested_count':5}).status_code==400
    finish(f)
    ready=client.get(f'{base}/{identity}').json()
    assert ready['published_count']==3
    assert not {'runtime_lock_document','execution_identity','target_plan','action_receipts'} & ready.keys()
    for item in ready['items']:
        assert not {'prepared_document','correct_option_id','generation_provenance'} & item.keys()
    with pytest.raises(sets.AssessmentSetError,match='NOT_FOUND'):
        sets.read_set(TrustedLearner(uuid4()),sid,work.set_id,dsn=f['dsn'])


def test_ended_study_automatically_cancels_pending_generation(closed_loop):
    from runtime.storage.tables import StudySession
    f=concept_fixture(closed_loop,1);identity=create(f)
    with database_session(f['dsn']) as session:
        study=session.get(StudySession,f['study'].study_session_id)
        study.status='completed';study.completed_at=sets._now()
    assert sets.claim_set_work(dsn=f['dsn']) is None
    result=read(f,identity)
    assert result['status']=='cancelled' and result['published_count']==0
    assert read_answer_events(f['learner'],f['study'].study_session_id,dsn=f['dsn'])==()


def test_material_delete_cancels_set_and_late_model_result_cannot_recreate_records(closed_loop):
    from runtime.material_discard import request_material_discard
    f=concept_fixture(closed_loop,1);identity=create(f);work=sets.claim_set_work(dsn=f['dsn'])
    model=model_for(f)
    def discard(client,**kwargs):
        response=model(client,**kwargs)
        assert request_material_discard(f['learner'].learner_id,f['source'].material_id,dsn=f['dsn'])=='removed'
        return response
    sets.execute_set_work(work,dsn=f['dsn'],semantic_call=discard)
    with database_session(f['dsn']) as session:
        assert session.get(AssessmentSet,identity) is None
        assert session.get(StudySession,f['study'].study_session_id) is None
    assert sets.claim_set_work(dsn=f['dsn']) is None


@pytest.mark.parametrize('committed',[False,True])
def test_uncertain_commit_reuses_verified_result_without_another_model_call(closed_loop,monkeypatch,committed):
    f=concept_fixture(closed_loop,1);identity=create(f);work=sets.claim_set_work(dsn=f['dsn'])
    commit=sets._commit_prepared;attempts=[]
    def uncertain(*args,**kwargs):
        attempts.append(True)
        if len(attempts)==1:
            if committed:commit(*args,**kwargs)
            raise RuntimeError('Synthetic commit response interruption')
        return commit(*args,**kwargs)
    monkeypatch.setattr(sets,'_commit_prepared',uncertain)
    calls=[]
    sets.execute_set_work(work,dsn=f['dsn'],semantic_call=model_for(f,calls=calls))
    result=read(f,identity)
    assert result['status']=='ready' and result['published_count']==1
    assert calls==['assessment','assessment_check']
    assert result['items'][0]['attempts']==1
    with database_session(f['dsn']) as session:
        assert len(list(session.scalars(select(Assessment).where(Assessment.study_session_id==f['study'].study_session_id))))==1


def test_concurrent_creation_replays_same_intent_and_rejects_another_active_set_for_same_concept(closed_loop):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    f = concept_fixture(closed_loop, 1)
    barrier = Barrier(2)
    def concurrent():
        barrier.wait(timeout=5)
        return create(f)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: concurrent(), range(2)))
    assert first == second
    with pytest.raises(sets.AssessmentSetError, match='ACTIVE'):
        create(f, 'another-intent')
    with database_session(f['dsn']) as session:
        assert len(list(session.scalars(select(AssessmentSet).where(
            AssessmentSet.study_session_id == f['study'].study_session_id)))) == 1


def test_retry_only_failed_point_and_published_scope_is_immutable(closed_loop):
    from sqlalchemy.exc import DBAPIError
    f = concept_fixture(closed_loop)
    identity = create(f)
    finish(f, fail={1})
    with database_session(f['dsn']) as session:
        preserved = {item.ordinal: deepcopy(item.prepared_document) for item in session.scalars(
            select(AssessmentSetItem).where(AssessmentSetItem.set_id == identity)) if item.state == 'verified'}
    before = read(f, identity)
    sets.change_set(f['learner'], f['study'].study_session_id, identity, 'retry',
                    before['set_version'], 'retry-failed', dsn=f['dsn'])
    calls = []
    finish(f, calls=calls)
    ready = read(f, identity)
    assert calls == ['assessment', 'assessment_check']
    assert ready['published_count'] == 3
    for item in ready['items']:
        if item['ordinal'] in preserved:
            assert item['assessment'] == preserved[item['ordinal']]['public'] and item['attempts'] == 1
        else:
            assert item['attempts'] == 2
    with pytest.raises(DBAPIError, match='immutable'):
        with database_session(f['dsn']) as session:
            session.get(AssessmentSetItem, (identity, 1)).target_claim_id = 'replacement'
    with pytest.raises(DBAPIError, match='immutable'):
        with database_session(f['dsn']) as session:
            session.get(AssessmentSet, identity).requested_count = 99
    assert read(f, identity) == ready


def test_heading_classification_does_not_exclude_a_grounded_definition(closed_loop):
    f = concept_fixture(closed_loop, 1, facts=['The integer at position i is stored in list[i].'], evidence_kind='heading')
    assert all(row['kind'] == 'heading' for row in f['document']['evidence'])
    plan = sets.read_plan(f['learner'], f['study'].study_session_id, f['concept']['concept_id'], dsn=f['dsn'])
    assert plan['requested_count'] == 1 and plan['excluded'] == []
