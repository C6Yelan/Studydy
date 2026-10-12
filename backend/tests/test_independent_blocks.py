"""正式八路子圖、乱序保存及舊進度接續，不呼叫模型。"""
from copy import deepcopy
from dataclasses import asdict
from threading import Barrier, Event, Lock
import time

import pytest

from knowledge_map.structure import build_document_context, SemanticState
from knowledge_map.independent_blocks import block_request, block_schema, project_block, merge_block
from pdf_evidence.semantic_blocks import analyze_blocks
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.material_pipeline import validate_runtime_lock
from runtime.local_app import _runtime_lock
from runtime.luna_test import test_lock as luna_lock
from runtime.material_runtime import runtime_binding, same_material_runtime
from runtime.semantic_service import SemanticServiceError
from test_knowledge_structure_v1 import _page, _block
from test_material_pipeline_v1 import _settings


class Archive:
    def __init__(self):
        self.responses={};self.raw={};self.requests={}
    def load_block_response(self, identity, signature):
        saved=self.responses.get(identity)
        if saved:assert saved['request_sha256']==signature
        return deepcopy(saved)
    def prepare_block_call(self, identity, number, request):self.requests[(identity,number)]=deepcopy(request)
    def save_block_response(self, identity, number, response):self.raw[(identity,number)]=deepcopy(response)
    def complete_block(self, identity, signature, number, response):
        self.responses[identity]={'request_sha256':signature,'call_number':number,'response':deepcopy(response)}


def context(count=8):
    return build_document_context([_page(1,[_block(1,i,'paragraph',f'Fact number {i}.') for i in range(count)])],page_count=1)


def response(kwargs):
    handle=kwargs['request']['sections'][0]['evidence'][0][0]
    return {'concepts':[{'l':f'Concept {handle}','a':[],'c':[{'m':None,'s':[handle]}]}],'relations':[]}


def run(ctx,state,progress,archive,call,check=lambda:None,snapshots=None):
    snapshots=[] if snapshots is None else snapshots
    def save():snapshots.append({'state':deepcopy(asdict(state)),'progress':deepcopy(progress)})
    return analyze_blocks(ctx,state,progress,lock=_runtime_lock(),
        fits=lambda r:sum(len(s['evidence']) for s in r['sections'])<=1,
        semantic_call=call,archive=archive,checkpoint=save,report=lambda *_:None,check_cancel=check)


def test_eight_calls_are_parallel_and_global_state_has_no_key_collisions():
    gate=Barrier(8);mutex=Lock();active=0;peak=0
    def call(client,**kwargs):
        nonlocal active,peak
        with mutex:active+=1;peak=max(peak,active)
        gate.wait(timeout=10)
        result=response(kwargs)
        if result['concepts'][0]['l']=='Concept 0':time.sleep(.04)
        with mutex:active-=1
        return result
    state=SemanticState();progress={'cursor':0,'semantic_calls':0,'ranges':[]};archive=Archive();snapshots=[]
    assert run(context(),state,progress,archive,call,snapshots=snapshots)
    assert peak==8 and active==0 and progress=={'cursor':8,'semantic_calls':8,'ranges':[]}
    assert len(state.concepts)==len(archive.responses)==8
    assert any(s['progress']['ranges'] for s in snapshots)


def test_failed_gap_resume_does_not_repeat_completed_out_of_order_blocks():
    archive=Archive();state=SemanticState();progress={'cursor':0,'semantic_calls':0,'ranges':[]};gate=Barrier(8)
    first_attempt=set();mutex=Lock()
    def call(client,**kwargs):
        handle=kwargs['request']['sections'][0]['evidence'][0][0]
        with mutex:new=handle not in first_attempt;first_attempt.add(handle)
        if new:gate.wait(timeout=10)
        if handle==0:raise SemanticServiceError('SEMANTIC_RESPONSE_INVALID')
        return response(kwargs)
    with pytest.raises(SemanticServiceError):run(context(),state,progress,archive,call)
    assert progress['cursor']==0 and len(progress['ranges'])==7 and len(state.concepts)==7
    before=deepcopy(state);calls=[]
    def resumed(client,**kwargs):calls.append(kwargs['request']['sections'][0]['evidence'][0][0]);return response(kwargs)
    assert run(context(),state,progress,archive,resumed)
    assert calls==[0] and progress['cursor']==8 and progress['ranges']==[] and len(state.concepts)==8
    assert all(state.concepts[k]==v for k,v in before.concepts.items())


