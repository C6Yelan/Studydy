"""B3-A 的真 DB／真 PDF 行為測試；模型 transport 由 fixture 阻擋。"""
from copy import deepcopy
import io
from uuid import uuid4

import pymupdf
import pytest
from sqlalchemy import select

from test_closed_loop_v1 import closed_loop
from test_source_normalization import normalizer
from runtime.source_normalization import create_draft, upload_source, read_sources, normalize_next, SourceError
from runtime.source_revisions import create_revision
from runtime.material_processing import (claim_next_material_processing_run, execute_claimed_material_processing_run,
    request_material_processing_cancellation, read_material_processing_run, runtime_binding)
from runtime.storage.knowledge_structures import read_knowledge_structure
from runtime.storage.tables import database_session, Material, MaterialSourceSet, KnowledgeStructure
from runtime.source_resolver import resolve_evidence_source
from learning_adaptation.study_sessions import create_study_session


def pdf(text):
    with pymupdf.open() as document:
        document.new_page().insert_text((72,72),text)
        return document.tobytes()


@pytest.fixture
def revisions(closed_loop, normalizer, monkeypatch):
    learner,_,settings,_,dsn,_ = closed_loop
    from pdf_evidence import material_pipeline
    import runtime.material_processing as processing
    monkeypatch.setattr(processing,'runtime_preflight',lambda config:runtime_binding(config))
    monkeypatch.setattr(material_pipeline,'material_request_fits',lambda *_:True)
    actual=material_pipeline.analyze_material
    requests=[]
    def semantic(_client,**kwargs):
        request=kwargs['request'];requests.append(deepcopy(request))
        rows=[row for section in request['sections'] for row in section['evidence'] if row[2]!='heading']
        result={'concepts':[{'k':f'new_{row[0]}','l':f'Concept {row[0]}','a':[],'c':[{'m':None,'s':[row[0]]}]} for row in rows], 'relations':[]}
        if 'update_policy' in request:result['review_required']=False
        return result
    monkeypatch.setattr(processing,'analyze_material',lambda *args,**kw:actual(*args,**kw,semantic_call=semantic))
    material=create_draft(learner.learner_id,'Combined textbook','new-material',dsn=dsn)
    def add(name,text):
        identity=upload_source(learner.learner_id,material,pdf(text),name,'application/pdf',str(uuid4()),dsn=dsn)
        assert normalize_next(dsn=dsn)
        return next(s['normalization_id'] for s in read_sources(learner.learner_id,material,dsn=dsn) if s['source_id']==identity)
    def start(ids,key,base=None):
        return create_revision(learner.learner_id,material,ids,key,settings,base_revision=base,dsn=dsn)
    def execute():
        claim=claim_next_material_processing_run(dsn=dsn)
        return execute_claimed_material_processing_run(claim,settings,dsn=dsn)
    first=add('A.pdf','A stack uses LIFO to remove the last inserted element first.')
    start([first],'initial');r1=execute()
    assert r1.status=='succeeded',r1.error_code
    doc=read_knowledge_structure(learner.learner_id,material,run_id=r1.run_id,dsn=dsn).document
    return learner,material,settings,dsn,add,start,execute,r1,doc,requests


def test_append_uses_only_new_semantics_preserves_history_and_replays_after_promotion(revisions):
    learner,material,settings,dsn,add,start,execute,r1,old,requests=revisions
    study=create_study_session(learner,material,old['revision'],'study',dsn=dsn)
    snapshot=deepcopy(old)
    second=add('B.pdf','A queue removes the first inserted element first.')
    run=start([second],'append',old['revision'])
    with pytest.raises(SourceError,match='REVISION_IN_PROGRESS'):
        start([second],'competing',old['revision'])
    result=execute()
    assert result.status=='succeeded',result.error_code
    newest=read_knowledge_structure(learner.learner_id,material,run_id=result.run_id,dsn=dsn).document
    assert newest['schema']=='knowledge-structure/v4' and 'source_sha256' not in newest
    assert newest['page_count']==2 and len(newest['input_binding']['manifest']['items'])==2
    assert requests[-1]['existing_concepts']
    assert {row[1] for section in requests[-1]['sections'] for row in section['evidence']}=={2}
    assert read_knowledge_structure(learner.learner_id,material,revision=old['revision'],dsn=dsn).document==snapshot
    assert start([second],'append',old['revision']).run_id==run.run_id
    with pytest.raises(SourceError,match='REVISION_CONFLICT'):
        start([second],'wrong-base',old['revision'])
    evidence=next(e for e in newest['evidence'] if e['page']==2)
    location=resolve_evidence_source(learner.learner_id,material,newest['revision'],evidence['evidence_id'],dsn=dsn)
    assert location['original_name']=='B.pdf' and location['normalized_page']==1
    with database_session(dsn) as session:
        assert session.get(Material,material).head_revision==newest['revision']


