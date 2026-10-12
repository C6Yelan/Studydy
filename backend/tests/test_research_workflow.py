from copy import deepcopy
import pytest
from runtime import research_workflow as flow
from runtime.source_normalization import SourceError
from research_fixtures import review_provider


def candidate(identity='one',title='Agent memory'):
    return {'id':identity,'kind':'web','title':title,'authors':'Fixture','url':'https://example.org/'+identity,
            'eligible':False,'state':'candidate','license':'unknown','reason':'not checked','description':'Synthetic agent memory and tool usage.'}


def state():
    return {'query':'了解 Agent 的記憶与工具','mode':'self-study','material_title':'Synthetic','approved_scope':None,
            'search_state':flow.new_state(),'candidates':[]}


def inspect(c):
    return {**c,'eligible':True,'access':{'status':'read','method':'html','characters':len('Agent memory stores observations.')},
            '_reading_text':'Agent memory stores observations.'}


def drive(value,provider=review_provider):
    stages=[]
    for _ in range(100):
        stages.append(value['search_state']['stage'])
        value={**value,**flow.advance(value,provider)}
        if value['status']=='selecting':return value,stages
    pytest.fail('workflow did not finish')


def test_stages_are_resumable_and_all_planned_languages_are_searched(monkeypatch):
    calls=[]
    def provider(path,body):
        if body['request']['task']=='plan':return {'summary':'agent','topics':['Agent memory'],
            'searches':[{'query':'LLM agent memory','topics':[0],'reason':'English'},{'query':'智慧代理 記憶 原理','topics':[0],'reason':'Chinese'}]}
        return review_provider(path,body)
    monkeypatch.setattr(flow.research_sources,'search',lambda q,c:(calls.append(q) or [candidate()],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',inspect)
    saved={**state(),**flow.advance(state(),provider)}
    assert saved['search_state']['stage']=='searching' and calls==[]
    done,stages=drive(deepcopy(saved),provider)
    assert calls==['LLM agent memory','智慧代理 記憶 原理']
    assert set(stages)>={'searching','screening','reading','assessing','following_up','organizing'}
    assert done['search_state']['finish_reason']=='covered'
    assert done['candidates'][0]['review']['quotes'][0]['quote']=='Agent memory stores observations.'
    assert '_reading_text' not in done['candidates'][0]
    assert flow.progress(done['search_state'],done['candidates'])['read_count']==1


def test_missing_topic_triggers_another_round_without_user_pagination(monkeypatch):
    queries=[];rounds=[]
    def search(q,c):queries.append(q);return [candidate('initial' if len(queries)==1 else 'follow-up')],None,[]
    def provider(path,body):
        request=body['request']
        if request['task']=='follow_up':
            rounds.append(request['round']);result=review_provider(path,body)
            if request['round']==1:
                result['coverage'][0].update(status='partial',reason='missing case')
                result['searches']=[{'query':'agent memory worked example','topics':[0],'reason':'find missing case'}]
            return result
        return review_provider(path,body)
    monkeypatch.setattr(flow.research_sources,'search',search);monkeypatch.setattr(flow,'inspect_candidate',inspect)
    done,_=drive(state(),provider)
    assert queries==['queue tutorial','agent memory worked example'] and rounds==[1,2]
    assert done['search_state']['round']==2 and len(done['candidates'])==2


def test_a_long_source_is_read_in_full_not_cut_off_after_first_chunk(monkeypatch):
    text='Introductory agent details. '*600+'Final limitation: memory can retain an incorrect observation.'
    seen=[]
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate()],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:{**c,'eligible':True,'_reading_text':text,'access':{'status':'read','characters':len(text)}})
    def provider(path,body):
        if body['request']['task']=='read':seen.append((body['request']['source']['offset'],''.join(p['text'] for p in body['request']['passages'])))
        return review_provider(path,body)
    done,_=drive(state(),provider)
    assert ''.join(t for _,t in sorted(seen))==text
    assert done['candidates'][0]['_read_offset']==len(text)


def test_unknown_passage_cannot_become_a_completed_result(monkeypatch):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate()],None,[]));monkeypatch.setattr(flow,'inspect_candidate',inspect)
    def provider(path,body):
        r=review_provider(path,body)
        if body['request']['task']=='read':r['supports'][0]['passages']=[999]
        return r
    with pytest.raises(SourceError,match='RESEARCH_REVIEW_FAILED'):drive(state(),provider)


