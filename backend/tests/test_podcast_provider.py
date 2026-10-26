"""講稿生成、逐段原文核對與合法來源綁定；無真實模型呼叫。"""
import importlib.util
import json
from pathlib import Path
from copy import deepcopy
import pytest

spec=importlib.util.spec_from_file_location('podcast_provider',Path(__file__).resolve().parents[2]/'ops/podcast/provider.py')
provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)


def body():
    return {'delivery':'solo','hosts':{'host':'小晴'},
        'series':{'episode_number':1,'episode_count':1,'next_concepts':[]},
        'claims':[{'concept_id':'c0','claim_id':'repeated','label':'非空檢查',
        'text':'空堆疊不可 pop。','evidence':[{'evidence_id':'e0','page_ref':'p0','quote':'空堆疊不可 pop。'}]}]}


def dialogue():
    return {**body(),'delivery':'dialogue','hosts':{'host':'小晴','guest':'阿哲'},
        'series':{'episode_number':1,'episode_count':2,'next_concepts':['佇列']}}


def candidate(text='空堆疊不可 pop。', sources=None):
    return {'segments':[{'title':'非空檢查','turns':[{'speaker':'host','text':text,'source_indices':[0] if sources is None else sources}]}]}


def review_input(prompt):
    return json.loads(prompt.split('\nINPUT:\n',1)[1])


def verdict(request, rejected=()):
    """依請求逐項回覆；rejected 列出判為不符的台詞位置或重點 ref。"""
    return {'checks':[{'part':p,'supported':p not in rejected,'reason':'' if p not in rejected else '數值與原文不符'}
            for p in request['parts']],
        'coverage':[{'claim':c,'explained':c not in rejected,
            'script_parts':request['coverage_locations'][c][:1] if c not in rejected else [],
            'reason':'' if c not in rejected else '只念數字沒有說明'} for c in request['coverage_claims']]}


def fake_model(script, *, reviews=None, rewrites=()):
    """依 schema 分辨生成、核對與單段重寫；reviews 依序回覆核對，未指定就全數通過。"""
    calls=[];reviews=list(reviews or []);rewrites=list(rewrites)
    def model(prompt,schema,**kwargs):
        props=schema['properties']
        if 'checks' in props:
            request=review_input(prompt);calls.append(('review',request))
            answer=reviews.pop(0) if reviews else None
            return answer(request) if callable(answer) else verdict(request)
        if 'turns' in props:
            calls.append(('rewrite',prompt));return rewrites.pop(0)
        calls.append(('script',prompt));return script
    return model,calls


def test_passing_script_uses_one_generation_and_one_review_per_segment(monkeypatch):
    request=body()
    value=candidate('必要條件保留。'*80, [0,0])
    model,calls=fake_model(value)
    monkeypatch.setattr(provider,'luna',model)
    diagnostics={}
    result=provider.script(request,diagnostics=diagnostics)
    assert [kind for kind,_ in calls]==['script','review'] and 'review' not in result
    assert diagnostics=={'model_calls':2,'rewrites':[]}
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
    monkeypatch.setattr(provider,'luna',fake_model(candidate(sources=[0,1]))[0])
    refs=provider.script(request)['segments'][0]['turns'][0]['parts'][0]['source_refs']
    assert refs==[{'source_index':0,'evidence_ids':['e0']},{'source_index':1,'evidence_ids':['e1']}]


def test_source_free_questions_and_consecutive_speakers_are_allowed(monkeypatch):
    value=candidate();value['segments'][0]['turns'].insert(0,{'speaker':'host','text':'為什麼？','source_indices':[]})
    monkeypatch.setattr(provider,'luna',fake_model(value)[0])
    result=provider.script(dialogue())
    assert [t['speaker'] for t in result['segments'][0]['turns']]==['host','host']
    assert result['segments'][0]['turns'][0]['parts'][0]['source_refs']==[]


