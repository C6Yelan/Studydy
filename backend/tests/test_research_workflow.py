from copy import deepcopy
import pytest
from runtime import research_workflow as flow
from runtime.source_normalization import SourceError
from research_fixtures import review_provider, requested_coverage


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


def test_stages_are_resumable_and_sufficient_evidence_stops_unneeded_searches(monkeypatch):
    calls=[]
    def provider(path,body):
        if body['request']['task']=='plan':return {'summary':'agent','topics':['Agent memory'],
            'searches':[{'query':'LLM agent memory','channel':'web','topics':[0],'reason':'English'},{'query':'智慧代理 記憶 原理','channel':'web','topics':[0],'reason':'Chinese'}]}
        return review_provider(path,body)
    monkeypatch.setattr(flow.research_sources,'search',lambda q,c,**kwargs:(calls.append(q) or [candidate()],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',inspect)
    saved={**state(),**flow.advance(state(),provider)}
    assert saved['search_state']['stage']=='searching' and calls==[]
    done,stages=drive(deepcopy(saved),provider)
    assert calls==['LLM agent memory']
    assert set(stages)>={'searching','screening','reading','assessing','following_up','organizing'}
    assert done['search_state']['finish_reason']=='covered'
    assert done['candidates'][0]['review']['quotes'][0]['quote']=='Agent memory stores observations.'
    assert '_reading_text' not in done['candidates'][0]
    assert flow.progress(done['search_state'],done['candidates'])['read_count']==1


def test_missing_topic_triggers_another_round_without_user_pagination(monkeypatch):
    queries=[];rounds=[]
    def search(q,c,**kwargs):queries.append(q);return [candidate('initial' if len(queries)==1 else 'follow-up')],None,[]
    def provider(path,body):
        request=body['request']
        if request['task']=='follow_up':
            rounds.append(request['round']);result=review_provider(path,body)
            if request['round']==1:
                result['coverage'][0].update(status='partial',reason='missing case')
                result['searches']=[{'query':'agent memory worked example','channel':'web','topics':[0],'reason':'find missing case'}]
            return result
        return review_provider(path,body)
    monkeypatch.setattr(flow.research_sources,'search',search);monkeypatch.setattr(flow,'inspect_candidate',inspect)
    done,_=drive(state(),provider)
    assert queries==['queue tutorial','agent memory worked example'] and rounds==[1,2]
    assert done['search_state']['round']==2 and len(done['candidates'])==2


def test_a_long_source_is_read_in_full_not_cut_off_after_first_chunk(monkeypatch):
    text='Introductory agent details. '*600+'Final limitation: memory can retain an incorrect observation.'
    seen=[]
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate()],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:{**c,'eligible':True,'_reading_text':text,'access':{'status':'read','characters':len(text)}})
    def provider(path,body):
        if body['request']['task']=='read':seen.append((body['request']['source']['offset'],''.join(p['text'] for p in body['request']['passages'])))
        return review_provider(path,body)
    done,_=drive(state(),provider)
    assert ''.join(t for _,t in sorted(seen))==text
    assert done['candidates'][0]['_read_offset']==len(text)


def test_unknown_passage_cannot_become_a_completed_result(monkeypatch):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate()],None,[]));monkeypatch.setattr(flow,'inspect_candidate',inspect)
    def provider(path,body):
        r=review_provider(path,body)
        if body['request']['task']=='read':r['supports'][0]['passages']=[999]
        return r
    with pytest.raises(SourceError,match='RESEARCH_REVIEW_FAILED'):drive(state(),provider)


def test_metadata_and_body_disagreement_does_not_recommend_off_topic_content(monkeypatch):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate('cpp','C++ memory')],None,[]));monkeypatch.setattr(flow,'inspect_candidate',inspect)
    def provider(path,body):
        r=review_provider(path,body)
        if body['request']['task']=='read':r.update(relevance='unrelated',reason='C++ memory is not agent memory',supports=[])
        return r
    done,_=drive(state(),provider)
    assert done['candidates'][0]['review']['relevance']=='unrelated' and not done['candidates'][0]['eligible']
    assert done['search_state']['finish_reason']=='gaps_remaining'


def test_repeated_query_without_more_results_finishes_with_honest_gap(monkeypatch):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([],None,[]))
    def provider(path,body):
        r=review_provider(path,body)
        if body['request']['task']=='follow_up':r['searches']=[{'query':'queue tutorial','channel':'web','topics':[0],'reason':'no new direction'}]
        return r
    done,_=drive(state(),provider)
    assert done['search_state']['finish_reason']=='gaps_remaining' and done['candidates']==[]


def test_reference_coverage_is_separate_from_import_permission(monkeypatch):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate()],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:{**inspect(c),'eligible':False})
    def provider(path,body):
        assert body['request']['task'] not in ('locate','read')
        result=review_provider(path,body)
        if body['request']['task']=='follow_up':
            assert body['request']['unread']==[]
            assert body['request']['reference_only']['count']==1
            assert body['request']['reference_only']['reviewed_count']==0
            assert result['coverage']==[]  # 無可匯入正文，不請模型宣稱教材涵蓋。
        return result
    done,_=drive(state(),provider)
    assert done['search_state']['finish_reason']=='references_only'
    assert done['search_state']['coverage'][0]['status']=='missing'
    assert done['search_state']['coverage'][0]['importable_source_ids']==[]
    assert not done['candidates'][0]['eligible']
    assert done['candidates'][0]['review']['phase']=='metadata'


def test_explicit_retry_after_is_saved_instead_of_busy_waiting(monkeypatch):
    monkeypatch.setattr(flow.time,'time',lambda:1000)
    value=state();value['search_state'].update(stage='searching',topics=['queue'],coverage=[],
        searches=[{'query':'queue','channel':'web','topics':[0],'status':'pending','cursor':None,'pages':0,'seen_ids':[]}])
    def fail(*args,**kwargs):raise flow.research_sources.SourceAccessError('RESEARCH_RATE_LIMITED',retry_at=1600)
    monkeypatch.setattr(flow.research_sources,'search',fail)
    for _ in range(6):value.update(flow.advance(value,review_provider))
    assert value['search_state']['waiting_until']==1600
    assert value['status']=='searching'
    assert flow.progress(value['search_state'],value['candidates'])['waiting_until']==1600