def test_initial_multiple_sources_freeze_order_and_then_allow_incremental_append(revisions):
    learner,_,settings,dsn,_,_,execute,_,_,requests=revisions
    material=create_draft(learner.learner_id,'Initial sources','initial-many',dsn=dsn)
    def add(name,data,media):
        identity=upload_source(learner.learner_id,material,data,name,media,str(uuid4()),dsn=dsn)
        assert normalize_next(dsn=dsn)
        return next(item['normalization_id'] for item in read_sources(learner.learner_id,material,dsn=dsn) if item['source_id']==identity)
    a=add('A.pdf',pdf('A stack follows last in first out order.'),'application/pdf')
    b=add('B.txt',b'A queue follows first in first out order.','text/plain')
    start=lambda ids,key,base=None:create_revision(learner.learner_id,material,ids,key,settings,base_revision=base,dsn=dsn)
    run=start([b,a],'mixed-initial')
    with pytest.raises(SourceError,match='IDEMPOTENCY_CONFLICT'):start([a,b],'mixed-initial')
    c=add('C.txt',b'A tree consists of a root and child nodes.','text/plain')
    before_calls=len(requests)
    result=execute();assert result.status=='succeeded',result.error_code
    document=read_knowledge_structure(learner.learner_id,material,run_id=result.run_id,dsn=dsn).document
    assert [item['original_name'] for item in document['input_binding']['manifest']['items']]==['B.txt','A.pdf']
    assert {row[1] for request in requests[before_calls:] for section in request['sections'] for row in section['evidence']}=={1,2}
    for page,name in [(1,'B.txt'),(2,'A.pdf')]:
        evidence=next(item for item in document['evidence'] if item['page']==page)
        location=resolve_evidence_source(learner.learner_id,material,document['revision'],evidence['evidence_id'],dsn=dsn)
        assert location['original_name']==name and location['normalized_page']==1
    assert start([b,a],'mixed-initial').run_id==run.run_id
    with database_session(dsn) as session:assert session.get(Material,material).head_revision==document['revision']
    start([c],'after-initial-many',document['revision'])
    before_calls=len(requests)
    appended=execute();assert appended.status=='succeeded',appended.error_code
    assert {row[1] for request in requests[before_calls:] for section in request['sections'] for row in section['evidence']}=={3}


def test_initial_multiple_sources_reject_unready_or_foreign_members_without_partial_run(revisions):
    from runtime.storage.tables import MaterialProcessingRun
    learner,other_material,settings,dsn,_,_,_,_,_,_=revisions
    material=create_draft(learner.learner_id,'Unready initial','unready-many',dsn=dsn)
    upload_source(learner.learner_id,material,pdf('A stack follows last in first out order.'),'A.pdf','application/pdf','a',dsn=dsn)
    assert normalize_next(dsn=dsn)
    upload_source(learner.learner_id,material,b'Queue uses FIFO.','B.txt','text/plain','b',dsn=dsn)
    a,b=read_sources(learner.learner_id,material,dsn=dsn)
    foreign=read_sources(learner.learner_id,other_material,dsn=dsn)[0]['normalization_id']
    for member in (b['normalization_id'],foreign):
        with pytest.raises(SourceError,match='SOURCE_NOT_READY'):
            create_revision(learner.learner_id,material,[a['normalization_id'],member],str(uuid4()),settings,dsn=dsn)
    with database_session(dsn) as session:
        assert session.scalar(select(MaterialProcessingRun).where(MaterialProcessingRun.material_id==material)) is None
        assert session.scalar(select(MaterialSourceSet).where(MaterialSourceSet.material_id==material)) is None
        assert session.get(Material,material).head_revision is None


def test_run_only_cancel_retains_head_and_unreferenced_old_map_is_pruned_on_success(revisions):
    learner,material,settings,dsn,add,start,execute,r1,old,_=revisions
    second=add('B.pdf','A queue removes the first inserted element first.')
    run=start([second],'cancel',old['revision'])
    cancelled=request_material_processing_cancellation(learner.learner_id,run.run_id,update_only=True,dsn=dsn)
    assert cancelled.status=='cancelled'
    assert request_material_processing_cancellation(learner.learner_id,run.run_id,update_only=True,dsn=dsn)==cancelled
    assert read_knowledge_structure(learner.learner_id,material,revision=old['revision'],dsn=dsn).document==old
    start([second],'retry',old['revision']);result=execute()
    assert result.status=='succeeded',result.error_code
    with database_session(dsn) as session:
        assert session.scalar(select(KnowledgeStructure).where(KnowledgeStructure.material_id==material,
            KnowledgeStructure.structure_revision==old['revision'])) is None
    assert start([second],'cancel',old['revision']).run_id==run.run_id