def test_show_prompt_uses_host_names_series_preview_and_spoken_numbers(monkeypatch):
    model,calls=fake_model(candidate())
    monkeypatch.setattr(provider,'luna',model)
    result=provider.script(dialogue())
    assert result['provider'].endswith(';source-script/v15')
    prompts=[prompt for kind,prompt in calls if kind=='script']
    prompt=prompts[0]
    assert 'host「小晴」' in prompt and 'guest「阿哲」' in prompt
    assert '"next_concepts": ["佇列"]' in prompt and '"episode_count": 2' in prompt
    assert '本集路線' in prompt and '3～5 點回顧' in prompt and '本集重點' in prompt
    assert '不要補來源沒有的邊界' in prompt and '另一個範例的數值' in prompt
    assert '自稱一律用「我」' in prompt and '小晴不要說「小晴覺得」' in prompt and '阿哲不要說「阿哲覺得」' in prompt
    provider.script(body())
    prompts=[prompt for kind,prompt in calls if kind=='script']
    assert '單人主持：由主持人「小晴」' in prompts[1] and '雙主持' not in prompts[1]
    assert '"next_concepts": []' in prompts[1]


def show():
    """開場（無引用）、兩段主體、收尾回顧；第二個重點的原文與同頁表頭在 page context。"""
    request=dialogue()
    request['claims'].append({'concept_id':'c1','claim_id':'rate','label':'學習率',
        'text':'學習率 0.1 時損失 0.16。','evidence':[{'evidence_id':'e1','page_ref':'p1','quote':'0.1 0.16'}]})
    request['source_context']={'p1':{'source_id':'s1','source_name':'講義.pdf','normalized_page':9,'blocks':[
        {'evidence_id':'header','block_order':0,'text':'η 損失','region':[50,90,200,110]},
        {'evidence_id':'e1','block_order':1,'text':'0.1 0.16','region':[50,120,200,140]}]}}
    script={'segments':[
        {'title':'開場','turns':[{'speaker':'host','text':'歡迎收聽，我是小晴，今天談非空檢查與學習率。','source_indices':[]}]},
        {'title':'非空檢查','turns':[{'speaker':'guest','text':'空堆疊不可 pop。','source_indices':[0]}]},
        {'title':'學習率','turns':[{'speaker':'guest','text':'學習率 0.1 時損失是 0.16。','source_indices':[1]},
            {'speaker':'host','text':'所以步伐要合適。','source_indices':[]}]},
        {'title':'回顧','turns':[{'speaker':'host','text':'空堆疊不可 pop；學習率 0.1 時損失 0.16。下集談佇列。','source_indices':[0,1]}]}]}
    return request,script


def test_each_segment_is_checked_against_its_cited_original_and_same_page_headers(monkeypatch):
    request,script=show()
    model,calls=fake_model(script)
    monkeypatch.setattr(provider,'luna',model)
    result=provider.script(request)
    reviews=[r for kind,r in calls if kind=='review']
    assert [kind for kind,_ in calls]==['script']+['review']*4
    assert [r['segment_index'] for r in reviews]==[0,1,2,3]
    opening,rate,recap=reviews[0],reviews[2],reviews[3]
    # 沒有引用的開場只能交由核對確認沒有事實主張：沒有原文、也沒有涵蓋項目。
    assert opening['evidence']==[] and opening['claims']==[] and opening['coverage_claims']==[]
    assert opening['episode_concepts']==['非空檢查','學習率'] and opening['series']=={'episode_number':1,'episode_count':2,'next_concepts':['佇列']}
    assert opening['segment']['turns'][0]['name']=='小晴'
    assert rate['parts']==['title','turn.0','turn.1']
    assert [(e['text'],e['cited']) for e in rate['evidence']]==[('0.1 0.16',True),('η 損失',False)]
    assert rate['evidence'][0]['page']==9 and rate['evidence'][0]['source']=='講義.pdf'
    # 收尾只是回顧：前面講過的重點在最後講解它的段落判斷涵蓋，不在回顧段判斷。
    assert rate['coverage_claims']==['C1'] and rate['coverage_locations']=={'C1':['turn.0','turn.1']}
    assert recap['coverage_claims']==[] and recap['related_segments']==[]
    assert [b['title'] for b in result['segments']]==['開場','非空檢查','學習率','回顧']


