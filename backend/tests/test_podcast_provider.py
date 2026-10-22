"""單次講稿生成與合法來源綁定；無真實模型呼叫。"""
import importlib.util
from pathlib import Path
from copy import deepcopy
import pytest

spec=importlib.util.spec_from_file_location('podcast_provider',Path(__file__).resolve().parents[2]/'ops/podcast/provider.py')
provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)


def body():
    return {'delivery':'solo','claims':[{'concept_id':'c0','claim_id':'repeated','label':'非空檢查',
        'text':'空堆疊不可 pop。','evidence':[{'evidence_id':'e0','page_ref':'p0','quote':'空堆疊不可 pop。'}]}]}


def candidate(text='空堆疊不可 pop。', sources=None):
    return {'segments':[{'title':'非空檢查','turns':[{'speaker':'host','text':text,'source_indices':[0] if sources is None else sources}]}]}


def test_script_uses_one_model_call_without_review_or_style_rejection(monkeypatch):
    request=body();request['claims'].append(deepcopy(request['claims'][0]))
    value=candidate('必要條件保留。'*80, [0,0]);calls=[]
    def model(prompt,schema,**kwargs):calls.append(schema);return value
    monkeypatch.setattr(provider,'luna',model)
    result=provider.script(request)
    assert len(calls)==1 and 'review' not in result
    turn=result['segments'][0]['turns'][0]
    assert turn['text']==value['segments'][0]['turns'][0]['text']
    assert turn['parts'][0]['source_refs']==[{'source_index':0,'evidence_ids':['e0']}]


@pytest.mark.parametrize('index',[False,-1,99,'0'])
def test_script_rejects_foreign_or_invalid_references_without_retry(monkeypatch,index):
    calls=[]
    def model(*args,**kwargs):calls.append(args);return candidate(sources=[index])
    monkeypatch.setattr(provider,'luna',model)
    with pytest.raises(RuntimeError,match='PODCAST_SCRIPT_INVALID'):provider.script(body())
    assert len(calls)==1


def test_empty_script_is_not_published(monkeypatch):
    monkeypatch.setattr(provider,'luna',lambda *_,**kwargs:{'segments':[]})
    with pytest.raises(RuntimeError,match='PODCAST_SCRIPT_INVALID'):provider.script(body())


def test_same_claim_ids_keep_distinct_source_positions(monkeypatch):
    request=body();request['claims'].append({**deepcopy(request['claims'][0]),'concept_id':'c1','evidence':[{'evidence_id':'e1'}]})
    monkeypatch.setattr(provider,'luna',lambda *_,**kwargs:candidate(sources=[0,1]))
    refs=provider.script(request)['segments'][0]['turns'][0]['parts'][0]['source_refs']
    assert refs==[{'source_index':0,'evidence_ids':['e0']},{'source_index':1,'evidence_ids':['e1']}]


def test_source_free_questions_and_consecutive_speakers_are_allowed(monkeypatch):
    value=candidate();value['segments'][0]['turns'].insert(0,{'speaker':'host','text':'為什麼？','source_indices':[]})
    monkeypatch.setattr(provider,'luna',lambda *_,**kwargs:value)
    result=provider.script({**body(),'delivery':'dialogue'})
    assert [t['speaker'] for t in result['segments'][0]['turns']]==['host','host']
    assert result['segments'][0]['turns'][0]['parts'][0]['source_refs']==[]


def test_model_schema_only_exposes_current_source_indices():
    schema=provider.script_schema(body()['claims'])
    turn=schema['properties']['segments']['items']['properties']['turns']['items']
    assert set(turn['properties'])=={'speaker','text','source_indices'}
    assert turn['properties']['source_indices']['items']['enum']==[0]


def test_source_capacity_stays_aligned_with_episode_planner(monkeypatch):
    from runtime.podcast_script import MAX_EPISODE_CLAIMS
    request=body();request['claims']*=MAX_EPISODE_CLAIMS
    monkeypatch.setattr(provider,'luna',lambda *_,**kwargs:candidate(sources=list(range(MAX_EPISODE_CLAIMS))))
    assert len(provider.script(request)['segments'][0]['turns'][0]['parts'][0]['source_refs'])==MAX_EPISODE_CLAIMS
    request['claims'].append(deepcopy(request['claims'][0]))
    with pytest.raises(ValueError,match='REQUEST_INVALID'):provider.script(request)

@pytest.mark.parametrize('purpose', ['podcast'])
def test_audio_failure_never_returns_partial_file_or_falls_back(monkeypatch, purpose):
    from types import SimpleNamespace
    monkeypatch.setenv('STUDYDY_PODCAST_TTS_PYTHON','/isolated/python')
    def failed(args,**kwargs):
        Path(args[-1]).write_bytes(b'RIFFpartial')
        assert kwargs['timeout'] == None
        assert 'STUDYDY_PODCAST_PROVIDER_TOKEN' not in kwargs['env']
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(provider.podcast_process,'run',failed)
    with pytest.raises(RuntimeError,match='PODCAST_AUDIO_INVALID'):provider.audio({'script':candidate(), 'purpose':purpose})