def test_all_selected_deep_reads_finish_before_followup(monkeypatch):
    read=[]
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate('first'),candidate('background'),candidate('specific')],None,[]))
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
    monkeypatch.setattr(flow,'READ_CHECK_BATCH',5)
    monkeypatch.setattr(flow,'SCREEN_BATCH',2)
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate(str(i)) for i in range(5)],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',inspect)
    batches=[]
    def provider(path,body):
        if body['request']['task']=='follow_up':
            batches.append(len(body['request']['items']))
            assert all(item['evidence'] and 'review' not in item for item in body['request']['items'])
        return review_provider(path,body)
    done,_=drive(state(),provider)
    assert batches==[5]
    assert len(done['search_state']['coverage'][0]['source_ids'])==5


def test_failed_fetch_does_not_skip_the_next_candidate(monkeypatch):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate('blocked'),candidate('read')],None,[]))
    def fetch(c):
        if c['id']=='blocked':return {**c,'access':{'status':'unavailable','attempts':[]},'eligible':False}
        return inspect(c)
    monkeypatch.setattr(flow,'inspect_candidate',fetch)
    done,_=drive(state())
    assert next(c for c in done['candidates'] if c['id']=='read')['review']['phase']=='body'
    assert next(c for c in done['candidates'] if c['id']=='blocked')['entry_lookup_done']


@pytest.mark.parametrize("sufficient", [True, False])
def test_deferred_source_waits_only_after_checking_available_evidence(monkeypatch,sufficient):
    value=state();deferred=candidate('deferred');deferred['review']={'phase':'metadata','relevance':'direct','topics':[0],'reason':'fixture','quotes':[]}
    deferred['screening']={'decision':'read','reason':'fixture','basis':'abstract'}
    deferred['access']={'status':'unavailable','retry_at':1600,'attempts':[]}
    other=deepcopy(deferred);other.update(id='other',url='https://example.org/other');other.pop('access')
    value['candidates']=[deferred,other];value['search_state'].update(stage='reading',topics=['queue'])
    read=[]
    monkeypatch.setattr(flow.time,'time',lambda:1000)
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:read.append(c['id']) or inspect(c))
    def provider(path,body):
        result=review_provider(path,body)
        if body['request']['task']=='follow_up' and not sufficient and read==['other']:
            result['coverage'][0].update(status='partial',reason='Need deferred source')
            result['read_next']=[];result['searches']=[]
        return result
    for _ in range(10):
        value.update(flow.advance(value,provider))
        if value['search_state'].get('waiting_until') or value['status']=='selecting':break
    if sufficient:
        assert value['status']=='selecting' and read==['other']
        assert next(c for c in value['candidates'] if c['id']=='deferred')['review']['phase']=='metadata'
        return
    assert read==['other'] and value['search_state']['waiting_until']==1600
    assert flow.progress(value['search_state'],value['candidates'])['completed']==1
    monkeypatch.setattr(flow.time,'time',lambda:1600)
    done,_=drive(value,provider)
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
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate()],None,[]))
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
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate('deep'),candidate('reference'),candidate('excluded')],None,[]))
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
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate()],None,[]))
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
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate()],None,[]))
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
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate('initial'),candidate('reference')],None,[]))
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


def test_gap_in_selected_sections_only_reads_new_ranges(monkeypatch):
    text='Agent observations and tool results.\n'*900
    fetched=[];scopes=[];offsets=[]
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate()],None,[]))
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:fetched.append(c['id']) or {**inspect(c),'_reading_text':text})
    def provider(path,body):
        request=body['request'];task=request['task']
        if task=='locate':return {'sections':[{'index':s['index'],'decision':'read' if s['index']==1 else 'skip','reason':'Selected section' if s['index']==1 else 'Unrelated appendix'} for s in request['sections']]}
        if task=='read':scopes.append(request['source']['reading_scope']);offsets.append(request['source']['offset'])
        result=review_provider(path,body)
        if task=='follow_up' and request['round']==1:
            assert request['unread'][0]['scope']=='sections'
            assert request['unread'][0]['skipped_sections']
            assert all(s['reason']=='Unrelated appendix' for s in request['unread'][0]['skipped_sections'])
            result['coverage'][0]['status']='partial';result['read_next']=[0]
        return result
    done,_=drive(state(),provider)
    assert fetched==['one','one'] and set(scopes)=={'sections'}
    assert len(offsets)==len(set(offsets))
    assert done['candidates'][0]['review']['scope']=='sections'
    assert len(done['candidates'][0]['review']['read_ranges'])==len(offsets)


@pytest.mark.parametrize('fault',['duplicate','missing','foreign'])
def test_invalid_section_coverage_cannot_silently_skip_body(monkeypatch,fault):
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate()],None,[]))
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
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kwargs:([candidate()],None,[]))
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
def test_version_alias_reuses_selected_review_only_when_bytes_match(monkeypatch,same_content,prior_scope):
    value=state();value['search_state'].update(stage='reading',topics=['queue'])
    first=candidate('original');first.update(kind='paper',url='https://arxiv.org/abs/2401.12345',inspection_sha256='a'*64,screening={'decision':'read','basis':'abstract','reason':'match'},review={'phase':'body','scope':prior_scope,'relevance':'direct','reason':'verified','quotes':[{'topic':0,'quote':'Agent memory stores observations.'}],'topics':[0]})
    second=candidate('version');second.update(kind='paper',url='https://arxiv.org/abs/2401.12345v1',screening={'decision':'read','basis':'abstract','reason':'match'})
    value['candidates']=[first,second];reads=[]
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:{**inspect(c),'inspection_sha256':('a' if same_content else 'b')*64})
    def provider(path,body):
        if body['request']['task']=='read':reads.append(body['request']['source']['url'])
        return review_provider(path,body)
    done,_=drive(value,provider);c=next(c for c in done['candidates'] if c['id']=='version')
    reused=same_content
    assert reads==([] if reused else [second['url']])
    assert c['review'].get('reused_from')==('original' if reused else None)
    assert bool(next((c for c in done['candidates'] if c.get('duplicate_of')),None))==same_content