def test_claim_explained_across_segments_is_judged_at_its_last_teaching_segment(monkeypatch):
    request,script=show()
    script['segments'][2]['turns'][1]['source_indices']=[0]
    model,calls=fake_model(script)
    monkeypatch.setattr(provider,'luna',model)
    provider.script(request)
    reviews=[r for kind,r in calls if kind=='review']
    # 非空檢查跨第 1、2 段，在第 2 段合併前段判斷；收尾回顧不判斷涵蓋。
    assert reviews[1]['coverage_claims']==[] and reviews[2]['coverage_claims']==['C1','C2']
    assert reviews[2]['coverage_locations']['C2']==['turn.0','turn.1','related.0.turn.0']
    assert reviews[3]['coverage_claims']==[]


def test_claim_only_recapped_is_assigned_to_a_body_segment_and_must_be_cited(monkeypatch):
    request,script=show()
    script['segments'][2]['turns'][0]['source_indices']=[]
    fixed=deepcopy(script['segments'][2]);fixed['turns'][0]['source_indices']=[1]
    model,calls=fake_model(script,rewrites=[fixed])
    monkeypatch.setattr(provider,'luna',model)
    diagnostics={}
    result=provider.script(request,diagnostics=diagnostics)
    # 學習率只在收尾一句帶過：沒有同概念的正文段落，分到最後一個正文段落重寫。
    assert [kind for kind,_ in calls]==['script','review','review','review','rewrite','review','review']
    correction=json.loads(calls[4][1][calls[4][1].index('{"series"'):])['correction']
    assert correction['segment_index']==2 and correction['required_source_indices']==[1]
    assert correction['issues']==[{'part':'source.1','reason':'本集應講解這個重點，但沒有任何正文段落引用並講解它'}]
    assert [r for kind,r in calls if kind=='review'][3]['coverage_claims']==['C1']
    assert result['segments'][2]['turns'][0]['parts'][0]['source_refs'][0]['source_index']==1
    assert diagnostics['rewrites']==[{'segment':2,'rejected':['source.1']}]


def test_uncited_claim_goes_to_the_last_body_segment_of_its_concept(monkeypatch):
    request,script=show()
    request['claims'].append({'concept_id':'c0','claim_id':'empty-check','label':'非空檢查',
        'text':'pop 前先確認非空。','evidence':[{'evidence_id':'e2','page_ref':'p0','quote':'pop 前先確認非空。'}]})
    fixed=deepcopy(script['segments'][1]);fixed['turns'].append({'speaker':'guest','text':'pop 前先確認非空。','source_indices':[2]})
    model,calls=fake_model(script,rewrites=[fixed])
    monkeypatch.setattr(provider,'luna',model)
    result=provider.script(request)
    assert [kind for kind,_ in calls]==['script','review','review','rewrite','review','review','review']
    correction=json.loads(calls[3][1][calls[3][1].index('{"series"'):])['correction']
    assert correction['segment_index']==1 and correction['required_source_indices']==[0,2]
    assert result['segments'][1]['turns'][-1]['text']=='pop 前先確認非空。'


def test_rewrite_that_still_leaves_the_assigned_claim_uncited_fails(monkeypatch):
    request,script=show()
    script['segments'][2]['turns'][0]['source_indices']=[]
    model,calls=fake_model(script,rewrites=[script['segments'][2]])
    monkeypatch.setattr(provider,'luna',model)
    with pytest.raises(RuntimeError,match='^PODCAST_SCRIPT_NEEDS_REVIEW$'):provider.script(request)
    assert [kind for kind,_ in calls]==['script','review','review','review','rewrite']