def test_unchanged_claim_inherits_actual_answers_and_late_old_tab_answer(revisions):
    from learning_adaptation.answer_events import submit_answer,read_assessment_records
    from learning_adaptation.learner_progress import derive_learner_progress
    from test_closed_loop_v1 import generate_assessment,Client,_assessment_response
    learner,material,settings,dsn,add,start,execute,r1,old,_=revisions
    prior=create_study_session(learner,material,old['revision'],'old-study',dsn=dsn)
    concept=old['concepts'][0]
    def question(key):
        return generate_assessment(learner,prior.study_session_id,concept['claims'][0]['claim_id'],key,settings,
            dsn=dsn,client=Client(),semantic_call=lambda *_args,**_kw:_assessment_response(key,f'{key}: Which removal order?',concept['evidence_refs'][0]))
    first=question('First question');second=question('Second question')
    submit_answer(learner,prior.study_session_id,first.assessment_revision,first.question_id,
        first.private_answer_document['correct_option_id'],'answer-one',dsn=dsn)
    addition=add('B.pdf','A queue removes the first inserted element first.')
    start([addition],'append-after-answers',old['revision'])
    # 更新已排程時仍可提交原題，切換後由同一份真實事件推導進度。
    submit_answer(learner,prior.study_session_id,second.assessment_revision,second.question_id,
        second.private_answer_document['correct_option_id'],'answer-two',dsn=dsn)
    records=read_assessment_records(learner,prior.study_session_id,dsn=dsn)
    result=execute();assert result.status=='succeeded',result.error_code
    new=create_study_session(learner,material,result.output_binding['knowledge_structure_revision'],'new-study',dsn=dsn)
    progress=derive_learner_progress(learner,new.study_session_id,dsn=dsn)
    assert sorted(state.status for state in progress.concept_states)==['mastered','not_started']
    assert progress.event_watermark==0
    assert read_assessment_records(learner,prior.study_session_id,dsn=dsn)==records
    third=question('Late old tab question')
    wrong=next(option['option_id'] for option in third.public_document['options'] if option['option_id']!=third.private_answer_document['correct_option_id'])
    submit_answer(learner,prior.study_session_id,third.assessment_revision,third.question_id,wrong,'late-answer',dsn=dsn)
    assert sorted(state.status for state in derive_learner_progress(learner,new.study_session_id,dsn=dsn).concept_states)==['needs_review','not_started']


def test_late_upload_does_not_change_frozen_input_and_duplicate_bytes_are_rejected(revisions):
    learner,material,settings,dsn,add,start,execute,r1,old,_=revisions
    second=add('B.pdf','Queue content.')
    run=start([second],'frozen',old['revision'])
    third=add('C.pdf','Separate late upload content.')
    with database_session(dsn) as session:
        frozen=deepcopy(session.get(MaterialSourceSet,run.input_source_set_id).manifest)
    assert len(frozen['items'])==2
    result=execute();assert result.status=='succeeded',result.error_code
    document=read_knowledge_structure(learner.learner_id,material,run_id=result.run_id,dsn=dsn).document
    assert [item['original_name'] for item in document['input_binding']['manifest']['items']]==['A.pdf','B.pdf']
    assert next(s for s in read_sources(learner.learner_id,material,dsn=dsn) if s['normalization_id']==third)['included'] is False
    from runtime.storage.source_artifacts import open_verified_artifact
    original_id=next(s['original_artifact_id'] for s in read_sources(learner.learner_id,material,dsn=dsn) if s['original_name']=='B.pdf')
    with open_verified_artifact(learner.learner_id,original_id,dsn=dsn) as stream: same_bytes=stream.file.read()
    with pytest.raises(SourceError,match='DUPLICATE_SOURCE'):
        upload_source(learner.learner_id,material,same_bytes,'renamed.pdf','application/pdf','duplicate-bytes',dsn=dsn)


def test_cancel_during_publishing_rejects_late_result(revisions,monkeypatch):
    learner,material,settings,dsn,add,start,execute,r1,old,_=revisions
    import runtime.material_processing as processing
    actual=processing.publish_knowledge_structure
    second=add('B.pdf','Queue content.')
    run=start([second],'cancel-publication',old['revision'])
    def cancel_first(*args,**kwargs):
        request_material_processing_cancellation(learner.learner_id,run.run_id,update_only=True,dsn=dsn)
        return actual(*args,**kwargs)
    monkeypatch.setattr(processing,'publish_knowledge_structure',cancel_first)
    assert execute().status=='cancelled'
    with database_session(dsn) as session:assert session.get(Material,material).head_revision==old['revision']


