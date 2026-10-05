"""有界修稿與獨立 blocking verdict；無真實模型呼叫。"""
import importlib.util
from pathlib import Path
from copy import deepcopy
import pytest

spec=importlib.util.spec_from_file_location('podcast_provider',Path(__file__).resolve().parents[2]/'ops/podcast/provider.py')
provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)


def body():
    return {'mode':'full','delivery':'solo','claims':[{'concept_id':'c0','claim_id':'repeated','label':'非空檢查',
        'text':'空堆疊不可 pop。','evidence':[{'evidence_id':'e0','page_ref':'p0','quote':'空堆疊不可 pop。'}]}]}


def part(text='空堆疊不可 pop。', index=0):
    return {'text':text,'source_refs':[{'source_index':index,'evidence_indices':[0]}]}


def candidate(text='空堆疊不可 pop。'):
    return {'segments':[{'title':'先確認非空','turns':[{'speaker':'host','parts':[part(text)]}]}]}


def check(correct=True, teaching=True):
    return {'correctness':{'passed':correct,'reason':'否定條件須保留'},
            'teaching_quality':{'passed':teaching,'reason':'避免重複改述'}}


@pytest.mark.parametrize('verdict',[(False,True),(True,False),(False,False)])
def test_each_verdict_blocks_and_repair_rechecks_both(monkeypatch,verdict):
    responses=iter([candidate(),check(*verdict),candidate(),check()]);prompts=[]
    def model(prompt,schema):prompts.append(prompt);return next(responses)
    monkeypatch.setattr(provider,'luna',model)
    result=provider.script(body())
    assert len(prompts)==4 and '避免重複改述' in prompts[2] and '否定條件須保留' in prompts[2]
    assert result['schema']=='podcast-script/v2' and result['segments'][0]['turns'][0]['text']=='空堆疊不可 pop。'
    assert all(v['passed'] for v in result['review'].values())


def test_repeated_failure_stops_after_four_requests(monkeypatch):
    responses=iter([candidate(),check(False)]*2);calls=[]
    def model(*args):calls.append(args);return next(responses)
    monkeypatch.setattr(provider,'luna',model)
    with pytest.raises(RuntimeError,match='PODCAST_SCRIPT_NEEDS_REVIEW'):provider.script(body())
    assert len(calls)==4


@pytest.mark.parametrize('change',['foreign_claim','foreign_evidence','missing_source','one_role'])
def test_invalid_contract_never_reaches_review(monkeypatch,change):
    request=body();value=candidate()
    ref=value['segments'][0]['turns'][0]['parts'][0]['source_refs'][0]
    if change=='foreign_claim':ref['source_index']=9
    elif change=='foreign_evidence':ref['evidence_indices']=[99]
    elif change=='missing_source':value['segments'][0]['turns'][0]['parts'][0]['source_refs']=[]
    else:request['delivery']='dialogue'
    calls=[]
    def model(*args):calls.append(args);return value
    monkeypatch.setattr(provider,'luna',model)
    with pytest.raises(RuntimeError,match='PODCAST_SCRIPT_INVALID'):provider.script(request)
    assert len(calls)==1


def test_one_beat_integrates_repeated_claim_ids_without_merging_sources(monkeypatch):
    request=body();request['claims'].append({**deepcopy(request['claims'][0]),'concept_id':'c1'})
    value=candidate();value['segments'][0]['turns'][0]['parts'].append(part('另一個概念也必須先確認。',1))
    responses=iter([value,check()]);monkeypatch.setattr(provider,'luna',lambda *_:next(responses))
    result=provider.script(request)
    assert len(result['segments'])==1
    assert [p['source_refs'][0]['source_index'] for p in result['segments'][0]['turns'][0]['parts']]==[0,1]


def test_same_page_context_reaches_generation_and_review(monkeypatch):
    request=body();request['source_context']={'p0':{'blocks':[{'evidence_id':'header','text':'合成表格標題'}]}}
    responses=iter([candidate(),check()]);prompts=[]
    def model(prompt,*args):prompts.append(prompt);return next(responses)
    monkeypatch.setattr(provider,'luna',model);provider.script(request)
    assert all('合成表格標題' in p for p in prompts)


def test_dialogue_allows_consecutive_same_speaker_and_short_questions(monkeypatch):
    request={**body(),'delivery':'dialogue'};value=candidate()
    value['segments'][0]['turns']=[{'speaker':'guest','parts':[part()]},
        {'speaker':'guest','parts':[part('因此要先檢查。')]},{'speaker':'host','parts':[{'text':'為什麼？','source_refs':[]}]}]
    responses=iter([value,check()]);monkeypatch.setattr(provider,'luna',lambda *_:next(responses))
    assert [t['speaker'] for t in provider.script(request)['segments'][0]['turns']]==['guest','guest','host']


def test_audio_failure_never_returns_partial_file_or_falls_back(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('STUDYDY_PODCAST_TTS_PYTHON','/isolated/python')
    def failed(args,**kwargs):
        Path(args[-1]).write_bytes(b'RIFFpartial')
        assert kwargs['timeout']==540 and 'STUDYDY_PODCAST_PROVIDER_TOKEN' not in kwargs['env']
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(provider.subprocess,'run',failed)
    with pytest.raises(RuntimeError,match='PODCAST_AUDIO_INVALID'):provider.audio({'script':candidate()})


def test_luna_timeout_has_fixed_reason_and_task_specific_deadline(monkeypatch):
    def expired(args,**kwargs):
        assert kwargs['timeout']==300 and kwargs['input']=='synthetic prompt'
        raise provider.subprocess.TimeoutExpired('codex',300)
    monkeypatch.setattr(provider.subprocess,'run',expired)
    with pytest.raises(RuntimeError,match='^LUNA_GENERATION_TIMEOUT$'):
        provider.luna('synthetic prompt',{'type':'object'},timeout=300)


def test_model_reference_schema_only_allows_local_source_positions():
    claims=body()['claims'];claims.append({**deepcopy(claims[0]),'evidence':[{'evidence_id':'other-evidence','quote':'另一個來源'}]})
    schema,_=provider.response_schemas(claims)
    alternatives=schema['properties']['segments']['items']['properties']['turns']['items']['properties']['parts']['items']['properties']['source_refs']['items']['anyOf']
    assert [r['properties']['source_index']['enum'] for r in alternatives]==[[0],[1]]
    assert all(r['properties']['evidence_indices']['items']['enum']==[0] for r in alternatives)
    value=candidate();value['segments'][0]['turns'][0]['parts'][0]['source_refs']=[{'source_index':1,'evidence_indices':[0]}]
    compiled=provider.compile_beats(value,claims)
    assert compiled[0]['turns'][0]['parts'][0]['source_refs']==[{'source_index':1,'evidence_ids':['other-evidence']}]
    assert 'evidence_indices' in value['segments'][0]['turns'][0]['parts'][0]['source_refs'][0]


@pytest.mark.parametrize('source,evidence',[(False,0),(0,False),(-1,0),(0,-1),(0,2)])
def test_reference_compilation_rejects_boolean_negative_and_out_of_range(source,evidence):
    value=candidate();value['segments'][0]['turns'][0]['parts'][0]['source_refs']=[{'source_index':source,'evidence_indices':[evidence]}]
    with pytest.raises(ValueError,match='PODCAST_SCRIPT_INVALID'):provider.compile_beats(value,body()['claims'])