def test_metadata_and_body_disagreement_does_not_recommend_off_topic_content(monkeypatch):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate('cpp','C++ memory')],None,[]));monkeypatch.setattr(flow,'inspect_candidate',inspect)
    def provider(path,body):
        r=review_provider(path,body)
        if body['request']['task']=='read':r.update(relevance='unrelated',reason='C++ memory is not agent memory',supports=[])
        return r
    done,_=drive(state(),provider)
    assert done['candidates'][0]['review']['relevance']=='unrelated' and not done['candidates'][0]['eligible']
    assert done['search_state']['finish_reason']=='gaps_remaining'


def test_repeated_query_without_more_results_finishes_with_honest_gap(monkeypatch):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([],None,[]))
    def provider(path,body):
        r=review_provider(path,body)
        if body['request']['task']=='follow_up':r['searches']=[{'query':'queue tutorial','topics':[0],'reason':'no new direction'}]
        return r
    done,_=drive(state(),provider)
    assert done['search_state']['finish_reason']=='gaps_remaining' and done['candidates']==[]


def test_reference_coverage_is_separate_from_import_permission(monkeypatch):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate()],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:{**inspect(c),'eligible':False})
    def provider(path,body):
        result=review_provider(path,body)
        if body['request']['task']=='follow_up':result['coverage'][0]['status']='covered'
        return result
    done,_=drive(state(),provider)
    assert done['search_state']['finish_reason']=='references_only'
    assert done['search_state']['coverage'][0]['status']=='missing'
    assert done['search_state']['coverage'][0]['importable_source_ids']==[]
    assert not done['candidates'][0]['eligible']


def test_explicit_retry_after_is_saved_instead_of_busy_waiting(monkeypatch):
    monkeypatch.setattr(flow.time,'time',lambda:1000)
    value=state();value['search_state'].update(stage='searching',topics=['queue'],coverage=[],
        searches=[{'query':'queue','topics':[0],'status':'pending','cursor':None,'pages':0,'seen_ids':[]}])
    def fail(*args):raise flow.research_sources.SourceAccessError('RESEARCH_RATE_LIMITED',retry_at=1600)
    monkeypatch.setattr(flow.research_sources,'search',fail)
    for _ in range(6):value.update(flow.advance(value,review_provider))
    assert value['search_state']['waiting_until']==1600
    assert value['status']=='searching'
    assert flow.progress(value['search_state'],value['candidates'])['waiting_until']==1600


def test_all_selected_deep_reads_finish_before_followup(monkeypatch):
    read=[]
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate('first'),candidate('background'),candidate('specific')],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:(read.append(c['id']) or inspect(c)))
    def provider(path,body):
        result=review_provider(path,body)
        if body['request']['task']=='screen':result['items'][1]['relevance']='unrelated'
        if body['request']['task']=='follow_up':assert set(read)=={'first','background','specific'}
        return result
    done,_=drive(state(),provider)
    assert len(read)==3
    assert all(c['review']['phase']=='body' for c in done['candidates'])
    assert done['search_state']['finish_reason']=='covered'


def test_resuming_previously_completed_research_reads_remaining_candidates(monkeypatch):
    value=state();value['candidates']=[candidate('unread')]
    value['candidates'][0]['review']={'phase':'metadata','relevance':'unrelated','topics':[],'reason':'initial guess','quotes':[]}
    value['search_state'].update(stage='following_up',topics=['queue'],continue_requested=True)
    monkeypatch.setattr(flow,'inspect_candidate',inspect)
    done,_=drive(value)
    assert done['candidates'][0]['review']['phase']=='body'


def test_public_entry_proposals_are_site_independent_and_must_preserve_source_origin():
    row=candidate();row['url']='https://learning.example/people/author/post'
    row['access']={'attempts':[{'url':row['url'],'outcome':'failed','http_status':403,'error_code':'RESEARCH_SOURCE_BLOCKED'}]}
    def call(path,body):
        assert body['request']['task']=='find_entries'
        assert body['request']['allowed_hosts']==['learning.example']
        return {'entries':[{'url':'https://learning.example/people/author/atom.xml','kind':'rss','reason':'public feed'},
                           {'url':'https://unrelated.example/proxy','kind':'document','reason':'not original source'},
                           {'url':row['url'],'kind':'document','reason':'already attempted'}]}
    assert flow.public_entries(row,call)==[{'url':'https://learning.example/people/author/atom.xml','kind':'rss','reason':'public feed'}]


