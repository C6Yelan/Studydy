
from product_fixtures import seed_pdf, seed_run, publish_fixture_structure
"""單一觀念、多個重點的真 PostgreSQL／API 題組；模型使用受控回應。"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import io
import re
from uuid import uuid4

import psycopg
import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context, build_knowledge_structure
from learning_adaptation import assessment_sets as sets
from learning_adaptation.answer_events import read_answer_events, AnswerSubmissionError
from learning_adaptation.study_sessions import create_study_session
from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.material_processing import claim_next_material_processing_run, _record_progress
from runtime.semantic_service import SemanticServiceError
from runtime.storage.tables import Assessment, AssessmentSet, AssessmentSetItem, StudySession, database_session
from test_closed_loop_v1 import closed_loop
from test_accounts import _app, ORIGIN, HEADERS


def concept_fixture(closed_loop, count=3, *, facts=None, label="Signals", evidence_kind="paragraph", source_review_required=False):
    learner, _, settings, _, dsn, token = closed_loop
    points = [f'Signal {index} uses code{index}.' for index in range(count)] if facts is None else facts
    count = len(points)
    facts = [*points, 'Other topic uses EXTERNAL.']
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for index, value in enumerate(facts):
            page.insert_text((72, 72 + index * 22), value)
        regions = [list(page.search_for(value)[0]) for value in facts]
        payload = pdf.tobytes()
    source = seed_pdf(learner.learner_id, io.BytesIO(payload), str(uuid4()), dsn=dsn)
    run = seed_run(learner.learner_id, source.material_id, source.artifact_id,
                                        str(uuid4()), settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == run.run_id
    for stage in ('evidence','semantics','publishing'):
        _record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    page_ref = 'page:sha256:' + canonical_sha256({'source_sha256': source.sha256, 'page_number': 1})
    blocks = []
    for index, value in enumerate(facts):
        region = regions[index]
        block_id = 'block:sha256:' + canonical_sha256({'page_ref':page_ref,'reading_order':index,'region':region})
        content = {'page_ref':page_ref,'block_id':block_id,'kind':evidence_kind,'source':'native_text',
                   'text':value,'reading_order':index,'region':region}
        blocks.append({'evidence_id':'evidence:sha256:'+canonical_sha256(content),'block_id':block_id,
            'kind':evidence_kind,'source':'native_text','text':value,'reading_order':index,
            'locator':{'page':1,'block_id':block_id,'region':region}})
    page = {'schema':'page-evidence/v4','material_id':'material:sha256:'+source.sha256,
            'page_ref':page_ref,'page_number':1,'evidence_blocks':blocks}
    context = build_document_context([page], page_count=1)
    state = SemanticState()
    state.source_review_required = source_review_required
    apply_semantic_response({'concepts':[
        {'k':'signals','l':label,'a':[],'c':[{'m':None,'s':[index]} for index in range(count)]},
        {'k':'other','l':'Other topic','a':[],'c':[{'m':None,'s':[count]}]},
    ],'relations':[]}, context=context, bundle={'sections':context['sections'],'evidence':context['evidence']},state=state)
    lock = settings['runtime_lock']
    document = build_knowledge_structure(context,state,source_sha256=source.sha256,run_id=str(run.run_id),
        produced_at='2026-09-20T00:00:00+00:00',runtime_lock_sha256=canonical_sha256(lock),
        model_id=lock['semantic_service']['model_id'],model_revision=lock['semantic_service']['revision'],semantic_calls=1,ocr_calls=0)
    publish_fixture_structure(learner.learner_id,source.material_id,run.run_id,document,dsn=dsn)
    concept = next(item for item in document['concepts'] if item['label']==label)
    study = create_study_session(learner,source.material_id,document['revision'],str(uuid4()),
                                 current_concept_id=concept['concept_id'],dsn=dsn)
    return {'learner':learner,'settings':settings,'dsn':dsn,'token':token,'source':source,
            'run':run,'document':document,'concept':concept,'study':study}


def model_for(fixture, *, fail=(), calls=None):
    answers = {}
    calls = [] if calls is None else calls
    def model(_client, **kwargs):
        calls.append(kwargs['task'])
        # 真正的另一個 connection 可立即取得 session 鎖，證明推論沒有包在長交易裡。
        with psycopg.connect(fixture['dsn']) as connection:
            connection.execute('SELECT study_session_id FROM study_sessions WHERE study_session_id=%s FOR UPDATE NOWAIT',
                               (fixture['study'].study_session_id,))
        if kwargs['task']=='assessment':
            request = kwargs['request']
            match = re.search(r'Signal (\d+) uses',request['claim']['text'])
            assert match is not None,'題組不可跨到其他觀念'
            number = int(match[1])
            if number in fail:
                raise SemanticServiceError('SEMANTIC_SERVICE_UNAVAILABLE')
            candidate={'learning_angle':'signal code','novelty':'distinct','safety':'safe',
                'prompt':f'Which code does Signal {number} use?','correct_answer':f'code{number}',
                'supporting_evidence_ids':[request['claim']['evidence'][0]['evidence_id']],
                'distractors':[f'wrong{number}a',f'wrong{number}b',f'wrong{number}c']}
            answers[candidate['prompt']]=candidate['correct_answer']
            return {'schema':'assessment-semantics-response/v2','candidates':[
                candidate,{**candidate,'safety':'reject'},{**candidate,'safety':'reject'}]}
        return {'schema':'assessment-check-response/v2','verdicts':[
            {'question_index':question['question_index'],'answer_status':'unique',
             'selected_option_index':question['options'].index(answers[question['prompt']]),
             'duplicate_prior_index':None,'quality_issues':[]}
            for question in kwargs['request']['questions']]}
    return model


def finish(fixture, *, fail=(), calls=None):
    model = model_for(fixture,fail=fail,calls=calls)
    while work := sets.claim_set_work(dsn=fixture['dsn']):
        sets.execute_set_work(work,dsn=fixture['dsn'],semantic_call=model)


def create(fixture, key='round'):
    return sets.create_set(fixture['learner'],fixture['study'].study_session_id,
        fixture['concept']['concept_id'],key,fixture['settings'],dsn=fixture['dsn'])


def read(fixture, identity):
    return sets.read_set(fixture['learner'],fixture['study'].study_session_id,identity,dsn=fixture['dsn'])


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
    assert restored.json()['schema']=='study-resume/v4'
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


def test_cancel_during_generation_fences_checker_and_publication(closed_loop):
    f=concept_fixture(closed_loop,1);identity=create(f);work=sets.claim_set_work(dsn=f['dsn'])
    calls=[];model=model_for(f,calls=calls)
    def cancel(client,**kwargs):
        response=model(client,**kwargs)
        current=read(f,identity)
        sets.change_set(f['learner'],f['study'].study_session_id,identity,'cancel',current['set_version'],'cancel-mid-call',dsn=f['dsn'])
        return response
    sets.execute_set_work(work,dsn=f['dsn'],semantic_call=cancel)
    result=read(f,identity)
    assert result['status']=='cancelled' and result['published_count']==0
    assert calls==['assessment']
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
