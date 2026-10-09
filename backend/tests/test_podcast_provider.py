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
    def model(prompt,schema):calls.append(schema);return value
    monkeypatch.setattr(provider,'luna',model)
    result=provider.script(request)
    assert len(calls)==1 and 'review' not in result
    turn=result['segments'][0]['turns'][0]
    assert turn['text']==value['segments'][0]['turns'][0]['text']
    assert turn['parts'][0]['source_refs']==[{'source_index':0,'evidence_ids':['e0']}]


@pytest.mark.parametrize('index',[False,-1,99,'0'])
def test_script_rejects_foreign_or_invalid_references_without_retry(monkeypatch,index):
    calls=[]
    def model(*args):calls.append(args);return candidate(sources=[index])
    monkeypatch.setattr(provider,'luna',model)
    with pytest.raises(RuntimeError,match='PODCAST_SCRIPT_INVALID'):provider.script(body())
    assert len(calls)==1


def test_empty_script_is_not_published(monkeypatch):
    monkeypatch.setattr(provider,'luna',lambda *_:{'segments':[]})
    with pytest.raises(RuntimeError,match='PODCAST_SCRIPT_INVALID'):provider.script(body())


def test_same_claim_ids_keep_distinct_source_positions(monkeypatch):
    request=body();request['claims'].append({**deepcopy(request['claims'][0]),'concept_id':'c1','evidence':[{'evidence_id':'e1'}]})
    monkeypatch.setattr(provider,'luna',lambda *_:candidate(sources=[0,1]))
    refs=provider.script(request)['segments'][0]['turns'][0]['parts'][0]['source_refs']
    assert refs==[{'source_index':0,'evidence_ids':['e0']},{'source_index':1,'evidence_ids':['e1']}]


def test_source_free_questions_and_consecutive_speakers_are_allowed(monkeypatch):
    value=candidate();value['segments'][0]['turns'].insert(0,{'speaker':'host','text':'為什麼？','source_indices':[]})
    monkeypatch.setattr(provider,'luna',lambda *_:value)
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
    monkeypatch.setattr(provider,'luna',lambda *_:candidate(sources=list(range(MAX_EPISODE_CLAIMS))))
    assert len(provider.script(request)['segments'][0]['turns'][0]['parts'][0]['source_refs'])==MAX_EPISODE_CLAIMS
    request['claims'].append(deepcopy(request['claims'][0]))
    with pytest.raises(ValueError,match='REQUEST_INVALID'):provider.script(request)

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


def test_qa_repairs_missing_citations_and_rechecks_without_changing_supported_text(monkeypatch):
    request={**body(),'question':'合成來源問題'}
    request['claims'].append(deepcopy(request['claims'][0]))
    candidate={'text':'兩項來源支持的合成回答。','supported':True,'citations':[0]}
    fixed={**candidate,'citations':[0,1,0]}
    responses=iter([candidate,{'supported':False,'unsupported_claims':['缺少第二項來源']},fixed,{'supported':True,'unsupported_claims':[]}]);calls=[]
    def model(prompt,schema):calls.append(prompt);return next(responses)
    monkeypatch.setattr(provider,'luna',model)
    assert provider.answer(request)=={**candidate,'citations':[0,1]}
    assert len(calls)==4 and '缺少第二項來源' in calls[2]


def test_qa_unsupported_answer_is_not_published_after_bounded_repair(monkeypatch):
    request={**body(),'question':'合成來源問題'}
    responses=iter([{'text':'沒有來源的斷言','supported':True,'citations':[0]}, {'supported':False,'unsupported_claims':['沒有來源的斷言']}]*2)
    calls=[]
    def model(*args):calls.append(args);return next(responses)
    monkeypatch.setattr(provider,'luna',model)
    result=provider.answer(request)
    assert result['supported'] is False and result['citations']==[]
    assert '沒有來源的斷言' not in result['text'] and len(calls)==4


def test_qa_grounding_rejects_foreign_indices_before_review(monkeypatch):
    monkeypatch.setattr(provider,'luna',lambda *_:{'text':'合成回答','supported':True,'citations':[999]})
    with pytest.raises(ValueError,match='VOICE_ANSWER_INVALID'):
        provider.answer({**body(),'question':'合成問題'})


def test_qa_citation_verifier_does_not_treat_claim_paraphrase_as_evidence(monkeypatch):
    request={**body(),'question':'合成問題'}
    request['claims'][0]['text']='UNSUPPORTED_CLAIM_PARAPHRASE'
    calls=[];responses=iter([{'text':'空堆疊不可 pop。','supported':True,'citations':[0]}, {'supported':True,'unsupported_claims':[]}])
    def model(prompt,schema):calls.append(prompt);return next(responses)
    monkeypatch.setattr(provider,'luna',model)
    provider.answer(request)
    assert 'UNSUPPORTED_CLAIM_PARAPHRASE' in calls[0]
    assert 'UNSUPPORTED_CLAIM_PARAPHRASE' not in calls[1]
    assert '空堆疊不可 pop。' in calls[1]


def test_context_does_not_guess_a_header_from_another_column_or_distant_page_text():
    request=body()
    pages={'p0':{'blocks':[{'evidence_id':'e0','text':'16 bits','region':[100,200,150,220]},
        {'evidence_id':'wrong-column','text':'別欄','region':[250,170,350,195]},
        {'evidence_id':'distant','text':'遠處段落','region':[90,20,180,40]}]},
        'foreign':{'blocks':[{'evidence_id':'e0','text':'其他頁'}]}}
    result=provider.script_context(request['claims'],pages)
    assert list(result)==['p0']
    assert [b['evidence_id'] for b in result['p0']['blocks']]==['e0']