def test_review_batches_do_not_drop_previously_verified_sources(monkeypatch):
    monkeypatch.setattr(flow,'SCREEN_BATCH',2)
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate(str(i)) for i in range(5)],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',inspect)
    batches=[]
    def provider(path,body):
        if body['request']['task']=='follow_up':
            batches.append(len(body['request']['items']))
            assert all('quotes' not in item['review'] for item in body['request']['items'])
        return review_provider(path,body)
    done,_=drive(state(),provider)
    assert batches==[2,2,1]
    assert len(done['search_state']['coverage'][0]['source_ids'])==5


def test_failed_fetch_does_not_skip_the_next_candidate(monkeypatch):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate('blocked'),candidate('read')],None,[]))
    def fetch(c):
        if c['id']=='blocked':return {**c,'access':{'status':'unavailable','attempts':[]},'eligible':False}
        return inspect(c)
    monkeypatch.setattr(flow,'inspect_candidate',fetch)
    done,_=drive(state())
    assert next(c for c in done['candidates'] if c['id']=='read')['review']['phase']=='body'
    assert next(c for c in done['candidates'] if c['id']=='blocked')['entry_lookup_done']


def test_deferred_source_is_not_counted_finished_and_is_retried_after_other_sources(monkeypatch):
    value=state();deferred=candidate('deferred');deferred['review']={'phase':'metadata','relevance':'direct','topics':[0],'reason':'fixture','quotes':[]}
    deferred['screening']={'decision':'read','reason':'fixture','basis':'abstract'}
    deferred['access']={'status':'unavailable','retry_at':1600,'attempts':[]}
    other=deepcopy(deferred);other.update(id='other',url='https://example.org/other');other.pop('access')
    value['candidates']=[deferred,other];value['search_state'].update(stage='reading',topics=['queue'])
    read=[]
    monkeypatch.setattr(flow.time,'time',lambda:1000)
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:read.append(c['id']) or inspect(c))
    for _ in range(10):
        value.update(flow.advance(value,review_provider))
        if value['search_state'].get('waiting_until'):break
    assert read==['other'] and value['search_state']['waiting_until']==1600
    assert flow.progress(value['search_state'],value['candidates'])['completed']==1
    monkeypatch.setattr(flow.time,'time',lambda:1600)
    done,_=drive(value)
    assert read==['other','deferred']
    assert all(c['review']['phase']=='body' for c in done['candidates'])


def test_final_organization_merges_identical_document_entries_without_losing_review_records():
    value=state();first=candidate('first');second=candidate('second')
    for c in [first,second]:
        c.update(url='https://arxiv.org/abs/2401.12345',kind='paper',eligible=True,inspection_sha256='a'*64,
                 review={'phase':'body','relevance':'direct','reason':'reviewed','quotes':[{'topic':0,'quote':'An agent uses tools.'}],'topics':[0]})
    first['discovered_by']=['OpenAlex'];second['discovered_by']=['SearXNG']
    value['candidates']=[first,second];value['search_state'].update(stage='organizing',finish_reason='covered',
       coverage=[{'source_ids':['first','second'],'importable_source_ids':['first','second']}])
    result=flow.advance(value,review_provider)
    assert len(result['candidates'])==2
    assert result['candidates'][0]['discovered_by']==['OpenAlex','SearXNG']
    assert result['candidates'][1]['duplicate_of']=='first' and not result['candidates'][1]['eligible']
    assert result['candidates'][1]['review']['quotes']==second['review']['quotes']
    assert result['search_state']['coverage'][0]['source_ids']==['first']
    assert second['eligible'] and 'duplicate_of' not in second


def test_duplicate_organization_keeps_importable_copy_and_combines_verified_support():
    value=state();reference=candidate('reference');usable=candidate('usable')
    for c in [reference,usable]:
        c.update(url='https://arxiv.org/abs/2401.12345',kind='paper',inspection_sha256='b'*64,
                 review={'phase':'body','relevance':'direct','reason':'read','quotes':[{'topic':0,'quote':'The agent observes results.'}],'topics':[0]})
    usable.update(eligible=True,review={**usable['review'],'relevance':'related','quotes':[{'topic':1,'quote':'The agent calls a tool.'}],'topics':[1]})
    value['candidates']=[reference,usable];value['search_state'].update(stage='organizing',finish_reason='covered',coverage=[])
    result=flow.advance(value,review_provider)
    kept=next(c for c in result['candidates'] if c['id']=='usable')
    assert kept['eligible'] and set(kept['review']['topics'])=={0,1}
    assert next(c for c in result['candidates'] if c['id']=='reference')['duplicate_of']=='usable'