def test_unsupported_segment_is_rewritten_alone_and_checked_again(monkeypatch):
    request,script=show()
    fixed={'title':'學習率','turns':[{'speaker':'guest','text':'表中學習率 0.1 對應損失 0.16。','source_indices':[1]}]}
    model,calls=fake_model(script,reviews=[None,None,lambda r:verdict(r,{'turn.0'})],rewrites=[fixed])
    monkeypatch.setattr(provider,'luna',model)
    diagnostics={}
    result=provider.script(request,diagnostics=diagnostics)
    assert [kind for kind,_ in calls]==['script','review','review','review','rewrite','review','review']
    rewrite=calls[4][1]
    correction=json.loads(rewrite[rewrite.index('{"series"'):])['correction']
    assert correction['segment_index']==2 and correction['issues']==[{'part':'turn.0','reason':'數值與原文不符'}]
    assert correction['segment']['turns'][0]['text']=='學習率 0.1 時損失是 0.16。'
    assert correction['previous_segment']['title']=='非空檢查' and correction['next_segment']['title']=='回顧'
    assert correction['required_source_indices']==[1]
    assert '只重寫 correction.segment_index' in rewrite and '"page_context"' in rewrite
    # 其他段落原樣保留，重寫段沿用原位置的 beat_id 並重新綁定來源。
    assert result['segments'][2]['beat_id']=='beat-2'
    assert result['segments'][2]['turns'][0]['parts'][0]['source_refs']==[{'source_index':1,'evidence_ids':['e1']}]
    assert result['segments'][1]['turns'][0]['text']=='空堆疊不可 pop。'
    assert diagnostics=={'model_calls':7,'rewrites':[{'segment':2,'rejected':['turn.0']}]}


def test_segment_still_unsupported_after_one_rewrite_fails_the_episode(monkeypatch):
    request,script=show()
    model,calls=fake_model(script,reviews=[None,lambda r:verdict(r,{'turn.0'}),lambda r:verdict(r,{'turn.0'})],
        rewrites=[script['segments'][1]])
    monkeypatch.setattr(provider,'luna',model)
    diagnostics={}
    with pytest.raises(RuntimeError,match='^PODCAST_SCRIPT_NEEDS_REVIEW$'):provider.script(request,diagnostics=diagnostics)
    # 第二段失敗後不再核對後面的段落。
    assert [kind for kind,_ in calls]==['script','review','review','rewrite','review']
    assert diagnostics['rewrites']==[{'segment':1,'rejected':['turn.0']},{'segment':1,'rejected_after_rewrite':['turn.0']}]
    assert provider.failure_code(RuntimeError('PODCAST_SCRIPT_NEEDS_REVIEW'))=='PODCAST_SCRIPT_NEEDS_REVIEW'


def test_uncited_opening_with_factual_claim_is_rewritten_and_fails_if_it_keeps_the_claim(monkeypatch):
    request,script=show()
    script['segments'][0]['turns'][0]['text']='歡迎，我是小晴。學習率 0.5 時損失會變成 36。'
    model,calls=fake_model(script,reviews=[lambda r:verdict(r,{'turn.0'})]*2,rewrites=[script['segments'][0]])
    monkeypatch.setattr(provider,'luna',model)
    with pytest.raises(RuntimeError,match='^PODCAST_SCRIPT_NEEDS_REVIEW$'):provider.script(request)
    reviews=[r for kind,r in calls if kind=='review']
    assert all(r['evidence']==[] and r['coverage_claims']==[] for r in reviews)


def test_missing_explanation_is_reported_by_source_index_and_rewrite_cannot_drop_it(monkeypatch):
    request,script=show()
    script['segments'][3]['turns'][0]['source_indices']=[0]
    dropped={'title':'學習率','turns':[{'speaker':'guest','text':'接著看步伐大小。','source_indices':[]}]}
    model,calls=fake_model(script,reviews=[None,None,lambda r:verdict(r,{'C1'})],rewrites=[dropped])
    monkeypatch.setattr(provider,'luna',model)
    diagnostics={}
    with pytest.raises(RuntimeError,match='^PODCAST_SCRIPT_NEEDS_REVIEW$'):provider.script(request,diagnostics=diagnostics)
    rewrite=calls[4][1]
    correction=json.loads(rewrite[rewrite.index('{"series"'):])['correction']
    assert correction['issues']==[{'part':'source.1','reason':'只念數字沒有說明'}]
    assert correction['required_source_indices']==[1]
    # 重寫刪掉唯一的引用就不再送核對，直接不發布。
    assert [kind for kind,_ in calls]==['script','review','review','review','rewrite']
    assert diagnostics['rewrites'][-1]=={'segment':2,'rejected_after_rewrite':['source.1']}


