"""只套用既有 next_action；不增加學習路徑或評分策略。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from learning_adaptation.learner_progress import apply_guidance, derive_learner_progress, LearnerProgressError
from learning_adaptation.study_sessions import read_study_session
from runtime.learner_session import TrustedLearner
from test_assessment_sets import closed_loop, concept_fixture, create, read
from test_assessment_remediation import finish, answer
from learning_adaptation import assessment_sets as sets
from test_accounts import _app, ORIGIN, HEADERS


def progress(f):return derive_learner_progress(f['learner'],f['study'].study_session_id,dsn=f['dsn'])
def apply(f,revision):return apply_guidance(f['learner'],f['study'].study_session_id,revision,dsn=f['dsn'])


def test_apply_advance_complete_and_replay_preserves_session_and_answers(closed_loop,tmp_path,monkeypatch):
    from test_assessment_concept_navigation import create_other, other_model
    f=concept_fixture(closed_loop,1);root=create(f);finish(f);answer(f,root)
    before=progress(f);assert before.next_action.action=='advance'
    client=TestClient(_app(f['dsn'],tmp_path,monkeypatch),base_url=ORIGIN);client.cookies.set('studydy_session',f['token'])
    sid=f['study'].study_session_id;url=f'/v1/study-sessions/{sid}/guidance/apply'
    body={'schema':'guidance-apply/v1','guidance_revision':before.guidance_revision}
    stale=client.post(url,headers=HEADERS,json={**body,'guidance_revision':'learner-guidance:sha256:'+'f'*64})
    assert stale.status_code==409 and stale.json()['reason_code']=='LEARNER_GUIDANCE_STALE'
    result=client.post(url,headers=HEADERS,json=body)
    assert result.status_code==200,result.json()
    assert result.json()['current_concept_id']==before.next_action.target_concept_id
    assert client.post(url,headers=HEADERS,json=body).json()==result.json()
    assert read(f,root)['answered_count']==1
    second=create_other(f)
    while work:=sets.claim_set_work(dsn=f['dsn']):sets.execute_set_work(work,dsn=f['dsn'],semantic_call=other_model)
    answer(f,second)
    final=progress(f);assert final.next_action.action=='complete'
    assert apply(f,final.guidance_revision).study_session_id==sid
    stored=read_study_session(f['learner'],sid,dsn=f['dsn'])
    assert stored.status=='completed' and stored.completed_at is not None
    apply(f,final.guidance_revision)
    assert read_study_session(f['learner'],sid,dsn=f['dsn'])==stored
    assert client.post(url,headers=HEADERS,json={**body,'current_concept_id':before.current_concept_id}).status_code==400


def test_active_and_stale_guidance_never_change_focus(closed_loop):
    f=concept_fixture(closed_loop,1);root=create(f);finish(f);answer(f,root)
    before=progress(f)
    active=create(f,'another-activity')
    with pytest.raises(LearnerProgressError,match='STALE'):apply(f,before.guidance_revision)
    current=progress(f);assert current.next_action.action=='continue_set'
    with pytest.raises(LearnerProgressError,match='STALE'):apply(f,current.guidance_revision)
    assert read(f,active)['status']=='preparing' and progress(f).current_concept_id==before.current_concept_id
    with pytest.raises(LearnerProgressError):apply_guidance(TrustedLearner(uuid4()),f['study'].study_session_id,current.guidance_revision,dsn=f['dsn'])


def test_concurrent_same_revision_advances_only_once(closed_loop):
    f=concept_fixture(closed_loop,1);root=create(f);finish(f);answer(f,root)
    before=progress(f);barrier=Barrier(2)
    def run(_):barrier.wait(timeout=10);return apply(f,before.guidance_revision)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert all(result.current_concept_id==before.next_action.target_concept_id for result in results)
    assert read_study_session(f['learner'],f['study'].study_session_id,dsn=f['dsn']).status=='active'