def test_old_workflow_is_not_silently_upgraded_or_sent_to_model():
    value=state();value['search_state'].pop('schema')
    def forbidden(*args,**kwargs):raise AssertionError('old workflow must not call a model')
    with pytest.raises(SourceError,match='MATERIAL_FORMAT_UNSUPPORTED'):
        flow.advance(value,forbidden)


@pytest.mark.parametrize("already_planned", [True, False])
def test_gap_explores_new_search_before_expanding_a_read_source(already_planned):
    value=state();source=inspect(candidate())
    source['screening']={'decision':'read','reason':'Related','basis':'abstract'}
    source['review']={'phase':'body','scope':'sections','relevance':'direct','reason':'Saved',
                     'quotes':[{'topic':0,'quote':'Agent memory stores observations.'}],'topics':[0]}
    value['candidates']=[source]
    value['search_state'].update(stage='following_up',topics=['Agent memory and tools'],searches=[
        {'query':'Agent tools execution','channel':'web','topics':[0],'reason':'Unexplored mechanism',
         'status':'pending','pages':0,'cursor':None,'seen_ids':[]}] if already_planned else [])
    def provider(path,body):
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'partial','reason':'Missing tool execution','sources':[0]}]),
                'read_next':[0],'searches':[] if already_planned else [
                    {'query':'Agent tools execution','channel':'web','topics':[0],'reason':'Unexplored mechanism'}]}
    result=flow.advance(value,provider)
    assert result['search_state']['stage']=='searching'
    assert result['candidates'][0]['review']==source['review']
    result['search_state'].update(stage='following_up',stalled_checks=1)
    resumed=flow.advance({**value,**result},provider)
    assert resumed['search_state']['stage']=='searching'


def test_pending_search_without_new_reading_reuses_existing_gap_assessment():
    value=state()
    value['search_state'].update(stage='following_up',topics=['Agent tools'],
        searches=[{'query':'Agent tools execution','topics':[0],'channel':'web',
                   'status':'pending','pages':0,'cursor':None}])
    def provider(*args):
        return {'coverage':requested_coverage(args[1],[{'topic':0,'status':'missing','reason':'No supporting text','sources':[]}]),
                'read_next':[],'searches':[]}
    first={**value,**flow.advance(value,provider)}
    coverage=deepcopy(first['search_state']['coverage'])
    # 不可匯入的頁面也會增加已讀數，但沒有新教材證據。
    first['search_state'].update(stage='following_up',read_since_check=4)
    def forbidden(*args):
        pytest.fail('Unchanged evidence must not trigger another model call')
    result=flow.advance(first,forbidden)
    assert result['search_state']['stage']=='searching'
    assert result['search_state']['coverage']==coverage
    source=inspect(candidate())
    source.update(screening={'decision':'read','basis':'abstract','reason':'New source'},
        review={'phase':'body','scope':'full','relevance':'direct','reason':'New evidence','topics':[0],
                'quotes':[{'topic':0,'quote':'Agent memory stores observations.'}]})
    resumed={**first,**result,'candidates':[source]};resumed['search_state']['stage']='following_up'
    calls=[]
    def changed(*args):
        calls.append(True)
        return {'coverage':requested_coverage(args[1],[{'topic':0,'status':'partial','reason':'New evidence','sources':[0]}]),
                'read_next':[],'searches':[]}
    updated=flow.advance(resumed,changed)
    assert calls==[True] and updated['search_state']['coverage'][0]['status']=='partial'
    final={**resumed,**updated}
    final['search_state'].update(stage='following_up')
    final['search_state']['searches'][0]['status']='searched'
    def plan_only(path,body):
        payload=body['request']
        assert payload['unchanged_evidence'] and payload['items']==[]
        assert payload['previous_coverage'][0]['status']=='partial'
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'missing','reason':'No text in this batch','sources':[]}]),
                'read_next':[],'searches':[{'query':'Agent execution','channel':'web','topics':[0],'reason':'Fill gap'}]}
    planned=flow.advance(final,plan_only)
    assert planned['search_state']['coverage']==updated['search_state']['coverage']
    assert planned['search_state']['stage']=='searching'
    assert planned['search_state']['searches'][-1]['query']=='Agent execution'


def test_search_reordering_does_not_invalidate_verified_evidence():
    value=state();value['search_state'].update(stage='following_up',topics=['Agent tools'],searches=[
        {'query':'Agent execution','topics':[0],'channel':'web','status':'pending','pages':0,'cursor':None}])
    for identity in ['first','second']:
        source=inspect(candidate(identity))
        source.update(screening={'decision':'read','basis':'abstract','reason':'Saved'},
            review={'phase':'body','scope':'full','relevance':'direct','reason':'Saved','topics':[0],
                    'quotes':[{'topic':0,'quote':'Agent memory stores observations.'},
                              {'topic':0,'quote':'The framework executes tool calls.'}]})
        value['candidates'].append(source)
    def provider(*args):
        return {'coverage':requested_coverage(args[1],[{'topic':0,'status':'partial','reason':'Still need retries','sources':[0,1]}]),
                'read_next':[],'searches':[]}
    saved={**value,**flow.advance(value,provider)}
    saved['candidates'].reverse()
    for source in saved['candidates']:source['review']['quotes'].reverse()
    saved['search_state']['stage']='following_up'
    result=flow.advance(saved,lambda *args:pytest.fail('Only ordering changed'))
    assert result['search_state']['stage']=='searching'
    assert result['search_state']['coverage']==saved['search_state']['coverage']


def test_new_search_results_are_read_before_old_screened_candidates(monkeypatch):
    value=state();old=candidate('old')
    old.update(preview={'kind':'abstract'},screening={'decision':'read','basis':'abstract','reason':'Related'},
               review={'phase':'metadata','relevance':'direct','topics':[0],'reason':'Related','quotes':[]})
    value['candidates']=[old]
    value['search_state'].update(stage='searching',topics=['Agent memory'],searches=[
        {'query':'Agent memory new direction','channel':'web','topics':[0],'reason':'New angle',
         'status':'pending','pages':0,'cursor':None,'seen_ids':[]}])
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**k:([candidate('fresh')],None,[]))
    fetched=[]
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:fetched.append(c['id']) or inspect(c))
    result={**value,**flow.advance(value,review_provider)}
    for _ in range(12):
        result={**result,**flow.advance(result,review_provider)}
        if fetched:break
    assert fetched==['fresh']
    assert any(c['id']=='old' for c in result['candidates'])


