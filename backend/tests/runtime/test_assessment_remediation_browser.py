"""錯題補強的真 API／DB 瀏覽器，模型全數由受控 fixture 提供。"""
from copy import deepcopy
import json
from threading import Event, Thread

import httpx
import pytest
from sqlalchemy import select

import runtime.api.app as api
from learning_adaptation import assessment_sets as sets
from runtime.storage.tables import AssessmentSet, AnswerEvent, database_session
from browser_e2e_runner import PORT, local_api, main as run_browser
from test_closed_loop_v1 import closed_loop
from assessment_fixtures import concept_fixture, model_for


@pytest.mark.parametrize('width', [1536, 390])
def test_direct_remediation_and_unknown_responses_resume_without_repeating(closed_loop, monkeypatch, width):
    f=concept_fixture(closed_loop, 3)
    monkeypatch.setattr(api,'runtime_binding',lambda _: {})
    app=api.create_app(api.ApiSettings(profile='local',public_origin=f'http://127.0.0.1:{PORT}',
        secure_cookie=False,local_config=f['settings'],dsn=f['dsn']))
    stop=Event(); errors=[]; calls=[]; known={}; prompts={}
    base=model_for(f,calls=calls)
    def worker():
        while not stop.wait(.05):
            try:
                work=sets.claim_set_work(dsn=f['dsn'])
                if work is None:continue
                if work.set_id not in known:known[work.set_id]=len(known)+1
                def model(client,**kwargs):
                    if kwargs['task']=='assessment':
                        result=base(client,**kwargs)
                        for candidate in result['candidates']:
                            old=candidate['prompt'];candidate['prompt']=f'Round {known[work.set_id]}: {old}'
                            prompts[candidate['prompt']]=old
                        return result
                    request=deepcopy(kwargs['request'])
                    for question in request['questions']:question['prompt']=prompts[question['prompt']]
                    return base(client,**{**kwargs,'request':request})
                sets.execute_set_work(work,dsn=f['dsn'],semantic_call=model)
            except Exception as error:
                errors.append(type(error).__name__);return
    def no_http(*args,**kwargs):raise AssertionError('MODEL_HTTP_NOT_ALLOWED')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',no_http)
    monkeypatch.setenv('STUDYDY_E2E_REMEDIATION','true')
    monkeypatch.setenv('STUDYDY_E2E_REMEDIATION_DATA',json.dumps({'material':str(f['source'].material_id),
        'run':str(f['run'].run_id),'revision':f['document']['revision'],'session':str(f['study'].study_session_id),
        'concept':f['concept']['concept_id'],'width':width}))
    thread=Thread(target=worker);thread.start()
    try:
        with local_api(app):
            result=run_browser('e2e/assessment-remediation.spec.ts',production=True)
            if result:
                from runtime.storage.tables import AssessmentSetItem
                with database_session(f['dsn']) as session:
                    groups=list(session.scalars(select(AssessmentSet).where(AssessmentSet.study_session_id==f['study'].study_session_id)))
                    items=list(session.scalars(select(AssessmentSetItem).where(AssessmentSetItem.study_session_id==f['study'].study_session_id)))
                    print(json.dumps({'worker_errors':errors,'calls':calls,'groups':[g.status for g in groups],
                        'items':[(i.state,i.failure_reason) for i in items]}))
            assert result==0
    finally:
        stop.set();thread.join(timeout=10)
    assert not thread.is_alive() and errors==[]
    assert calls==['assessment','assessment_check']*6
    with database_session(f['dsn']) as session:
        groups=list(session.scalars(select(AssessmentSet).where(AssessmentSet.study_session_id==f['study'].study_session_id)))
        assert len(groups)==3 and all(group.status=='completed' for group in groups)
        root=next(group for group in groups if group.kind=='diagnostic')
        assert all(g.diagnostic_set_id==root.set_id for g in groups if g.kind=='remediation')
        answers=list(session.scalars(select(AnswerEvent).where(AnswerEvent.study_session_id==f['study'].study_session_id)))
        assert len(answers)==6 and sum(answer.is_correct for answer in answers)==3