@pytest.mark.parametrize('failure_stage',['construction','publication'])
def test_retry_after_completed_analysis_reuses_saved_results_without_model_or_ocr(revisions,monkeypatch,failure_stage):
    import json
    from runtime.storage.analysis_archive import _material_directory
    from pdf_evidence import material_pipeline
    import runtime.material_processing as processing
    learner,material,settings,dsn,add,start,execute,_,old,requests=revisions
    second=add('B.pdf','A queue removes the first inserted element first.')
    run=start([second],'finish-failure',old['revision'])
    def fail(*_args,**_kwargs):raise ValueError('KNOWLEDGE_STRUCTURE_INVALID')
    with monkeypatch.context() as patch:
        patch.setattr(material_pipeline if failure_stage=='construction' else processing,
                      'build_knowledge_structure' if failure_stage=='construction' else 'publish_knowledge_structure',fail)
        failed=execute()
    assert failed.status=='failed'
    if failure_stage=='construction':assert failed.error_code=='KNOWLEDGE_STRUCTURE_INVALID'
    directory=_material_directory(learner.learner_id,material)/run.run_id.hex
    saved=json.loads((directory/'checkpoint.json').read_bytes())['data']
    assert saved['cursor']==len(saved['context']['evidence'])
    assert (directory/'call-000001/decoded.json').is_file() and (directory/'failure.json').is_file()
    calls_before=len(requests)
    def no_extraction(*_args,**_kwargs):raise AssertionError('Completed Evidence must be reused')
    monkeypatch.setattr(material_pipeline,'_page_evidence',no_extraction)
    monkeypatch.setattr(processing,'runtime_preflight',lambda *_:(_ for _ in ()).throw(AssertionError('Completed analysis must not require an online model')))
    monkeypatch.setattr(material_pipeline,'semantic_client',lambda:(_ for _ in ()).throw(AssertionError('Completed analysis must not create a model client')))
    retry=start([second],'finish-retry',old['revision'])
    result=execute()
    assert result.status=='succeeded',result.error_code
    assert len(requests)==calls_before,'Retry called the model again for completed analysis'
    completed=_material_directory(learner.learner_id,material)/retry.run_id.hex
    assert json.loads((completed/'completion.json').read_bytes())['reused_from_run']==str(run.run_id)
    assert not (completed/'checkpoint.json').exists()
    assert not (directory/'checkpoint.json').exists(),'已被成功接續的 checkpoint 不再需要保留'
    assert (directory/'call-000001/decoded.json').is_file() and (directory/'failure.json').is_file()


def test_checkpoint_write_failure_stops_before_model_and_corrupt_checkpoint_never_restarts_silently(revisions,monkeypatch):
    import json
    from runtime.storage.analysis_archive import AnalysisArchive,AnalysisArchiveError,_material_directory
    from pdf_evidence import material_pipeline
    learner,material,settings,dsn,add,start,execute,_,old,requests=revisions
    second=add('B.pdf','A queue removes the first inserted element first.')
    start([second],'cannot-save',old['revision'])
    before=len(requests)
    with monkeypatch.context() as patch:
        patch.setattr(AnalysisArchive,'save_checkpoint',lambda *_:(_ for _ in ()).throw(AnalysisArchiveError('ANALYSIS_ARTIFACT_WRITE_FAILED')))
        failed=execute()
    assert failed.error_code=='ANALYSIS_ARTIFACT_WRITE_FAILED' and len(requests)==before
    run=start([second],'save-before-construction',old['revision'])
    with monkeypatch.context() as patch:
        patch.setattr(material_pipeline,'build_knowledge_structure',lambda *_args,**_kwargs:(_ for _ in ()).throw(ValueError('KNOWLEDGE_STRUCTURE_INVALID')))
        assert execute().error_code=='KNOWLEDGE_STRUCTURE_INVALID'
    path=_material_directory(learner.learner_id,material)/run.run_id.hex/'checkpoint.json'
    saved=json.loads(path.read_text());saved['data']['cursor']=0;path.write_text(json.dumps(saved))
    before=len(requests)
    start([second],'corrupt-retry',old['revision'])
    rejected=execute()
    assert rejected.error_code=='ANALYSIS_CHECKPOINT_INVALID' and len(requests)==before


def test_retry_without_usable_added_claims_reuses_evidence_but_retries_semantics(revisions,monkeypatch):
    from pdf_evidence import material_pipeline
    import runtime.material_processing as processing
    learner,material,settings,dsn,add,start,execute,_,old,requests=revisions
    with pymupdf.open() as document:
        document.new_page().insert_text((72,72),'A queue removes the first inserted element first.')
        document.new_page().insert_text((72,72),'A binary tree has left and right child nodes.')
        identity=upload_source(learner.learner_id,material,document.tobytes(),'B.pdf','application/pdf','two-pages',dsn=dsn)
    assert normalize_next(dsn=dsn)
    second=next(item['normalization_id'] for item in read_sources(learner.learner_id,material,dsn=dsn) if item['source_id']==identity)
    monkeypatch.setattr(material_pipeline,'material_request_fits',lambda _client,_lock,request:sum(len(section['evidence']) for section in request['sections'])<=1)
    start([second],'no-added-claims',old['revision'])
    with monkeypatch.context() as patch:
        patch.setattr(processing,'analyze_material',lambda *args,**kwargs:material_pipeline.analyze_material(*args,**kwargs,
            semantic_call=lambda *_a,**_kw:{'concepts':[],'relations':[],'review_required':False}))
        assert execute().error_code=='NO_USABLE_ADDED_CONTENT'
    before=len(requests)
    monkeypatch.setattr(material_pipeline,'_page_evidence',lambda *_a,**_kw:(_ for _ in ()).throw(AssertionError('Saved Evidence must be reused')))
    start([second],'retry-empty-semantics',old['revision'])
    result=execute()
    assert result.status=='succeeded',result.error_code
    assert len(requests)==before+2