def test_new_gap_query_does_not_wait_for_every_old_candidate_to_be_read():
    value=state();old=candidate('old')
    old.update(preview={'kind':'abstract'},screening={'decision':'read','basis':'abstract','reason':'Related'},
               review={'phase':'metadata','relevance':'related','topics':[0],'reason':'Related','quotes':[]})
    value['candidates']=[old]
    value['search_state'].update(stage='following_up',topics=['Agent tools'])
    def provider(path,body):
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'missing','reason':'Need an importable tool example','sources':[]}]),
                'read_next':[],'searches':[{'query':'open textbook agent tool execution','channel':'web','topics':[0],'reason':'Deliverable teaching source'}]}
    result=flow.advance(value,provider)
    assert result['search_state']['stage']=='searching'
    assert result['search_state']['searches'][0]['query']=='open textbook agent tool execution'
    assert result['candidates']==[old]


def large_reference_pool():
    value=state();value['search_state'].update(stage='following_up',topics=['Agent tools'])
    reason='尚未核對正文，可能包含工具使用的背景說明，需確認是否有實際執行步驟。'*10
    for i in range(100):
        c=candidate('reference-'+str(i))
        c.update(preview={'kind':'abstract'},screening={'decision':'reference','basis':'abstract','reason':reason},
                 review={'phase':'metadata','relevance':'related','topics':[0],'reason':reason,'quotes':[]})
        value['candidates'].append(c)
    return value


def test_large_candidate_metadata_is_batched_without_omissions_or_false_stalls():
    from runtime.semantic_service import _luna_token_count
    import json
    value=large_reference_pool();seen=[];batches=[]
    def provider(path,body):
        payload=body['request'];assert payload['task']=='follow_up'
        assert _luna_token_count(json.dumps(payload['unread'],ensure_ascii=False))<=flow.FOLLOW_UP_UNREAD_TOKENS
        seen.extend(c['url'] for c in payload['unread']);batches.append(payload['unread_more'])
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'missing','reason':'No verified body','sources':[]}]),'read_next':[],'searches':[]}
    result,_=drive(value,provider)
    assert len(batches)>1 and batches[-1] is False
    assert len(seen)==len(set(seen))==len(value['candidates'])
    assert result['search_state']['coverage'][0]['status']=='missing'
    assert 'RESEARCH_NO_NEW_EVIDENCE' not in result['search_state']['warnings']


def test_later_metadata_batch_read_index_maps_to_that_batch():
    value=large_reference_pool();calls=[]
    def provider(path,body):
        calls.append(body['request']['unread'])
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'missing','reason':'Need body','sources':[]}]),
                'read_next':[0] if len(calls)==2 else [],'searches':[]}
    for _ in range(2):value={**value,**flow.advance(value,provider)}
    target=next(c for c in value['candidates'] if c['url']==calls[1][0]['url'])
    assert calls[1][0]['url']!=calls[0][0]['url']
    assert value['search_state']['stage']=='reading' and value['search_state']['read_queue']==[target['id']]
    assert 'unread_offset' not in value['search_state']


def test_large_book_keeps_all_unread_ranges_but_only_sends_relevant_clues():
    value=state();source=inspect(candidate());source.pop('_reading_text')
    source['screening']={'decision':'read','basis':'abstract','reason':'Agent tools'}
    sections=[{'start':i*6000,'end':(i+1)*6000,'headings':['Agent appendix '+str(i)],'reason':'Not read yet'} for i in range(1500)]
    source['review']={'phase':'body','scope':'sections','relevance':'direct','reason':'Tools need permissions',
        'topics':[0],'quotes':[{'topic':0,'quote':'Agent memory stores observations.'}],'read_ranges':[[0,20]],'skipped_sections':sections}
    value['candidates']=[source];value['search_state'].update(stage='following_up',topics=['Agent tools'])
    def provider(path,body):
        payload=body['request']
        assert 'review' not in payload['items'][0]
        assert payload['items'][0]['evidence'][0]['text']=='Agent memory stores observations.'
        assert payload['unread'][0]['skipped_section_count']==1500
        assert len(payload['unread'][0]['skipped_sections'])==flow.READ_WINDOW_CANDIDATES
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'partial','reason':'Need loop example','sources':[0]}]),
                'read_next':[],'searches':[{'query':'agent tool loop','topics':[0],'reason':'New source','channel':'web'}]}
    result=flow.advance(value,provider)
    assert result['candidates'][0]['review']['skipped_sections']==sections


def test_gap_evidence_budget_keeps_multiple_topics_and_exact_original_text():
    from runtime.semantic_service import _luna_token_count
    import json
    reviewed=[]
    for topic in range(2):
        source=inspect(candidate(str(topic)))
        source['review']={'scope':'sections','reason':'A summary is not evidence','quotes':[
            {'topic':topic,'quote':f'Original section {i}: '+('Tool execution requires sandbox permissions. '*60)} for i in range(30)]}
        reviewed.append(source)
    items,visible,next_offset=flow._coverage_items(reviewed,{'topics':['Agent tool loop','Sandbox permissions'],'searches':[]})
    assert visible==[{0},{1}]
    assert _luna_token_count(json.dumps(items,ensure_ascii=False))<=flow.FOLLOW_UP_EVIDENCE_TOKENS
    for i,item in enumerate(items):
        assert 'review' not in item
        originals={q['quote'] for q in reviewed[i]['review']['quotes']}
        assert item['evidence'] and all(e['text'] in originals for e in item['evidence'])


@pytest.mark.parametrize('status',['covered','partial'])
def test_coverage_cannot_claim_support_from_text_not_sent_to_the_model(status,monkeypatch):
    monkeypatch.setattr(flow,'FOLLOW_UP_EVIDENCE_TOKENS',8000)
    value=state();source=inspect(candidate())
    source['screening']={'decision':'read','basis':'abstract','reason':'Saved'}
    source['review']={'phase':'body','scope':'full','relevance':'direct','reason':'Saved','topics':[0],
                     'quotes':[{'topic':0,'quote':'🧑‍💻'*2000}]}
    value['candidates']=[source];value['search_state'].update(stage='following_up',topics=['Agent tools'])
    def provider(path,body):
        assert body['request']['items'][0]['evidence']==[]
        return {'coverage':requested_coverage(body,[{'topic':0,'status':status,'reason':'Unjustified title inference','sources':[0]}]),'read_next':[],'searches':[]}
    result=flow.advance(value,provider)
    assert result['search_state']['coverage'][0]['status']=='missing'