def test_explanation_located_in_lines_about_another_claim_counts_as_missing(monkeypatch):
    request,script=show()
    script['segments'][2]['turns'][0]['source_indices']=[0,1]
    fixed=deepcopy(script['segments'][2]);fixed['title']='重寫學習率'
    def elsewhere(r):
        answer=verdict(r)
        # 學習率（C2）的講解位置指到只講非空檢查的前段台詞。
        answer['coverage']=[{**c,'script_parts':['related.0.turn.0']} if c['claim']=='C2' else c for c in answer['coverage']]
        return answer
    model,calls=fake_model(script,reviews=[None,None,elsewhere],rewrites=[fixed])
    monkeypatch.setattr(provider,'luna',model)
    result=provider.script(request)
    rewrite=calls[4][1]
    correction=json.loads(rewrite[rewrite.index('{"series"'):])['correction']
    assert correction['issues']==[{'part':'source.1','reason':'核對沒有指出講解這個重點的台詞'}]
    assert result['segments'][2]['title']=='重寫學習率'


def test_host_calling_themself_by_name_is_rewritten_while_introductions_and_partner_names_stay(monkeypatch):
    request,script=show()
    script['segments'][0]['turns']=[
        {'speaker':'host','text':'歡迎收聽，我是小晴，阿哲，我們開始吧。','source_indices':[]},
        {'speaker':'guest','text':'我們是小晴和阿哲。小晴，先從非空檢查談起。','source_indices':[]}]
    script['segments'][2]['turns'][1]['text']='小晴幫大家整理：所以步伐要合適。'
    fixed=deepcopy(script['segments'][2]);fixed['turns'][1]['text']='我幫大家整理：所以步伐要合適。'
    model,calls=fake_model(script,rewrites=[fixed])
    monkeypatch.setattr(provider,'luna',model)
    diagnostics={}
    result=provider.script(request,diagnostics=diagnostics)
    # 自稱由程式檢查：開場自我介紹與叫對方名字不算，只有第三人稱自稱的那句交給同一段重寫。
    assert [kind for kind,_ in calls]==['script','review','review','review','rewrite','review','review']
    correction=json.loads(calls[4][1][calls[4][1].index('{"series"'):])['correction']
    assert [i['part'] for i in correction['issues']]==['turn.1']
    assert '小晴用自己的名字稱呼自己' in correction['issues'][0]['reason']
    assert result['segments'][2]['turns'][1]['text']=='我幫大家整理：所以步伐要合適。'
    assert diagnostics['rewrites']==[{'segment':2,'rejected':['turn.1']}]


def test_self_naming_left_after_rewrite_is_published_and_only_its_position_logged(monkeypatch):
    request,script=show()
    script['segments'][1]['turns'][0]['text']='阿哲覺得：空堆疊不可 pop。'
    model,calls=fake_model(script,rewrites=[script['segments'][1]])
    monkeypatch.setattr(provider,'luna',model)
    diagnostics={}
    result=provider.script(request,diagnostics=diagnostics)
    # 只剩自稱時仍重核事實，通過就照常發布；診斷只記位置，不記台詞。
    assert [kind for kind,_ in calls]==['script','review','review','rewrite','review','review','review']
    assert result['segments'][1]['turns'][0]['text']=='阿哲覺得：空堆疊不可 pop。'
    assert diagnostics['rewrites']==[{'segment':1,'rejected':['turn.0']},{'segment':1,'self_naming_after_rewrite':['turn.0']}]


def test_self_naming_with_unsupported_facts_after_rewrite_still_fails(monkeypatch):
    request,script=show()
    script['segments'][1]['turns'][0]['text']='阿哲覺得：空堆疊不可 pop。'
    model,calls=fake_model(script,reviews=[None,None,lambda r:verdict(r,{'turn.0'})],rewrites=[script['segments'][1]])
    monkeypatch.setattr(provider,'luna',model)
    diagnostics={}
    with pytest.raises(RuntimeError,match='^PODCAST_SCRIPT_NEEDS_REVIEW$'):provider.script(request,diagnostics=diagnostics)
    assert diagnostics['rewrites'][-1]=={'segment':1,'rejected_after_rewrite':['turn.0']}


