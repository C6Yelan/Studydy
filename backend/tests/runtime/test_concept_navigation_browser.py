"""A 出題中仍能進入 B；每個觀念的原题組與作答獨立保存。"""
import json
from threading import Event,Thread
import httpx
import pytest
from sqlalchemy import select
import runtime.api.app as api
from learning_adaptation import assessment_sets as sets
from runtime.storage.tables import AssessmentSet,AnswerEvent,database_session
from browser_e2e_runner import PORT,local_api,main as run_browser
from test_closed_loop_v1 import closed_loop
from assessment_fixtures import concept_fixture, model_for
from test_assessment_concept_navigation import other_model,other_concept


@pytest.mark.parametrize('width',[1536,390])
def test_navigation_keeps_unfinished_concepts_and_resumes_duplicate_intent(closed_loop,monkeypatch,width):
    f=concept_fixture(closed_loop,1);other=other_concept(f)
    monkeypatch.setattr(api,'runtime_binding',lambda _: {})
    app=api.create_app(api.ApiSettings(profile='local',public_origin=f'http://127.0.0.1:{PORT}',
        secure_cookie=False,local_config=f['settings'],dsn=f['dsn']))
    release,stop=Event(),Event();calls=[];errors=[];base=model_for(f)
    def model(client,**kwargs):
        while not release.wait(.05):
            if stop.is_set():raise RuntimeError('Fixture stopped')
        calls.append(kwargs['task'])
        claim=kwargs['request']['claim'];value=claim['text'] if isinstance(claim,dict) else claim
        return other_model(client,**kwargs) if value=='Other topic uses EXTERNAL.' else base(client,**kwargs)
    @app.post('/v1/__test/navigation/release')
    def release_generation():release.set();return {'released':True}
    def worker():
        while not stop.wait(.05):
            try:
                work=sets.claim_set_work(dsn=f['dsn'])
                if work:sets.execute_set_work(work,dsn=f['dsn'],semantic_call=model)
            except Exception as error:errors.append(type(error).__name__);return
    def no_http(*args,**kwargs):raise AssertionError('NO_MODEL_HTTP')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',no_http)
    monkeypatch.setenv('STUDYDY_E2E_CONCEPT_NAVIGATION','true')
    monkeypatch.setenv('STUDYDY_E2E_NAVIGATION_DATA',json.dumps({'material':str(f['source'].material_id),'run':str(f['run'].run_id),
        'revision':f['document']['revision'],'session':str(f['study'].study_session_id),
        'first':f['concept']['concept_id'],'second':other['concept_id'],'width':width}))
    thread=Thread(target=worker);thread.start()
    try:
        with local_api(app):assert run_browser('e2e/concept-navigation.spec.ts',production=True)==0
    finally:stop.set();release.set();thread.join(timeout=10)
    assert not thread.is_alive() and not errors and calls==['assessment','assessment_check']*2
    with database_session(f['dsn']) as session:
        groups=list(session.scalars(select(AssessmentSet).where(AssessmentSet.study_session_id==f['study'].study_session_id)))
        assert len(groups)==2
        assert next(group for group in groups if group.target_concept_id==f['concept']['concept_id']).status=='ready'
        assert next(group for group in groups if group.target_concept_id==other['concept_id']).status=='completed'
        assert len(list(session.scalars(select(AnswerEvent).where(AnswerEvent.study_session_id==f['study'].study_session_id))))==1
