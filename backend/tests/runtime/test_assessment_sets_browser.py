"""單一觀念題組的真 API／DB browser：受控模型，沒有外部模型請求。"""
import json
from uuid import UUID
from threading import Event, Thread

import httpx
import pytest
from sqlalchemy import select

import runtime.api.app as api
from learning_adaptation import assessment_sets as sets
from runtime.storage.tables import AnswerEvent, AssessmentSet, database_session
from browser_e2e_runner import PORT, local_api, main as run_browser
from test_closed_loop_v1 import closed_loop
from assessment_fixtures import concept_fixture, model_for


@pytest.mark.parametrize('width',[1920,1536,1366,390])
def test_whole_set_browser_submits_once_and_restores_all_answers(closed_loop,monkeypatch,width):
    f=concept_fixture(closed_loop,3)
    monkeypatch.setattr(api,'runtime_binding',lambda _: {})
    app=api.create_app(api.ApiSettings(profile='local',public_origin=f'http://127.0.0.1:{PORT}',
        secure_cookie=False,local_config=f['settings'],dsn=f['dsn']))
    release,stop=Event(),Event();errors=[];calls=[]
    base_model=model_for(f,calls=calls)
    def model(*args,**kwargs):
        while not release.wait(.05):
            if stop.is_set():raise RuntimeError('Fixture stopped')
        return base_model(*args,**kwargs)
    @app.post('/v1/__test/sets/{set_id}/version')
    def advance_version(set_id: UUID):
        # 模擬讀取後的並行狀態更新，驗證交卷遇到明確 409 後不重用舊版本。
        with database_session(f['dsn']) as session:
            group=session.get(AssessmentSet,set_id)
            assert group.study_session_id==f['study'].study_session_id
            group.set_version+=1
        return {'updated':True}
    @app.post('/v1/__test/sets/release')
    def release_generation():
        release.set();return {'released':True}
    def worker():
        while not stop.wait(.05):
            try:
                work=sets.claim_set_work(dsn=f['dsn'])
                if work:sets.execute_set_work(work,dsn=f['dsn'],semantic_call=model)
            except Exception as error:
                errors.append(type(error).__name__);return
    def no_model_http(*args,**kwargs):raise AssertionError('MODEL_HTTP_NOT_ALLOWED')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',no_model_http)
    monkeypatch.setenv('STUDYDY_E2E_ASSESSMENT_SET','true')
    monkeypatch.setenv('STUDYDY_E2E_SET_DATA',json.dumps({'material':str(f['source'].material_id),
        'run':str(f['run'].run_id),'revision':f['document']['revision'],'session':str(f['study'].study_session_id),
        'concept':f['concept']['concept_id'],'width':width}))
    thread=Thread(target=worker);thread.start()
    try:
        with local_api(app):assert run_browser('e2e/assessment-sets.spec.ts',production=True)==0
    finally:
        stop.set();release.set();thread.join(timeout=10)
    assert not thread.is_alive() and errors==[]
    assert calls==['assessment','assessment_check']*3
    with database_session(f['dsn']) as session:
        groups=list(session.scalars(select(AssessmentSet).where(AssessmentSet.study_session_id==f['study'].study_session_id)))
        answers=list(session.scalars(select(AnswerEvent).where(AnswerEvent.study_session_id==f['study'].study_session_id)))
        assert len(groups)==1 and groups[0].status=='completed'
        assert len(answers)==3 and sum(row.is_correct for row in answers)==2