def test_unavailable_fulltext_with_only_viewer_text_is_not_reported_as_reviewed_content(monkeypatch):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate()],None,[]))
    def fetch(c):
        result=inspect(c);result.update(eligible=False,_reading_text='Full-text PDF viewer')
        result['access'].update(fulltext_unavailable=True,attempts=[{'url':c['url'],'outcome':'failed','error_code':'RESEARCH_SOURCE_BLOCKED'}])
        return result
    monkeypatch.setattr(flow,'inspect_candidate',fetch)
    def provider(path,body):
        result=review_provider(path,body)
        if body['request']['task']=='read':result.update(relevance='unrelated',supports=[])
        return result
    done,_=drive(state(),provider)
    assert done['candidates'][0]['access']['status']=='unavailable'
    assert done['candidates'][0]['review']['phase']=='unavailable'
    assert flow.progress(done['search_state'],done['candidates'])['read_count']==0


def test_summary_screening_routes_references_and_exclusions_without_body_reads(monkeypatch):
    fetched=[]
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate('deep'),candidate('reference'),candidate('excluded')],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:fetched.append(c['id']) or inspect(c))
    def provider(path,body):
        result=review_provider(path,body)
        if body['request']['task']=='screen':
            assert all(i['preview'] for i in body['request']['items'])
            result['items'][1].update(decision='reference',relevance='related')
            result['items'][2].update(decision='exclude',relevance='unrelated')
        return result
    done,_=drive(state(),provider)
    assert fetched==['deep']
    assert {c['id']:c['screening']['decision'] for c in done['candidates']}=={'deep':'read','reference':'reference','excluded':'exclude'}
    p=flow.progress(done['search_state'],done['candidates'])
    assert (p['screened_count'],p['deep_read_count'],p['reference_count'],p['excluded_count'])==(3,1,1,1)


def test_uncertain_summary_expands_then_checks_body_when_information_is_still_missing(monkeypatch):
    previews=[];fetched=[]
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate()],None,[]))
    def preview(c,*,expand=False):
        previews.append(expand)
        return {'preview':{'status':'missing','kind':'unavailable','expanded':expand},'_preview_text':''}
    monkeypatch.setattr(flow,'prepare_preview',preview)
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:fetched.append(c['id']) or inspect(c))
    def provider(path,body):
        result=review_provider(path,body)
        if body['request']['task']=='screen':result['items'][0]['decision']='uncertain'
        return result
    done,_=drive(state(),provider)
    assert previews==[False,True] and fetched==['one']
    assert done['candidates'][0]['review']['phase']=='body'


def test_selected_section_preserves_source_passages_and_does_not_claim_full_read(monkeypatch):
    text='Background unrelated to the learning task.\n'*400+'Agent memory stores observations.\n'*400+'Appendix with technical tables.\n'*400
    windows=flow.reading_windows(text,size=flow.READ_CHARS);seen=[]
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate()],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:{**inspect(c),'_reading_text':text})
    def provider(path,body):
        request=body['request']
        if request['task']=='locate':return {'sections':[{'index':s['index'],'decision':'read' if s['index']==1 else 'skip','reason':'Relevant section' if s['index']==1 else 'Unrelated appendix'} for s in request['sections']]}
        if request['task']=='read':seen.append(request['source']['offset'])
        return review_provider(path,body)
    done,_=drive(state(),provider);c=done['candidates'][0]
    assert seen==[windows[1]['start']] and c['review']['scope']=='sections'
    assert all(q['quote'] in text[windows[1]['start']:windows[1]['end']] for q in c['review']['quotes'])


def test_gap_check_promotes_reference_without_restarting_completed_source(monkeypatch):
    fetched=[];checks=[]
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate('initial'),candidate('reference')],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:fetched.append(c['id']) or inspect(c))
    def provider(path,body):
        request=body['request'];result=review_provider(path,body)
        if request['task']=='screen':result['items'][1].update(decision='reference',relevance='related')
        if request['task']=='follow_up':
            checks.append(request['round'])
            if request['round']==1:
                result['coverage'][0]['status']='partial';result['read_next']=[0]
        return result
    done,_=drive(state(),provider)
    assert fetched==['initial','reference'] and checks==[1,2]
    assert done['search_state']['finish_reason']=='covered'


