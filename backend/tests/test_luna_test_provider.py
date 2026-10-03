from copy import deepcopy
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
    assert config['runtime_lock']['semantic_service']['max_model_len']==272000


def test_luna_large_catalog_fits_without_truncating_existing_concepts():
    lock=luna_lock(_runtime_lock())
    request={'sections':[{'evidence':[[1,1,'paragraph','A queue uses FIFO.']]}],
             'existing_concepts':[{'label':'synthetic concept','claims':['Preserve this previously supported claim.']*8} for _ in range(500)]}
    before=deepcopy(request)
    assert len(str(request))>100000
    assert material_request_fits(None,lock,request)
    assert request==before