@pytest.mark.parametrize('name,text',[
    # 名字前面緊接非指人的中文字：只是詞語的一部分。
    ('小安','先把大小安排好，再看連接埠。'),
    ('小安','最小安全距離要先確認。'),
    # 名字末字與後一字組成原文用到的詞（原文寫「小型語言模型」「低資源語種」）。
    ('小語','小語種的資料比較少。'),
    ('小語','小語言模型也能用。'),
    ('小語','這裡指的就是小語言模型。'),
    # 自我介紹與節目介紹：說的是節目名稱或自己是誰，不是用名字當主詞。
    ('小安','這裡是小安和阿哲的學習時間。'),
    ('小安','謝謝收聽小安和阿哲的節目。'),
    ('小安','謝謝收聽，我是小安。'),
])
def test_words_containing_a_host_name_and_show_introductions_are_not_self_naming(monkeypatch,name,text):
    request,script=show()
    request['hosts']={'host':name,'guest':'阿哲'}
    request['claims'][0]['text']='小型語言模型在低資源語種上也要先確認非空。'
    script['segments'][0]['turns'][0]['text']=text
    model,calls=fake_model(script)
    monkeypatch.setattr(provider,'luna',model)
    provider.script(request)
    assert 'rewrite' not in [kind for kind,_ in calls]


@pytest.mark.parametrize('text',[
    '小安我來補充一下。','小安建議先看表頭。','小安發現一個重點。','小安以前也搞錯過。','小安喜歡這個例子。',
    '小安知道大家會問。','小安特別想強調這點。','跟著小安一起看。','陪你的小安今天要講非空檢查。','這集由小安陪大家。',
    # 道別時用名字當主詞，不是「我是／我們是」的自我介紹，也不是節目名稱。
    '我們小安和阿哲下次見。',
    # 「這是」後面不是「某某和某某的節目」，名字是「覺得」的主詞。
    '這是小安覺得最重要的一點。',
])
def test_host_name_used_as_own_subject_is_sent_to_rewrite(monkeypatch,text):
    request,script=show()
    request['hosts']={'host':'小安','guest':'阿哲'}
    script['segments'][0]['turns'][0]['text']=text
    fixed=deepcopy(script['segments'][0]);fixed['turns'][0]['text']='歡迎收聽，我是小安。'
    model,calls=fake_model(script,rewrites=[fixed])
    monkeypatch.setattr(provider,'luna',model)
    diagnostics={}
    provider.script(request,diagnostics=diagnostics)
    assert diagnostics['rewrites'][0]=={'segment':0,'rejected':['turn.0']}


@pytest.mark.parametrize('broken',[
    lambda r:{'checks':[],'coverage':[]},
    lambda r:{**verdict(r),'checks':verdict(r)['checks'][:-1]},
    lambda r:{**verdict(r),'checks':[{**c,'supported':False,'reason':' '} for c in verdict(r)['checks']]},
    lambda r:{**verdict(r),'coverage':[{**c,'script_parts':['turn.99']} for c in verdict(r)['coverage']]},
])
def test_incomplete_review_response_is_a_failure_not_a_pass(monkeypatch,broken):
    request,script=show()
    model,calls=fake_model(script,reviews=[None,None,broken])
    monkeypatch.setattr(provider,'luna',model)
    with pytest.raises(RuntimeError,match='^LUNA_GENERATION_FAILED$'):provider.script(request)
    assert 'rewrite' not in [kind for kind,_ in calls]


def test_review_contract_must_match_the_runtime_lock_before_any_model_call(monkeypatch):
    from runtime import local_ai_config
    calls=[]
    monkeypatch.setattr(provider,'luna',lambda *a,**k:calls.append(a) or candidate())
    lock=local_ai_config.configured_lock()
    monkeypatch.setattr(local_ai_config,'configured_lock',lambda:{**lock,'podcast_script_review':{**lock['podcast_script_review'],'prompt':'舊提示'}})
    with pytest.raises(RuntimeError) as error:provider.script(body())
    assert provider.failure_code(error.value)=='PODCAST_REVIEW_CONTRACT_INVALID' and calls==[]