@pytest.mark.parametrize('append',[False,True])
def test_retry_api_reuses_exact_frozen_order_and_ignores_later_uploads(revisions,closed_loop,monkeypatch,append):
    from fastapi.testclient import TestClient
    from pdf_evidence import material_pipeline
    import runtime.api.app as api
    learner,old_material,settings,dsn,_,_,execute,_,old,requests=revisions
    material=old_material if append else create_draft(learner.learner_id,'Initial retry','initial-retry',dsn=dsn)
    def add(name,text):
        identity=upload_source(learner.learner_id,material,pdf(text),name,'application/pdf',name,dsn=dsn)
        assert normalize_next(dsn=dsn)
        return next(s['normalization_id'] for s in read_sources(learner.learner_id,material,dsn=dsn) if s['source_id']==identity)
    b=add('B.pdf','A queue removes the first inserted element first.')
    c=add('C.pdf','A binary tree has left and right child nodes.')
    original=create_revision(learner.learner_id,material,[c,b],'frozen-failed',settings,base_revision=old['revision'] if append else None,dsn=dsn)
    with monkeypatch.context() as patch:
        patch.setattr(material_pipeline,'build_knowledge_structure',lambda *_a,**_kw:(_ for _ in ()).throw(ValueError('KNOWLEDGE_STRUCTURE_INVALID')))
        assert execute().status=='failed'
    add('D.pdf','A later source must not silently join the retry.')
    app=api.create_app(api.ApiSettings(profile='local',public_origin='http://127.0.0.1:4173',secure_cookie=False,local_config=settings,dsn=dsn))
    client=TestClient(app);client.cookies.set('studydy_session',closed_loop[-1])
    route=f'/v2/material-processing-runs/{original.run_id}/retry'
    headers={'Origin':'http://127.0.0.1:4173','Idempotency-Key':'retry-frozen'}
    assert client.post(route).status_code==403
    assert client.post(route,json={'unexpected':True},headers=headers).status_code==400
    saved=client.get(f'/v1/material-processing-runs/{original.run_id}').json()
    assert saved['analysis_saved'] is True
    response=client.post(route,headers=headers)
    assert response.status_code==202,response.text
    assert response.json()['source_names']==list(original.source_names)
    before=len(requests)
    result=execute();assert result.status=='succeeded',result.error_code
    assert len(requests)==before
    # base 的完整 KS 可能已清理，重播仍須由保留的 run／SourceSet metadata 成立。
    replay=client.post(route,headers=headers)
    assert replay.status_code==202 and replay.json()['run_id']==response.json()['run_id']
    document=read_knowledge_structure(learner.learner_id,material,run_id=result.run_id,dsn=dsn).document
    names=[item['original_name'] for item in document['input_binding']['manifest']['items']]
    assert names==(['A.pdf','C.pdf','B.pdf'] if append else ['C.pdf','B.pdf'])


def test_retry_continues_at_failed_block_then_cleans_checkpoints_and_keeps_call_outputs(revisions,monkeypatch):
    from pdf_evidence import material_pipeline
    from runtime.semantic_service import SemanticServiceError
    from runtime.storage.analysis_archive import _material_directory
    from runtime.material_discard import request_material_discard
    import runtime.material_processing as processing
    learner,material,settings,dsn,_,start,execute,_,old,_=revisions
    with pymupdf.open() as doc:
        page=doc.new_page()
        page.insert_text((72,72),'A queue removes the first inserted element first.')
        page.insert_text((72,180),'A binary tree has left and right child nodes.')
        identity=upload_source(learner.learner_id,material,doc.tobytes(),'Two blocks.pdf','application/pdf','two-blocks',dsn=dsn)
    assert normalize_next(dsn=dsn)
    source=next(item for item in read_sources(learner.learner_id,material,dsn=dsn) if item['source_id']==identity)
    monkeypatch.setattr(material_pipeline,'material_request_fits',lambda _client,_lock,request:sum(len(section['evidence']) for section in request['sections'])<=1)
    calls=[];fail_later=True
    def semantic(_client,**kwargs):
        rows=[row for section in kwargs['request']['sections'] for row in section['evidence']]
        assert len(rows)==1
        handle=rows[0][0];calls.append(handle)
        if fail_later and len(calls)>1:raise SemanticServiceError('SEMANTIC_SERVICE_UNAVAILABLE')
        return {'concepts':[{'k':f'item_{handle}','l':f'Item {handle}','a':[],'c':[{'m':None,'s':[handle]}]}],
                'relations':[],'review_required':False}
    monkeypatch.setattr(processing,'analyze_material',lambda *args,**kwargs:material_pipeline.analyze_material(*args,**kwargs,semantic_call=semantic))
    start([source['normalization_id']],'mid-page-failure',old['revision'])
    failed=execute()
    assert failed.status=='failed' and failed.completed_pages==failed.total_pages==2
    completed_handle=calls[0];failed_handle=calls[-1]
    assert completed_handle!=failed_handle
    before=len(calls);fail_later=False
    def no_extraction(*_args,**_kwargs):raise AssertionError('Retry must use saved page Evidence')
    monkeypatch.setattr(material_pipeline,'_page_evidence',no_extraction)
    start([source['normalization_id']],'mid-page-retry',old['revision'])
    result=execute()
    assert result.status=='succeeded',result.error_code
    assert calls[before:]==[failed_handle]
    assert calls.count(completed_handle)==1
    directory=_material_directory(learner.learner_id,material)
    assert not (directory/failed.run_id.hex/'checkpoint.json').exists()
    assert not (directory/result.run_id.hex/'checkpoint.json').exists()
    assert (directory/failed.run_id.hex/'call-000001/decoded.json').is_file()
    # 只在使用者明確刪除整份教材時，連同該教材的分析保存資料清除。
    assert request_material_discard(learner.learner_id,material,dsn=dsn)=='removed'
    assert not directory.exists()