def test_skipped_gap_read_restores_saved_evidence_and_clears_the_read_queue():
    value=state();text='Agent tool loop and observations. '*1000
    source=inspect(candidate());source['_reading_text']=text;source['reading_focus']=['Agent tools']
    first=flow.reading_windows(text,size=flow.READ_CHARS)[0]
    prior={'phase':'body','scope':'sections','relevance':'direct','reason':'Saved','topics':[0],
           'quotes':[{'topic':0,'quote':text[:40]}],'read_ranges':[[first['start'],first['end']]]}
    source.update(screening={'decision':'read','basis':'gap_followup','reason':'Gap'},
                  review={**prior,'phase':'metadata'},_prior_review=deepcopy(prior))
    value['candidates']=[source];value['search_state'].update(stage='locating',topics=['Agent tools'],read_queue=[source['id']])
    def provider(path,body):
        assert body['request']['task']=='locate'
        return {'sections':[{'index':s['index'],'decision':'skip','reason':'No new mechanism'} for s in body['request']['sections']]}
    result=flow.advance(value,provider);kept=result['candidates'][0]
    assert kept['review']['phase']=='body' and kept['review']['quotes']==prior['quotes']
    assert kept['review']['read_ranges']==prior['read_ranges'] and kept['review']['skipped_sections']
    assert '_prior_review' not in kept and '_reading_text' not in kept
    assert result['search_state']['read_queue']==[] and result['search_state']['stage']=='following_up'


def test_read_request_reloads_released_text_instead_of_looping_between_stages(monkeypatch):
    value=state();source=candidate()
    source.update(screening={'decision':'read','basis':'gap_followup','reason':'Gap'},
                  review={'phase':'metadata','relevance':'related','topics':[0],'reason':'Earlier outline','quotes':[]},
                  access={'status':'read','method':'html'})
    value['candidates']=[source];value['search_state'].update(stage='reading',topics=['Agent tools'])
    fetched=[];monkeypatch.setattr(flow,'inspect_candidate',lambda c:fetched.append(c['id']) or inspect(c))
    result=flow.advance(value,lambda *args:pytest.fail('No model needed for fetching'))
    assert fetched==['one'] and result['candidates'][0]['_reading_text']
    assert result['search_state']['stage']=='locating'


def test_gap_review_paginates_all_saved_evidence_before_requesting_more_sources(monkeypatch):
    monkeypatch.setattr(flow,'FOLLOW_UP_EVIDENCE_TOKENS',8000)
    value=state();source=inspect(candidate())
    quotes=[{'topic':0,'quote':f'Section {i}: '+('Agent execution requires a verified tool observation. '*35)} for i in range(30)]
    source.update(screening={'decision':'read','basis':'abstract','reason':'Saved'},
        review={'phase':'body','scope':'full','relevance':'direct','reason':'Saved','topics':[0],'quotes':quotes})
    value['candidates']=[source];value['search_state'].update(stage='following_up',topics=['Agent execution'])
    seen=[];calls=[]
    def provider(path,body):
        payload=body['request'];assert payload['task']=='follow_up'
        seen.extend(e['text'] for item in payload['items'] for e in item['evidence'])
        calls.append(payload['more_batches'])
        if len(calls)>1:assert payload['previous_coverage'][0]['status']=='partial'
        final=not payload['more_batches']
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'covered' if final else 'partial','reason':'Verified saved evidence','sources':[0]}]),
            'read_next':[],'searches':[] if final else [{'query':'premature search','channel':'web','topics':[0],'reason':'Ignored until evidence is read'}]}
    for _ in range(20):
        value={**value,**flow.advance(value,provider)}
        if value['search_state']['stage']!='following_up':break
    assert len(calls)>1 and calls[-1] is False
    assert len(seen)==len(set(seen))==len(quotes)
    assert set(seen)=={q['quote'] for q in quotes}
    assert value['search_state']['stage']=='organizing'
    assert value['search_state']['coverage'][0]['status']=='covered'
    assert value['search_state']['searches']==[]
    assert 'evidence_offset' not in value['search_state']


def test_later_unrelated_batch_cannot_erase_verified_topic_coverage(monkeypatch):
    monkeypatch.setattr(flow,'SCREEN_BATCH',1)
    value=state();value['search_state'].update(stage='following_up',topics=['Diffusion','Agent tools'])
    for topic,quote in enumerate(['Diffusion training adds noise; generation removes noise.',
                                  'The framework executes tool calls and returns observations.']):
        source=inspect(candidate(str(topic)))
        source.update(screening={'decision':'read','basis':'abstract','reason':'Saved'},
            review={'phase':'body','scope':'full','relevance':'direct','reason':'Saved',
                    'topics':[topic],'quotes':[{'topic':topic,'quote':quote}]})
        value['candidates'].append(source)
    def provider(path,body):
        topic=body['request']['items'][0]['evidence'][0]['topics'][0]
        return {'coverage':requested_coverage(body,[{'topic':i,'status':'covered' if i==topic else 'missing',
                'reason':'Verified original mechanism' if i==topic else 'No evidence in this batch',
                'sources':[0] if i==topic else []} for i in range(2)]),'read_next':[],'searches':[]}
    first={**value,**flow.advance(value,provider)}
    done=flow.advance(first,provider)
    assert done['search_state']['coverage'][0]==first['search_state']['coverage'][0]
    assert [c['status'] for c in done['search_state']['coverage']]==['covered','covered']
    assert done['search_state']['stage']=='organizing'


