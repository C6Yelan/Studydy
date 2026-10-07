from copy import deepcopy
import pytest
from runtime.luna_test import test_lock as luna_lock
from runtime.local_app import _runtime_lock,read_local_ai_config_from_environment
from runtime.material_runtime import runtime_binding,runtime_binding_is_valid,lock_matches_binding
from runtime.semantic_service import request_semantics,preflight_semantic_service,material_request_fits
from pdf_evidence.material_pipeline import validate_runtime_lock
from runtime import voice


def test_new_luna_runs_record_actual_identity_without_mutating_gemma_lock():
    original=_runtime_lock();before=deepcopy(original)
    lock=luna_lock(original)
    assert validate_runtime_lock(lock,assessment=False)
    config=read_local_ai_config_from_environment({'STUDYDY_TEXT_TEST_PROVIDER':'codex-exec-luna'})
    binding=runtime_binding(config)
    assert binding['model_id']=='gpt-5.6-luna'
    assert binding['semantic_service']['server']['package']=='codex-exec'
    assert runtime_binding_is_valid(binding) and lock_matches_binding(lock,binding)
    assert original==before and original['semantic_service']['model_id']!='gpt-5.6-luna'


def test_luna_semantics_and_preflight_never_call_gemma(monkeypatch):
    lock=luna_lock(_runtime_lock());calls=[]
    def provider(path,body):
        calls.append((path,body))
        return {'model':'gpt-5.6-luna'} if path=='/luna-health' else {'concepts':[],'relations':[]}
    monkeypatch.setattr(voice,'provider',provider)
    class NoGemma:
        def __getattr__(self,name):raise AssertionError('Gemma must not be called')
    preflight_semantic_service(lock,client=NoGemma())
    assert request_semantics(NoGemma(),runtime_lock=lock,task='material_semantics',request={'synthetic':True},response_schema={'type':'object'})=={'concepts':[],'relations':[]}
    assert material_request_fits(NoGemma(),lock,{'sections':[{'evidence':[[1,1,'paragraph','synthetic']]}]})
    assert [c[0] for c in calls]==['/luna-health','/semantics']


def test_luna_capacity_increase_reuses_verified_old_work_without_changing_hash():
    from runtime.material_runtime import same_material_runtime
    config=read_local_ai_config_from_environment({'STUDYDY_TEXT_TEST_PROVIDER':'codex-exec-luna'})
    old=deepcopy(config);old['runtime_lock']['semantic_service']['max_model_len']=32768
    before=deepcopy(old)
    assert same_material_runtime(old['runtime_lock'],runtime_binding(old),config['runtime_lock'],runtime_binding(config))
    assert not same_material_runtime(config['runtime_lock'],runtime_binding(config),old['runtime_lock'],runtime_binding(old))
    assert old==before
    assert config['runtime_lock']['semantic_service']['max_model_len']==65536


def test_luna_large_catalog_fits_without_truncating_existing_concepts():
    lock=luna_lock(_runtime_lock())
    request={'sections':[{'evidence':[[1,1,'paragraph','A queue uses FIFO.']]}],
             'existing_concepts':[{'label':'synthetic concept','claims':['Preserve this previously supported claim.']*8} for _ in range(500)]}
    before=deepcopy(request)
    assert len(str(request))>100000
    assert material_request_fits(None,lock,request)
    assert request==before


def test_luna_input_limit_uses_actual_provider_serialization_and_retains_output_room(monkeypatch):
    import json
    import tiktoken
    lock=luna_lock(_runtime_lock())
    request={'sections':[{'evidence':[[1,1,'paragraph','完整來源文字']]}],'existing_concepts':[{'k':'a','c':['既有內容']}]}
    original=deepcopy(request);seen=[];count=57343
    class Encoding:
        def encode(self,text,**kwargs):seen.append(text);return range(count)
    monkeypatch.setattr(tiktoken,'get_encoding',lambda name:Encoding())
    assert material_request_fits(None,lock,request)
    assert seen[-1]==lock['material_semantics']['prompt']+'\nINPUT:\n'+json.dumps(request,ensure_ascii=False)
    count=57344;assert material_request_fits(None,lock,request)
    count=57345;assert not material_request_fits(None,lock,request)
    lock['semantic_service']['max_model_len']=272000
    count=57344
    assert material_request_fits(None,lock,request)
    count+=1;assert not material_request_fits(None,lock,request)
    assert request==original


def test_luna_capacity_increase_does_not_expand_new_evidence_batch():
    lock=luna_lock(_runtime_lock())
    request={'sections':[{'evidence':[[1,1,'paragraph','x '*4000],[2,1,'paragraph','y '*4000]]}], 'existing_concepts':[]}
    assert not material_request_fits(None,lock,request)
    request['sections'][0]['evidence']=request['sections'][0]['evidence'][:1]
    assert material_request_fits(None,lock,request)


@pytest.mark.parametrize('saved_context', [65536, 272000])
@pytest.mark.parametrize('task,reserve', [
    ('material_semantics', 8192), ('material_review', 8192),
    ('assessment', 16384), ('assessment_check', 16384),
])
def test_luna_all_semantic_tasks_block_before_provider_at_shared_64k_limit(monkeypatch, saved_context, task, reserve):
    from runtime import semantic_service as service
    lock = luna_lock(_runtime_lock())
    lock['semantic_service']['max_model_len'] = saved_context
    count = 65536 - reserve
    monkeypatch.setattr(service, '_luna_input_tokens', lambda *_: count)
    calls = []
    monkeypatch.setattr(voice, 'provider', lambda *args: calls.append(args) or {'ok': True})
    arguments = dict(runtime_lock=lock, task=task, request={}, response_schema={})
    assert request_semantics(None, **arguments) == {'ok': True}
    count += 1
    with pytest.raises(service.SemanticServiceError, match='SEMANTIC_INPUT_TOO_LARGE'):
        request_semantics(None, **arguments)
    assert len(calls) == 1


@pytest.mark.parametrize('output_tokens,accepted', [(8192, True), (8193, False)])
def test_luna_rejects_returned_output_over_remaining_budget(monkeypatch, output_tokens, accepted):
    from runtime import semantic_service as service
    lock = luna_lock(_runtime_lock())
    monkeypatch.setattr(service, '_luna_input_tokens', lambda *_: 57344)
    monkeypatch.setattr(service, '_luna_token_count', lambda *_: output_tokens)
    monkeypatch.setattr(voice, 'provider', lambda *_: {'ok': True})
    arguments = dict(runtime_lock=lock, task='material_semantics', request={}, response_schema={})
    if accepted:
        assert request_semantics(None, **arguments) == {'ok': True}
    else:
        with pytest.raises(service.SemanticServiceError, match='SEMANTIC_OUTPUT_TOO_LARGE'):
            request_semantics(None, **arguments)


def test_old_luna_272k_checkpoint_identity_can_resume_under_64k_policy():
    from runtime.material_runtime import same_material_runtime
    current = read_local_ai_config_from_environment({'STUDYDY_TEXT_TEST_PROVIDER': 'codex-exec-luna'})
    old = deepcopy(current)
    old['runtime_lock']['semantic_service']['max_model_len'] = 272000
    before = deepcopy(old)
    assert runtime_binding_is_valid(runtime_binding(old))
    assert same_material_runtime(old['runtime_lock'], runtime_binding(old), current['runtime_lock'], runtime_binding(current))
    assert old == before