@pytest.mark.parametrize('review_required',[False,True])
def test_empty_update_never_replaces_head_even_with_a_review_flag(revisions,monkeypatch,review_required):
    from pdf_evidence import material_pipeline
    import runtime.material_processing as processing
    learner,material,settings,dsn,add,start,execute,r1,old,_=revisions
    # B02 的部分單 PDF 資料尚無 head pointer；失敗的追加不能清掉其基準圖。
    with database_session(dsn) as session:session.get(Material,material).head_revision=None
    second=add('B.pdf','A new source requiring review.')
    run=start([second],'review',old['revision'])
    def semantics(*_args,**_kwargs):
        return {'concepts':[{'k':'invalid','l':'Unsupported','a':[],'c':[{'m':None,'s':[999999]}]}],
                'relations':[],'review_required':review_required}
    monkeypatch.setattr(processing,'analyze_material',lambda *args,**kw:material_pipeline.analyze_material(*args,**kw,semantic_call=semantics))
    result=execute()
    assert result.status=='failed'
    assert result.error_code=='NO_USABLE_ADDED_CONTENT'
    assert result.output_binding is None
    with database_session(dsn) as session:
        assert session.get(Material,material).head_revision==old['revision']
        assert session.scalar(select(KnowledgeStructure).where(KnowledgeStructure.run_id==run.run_id)) is None
    assert read_knowledge_structure(learner.learner_id,material,revision=old['revision'],dsn=dsn).document==old


def test_model_review_flag_is_advisory_and_keeps_old_claims_and_learning(revisions,monkeypatch):
    from pdf_evidence import material_pipeline
    import runtime.material_processing as processing
    from learning_adaptation.study_sessions import read_study_session
    learner,material,settings,dsn,add,start,execute,_,old,_=revisions
    study=create_study_session(learner,material,old['revision'],'before-review-notice',dsn=dsn)
    second=add('B.pdf','A queue removes the first inserted element first.')
    def semantic(_client,**kwargs):
        handle=kwargs['request']['sections'][0]['evidence'][0][0]
        return {'concepts':[{'k':'queue','l':'Queue','a':[],'c':[{'m':None,'s':[handle]}]}],
                'relations':[],'review_required':True}
    monkeypatch.setattr(processing,'analyze_material',lambda *args,**kwargs:material_pipeline.analyze_material(*args,**kwargs,semantic_call=semantic))
    start([second],'review-advisory',old['revision'])
    result=execute()
    assert result.status=='partial' and result.error_code is None
    document=read_knowledge_structure(learner.learner_id,material,run_id=result.run_id,dsn=dsn).document
    assert document['source_review_required'] is True
    assert document['status']['quality']=='needs_review'
    assert document['status']['reason_codes']==['SOURCE_REVIEW_SUGGESTED']
    assert len(document['concepts'])==2
    with database_session(dsn) as session:assert session.get(Material,material).head_revision==document['revision']
    assert read_knowledge_structure(learner.learner_id,material,revision=old['revision'],dsn=dsn).document==old
    assert read_study_session(learner,study.study_session_id,dsn=dsn)==study