@pytest.mark.parametrize('change',['append','expand','replace','revoke'])
def test_changed_topic_rechecks_its_complete_current_evidence(change):
    value=state();value['search_state'].update(stage='following_up',topics=['Agent tools'])
    for identity in ['first','second']:
        source=inspect(candidate(identity))
        source.update(screening={'decision':'read','basis':'abstract','reason':'Saved'},
            review={'phase':'body','scope':'full','relevance':'direct','reason':'Saved','topics':[0],
                    'quotes':[{'topic':0,'quote':identity+' tool observation mechanism.'}]})
        value['candidates'].append(source)
    def initial(*args):
        return {'coverage':requested_coverage(args[1],[{'topic':0,'status':'partial','reason':'Missing retries','sources':[0,1]}]),
                'read_next':[],'searches':[]}
    saved={**value,**flow.advance(value,initial)}
    saved['search_state']['stage']='following_up'
    if change=='append':
        new=deepcopy(saved['candidates'][0]);new['id']='new';new['title']='New retries source'
        new['review']['quotes']=[{'topic':0,'quote':'Failed tool calls are retried with a limit.'}]
        saved['candidates'].append(new)
    elif change=='expand':saved['candidates'][0]['review']['quotes'].append({'topic':0,'quote':'New retry mechanism.'})
    elif change=='replace':saved['candidates'][0]['review']['quotes']=[{'topic':0,'quote':'Corrected evidence.'}]
    else:saved['candidates'][0]['eligible']=False
    def updated(path,body):
        payload=body['request']
        assert payload['coverage_topic']==0
        assert payload['previous_coverage'][0]['status']=='missing'
        evidence=[e['text'] for item in payload['items'] for e in item['evidence']]
        if change=='append':
            assert len(payload['items'])==3
            assert 'first tool observation mechanism.' in evidence
            assert 'Failed tool calls are retried with a limit.' in evidence
        elif change=='expand':
            assert len(payload['items'])==2
            assert 'first tool observation mechanism.' in evidence and 'New retry mechanism.' in evidence
        else:
            assert len(payload['items'])==(2 if change=='replace' else 1)
            assert 'first tool observation mechanism.' not in evidence
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'covered','reason':'Verified mechanism','sources':[0]}]),
                'read_next':[],'searches':[]}
    result=flow.advance(saved,updated)
    ids=result['search_state']['coverage'][0]['source_ids']
    assert ids==(['second'] if change=='revoke' else ['first'])


def test_expanding_saved_source_keeps_verified_evidence_until_new_reading_finishes():
    value=state();value['search_state'].update(stage='following_up',topics=['Agent tools'],searches=[
        {'query':'Agent retries','topics':[0],'channel':'web','status':'pending','pages':0,'cursor':None}])
    source=inspect(candidate())
    prior={'phase':'body','scope':'sections','relevance':'direct','reason':'Verified execution',
           'topics':[0],'quotes':[{'topic':0,'quote':'The framework executes a tool and returns its observation.'}]}
    source.update(screening={'decision':'read','basis':'abstract','reason':'Saved'},review=deepcopy(prior))
    value['candidates']=[source]
    def provider(*args):
        return {'coverage':requested_coverage(args[1],[{'topic':0,'status':'partial','reason':'Missing retries','sources':[0]}]),
                'read_next':[],'searches':[]}
    saved={**value,**flow.advance(value,provider)}
    saved['search_state']['stage']='following_up'
    saved['candidates'][0].update(_prior_review=deepcopy(prior),review={**prior,'phase':'metadata'})
    resumed=flow.advance(saved,lambda *args:pytest.fail('Expanding a source does not revoke its saved evidence'))
    assert resumed['search_state']['coverage']==saved['search_state']['coverage']
    assert resumed['candidates'][0]['review']['phase']=='metadata'
    assert resumed['candidates'][0]['_prior_review']==prior
    finished={**saved,**resumed};finished['search_state'].update(stage='organizing',finish_reason='gaps_remaining')
    result=flow.advance(finished,lambda *args:pytest.fail('No model needed for organizing'))
    assert result['candidates'][0]['review']==prior
    assert result['candidates'][0]['eligible']
    assert '_prior_review' not in result['candidates'][0]


@pytest.mark.parametrize('decision',['read','uncertain'])
def test_followup_can_schedule_screened_sources_awaiting_body(decision,monkeypatch):
    value=state();source=candidate('foundation','Agent memory fundamentals')
    source['screening']={'decision':decision,'basis':'search_snippet','reason':'Explains missing foundation'}
    source['review']={'phase':'metadata','relevance':'direct','topics':[0],'reason':'Body not read','quotes':[]}
    value['candidates']=[source]
    value['search_state'].update(stage='following_up',topics=['Agent memory'])
    def provider(path,body):
        unread=body['request']['unread']
        assert len(unread)==1 and unread[0]['url']==source['url']
        assert unread[0]['screening_decision']==decision
        assert body['request']['items']==[]
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'missing','reason':'Need body','sources':[]}]),
                'read_next':[0],'searches':[]}
    value={**value,**flow.advance(value,provider)}
    assert value['search_state']['stage']=='reading'
    assert value['search_state']['read_queue']==['foundation']
    inspected=[]
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:(inspected.append(c['id']) or inspect(c)))
    flow.advance(value,review_provider)
    assert inspected==['foundation']


@pytest.mark.parametrize('propose_search', [False, True])
def test_unchanged_evidence_does_not_stop_before_pending_body_is_read(propose_search):
    value=state();source=candidate('pending','Agent memory fundamentals')
    source['screening']={'decision':'read','basis':'search_snippet','reason':'Needs body'}
    source['review']={'phase':'metadata','relevance':'direct','topics':[0],'reason':'Not read','quotes':[]}
    value['candidates']=[source]
    value['search_state'].update(stage='following_up',topics=['Agent memory'])
    calls = []
    def provider(path,body):
        calls.append(body)
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'missing','reason':'Need body','sources':[]}]),
                'read_next':[],'searches':[{'query':'Another agent memory search','channel':'web','topics':[0],
                    'reason':'A new wording'}] if propose_search and len(calls)>1 else []}
    value={**value,**flow.advance(value,provider)}
    value['search_state'].update(stage='following_up',stalled_checks=1)
    result=flow.advance(value,provider)
    assert result['search_state']['stalled_checks']==2
    assert result['search_state']['stage']=='reading'
    assert result['search_state']['searches'] == []
    assert 'RESEARCH_NO_NEW_EVIDENCE' not in result['search_state']['warnings']