def test_review_contract_is_added_without_invalidating_old_runtime_snapshots():
    from runtime import podcast_review
    from runtime.local_ai_config import configured_lock
    from runtime.material_runtime import validate_runtime_lock, MaterialAnalysisError
    from content_identity import canonical_sha256
    current=configured_lock()
    assert current['podcast_script_review']=={'version':'podcast-script-review/v1','prompt':podcast_review.PROMPT}
    old=deepcopy(current);old.pop('podcast_script_review');old_hash=canonical_sha256(old)
    assert validate_runtime_lock(old)==old and canonical_sha256(old)==old_hash
    assert validate_runtime_lock(current)==current
    for broken in ({'version':'podcast-script-review/v0','prompt':'x'},{'version':'podcast-script-review/v1','prompt':' '},
                   {'version':'podcast-script-review/v1','prompt':'x','extra':1}):
        with pytest.raises(MaterialAnalysisError):validate_runtime_lock({**current,'podcast_script_review':broken})


def test_script_review_diagnostics_are_logged_without_script_text(caplog):
    import logging, time
    from uuid import uuid4
    caplog.set_level(logging.INFO, logger='studydy.podcast.jobs')
    review={'model_calls':7,'rewrites':[{'segment':2,'rejected':['turn.0','source.1']}]}
    provider.record_job('/script',{'job_id':str(uuid4()),'claims':'do-not-log-material'},time.monotonic(),None,review)
    message=caplog.records[-1].getMessage()
    assert json.loads(message)['script_review']==review and 'do-not-log' not in message


@pytest.mark.parametrize('change',[
    lambda b:b.pop('hosts'),
    lambda b:b['hosts'].pop('guest'),
    lambda b:b['hosts'].update(guest='小晴'),
    lambda b:b['hosts'].update(host=''),
    lambda b:b['hosts'].update(host='主持人'*3),
    lambda b:b.pop('series'),
    lambda b:b['series'].update(next_concepts=[]),
    lambda b:b['series'].update(episode_number=3),
    lambda b:b['series'].update(episode_count='2'),
])
def test_show_input_is_validated_before_calling_the_model(monkeypatch,change):
    calls=[]
    monkeypatch.setattr(provider,'luna',lambda *a,**k:calls.append(a) or candidate())
    request=dialogue();change(request)
    with pytest.raises(ValueError,match='REQUEST_INVALID'):provider.script(request)
    assert calls==[]
    solo=body();solo['hosts']['guest']='阿哲'
    with pytest.raises(ValueError,match='REQUEST_INVALID'):provider.script(solo)
    last=dialogue();last['series']={'episode_number':2,'episode_count':2,'next_concepts':['佇列']}
    with pytest.raises(ValueError,match='REQUEST_INVALID'):provider.script(last)


def test_model_schema_only_exposes_current_source_indices():
    schema=provider.script_schema(body()['claims'])
    turn=schema['properties']['segments']['items']['properties']['turns']['items']
    assert set(turn['properties'])=={'speaker','text','source_indices'}
    assert turn['properties']['source_indices']['items']['enum']==[0]
    # 每段輪數與 synthesize.py 的語音上限一致。
    assert schema['properties']['segments']['items']['properties']['turns']['maxItems']==12


def test_source_capacity_stays_aligned_with_episode_planner(monkeypatch):
    from runtime.podcast_script import MAX_EPISODE_CLAIMS
    request=body();request['claims']*=MAX_EPISODE_CLAIMS
    monkeypatch.setattr(provider,'luna',fake_model(candidate(sources=list(range(MAX_EPISODE_CLAIMS))))[0])
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
    model, calls = fake_model(candidate())
    monkeypatch.setattr(provider, 'luna', model)
    provider.script(request)
    prompts = [prompt for kind, prompt in calls if kind == 'script']
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


