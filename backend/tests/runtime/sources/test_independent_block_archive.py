"""隔離 DB 驗證 v1 進度升級與已完成子圖重用。"""
import json
from copy import deepcopy
from pathlib import Path

from pdf_evidence import material_pipeline
from runtime import material_processing as processing
from runtime.local_ai_config import _runtime_lock
from runtime.semantic_service import SemanticServiceError
from runtime.storage.analysis_archive import AnalysisArchive,_material_directory
from runtime.storage.tables import MaterialProcessingRun
from runtime.storage.database import database_session
from backend.tests.runtime.sources.test_source_revisions import revisions
from product_fixtures import closed_loop


def fake_response(kwargs,legacy=False):
    h=kwargs['request']['sections'][0]['evidence'][0][0]
    source=kwargs['request']['sections'][0]['evidence'][0][3]
    meaning={'A queue removes the first inserted item.':'佇列取出最早加入的資料。',
             'A tree connects parent and child nodes.':'樹連接父節點與子節點。'}[source]
    concept={'l':f'概念 {h}','a':[],'c':[{'m':meaning,'s':[h]}]}
    if legacy:concept['k']=f'old_{h}'
    value={'concepts':[concept],'relations':[]}
    if legacy and 'update_policy' in kwargs['request']:value['review_required']=False
    return value


def analyze_with(monkeypatch,call):
    actual=material_pipeline.analyze_material
    monkeypatch.setattr(processing,'analyze_material',lambda *a,**kw:actual(*a,**kw,semantic_call=call))
    monkeypatch.setattr(material_pipeline,'material_request_fits',lambda _c,_l,r,**kw:sum(len(s['evidence']) for s in r['sections'])<=1)


def test_partial_v1_run_upgrades_and_calls_only_remaining_sources(revisions,monkeypatch):
    learner,material,settings,dsn,add,start,execute,_,old,_=revisions
    b=add('B.pdf','A queue removes the first inserted item.');c=add('C.pdf','A tree connects parent and child nodes.')
    count=0
    def legacy(client,**kwargs):
        nonlocal count
        count+=1
        if count>1:raise SemanticServiceError('SEMANTIC_SERVICE_UNAVAILABLE')
        return fake_response(kwargs,legacy=True)
    analyze_with(monkeypatch,legacy)
    # 暫時性失敗先自動接續一次，第二次失敗才停下。
    old_run=start([b,c],'legacy-partial',old['revision']);assert execute().status=='pending';assert execute().status=='failed'
    path=_material_directory(learner.learner_id,material)/old_run.run_id.hex/'checkpoint.json'
    raw=path.read_bytes();saved=json.loads(raw)['data'];assert saved['cursor']>0
    old_lock=deepcopy(old_run.runtime_lock_document)
    settings['runtime_lock']['material_semantics']=json.loads(
        (Path(__file__).parents[2]/'fixtures/independent_material_semantics.json').read_text())
    seen=[]
    def new(client,**kwargs):
        seen.extend(e[0] for s in kwargs['request']['sections'] for e in s['evidence'])
        assert 'existing_concepts' not in kwargs['request']
        return fake_response(kwargs)
    analyze_with(monkeypatch,new)
    monkeypatch.setattr(material_pipeline,'collect_source_set',lambda *_a,**_kw:(_ for _ in ()).throw(AssertionError('Evidence must not rerun')))
    resumed=start([b,c],'upgrade-resume',old['revision']);assert path.read_bytes()==raw
    result=execute();assert result.status in ('succeeded','partial'),result.error_code
    assert seen and min(seen)>=saved['cursor']
    with database_session(dsn) as db:assert db.get(MaterialProcessingRun,old_run.run_id).runtime_lock_document==old_lock
    completed=_material_directory(learner.learner_id,material)/resumed.run_id.hex/'completion.json'
    assert json.loads(completed.read_text())['reused_from_run']==str(old_run.run_id)


def test_validated_block_survives_checkpoint_failure_and_is_not_inferred_again(revisions,monkeypatch):
    learner,material,settings,dsn,add,start,execute,_,old,_=revisions
    b=add('B.pdf','A queue removes the first inserted item.')
    settings['runtime_lock']['material_semantics']=deepcopy(_runtime_lock()['material_semantics'])
    calls=[]
    def model(client,**kwargs):calls.append(kwargs['request']);return fake_response(kwargs)
    analyze_with(monkeypatch,model)
    before=start([b],'block-saved-checkpoint-failed',old['revision'])
    original=AnalysisArchive.save_checkpoint
    def fail_after_merge(self,value):
        if value['cursor']>0:
            from runtime.storage.analysis_archive import AnalysisArchiveError
            raise AnalysisArchiveError('ANALYSIS_ARTIFACT_WRITE_FAILED')
        return original(self,value)
    with monkeypatch.context() as patch:
        patch.setattr(AnalysisArchive,'save_checkpoint',fail_after_merge)
        assert execute().status=='failed'
    assert len(calls)==1
    directory=_material_directory(learner.learner_id,material)/before.run_id.hex
    assert len(list((directory/'blocks').glob('*/complete.json')))==1
    old_checkpoint=(directory/'checkpoint.json').read_bytes()
    def forbidden(*a,**kw):raise AssertionError('completed inference repeated')
    analyze_with(monkeypatch,forbidden)
    after=start([b],'reuse-completed-block',old['revision']);assert (directory/'checkpoint.json').read_bytes()==old_checkpoint
    result=execute();assert result.status in ('succeeded','partial'),result.error_code
    assert len(calls)==1


def test_cancelled_worker_cannot_save_late_parallel_result(revisions):
    import pytest
    from runtime.storage.analysis_archive import AnalysisArchiveError
    learner,material,settings,dsn,add,start,execute,_,old,_=revisions
    b=add('B.pdf','A queue removes the first inserted item.')
    run=start([b],'cancel-before-block-write',old['revision'])
    claim=processing.claim_next_material_processing_run(dsn=dsn)
    archive=AnalysisArchive(claim,dsn=dsn)
    processing.request_material_processing_cancellation(learner.learner_id,run.run_id,update_only=True,dsn=dsn)
    with pytest.raises(AnalysisArchiveError,match='MATERIAL_RUN_UNAVAILABLE'):
        archive.complete_block('a'*64,'a'*64,1,{'concepts':[],'relations':[]})
    assert not (archive.directory/'blocks'/('a'*64)/'complete.json').exists()