def test_retry_reuses_previously_blocked_review_response_before_requesting_remaining_batches(revisions,monkeypatch):
    from pdf_evidence import material_pipeline
    from runtime.storage.analysis_archive import AnalysisArchive
    import runtime.material_processing as processing
    learner,material,settings,dsn,_,start,execute,_,old,_=revisions
    with pymupdf.open() as document:
        document.new_page().insert_text((72,72),'A queue removes the first inserted element first.')
        document.new_page().insert_text((72,72),'A binary tree has left and right child nodes.')
        document.new_page().insert_text((72,72),'A graph contains vertices connected by edges.')
        source_id=upload_source(learner.learner_id,material,document.tobytes(),'B.pdf','application/pdf','review-replay-source',dsn=dsn)
    assert normalize_next(dsn=dsn)
    source=next(item for item in read_sources(learner.learner_id,material,dsn=dsn) if item['source_id']==source_id)
    monkeypatch.setattr(material_pipeline,'material_request_fits',lambda _client,_lock,request:sum(len(section['evidence']) for section in request['sections'])<=1)
    calls=[]
    def semantic(_client,**kwargs):
        handle=kwargs['request']['sections'][0]['evidence'][0][0];calls.append(handle)
        return {'concepts':[{'k':f'new_{handle}','l':f'Concept {handle}','a':[],'c':[{'m':None,'s':[handle]}]}],
                'relations':[],'review_required':len(calls)==2}
    monkeypatch.setattr(processing,'analyze_material',lambda *args,**kwargs:material_pipeline.analyze_material(*args,**kwargs,semantic_call=semantic))
    original_save=AnalysisArchive.save_response
    def old_veto(archive,index,request,response):
        original_save(archive,index,request,response)
        if response['review_required']:raise material_pipeline.MaterialAnalysisError('SOURCE_UPDATE_NEEDS_REVIEW')
    original=start([source['normalization_id']],'previous-review-veto',old['revision'])
    with monkeypatch.context() as patch:
        patch.setattr(AnalysisArchive,'save_response',old_veto)
        assert execute().error_code=='SOURCE_UPDATE_NEEDS_REVIEW'
    assert len(calls)==2
    completed_handles=list(calls)
    start([source['normalization_id']],'resume-review-veto',old['revision'])
    result=execute()
    assert result.status=='partial' and result.error_code is None
    assert len(calls)==3 and all(calls.count(handle)==1 for handle in completed_handles)
    document=read_knowledge_structure(learner.learner_id,material,run_id=result.run_id,dsn=dsn).document
    assert len(document['concepts'])==4 and document['metrics']['semantic_calls']==3
    assert document['source_review_required'] is True


def test_quality_notices_promote_new_content_and_do_not_block_the_next_append(revisions,monkeypatch):
    from pdf_evidence import material_pipeline
    import runtime.material_processing as processing
    learner,material,settings,dsn,add,start,execute,r1,old,_=revisions
    create_study_session(learner,material,old['revision'],'saved-study',dsn=dsn)
    usual_analysis=processing.analyze_material
    second=add('B.pdf','The queue capacity is 42 elements.')
    start([second],'append-with-notices',old['revision'])
    def semantics(_client,**kwargs):
        handle=kwargs['request']['sections'][0]['evidence'][0][0]
        return {'concepts':[{'k':'queue','l':'Queue','a':[],'c':[
                    {'m':'The queue capacity is 999 elements.','s':[handle]},
                    {'m':None,'s':[999999]},
                ]}],
                'relations':[{'s':'queue','t':'missing','k':'prerequisite','r':'Unsupported edge','e':[handle],'c':1}],
                'review_required':False}
    monkeypatch.setattr(processing,'analyze_material',lambda *args,**kw:material_pipeline.analyze_material(*args,**kw,semantic_call=semantics))
    result=execute()
    assert result.status=='partial',result.error_code
    second_doc=read_knowledge_structure(learner.learner_id,material,run_id=result.run_id,dsn=dsn).document
    assert second_doc['status']['quality']=='needs_review'
    assert set(second_doc['status']['reason_codes'])=={'LITERALS_RESTORED_FROM_SOURCE','CLAIMS_REJECTED','RELATIONS_REJECTED'}
    assert len(second_doc['concepts'])==2
    assert next(c for c in second_doc['concepts'] if c['label']=='Queue')['claims'][0]['text']=='The queue capacity is 42 elements.'
    with database_session(dsn) as session:assert session.get(Material,material).head_revision==second_doc['revision']

    # 第二次回應沒有新增品質問題；舊提示仍保留，但不得卡住再次追加。
    monkeypatch.setattr(processing,'analyze_material',usual_analysis)
    third=add('C.pdf','A tree traversal visits each node in the tree.')
    start([third],'append-after-notices',second_doc['revision'])
    next_result=execute()
    assert next_result.status=='partial',next_result.error_code
    third_doc=read_knowledge_structure(learner.learner_id,material,run_id=next_result.run_id,dsn=dsn).document
    assert third_doc['status']==second_doc['status']
    assert len(third_doc['concepts'])==3 and third_doc['page_count']==3
    with database_session(dsn) as session:
        assert session.get(Material,material).head_revision==third_doc['revision']
        assert session.scalar(select(KnowledgeStructure).where(KnowledgeStructure.structure_revision==second_doc['revision'])) is None
    assert read_knowledge_structure(learner.learner_id,material,revision=old['revision'],dsn=dsn).document==old


def test_racing_append_and_changed_order_never_seal_two_operations(revisions):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    learner,material,settings,dsn,add,start,execute,r1,old,_=revisions
    b=add('B.pdf','Second source content.');c=add('C.pdf','Third source content.')
    barrier=Barrier(2)
    def attempt(key):
        barrier.wait()
        try:return start([b,c],key,old['revision'])
        except SourceError as error:return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,['left','right']))
    assert sum(isinstance(result,str) and result=='REVISION_IN_PROGRESS' for result in results)==1
    winner=next(result for result in results if not isinstance(result,str))
    winning_key='left' if not isinstance(results[0],str) else 'right'
    with pytest.raises(SourceError,match='IDEMPOTENCY_CONFLICT'):start([c,b],winning_key,old['revision'])
    assert start([b,c],winning_key,old['revision']).run_id==winner.run_id