def test_media_transcription_runs_isolated_gpu_script_and_reports_failure(monkeypatch):
    import base64
    from types import SimpleNamespace
    monkeypatch.setenv('STUDYDY_STT_PYTHON','/isolated/stt-python')
    monkeypatch.setenv('STUDYDY_PODCAST_PROVIDER_TOKEN','x'*32)
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        assert args[0]=='/isolated/stt-python' and args[1].endswith('transcribe_media.py')
        assert Path(args[2]).read_bytes()==b'OggS-speech'
        assert kwargs['timeout'] is None and 'STUDYDY_PODCAST_PROVIDER_TOKEN' not in kwargs['env']
        Path(args[3]).write_text('{"segments":[{"start":0,"end":1,"text":"堆疊"}],"language":"zh"}')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(provider.podcast_process,'run',run)
    body={'audio':base64.b64encode(b'OggS-speech').decode()}
    assert provider.media_transcribe(body)['segments'][0]['text']=='堆疊'
    # 暫存音訊在回傳前刪除。
    assert not Path(calls[0][2]).exists()
    monkeypatch.setattr(provider.podcast_process,'run',lambda args,**kwargs:SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError,match='MEDIA_TRANSCRIPT_FAILED'):provider.media_transcribe(body)
    with pytest.raises(ValueError,match='REQUEST_INVALID'):provider.media_transcribe({'audio':''})



def recap(declared='3', *, unnumbered_fourth=False):
    turns = [
        {'speaker': 'host', 'text': f'回顧本集 {declared} 個重點。第一，空堆疊不可 pop。', 'source_indices': [0]},
        {'speaker': 'guest', 'text': '第二，先確認堆疊不是空的。', 'source_indices': [0]},
        {'speaker': 'host', 'text': '第三，再取出最上方元素。', 'source_indices': [0]},
    ]
    if unnumbered_fourth:
        turns.append({'speaker': 'guest', 'text': '取出後記得更新堆疊。謝謝收聽。', 'source_indices': [0]})
    else:
        turns.append({'speaker': 'host', 'text': '謝謝收聽。', 'source_indices': []})
    return {'title': '回顧', 'turns': turns}


@pytest.mark.parametrize('declared', ['3', '三'])
def test_recap_count_matches_numbered_source_turns_without_extra_calls(monkeypatch, declared):
    value = candidate()
    value['segments'].append(recap(declared))
    model, calls = fake_model(value)
    monkeypatch.setattr(provider, 'luna', model)
    provider.script(dialogue())
    assert [kind for kind, _ in calls] == ['script', 'review', 'review']


@pytest.mark.parametrize('bad_recap', [recap('4'), recap(unnumbered_fourth=True)])
@pytest.mark.parametrize('announcement', ['回顧本集', '最後記住', '本集有'])
def test_recap_mismatch_rewrites_only_the_closing_segment(monkeypatch, bad_recap, announcement):
    bad_recap = deepcopy(bad_recap)
    bad_recap['turns'][0]['text'] = bad_recap['turns'][0]['text'].replace('回顧本集', announcement)
    value = candidate()
    value['segments'].append(bad_recap)
    fixed = deepcopy(bad_recap)
    if len(fixed['turns']) == 4 and fixed['turns'][-1]['source_indices']:
        fixed['turns'][0]['text'] = fixed['turns'][0]['text'].replace('3 個', '4 個')
        fixed['turns'][-1]['text'] = '第四，' + fixed['turns'][-1]['text']
    else:
        fixed['turns'][0]['text'] = fixed['turns'][0]['text'].replace('4 個', '3 個')
    model, calls = fake_model(value, rewrites=[fixed])
    monkeypatch.setattr(provider, 'luna', model)
    result = provider.script(dialogue())
    assert [kind for kind, _ in calls] == ['script', 'review', 'review', 'rewrite', 'review']
    assert result['segments'][0]['turns'][0]['text'] == value['segments'][0]['turns'][0]['text']
    correction = json.loads(calls[3][1][calls[3][1].index('{"series"'):])['correction']
    assert correction['segment_index'] == 1
    assert '宣告的點數' in correction['issues'][0]['reason']


def test_recap_mismatch_after_rewrite_is_not_published(monkeypatch):
    value = candidate()
    bad = recap(unnumbered_fourth=True)
    value['segments'].append(bad)
    model, calls = fake_model(value, rewrites=[bad])
    monkeypatch.setattr(provider, 'luna', model)
    with pytest.raises(RuntimeError, match='^PODCAST_SCRIPT_NEEDS_REVIEW$'):
        provider.script(dialogue())
    assert [kind for kind, _ in calls] == ['script', 'review', 'review', 'rewrite', 'review']