def test_pending_foundation_precedes_old_expansion_in_followup_batch():
    value=state();old=inspect(candidate('old','Agent memory tool detailed survey'));old.pop('_reading_text')
    old['screening']={'decision':'read','basis':'abstract','reason':'Agent memory tools'}
    old['review']={'phase':'body','scope':'sections','relevance':'direct','topics':[0],
                  'reason':'Agent memory tools '*20,'quotes':[{'topic':0,'quote':'Agent memory stores observations.'}]}
    fresh=candidate('fresh','Introductory textbook')
    fresh['screening']={'decision':'read','basis':'search_snippet','reason':'Needed foundation'}
    fresh['review']={'phase':'metadata','relevance':'direct','topics':[0],'reason':'Foundation','quotes':[]}
    value['candidates']=[old,fresh]
    value['search_state'].update(stage='following_up',topics=['Agent memory tools'])
    def provider(path,body):
        assert [x['url'] for x in body['request']['unread']]==[fresh['url'],old['url']]
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'partial','reason':'Need foundation','sources':[0]}]),
                'read_next':[0],'searches':[]}
    result=flow.advance(value,provider)
    assert result['search_state']['read_queue']==['fresh']


def test_stalled_research_can_stop_without_expanding_every_unknown_preview():
    value=large_reference_pool()
    uncertain=candidate('uncertain')
    uncertain.update(screening={'decision':'uncertain','basis':'search_snippet','reason':'Cannot tell from snippet'},
                     preview={'expanded':False},
                     review={'phase':'metadata','relevance':'related','topics':[0],'reason':'Unknown','quotes':[]})
    value['candidates'].append(uncertain)
    def provider(path,body):
        assert body['request']['unread_more']
        return {'coverage':requested_coverage(body,[{'topic':0,'status':'missing','reason':'No body evidence','sources':[]}]),
                'read_next':[],'searches':[]}
    value={**value,**flow.advance(value,provider)}
    value['search_state'].update(stage='following_up',stalled_checks=1)
    value['search_state'].pop('unread_offset',None)
    result=flow.advance(value,provider)
    assert result['search_state']['stage']=='organizing'
    assert result['search_state']['coverage'][0]['status']=='missing'
    assert result['search_state']['finish_reason']=='gaps_remaining'
    assert len(result['candidates'])==len(value['candidates'])


def test_new_query_does_not_bypass_repeated_no_evidence_stop():
    value = large_reference_pool()
    calls = []
    def provider(path, body):
        calls.append(body)
        return {'coverage': requested_coverage(body, [{'topic': 0, 'status': 'missing',
            'reason': 'Still no verified body', 'sources': []}]), 'read_next': [],
            'searches': [{'query': f'Agent tools alternative {len(calls)}', 'channel': 'web',
                         'topics': [0], 'reason': 'Another wording'}]}
    value = {**value, **flow.advance(value, provider)}
    for search in value['search_state']['searches']:
        search.update(status='searched', cursor=None)
    value['search_state'].update(stage='following_up', stalled_checks=1)
    result = flow.advance(value, provider)
    assert result['search_state']['stage'] == 'organizing'
    assert result['search_state']['coverage'][0]['status'] == 'missing'
    assert len(result['search_state']['searches']) == 1
    assert result['search_state']['warnings'] == ['RESEARCH_NO_NEW_EVIDENCE']


def test_changed_topic_rechecks_old_and_new_evidence_without_reassessing_other_topics():
    value=state();value['search_state'].update(stage='following_up',topics=['Tokens and context limits','Tool permissions'])
    for identity,topic,quote in [('tokens',0,'Text becomes token IDs and embeddings.'),
                                  ('window',0,'The context window limits the total token count.'),
                                  ('permissions',1,'The operating system denies unauthorized writes.')]:
        source=inspect(candidate(identity));source.update(screening={'decision':'read','basis':'abstract','reason':'Saved'},
            review={'phase':'body','scope':'full','relevance':'direct','reason':'Saved','topics':[topic],
                    'quotes':[{'topic':topic,'quote':quote}]})
        value['candidates'].append(source)
    seen=[]
    def provider(path,body):
        request=body['request'];topic=request['coverage_topic'];seen.append(topic)
        assert all(e['topics']==[topic] for item in request['items'] for e in item['evidence'])
        assert body['schema']['properties']['coverage']['items']['properties']['topic']['enum']==[topic]
        return {'coverage':[{'topic':topic,'status':'covered','reason':'Verified mechanism',
                             'sources':[i['index'] for i in request['items']]}],'read_next':[],'searches':[]}
    for _ in range(2):value={**value,**flow.advance(value,provider)}
    assert seen==[0,1]
    unchanged=deepcopy(value['search_state']['coverage'][1])
    value['search_state']['stage']='following_up'
    value['candidates'][0]['review']['quotes'].append({'topic':0,'quote':'Position encodings carry order information.'})
    def changed(path,body):
        request=body['request'];assert request['coverage_topic']==0
        quotes={e['text'] for item in request['items'] for e in item['evidence']}
        assert quotes=={'Text becomes token IDs and embeddings.','The context window limits the total token count.',
                        'Position encodings carry order information.'}
        assert len(request['items'])==2
        return provider(path,body)
    done=flow.advance(value,changed)
    assert seen==[0,1,0]
    assert done['search_state']['coverage'][1]==unchanged
    assert done['search_state']['stage']=='organizing'


def test_topic_evidence_and_complete_request_share_the_input_budget():
    import json
    from runtime.semantic_service import _luna_input_tokens,_luna_token_count
    value=state();value['search_state'].update(stage='following_up',topics=['Tokens and context limits'])
    source=inspect(candidate());source.update(screening={'decision':'read','basis':'abstract','reason':'Saved'},
        review={'phase':'body','scope':'full','relevance':'direct','reason':'Saved','topics':[0],
                'quotes':[{'topic':0,'quote':f'Excerpt {i}: '+('The context window limits the total token count. '*45)} for i in range(80)]})
    value['candidates']=[source];counts=[]
    def provider(path,body):
        request=body['request']
        total=_luna_input_tokens(body['prompt'],request)+_luna_token_count(json.dumps(body['schema'],ensure_ascii=False))
        counts.append(total)
        assert 24000<total<=flow.RESEARCH_INPUT_TOKENS
        assert _luna_token_count(json.dumps(request['items'],ensure_ascii=False))<=flow.FOLLOW_UP_EVIDENCE_TOKENS
        return {'coverage':[{'topic':0,'status':'partial','reason':'Verified partial evidence','sources':[0]}],
                'read_next':[],'searches':[]}
    flow.advance(value,provider)
    assert len(counts)==1
    value=state();value['query']='instruction '*flow.RESEARCH_INPUT_TOKENS
    with pytest.raises(SourceError,match='RESEARCH_CONTEXT_TOO_LARGE'):
        flow.advance(value,lambda *args:pytest.fail('Over-budget requests must not reach the provider'))