def test_paused_work_keeps_finished_reviews_and_screens_pending_sources(monkeypatch):
    value=state();value['search_state'].update(stage='collecting',topics=['queue'])
    finished=inspect(candidate('finished'));finished.pop('_reading_text')
    finished['screening']={'decision':'read','reason':'saved decision','basis':'abstract'}
    finished['review']={'phase':'body','relevance':'direct','reason':'saved','quotes':[{'topic':0,'quote':'Agent memory stores observations.'}],'topics':[0]}
    unread=candidate('unread');unread['review']={'phase':'metadata','relevance':'unrelated','reason':'old title guess','topics':[]}
    value['candidates']=[finished,unread];fetched=[]
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:fetched.append(c['id']) or inspect(c))
    done,_=drive(value)
    assert fetched==['unread']
    assert next(c for c in done['candidates'] if c['id']=='finished')['review']==finished['review']


def test_gap_in_selected_sections_expands_reading_to_the_full_document(monkeypatch):
    text='Agent observations and tool results.\n'*900
    fetched=[];scopes=[]
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate()],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:fetched.append(c['id']) or {**inspect(c),'_reading_text':text})
    def provider(path,body):
        request=body['request'];task=request['task']
        if task=='locate':return {'sections':[{'index':s['index'],'decision':'read' if s['index']==1 else 'skip','reason':'Selected section' if s['index']==1 else 'Unrelated appendix'} for s in request['sections']]}
        if task=='read':scopes.append(request['source']['reading_scope'])
        result=review_provider(path,body)
        if task=='follow_up' and request['round']==1:
            assert request['unread'][0]['scope']=='sections'
            assert request['unread'][0]['skipped_sections']
            assert all(s['reason']=='Unrelated appendix' for s in request['unread'][0]['skipped_sections'])
            result['coverage'][0]['status']='partial';result['read_next']=[0]
        return result
    done,_=drive(state(),provider)
    assert fetched==['one','one'] and scopes[0]=='sections' and set(scopes[1:])=={'full'}
    assert done['candidates'][0]['review']['scope']=='full'
    assert done['candidates'][0]['_read_offset']==len(text)


@pytest.mark.parametrize('fault',['duplicate','missing','foreign'])
def test_invalid_section_coverage_cannot_silently_skip_body(monkeypatch,fault):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate()],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:{**inspect(c),'_reading_text':'Agent tool loop. '*1800})
    def provider(path,body):
        result=review_provider(path,body)
        if body['request']['task']=='locate':
            if fault=='duplicate':result['sections'][0]['index']=result['sections'][1]['index']
            elif fault=='missing':result['sections'].pop()
            else:result['sections'][0]['index']=999
        return result
    with pytest.raises(SourceError,match='RESEARCH_REVIEW_FAILED'):drive(state(),provider)


def test_uncertain_section_is_read_with_required_context(monkeypatch):
    text='Agent tool loop. '*1000;seen=[]
    monkeypatch.setattr(flow.research_sources,'search',lambda *a:([candidate()],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:{**inspect(c),'_reading_text':text})
    def provider(path,body):
        result=review_provider(path,body)
        if body['request']['task']=='locate':result['sections'][0].update(decision='uncertain',reason='Needs surrounding context')
        if body['request']['task']=='read':seen.append(body['request']['source']['offset'])
        return result
    done,_=drive(state(),provider)
    assert seen==[w['start'] for w in flow.reading_windows(text,size=flow.READ_CHARS)]
    assert done['candidates'][0]['review']['scope']=='full'


@pytest.mark.parametrize('same_content',[True,False])
@pytest.mark.parametrize('prior_scope',['full','sections'])
def test_version_alias_reuses_full_review_only_when_bytes_match(monkeypatch,same_content,prior_scope):
    value=state();value['search_state'].update(stage='reading',topics=['queue'])
    first=candidate('original');first.update(kind='paper',url='https://arxiv.org/abs/2401.12345',inspection_sha256='a'*64,screening={'decision':'read','basis':'abstract','reason':'match'},review={'phase':'body','scope':prior_scope,'relevance':'direct','reason':'verified','quotes':[{'topic':0,'quote':'Agent memory stores observations.'}],'topics':[0]})
    second=candidate('version');second.update(kind='paper',url='https://arxiv.org/abs/2401.12345v1',screening={'decision':'read','basis':'abstract','reason':'match'})
    value['candidates']=[first,second];reads=[]
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:{**inspect(c),'inspection_sha256':('a' if same_content else 'b')*64})
    def provider(path,body):
        if body['request']['task']=='read':reads.append(body['request']['source']['url'])
        return review_provider(path,body)
    done,_=drive(value,provider);c=next(c for c in done['candidates'] if c['id']=='version')
    reused=same_content and prior_scope=='full'
    assert reads==([] if reused else [second['url']])
    assert c['review'].get('reused_from')==('original' if reused else None)
    assert bool(next((c for c in done['candidates'] if c.get('duplicate_of')),None))==same_content