def test_saved_response_after_last_checkpoint_is_replayed_without_inference():
    archive=Archive();original=SemanticState();progress={'cursor':0,'semantic_calls':0,'ranges':[]}
    run(context(3),original,progress,archive,lambda client,**kwargs:response(kwargs))
    def forbidden(*_,**__):raise AssertionError('completed block must not call model again')
    restored=SemanticState();old_progress={'cursor':0,'semantic_calls':0,'ranges':[]}
    run(context(3),restored,old_progress,archive,forbidden)
    assert restored==original and old_progress['cursor']==3 and old_progress['semantic_calls']==3


def test_cancellation_drains_all_eight_inflight_calls():
    stop=Event();gate=Barrier(8);mutex=Lock();active=0
    class Cancelled(RuntimeError):pass
    def check():
        if stop.is_set():raise Cancelled()
    def call(client,**kwargs):
        nonlocal active
        with mutex:active+=1
        try:
            gate.wait(timeout=10);stop.set()
            kwargs['cancellation_check']()
            raise AssertionError('cancellation ignored')
        finally:
            with mutex:active-=1
    with pytest.raises(Cancelled):run(context(),SemanticState(),{'cursor':0,'semantic_calls':0,'ranges':[]},Archive(),call,check)
    assert active==0


def test_legacy_state_and_cursor_are_preserved_while_only_remaining_blocks_run():
    ctx=context(10);state=SemanticState(concepts={'saved':{'label':'Saved','aliases':[],'claims':[]}})
    old=deepcopy(state);calls=[]
    def call(client,**kwargs):calls.append(kwargs['request']['sections'][0]['evidence'][0][0]);return response(kwargs)
    p={'cursor':7,'semantic_calls':251,'ranges':[]}
    run(ctx,state,p,Archive(),call)
    assert sorted(calls)==[7,8,9] and state.concepts['saved']==old.concepts['saved']
    assert p['cursor']==10 and p['semantic_calls']==254


def test_local_schema_and_projection_keep_source_and_endpoint_boundaries():
    ctx=context(2);bundle={'sections':[{**ctx['sections'][0],'evidence_ids':[ctx['evidence'][0]['evidence_id']]}],'evidence':ctx['evidence'][:1],'catalog_keys':[]}
    schema=block_schema([0]);assert 'k' not in schema['properties']['concepts']['items']['properties']
    assert schema['properties']['relations']['items']['properties']['e']['items']['enum']==[0]
    result={'concepts':[{'l':'概念','a':[],'c':[{'m':None,'s':[0]},{'m':None,'s':[1]}]}],
            'relations':[{'s':0,'t':99,'k':'contrast','r':'Unknown endpoint','e':[0],'c':.9}]}
    a=project_block(ctx,bundle,'first',result);b=project_block(ctx,bundle,'second',result)
    assert a.rejected_claims==a.rejected_relations==1 and not set(a.concepts)&set(b.concepts)
    merge_block(a,b)
    assert len(a.concepts)==2
    with pytest.raises(ValueError,match='ANALYSIS_CHECKPOINT_INVALID'):merge_block(a,b)