def test_stale_worker_token_cannot_fail_or_publish_another_claim(revisions):
    from dataclasses import replace
    learner,material,settings,dsn,add,start,execute,r1,old,requests=revisions
    second=add('B.pdf','Second source content.');run=start([second],'fencing',old['revision'])
    claim=claim_next_material_processing_run(dsn=dsn)
    before=len(requests)
    result=execute_claimed_material_processing_run(replace(claim,worker_token=uuid4()),settings,dsn=dsn)
    assert result.status=='running' and len(requests)==before
    assert execute_claimed_material_processing_run(claim,settings,dsn=dsn).status=='succeeded'


def test_worker_renews_lease_while_semantic_call_is_waiting(revisions,monkeypatch):
    from datetime import datetime,UTC,timedelta
    from threading import Event,current_thread
    from runtime.storage.tables import MaterialProcessingRun
    import runtime.material_processing as processing
    learner,material,settings,dsn,add,start,execute,_,old,_=revisions
    second=add('B.pdf','A queue removes the first inserted element first.')
    run=start([second],'long-analysis',old['revision'])
    actual_analysis=processing.analyze_material
    actual_check=processing._check_cancellation
    renewed=Event()
    monkeypatch.setattr(processing,'_LEASE_HEARTBEAT_SECONDS',0.05)
    def observe(*args,**kwargs):
        actual_check(*args,**kwargs)
        if current_thread().name=='studydy-material-lease':renewed.set()
    monkeypatch.setattr(processing,'_check_cancellation',observe)
    def waiting_analysis(*args,**kwargs):
        with database_session(dsn) as session:
            session.get(MaterialProcessingRun,run.run_id).lease_expires_at=datetime.now(UTC)+timedelta(seconds=5)
        renewed.clear()
        assert renewed.wait(2),'Worker did not renew its lease during the model call'
        with database_session(dsn) as session:
            assert session.get(MaterialProcessingRun,run.run_id).lease_expires_at>datetime.now(UTC)+timedelta(minutes=5)
        assert processing.recover_interrupted_material_runs(dsn=dsn)==0
        return actual_analysis(*args,**kwargs)
    monkeypatch.setattr(processing,'analyze_material',waiting_analysis)
    result=execute()
    assert result.status=='succeeded',result.error_code


def test_existing_single_pdf_can_append_without_rewriting_its_structure(revisions,closed_loop):
    learner,source,settings,old,dsn,_=closed_loop
    from runtime.source_revisions import create_revision
    from runtime.storage.source_artifacts import open_verified_artifact
    original=deepcopy(old)
    create_study_session(learner,source.material_id,old['revision'],'existing-pdf-study',dsn=dsn)
    identity=upload_source(learner.learner_id,source.material_id,pdf('New topic content.'),'B.pdf','application/pdf','legacy-addition',dsn=dsn)
    normalize_next(dsn=dsn)
    normalization=next(item['normalization_id'] for item in read_sources(learner.learner_id,source.material_id,dsn=dsn) if item['source_id']==identity)
    create_revision(learner.learner_id,source.material_id,[normalization],'existing-pdf-update',settings,base_revision=old['revision'],dsn=dsn)
    claim=claim_next_material_processing_run(dsn=dsn)
    result=execute_claimed_material_processing_run(claim,settings,dsn=dsn)
    assert result.status=='succeeded',result.error_code
    assert read_knowledge_structure(learner.learner_id,source.material_id,revision=old['revision'],dsn=dsn).document==original


def test_staged_source_removal_is_owned_and_keeps_published_sources(revisions):
    from runtime.source_normalization import remove_staged_source
    from runtime.storage.source_artifacts import open_verified_artifact
    from runtime.storage.artifacts import ArtifactError
    learner,material,settings,dsn,add,start,execute,r1,old,_=revisions
    addition=add('staged.pdf','Unsealed staging content.')
    sources=read_sources(learner.learner_id,material,dsn=dsn)
    staged=next(source for source in sources if source['normalization_id']==addition)
    published=next(source for source in sources if source['included'])
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):
        remove_staged_source(uuid4(),material,staged['source_id'],dsn=dsn)
    with pytest.raises(SourceError,match='SOURCE_IN_USE'):
        remove_staged_source(learner.learner_id,material,published['source_id'],dsn=dsn)
    remove_staged_source(learner.learner_id,material,staged['source_id'],dsn=dsn)
    remove_staged_source(learner.learner_id,material,staged['source_id'],dsn=dsn)
    with pytest.raises(ArtifactError,match='ARTIFACT_NOT_AVAILABLE'):
        with open_verified_artifact(learner.learner_id,staged['original_artifact_id'],dsn=dsn):pass
    assert len(read_sources(learner.learner_id,material,dsn=dsn))==1
    assert read_knowledge_structure(learner.learner_id,material,revision=old['revision'],dsn=dsn).document==old