def test_luna_timeout_has_fixed_reason_and_task_specific_deadline(monkeypatch):
    def expired(args,**kwargs):
        assert kwargs['timeout']==300 and kwargs['input']=='synthetic prompt'
        raise provider.subprocess.TimeoutExpired('codex',300)
    monkeypatch.setattr(provider.podcast_process,'run',expired)
    with pytest.raises(RuntimeError,match='^LUNA_GENERATION_TIMEOUT$'):
        provider.luna('synthetic prompt',{'type':'object'},timeout=300)










def test_context_does_not_guess_a_header_from_another_column_or_distant_page_text():
    request=body()
    pages={'p0':{'blocks':[{'evidence_id':'e0','text':'16 bits','region':[100,200,150,220]},
        {'evidence_id':'wrong-column','text':'別欄','region':[250,170,350,195]},
        {'evidence_id':'distant','text':'遠處段落','region':[90,20,180,40]}]},
        'foreign':{'blocks':[{'evidence_id':'e0','text':'其他頁'}]}}
    result=provider.script_context(request['claims'],pages)
    assert list(result)==['p0']
    assert [b['evidence_id'] for b in result['p0']['blocks']]==['e0']


def test_retrieved_original_is_not_repeated_in_claim_and_quote(monkeypatch):
    request = body()
    request['source_context'] = {'p0': {'blocks': [{'evidence_id': 'e0', 'text': '空堆疊不可 pop。'}]}}
    prompts = []
    def model(prompt, schema, **kwargs):
        prompts.append(prompt)
        return candidate()
    monkeypatch.setattr(provider, 'luna', model)
    provider.script(request)
    assert prompts[0].count('空堆疊不可 pop。') == 1
    assert '"block_indices": [0]' in prompts[0]
    assert 'e0' not in prompts[0]


def test_job_diagnostics_exclude_private_input_and_exception_text(caplog):
    import json, logging, time
    from uuid import uuid4
    caplog.set_level(logging.INFO, logger='studydy.podcast.jobs')
    body={'job_id':str(uuid4()),'job_token':'do-not-log-token','episode_index':3,'script':'do-not-log-material'}
    provider.record_job('/audio',body,time.monotonic(),ValueError('do-not-log-exception'))
    message=caplog.records[-1].getMessage();value=json.loads(message)
    assert value['job_id']==body['job_id'] and value['episode_index']==3
    assert value['result']=='PODCAST_PROVIDER_FAILED' and value['error_type']=='ValueError'
    assert 'do-not-log' not in message


@pytest.mark.parametrize('metadata',[None,{'policy':'podcast-mastering/v1','integrated_lufs':-19,'true_peak_dbtp':-2,'loudness_range_lu':5}])
def test_voice_and_podcast_responses_use_the_same_dynamic_pause_identity(metadata):
    from io import BytesIO
    handler=object.__new__(provider.Handler)
    handler.path='/audio';handler.wfile=BytesIO()
    headers={}
    handler.send_response=lambda status: None
    handler.send_header=lambda key,value: headers.update({key:value})
    handler.end_headers=lambda: None
    handler.respond(b'RIFF-test','audio/wav',metadata)
    assert 'dynamic-pauses/v2' in headers['X-Studydy-Audio-Provider']
    assert 'spoken-input/v12' not in headers['X-Studydy-Audio-Provider']
    assert handler.wfile.getvalue()==b'RIFF-test'


def test_formula_keeps_nearby_variable_definition_without_using_a_foreign_or_distant_one():
    request=body();request['claims'][0].update(text='估計值 p < t 時升級。',evidence=[
        {'evidence_id':'formula','page_ref':'p0','source_id':'s0','quote':'p < t'}])
    pages={'p0':{'source_id':'s0','blocks':[
        {'evidence_id':'definition','block_order':0,'text':'p denotes the probability of a correct prediction.','region':[50,100,500,145]},
        {'evidence_id':'wrong-column','block_order':1,'text':'p represents an unrelated quantity.','region':[600,100,800,145]},
        {'evidence_id':'formula','block_order':3,'text':'Escalate if p < t.','region':[50,200,400,240]},
        {'evidence_id':'distant','block_order':30,'text':'p denotes another statistic.','region':[50,700,500,745]}]},
        'foreign':{'source_id':'other','blocks':[{'evidence_id':'other','text':'p denotes failure probability.'}]}}
    result=provider.script_context(request['claims'],pages)
    assert list(result)==['p0']
    assert [b['evidence_id'] for b in result['p0']['blocks']]==['definition','formula']