def test_only_approved_forward_policy_upgrade_preserves_runtime(tmp_path):
    old=_settings(tmp_path);new=deepcopy(old);new['runtime_lock']=_runtime_lock()
    old_before=deepcopy(old)
    validate_runtime_lock(old['runtime_lock']);validate_runtime_lock(new['runtime_lock'])
    assert same_material_runtime(old['runtime_lock'],runtime_binding(old),new['runtime_lock'],runtime_binding(new))
    assert not same_material_runtime(new['runtime_lock'],runtime_binding(new),old['runtime_lock'],runtime_binding(old))
    changed=deepcopy(new);changed['runtime_lock']['material_semantics']['prompt']+=' Different meaning.'
    assert not same_material_runtime(old['runtime_lock'],runtime_binding(old),changed['runtime_lock'],runtime_binding(changed))
    changed=deepcopy(new);changed['runtime_lock']['material_semantics']['max_concurrency']=4
    assert same_material_runtime(new['runtime_lock'],runtime_binding(new),changed['runtime_lock'],runtime_binding(changed))
    assert old==old_before


@pytest.mark.parametrize('length,expected',[(4096,True),(4097,False)])
def test_independent_budget_counts_source_tokens_not_json_chars(monkeypatch,length,expected):
    from runtime import semantic_service as service
    monkeypatch.setattr(service,'_luna_input_tokens',lambda prompt,r:900+sum(len(e[3]) for s in r['sections'] for e in s['evidence']))
    request={'sections':[{'evidence':[[0,1,'paragraph','a'*(length-1)],[1,1,'paragraph','b']]}]}
    assert service.material_request_fits(None,luna_lock(_runtime_lock()),request) is expected


def test_unprocessed_oversized_block_is_reported_only_after_prior_jobs_are_saved():
    ctx=context(3);archive=Archive();state=SemanticState();p={'cursor':0,'semantic_calls':0,'ranges':[]}
    def fits(r):
        rows=[e for s in r['sections'] for e in s['evidence']]
        return len(rows)==1 and rows[0][0]<2
    with pytest.raises(ValueError,match='SEMANTIC_INPUT_TOO_LARGE'):
        analyze_blocks(ctx,state,p,lock=_runtime_lock(),fits=fits,semantic_call=lambda client,**kwargs:response(kwargs),archive=archive,checkpoint=lambda:None,report=lambda *_:None,check_cancel=lambda:None)
    assert p['cursor']==2 and p['ranges']==[] and len(archive.responses)==2


def test_pipeline_restores_old_checkpoint_without_extracting_completed_pdf(tmp_path,monkeypatch):
    from pdf_evidence import material_pipeline as pipeline
    from test_material_pipeline_v1 import _pdf,_request,Client
    from knowledge_map.semantic_projection import apply_semantic_response
    ctx=context(10);state=SemanticState()
    bundle={'sections':[{**ctx['sections'][0],'evidence_ids':[ctx['evidence'][0]['evidence_id']]}],'evidence':ctx['evidence'][:1]}
    apply_semantic_response({'concepts':[{'k':'old','l':'Old','a':[],'c':[{'m':None,'s':[0]}]}],'relations':[]},context=ctx,bundle=bundle,state=state)
    saved={'context':ctx,'state':asdict(state),'cursor':7,'semantic_calls':251,'ocr_calls':4,'complete':False,'source_sha256':'1'*64,'evidence_duration_ms':1,'semantic_duration_ms':2}
    class Checkpoint(Archive):
        def load_checkpoint(self):return deepcopy(saved)
        def save_checkpoint(self,value):self.saved=deepcopy(value)
    archive=Checkpoint();source=tmp_path/'source.pdf';_pdf(source,1);settings=_settings(tmp_path);settings['runtime_lock']=_runtime_lock()
    monkeypatch.setattr(pipeline,'collect_source_set',lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('must reuse saved Evidence')))
    calls=[]
    def call(client,**kwargs):
        assert 'existing_concepts' not in kwargs['request']
        calls.extend(e[0] for s in kwargs['request']['sections'] for e in s['evidence'])
        return response(kwargs)
    result=pipeline.analyze_material([_request(source)],{'source_set_digest':'1'*64},settings,client=Client(),analysis_archive=archive,semantic_call=call)
    assert calls==[7,8,9]
    assert archive.saved['state']['concepts']['old']==saved['state']['concepts']['old']
    assert archive.saved['cursor']==10 and archive.saved['complete']
    assert result['metrics']['ocr_calls']==4 and result['metrics']['semantic_calls']==252