def test_gap_source_precedes_newly_queued_sources_for_already_covered_topics(monkeypatch):
    value=state();value['search_state'].update(stage='following_up',topics=['RAG indexing','Token sampling'],
        coverage=[{'topic':'RAG indexing','status':'covered','reason':'Verified','source_ids':['saved'],
                   'importable_source_ids':['saved']},
                  {'topic':'Token sampling','status':'missing','reason':'Need sampling mechanisms','source_ids':[],
                   'importable_source_ids':[]}],read_queue=['recent'])
    saved=inspect(candidate('saved'));saved.update(screening={'decision':'read','basis':'abstract','reason':'Saved'},
        review={'phase':'body','scope':'full','relevance':'direct','reason':'Saved','topics':[0],
                'quotes':[{'topic':0,'quote':'RAG stores document chunks in an index.'}]})
    value['candidates']=[saved]
    for identity,topic in [('recent',0),('older',1)]:
        c=candidate(identity);c.update(preview={'kind':'abstract'},
            screening={'decision':'read','basis':'abstract','reason':'Relevant'},
            review={'phase':'metadata','relevance':'direct','topics':[topic],'reason':'Relevant','quotes':[]})
        value['candidates'].append(c)
    def first(path,body):
        return {'coverage':[{'topic':0,'status':'covered','reason':'Verified','sources':[0]}],'read_next':[],'searches':[]}
    value={**value,**flow.advance(value,first)}
    value['search_state']['stage']='following_up';planned=[]
    def plan(path,body):
        request=body['request'];assert request['research_coverage']==[]
        planned.extend(c['url'] for c in request['unread'])
        return {'coverage':[],'read_next':[],'searches':[]}
    value={**value,**flow.advance(value,plan)}
    assert planned[0].endswith('/older')
    value['search_state'].update(stage='reading',read_queue=['recent'])
    fetched=[];monkeypatch.setattr(flow,'inspect_candidate',lambda c:fetched.append(c['id']) or inspect(c))
    flow.advance(value,lambda *args:pytest.fail('Fetch uses no model'))
    assert fetched==['older']


def test_relevant_arxiv_reference_gets_one_formal_license_check():
    source=candidate();source['url']='https://arxiv.org/html/2401.00001v1'
    source.update(screening={'decision':'reference','basis':'importability','reason':'No declared license in PDF'},
                  review={'phase':'metadata','relevance':'direct','topics':[0],'reason':'Sampling','quotes':[]},
                  access={'status':'read'})
    assert flow._needs_body(source)
    source['arxiv_license_checked']=True
    assert not flow._needs_body(source)
    source.pop('arxiv_license_checked');source['screening']['decision']='exclude'
    assert not flow._needs_body(source)


def test_provided_urls_read_originals_before_any_search(monkeypatch):
    value=state();value['query']='先閱讀 https://example.org/a，再讀 https://example.org/b。'
    monkeypatch.setattr(flow.research_sources,'search',lambda *a,**kw: pytest.fail('Known URLs were searched again'))
    monkeypatch.setattr(flow,'inspect_candidate',inspect)
    def provider(path,body):
        if body['request']['task']=='plan':
            assert body['request']['provided_sources']==['https://example.org/a','https://example.org/b']
            return {'summary':'讀指定文件','topics':['Agent memory'],'searches':[],'source_indices':[0,1]}
        return review_provider(path,body)
    planned={**value,**flow.advance(value,provider)}
    assert planned['search_state']['stage']=='reading'
    assert all(not c['eligible'] for c in planned['candidates'])
    done,stages=drive(planned,provider)
    assert 'searching' not in stages and 'screening' not in stages
    assert len(done['candidates'])==2
    assert all(c['review']['phase']=='body' for c in done['candidates'])


def test_provided_url_normalization_deduplicates_arxiv_and_rejects_credentials():
    rows=flow.provided_sources('讀 https://arxiv.org/pdf/2401.12345.pdf 與 https://arxiv.org/abs/2401.12345；不要使用 https://user:secret@example.org/a')
    assert [r['url'] for r in rows]==['https://arxiv.org/abs/2401.12345']
    assert not rows[0]['eligible']


def test_organizing_saved_results_keeps_gaps_and_excludes_unreviewed_sources():
    data=flow.new_state();data.update(topics=['A','B'],finish_reason='user_stopped',
        coverage=[{'topic':'A','status':'covered','source_ids':['one']},
                  {'topic':'B','status':'missing','source_ids':[]}])
    good=candidate();good.update(eligible=True,review={'phase':'body','relevance':'direct','quotes':[]})
    bad=candidate('unread');bad.update(eligible=True,_reading_text='Unverified text')
    rows=[bad,good]
    flow.organize(data,rows)
    assert data['stage']=='complete' and data['coverage'][1]['status']=='missing'
    assert rows[0]['id']=='one' and rows[0]['eligible']
    assert not rows[1]['eligible'] and '_reading_text' not in rows[1]


def test_provided_url_selection_can_exclude_a_url_and_rejects_foreign_indices():
    value=state();value['query']='讀 https://example.org/a，不要用 https://example.org/b。'
    plan={'summary':'只讀 a','topics':['Agent memory'],'searches':[],'source_indices':[0]}
    result=flow.advance(value,lambda *args:plan)
    assert [c['url'] for c in result['candidates']]==['https://example.org/a']
    for selected in [[2],[True],None]:
        with pytest.raises(SourceError,match='RESEARCH_REVIEW_FAILED'):
            flow.advance(value,lambda *args:{**plan,'source_indices':selected})
