"""有界修稿與獨立 blocking verdict；無真實模型呼叫。"""
import importlib.util
from pathlib import Path
from copy import deepcopy
import pytest

spec=importlib.util.spec_from_file_location('podcast_provider',Path(__file__).resolve().parents[2]/'ops/podcast/provider.py')
provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)


def body():
    return {'delivery':'solo','claims':[{'concept_id':'c0','claim_id':'repeated','label':'非空檢查',
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
    assert len(calls)==2


def test_invalid_reference_can_be_repaired_but_only_valid_script_is_reviewed(monkeypatch):
    invalid=candidate();invalid['segments'][0]['turns'][0]['parts'][0]['source_refs'][0]['source_index']=99
    responses=iter([invalid,candidate(),check()]);schemas=[]
    def model(prompt,schema):schemas.append(schema);return next(responses)
    monkeypatch.setattr(provider,'luna',model)
    result=provider.script(body())
    assert len(schemas)==3 and all('segments' in s['properties'] for s in schemas[:2])
    assert 'correctness' in schemas[-1]['properties']
    assert result['review']['correctness']['passed']


def test_one_beat_integrates_repeated_claim_ids_without_merging_sources(monkeypatch):
    request=body();request['claims'].append({**deepcopy(request['claims'][0]),'concept_id':'c1'})
    value=candidate();value['segments'][0]['turns'][0]['parts'].append(part('另一個概念也必須先確認。',1))
    responses=iter([value,check()]);monkeypatch.setattr(provider,'luna',lambda *_:next(responses))
    result=provider.script(request)
    assert len(result['segments'])==1
    assert [p['source_refs'][0]['source_index'] for p in result['segments'][0]['turns'][0]['parts']]==[0,1]


def test_same_page_context_reaches_generation_and_review(monkeypatch):
    request=body();request['source_context']={'p0':{'blocks':[
        {'evidence_id':'header','text':'合成表格標題','region':[90,10,180,40]},
        {'evidence_id':'e0','text':'16 bits','region':[100,60,150,80]},
        {'evidence_id':'unselected','text':'未選的其他機制','region':[300,100,600,120]}]}}
    responses=iter([candidate(),check()]);prompts=[]
    def model(prompt,*args):prompts.append(prompt);return next(responses)
    monkeypatch.setattr(provider,'luna',model);provider.script(request)
    assert all('合成表格標題' in p for p in prompts)
    assert all('未選的其他機制' not in p for p in prompts)


def test_dialogue_allows_consecutive_same_speaker_and_short_questions(monkeypatch):
    request={**body(),'delivery':'dialogue'};value=candidate()
    value['segments'][0]['turns']=[{'speaker':'guest','parts':[part()]},
        {'speaker':'guest','parts':[part('因此要先檢查。')]},{'speaker':'host','parts':[{'text':'為什麼？','source_refs':[]}]}]
    responses=iter([value,check()]);monkeypatch.setattr(provider,'luna',lambda *_:next(responses))
    assert [t['speaker'] for t in provider.script(request)['segments'][0]['turns']]==['guest','guest','host']


@pytest.mark.parametrize('problem', ['budget'])
def test_deterministic_quality_rewrite_skips_unnecessary_review(monkeypatch, problem):
    bad = candidate('字' * 600) if problem == 'budget' else candidate('沒錯。')
    responses = iter([bad, candidate(), check()]); prompts = []
    def model(prompt, schema):
        prompts.append(prompt)
        return next(responses)
    monkeypatch.setattr(provider, 'luna', model)
    result = provider.script(body())
    assert len(prompts) == 3 and result['review']['correctness']['passed']
    assert ('content_budget' if problem == 'budget' else 'empty_confirmation') in prompts[1]


def test_repeated_deterministic_failure_stops_without_spending_review_calls(monkeypatch):
    calls = []
    def model(*args):
        calls.append(args)
        return candidate('字' * 600)
    monkeypatch.setattr(provider, 'luna', model)
    with pytest.raises(RuntimeError, match='PODCAST_SCRIPT_NEEDS_REVIEW'):
        provider.script(body())
    assert len(calls) == 2


def test_source_free_question_joins_its_answer_before_review_without_an_extra_call(monkeypatch):
    value=candidate()
    value['segments'].insert(0, {'title':'待解問題','turns':[{'speaker':'host','parts':[{'text':'空堆疊為什麼不能直接取出？','source_refs':[]}]}]})
    value['segments'][1]['turns'][0]['speaker']='guest'
    raw=deepcopy(value);calls=[]
    def model(prompt,schema):
        calls.append(prompt)
        return deepcopy(value) if 'segments' in schema['properties'] else check()
    monkeypatch.setattr(provider,'luna',model)
    result=provider.script({**body(),'delivery':'dialogue'})
    assert len(calls)==2 and len(result['segments'])==1
    assert [t['text'] for t in result['segments'][0]['turns']]==['空堆疊為什麼不能直接取出？','空堆疊不可 pop。']
    assert result['segments'][0]['turns'][0]['parts'][0]['source_refs']==[]
    assert value==raw


def test_audio_failure_never_returns_partial_file_or_falls_back(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('STUDYDY_PODCAST_TTS_PYTHON','/isolated/python')
    def failed(args,**kwargs):
        Path(args[-1]).write_bytes(b'RIFFpartial')
        assert kwargs['timeout']==540 and 'STUDYDY_PODCAST_PROVIDER_TOKEN' not in kwargs['env']
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(provider.subprocess,'run',failed)
    with pytest.raises(RuntimeError,match='PODCAST_AUDIO_INVALID'):provider.audio({'script':candidate()})


def test_provider_accepts_planner_capacity_and_rejects_overflow(monkeypatch):
    from runtime.podcast_quality import MAX_EPISODE_CLAIMS
    request=body();request['claims']=[deepcopy(request['claims'][0]) for _ in range(MAX_EPISODE_CLAIMS)]
    value=candidate();value['segments'][0]['turns'][0]['parts'][0]['source_refs']=[
        {'source_index':i,'evidence_indices':[0]} for i in range(MAX_EPISODE_CLAIMS)]
    responses=iter([value,check()]);calls=[]
    def model(*args):calls.append(args);return next(responses)
    monkeypatch.setattr(provider,'luna',model)
    result=provider.script(request)
    assert len(result['segments'][0]['turns'][0]['parts'][0]['source_refs'])==MAX_EPISODE_CLAIMS
    assert len(calls)==2
    request['claims'].append(deepcopy(request['claims'][0]))
    with pytest.raises(ValueError,match='REQUEST_INVALID'):provider.script(request)
    assert len(calls)==2


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


def test_short_acknowledgement_reaches_contextual_review_instead_of_automatic_rejection(monkeypatch):
    value=candidate();value['segments'][0]['turns'].append({'speaker':'host','parts':[{'text':'原來如此。','source_refs':[]}]})
    calls=[]
    def model(prompt,schema):
        calls.append(schema)
        return value if 'segments' in schema['properties'] else check()
    monkeypatch.setattr(provider,'luna',model)
    result=provider.script(body())
    assert len(calls)==2 and 'correctness' in calls[1]['properties']
    assert result['segments'][0]['turns'][-1]['text']=='原來如此。'


def test_context_does_not_guess_a_header_from_another_column_or_distant_page_text():
    request=body()
    pages={'p0':{'blocks':[{'evidence_id':'e0','text':'16 bits','region':[100,200,150,220]},
        {'evidence_id':'wrong-column','text':'別欄','region':[250,170,350,195]},
        {'evidence_id':'distant','text':'遠處段落','region':[90,20,180,40]}]},
        'foreign':{'blocks':[{'evidence_id':'e0','text':'其他頁'}]}}
    result=provider.script_context(request['claims'],pages)
    assert list(result)==['p0']
    assert [b['evidence_id'] for b in result['p0']['blocks']]==['e0']
