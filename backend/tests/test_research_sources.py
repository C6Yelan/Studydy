import pytest
from runtime import research_sources as sources
from runtime.errors import SourceError

@pytest.mark.parametrize('url',['http://example.org/x','https://127.0.0.1/x','https://[::1]/x','https://user:password@example.org/x','https://example.org:8443/x','file:///tmp/a'])
def test_reject_unsafe_download_targets(url):
    with pytest.raises(SourceError,match='RESEARCH_URL_REJECTED'):sources.fetch(url)

def test_dns_mixed_public_private_is_rejected(monkeypatch):
    monkeypatch.setattr(sources.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443)),(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(SourceError,match='RESEARCH_URL_REJECTED'):sources.fetch('https://example.org/file.pdf')

def test_no_metadata_only_or_unknown_license_import():
    with pytest.raises(SourceError,match='RESEARCH_LICENSE_UNCONFIRMED'):sources.acquire({'eligible':False})

def test_extract_official_main_ignores_navigation_script_and_keeps_literals():
    text='Important condition: x != 0. '+('Network data ' * 15)
    assert text in sources.main_text('<nav>ignore</nav><main><p>'+text+'</p><script>secret</script><pre>a &lt; b</pre></main>')
    result=sources.main_text('<main>'+text+'<script>secret</script></main>')
    assert 'secret' not in result


def test_official_self_closing_break_and_table_cells_keep_text_boundaries():
    prefix='A documented prerequisite must remain intact. '*3
    value=sources.main_text('<main><p>'+prefix+'before<br/>after</p><table><tr><td>TCP</td><td>reliable</td></tr></table><p>Final required condition.</p></main>')
    assert 'before\nafter' in value
    assert '\tTCP\treliable' in value
    assert 'Final required condition.' in value


def paper(identity='one',doi='10.1234/example',locations=None):
    return sources._paper(identity,'Retrieval augmented generation', 'Author',2025,doi,'https://example.org/article',locations or [],'test')


def location(url='https://example.org/a.pdf',version='publishedVersion'):
    return sources._location(url,'cc-by',version)


def test_doi_merge_preserves_selection_id_and_collects_versioned_locations():
    old=paper('old','https://doi.org/10.1234/EXAMPLE',[location()])
    new=paper('new','10.1234/example',[location('https://mirror.org/a.pdf'),location('https://mirror.org/preprint.pdf','submittedVersion')])
    new['discovered_by']=['Crossref']
    merged=sources.merge_candidates([old],[new])
    assert len(merged)==1 and merged[0]['id']=='old'
    assert len(merged[0]['download_locations'])==3
    assert merged[0]['version']=='publishedVersion' and old['download_locations']==[location()]
    old.update(state='ready',source_id='saved',normalization_id='saved-normalization')
    retained=sources.merge_candidates([old],[new])[0]
    assert retained['source_id']=='saved' and retained['download_locations']==old['download_locations']


def test_pagination_continues_each_provider_and_failure_keeps_other_results(monkeypatch):
    calls=[]
    def oa(query,cursor):
        calls.append(('oa',cursor))
        if cursor==1:raise SourceError('RESEARCH_RATE_LIMITED')
        return [paper('oa',locations=[location()])],None
    def scholar(query,offset):
        calls.append(('scholar',offset));return [paper('cr','10.2/crossref',[location()])],20 if offset==0 else None
    monkeypatch.setattr(sources,'openalex_search',oa);monkeypatch.setattr(sources,'semantic_scholar_search',scholar)
    monkeypatch.setenv('STUDYDY_SEMANTIC_SCHOLAR_API_KEY','test-only')
    rows,cursor,warnings=sources.search('retrieval augmented generation')
    assert len(rows)==1 and warnings==['openalex']
    assert sources.json.loads(cursor)['openalex']==1
    assert sources.json.loads(cursor)['semantic_scholar']==20
    assert sources.json.loads(cursor)['_failures'][0]['provider']=='openalex'
    sources.search('retrieval augmented generation',cursor)
    assert ('scholar',20) in calls
    calls.clear()
    with pytest.raises(SourceError,match='REQUEST_INVALID'):
        sources.search('retrieval augmented generation','legacy-cursor')
    assert calls==[]


@pytest.mark.parametrize('query',['Python generators','JavaScript DOM events','LLM tokenization'])
def test_wikipedia_channel_only_uses_wikipedia_search(monkeypatch,query):
    from runtime import research_web
    called=[]
    monkeypatch.setenv('STUDYDY_SEARXNG_URL','http://search.test')
    monkeypatch.setattr(research_web,'search_wikipedia',lambda queries,page:(called.append((queries,page)) or [],None,[]))
    assert sources.search(query,channel='wikipedia')==([],None,[])
    assert called==[([query],1)]


def test_all_search_services_failing_is_not_an_empty_success(monkeypatch):
    def fail(*args):raise SourceError('RESEARCH_SEARCH_FAILED')
    for name in ['openalex_search']:monkeypatch.setattr(sources,name,fail)
    monkeypatch.delenv('STUDYDY_SEMANTIC_SCHOLAR_API_KEY',raising=False)
    with pytest.raises(SourceError,match='RESEARCH_SEARCH_FAILED'):sources.search('topic')


def test_acquisition_prefers_same_version_before_author_version(monkeypatch):
    primary=location();alternate=location('https://mirror.org/final.pdf')
    row=paper(locations=[primary,location('https://mirror.org/preprint.pdf','submittedVersion'),alternate])
    seen=[]
    def acquire(candidate,**kwargs):
        seen.append(candidate['download_url'])
        if len(seen)==1:raise SourceError('RESEARCH_SOURCE_BLOCKED')
        return b'%PDF-test','application/pdf','.pdf',{'version':candidate['version'],'download_url':candidate['download_url']}
    monkeypatch.setattr(sources,'_acquire_location',acquire)
    result=sources.acquire(row)
    assert seen==[primary['download_url'],alternate['download_url']]
    assert row['download_url']==alternate['download_url'] and result[3]['version']=='publishedVersion'


def test_first_read_can_use_a_declared_author_version_without_replacing_reviewed_bytes(monkeypatch):
    final=location();author=location('https://repository.org/author.pdf','acceptedVersion')
    row=paper(locations=[final,author]);seen=[]
    def acquire(candidate,**kwargs):
        seen.append(candidate['download_url'])
        if candidate['version']=='publishedVersion':raise SourceError('RESEARCH_SOURCE_BLOCKED')
        return b'%PDF-author','application/pdf','.pdf',{'version':candidate['version'],'download_url':candidate['download_url']}
    monkeypatch.setattr(sources,'_acquire_location',acquire)
    monkeypatch.setattr(sources,'_unpaywall_locations',lambda c:[])
    result=sources.acquire(row)
    assert seen==[final['download_url'],author['download_url']]
    assert row['version']=='acceptedVersion' and result[3]['alternate_version_of']=='publishedVersion'
    reviewed=paper(locations=[final,author]);reviewed['inspection_sha256']='original-reviewed-digest';seen.clear()
    with pytest.raises(SourceError,match='RESEARCH_SOURCE_BLOCKED'):sources.acquire(reviewed)
    assert seen==[final['download_url']]


def test_unpaywall_is_opt_in_and_cannot_change_document_identity(monkeypatch):
    monkeypatch.delenv('STUDYDY_UNPAYWALL_EMAIL',raising=False)
    def unexpected(*args,**kwargs):pytest.fail('unexpected external lookup')
    monkeypatch.setattr(sources,'_json',unexpected)
    assert sources._unpaywall_locations(paper())==[]
    monkeypatch.setenv('STUDYDY_UNPAYWALL_EMAIL','test@example.org')
    monkeypatch.setattr(sources,'_json',lambda *a,**k:{'doi':'10.9999/other','oa_locations':[]})
    with pytest.raises(SourceError,match='RESEARCH_IDENTITY_UNCONFIRMED'):sources._unpaywall_locations(paper())



def test_https_upgrade_does_not_expand_license_policy():
    assert sources._location('http://example.org/a.pdf','cc-by','publishedVersion')['download_url']=='https://example.org/a.pdf'
    assert sources._location('https://example.org/a.pdf','cc-by-nc-nd','publishedVersion') is None
    assert sources._location('https://example.org/a.pdf','https://evil.org/licenses/by/4.0/','publishedVersion') is None


def test_fetch_strips_api_credentials_on_cross_host_redirect_and_honors_429(monkeypatch):
    from types import SimpleNamespace
    requests=[];responses=[]
    class Response:
        def __init__(self,status,headers=None):self.status=status;self.headers=headers or {}
        def getheader(self,name,default=None):return self.headers.get(name,default)
        def read(self,limit):return b'{}'
    class Connection:
        def __init__(self,host,**kwargs):self.host=host
        def request(self,method,path,headers):requests.append((self.host,headers))
        def getresponse(self):return responses.pop(0)
        def close(self):pass
    monkeypatch.setattr(sources,'_rate_limited_until',{})
    monkeypatch.setattr(sources.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    monkeypatch.setattr(sources.socket,'create_connection',lambda *a,**k:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(sources.ssl,'create_default_context',lambda:SimpleNamespace(wrap_socket=lambda raw,**kwargs:raw))
    monkeypatch.setattr(sources.http.client,'HTTPSConnection',Connection)
    responses.extend([Response(302,{'Location':'https://mirror.example/file'}),Response(200)])
    sources.fetch('https://api.example/query',headers={'Authorization':'Bearer test-only'})
    assert requests[0][1]['Authorization']=='Bearer test-only' and 'Authorization' not in requests[1][1]
    responses.append(Response(429,{'Retry-After':'60'}))
    with pytest.raises(SourceError,match='RESEARCH_RATE_LIMITED'):sources.fetch('https://limited.example/query')
    count=len(requests)
    with pytest.raises(SourceError,match='RESEARCH_RATE_LIMITED'):sources.fetch('https://limited.example/query')
    assert len(requests)==count


def test_all_known_same_version_entries_are_tried_without_losing_original_failure(monkeypatch):
    row=paper(locations=[location(f'https://mirror{i}.org/a.pdf') for i in range(6)])
    calls=[]
    def fail(candidate,**kwargs):calls.append(candidate);raise SourceError('RESEARCH_SOURCE_BLOCKED')
    monkeypatch.setattr(sources,'_acquire_location',fail)
    monkeypatch.setattr(sources,'_unpaywall_locations',lambda c:[])
    with pytest.raises(SourceError,match='RESEARCH_SOURCE_BLOCKED'):sources.acquire(row)
    assert len(calls)==6


def test_semantic_scholar_uses_configured_key_and_requires_explicit_license(monkeypatch):
    calls=[];monkeypatch.setenv('STUDYDY_SEMANTIC_SCHOLAR_API_KEY','test-only')
    monkeypatch.setattr(sources,'_semantic_next',0)
    def response(url,**kwargs):
        calls.append((url,kwargs));return {'data':[{'url':'https://www.semanticscholar.org/paper/test','title':'Research',
            'externalIds':{'DOI':'10.1234/test'},'authors':[],'year':2025,
            'openAccessPdf':{'url':'https://example.org/test.pdf','license':'CCBY'}}]}
    monkeypatch.setattr(sources,'_json',response)
    rows,cursor=sources.semantic_scholar_search('research',0)
    assert rows[0]['eligible'] and rows[0]['license']=='cc-by' and cursor is None
    assert calls[0][1]['headers']=={'x-api-key':'test-only'}
    assert 'test-only' not in calls[0][0]


def test_pdf_identity_uses_actual_title_not_english_word_count_or_percentage(monkeypatch):
    import fitz
    def pdf(title,body):
        with fitz.open() as document:
            page=document.new_page();page.insert_text((72,72),title);page.insert_text((72,120),body)
            document.set_metadata({'title':title})
            return document.tobytes()
    data=pdf('AI','A short legitimate document title is still a document title.')
    monkeypatch.setattr(sources,'public_page',lambda url,**kw:(data,url,'application/pdf'))
    item=paper(locations=[location()]);item.update(title='AI',doi=None)
    assert sources.acquire(item)[0]==data
    data=pdf('Different paper','A large set of agents may use language. A model may use any tool.')
    item=paper(locations=[location()]);item.update(title='Large Language Model Agents',doi=None)
    with pytest.raises(SourceError,match='RESEARCH_IDENTITY_UNCONFIRMED'):sources.acquire(item)


def test_pdf_identity_accepts_a_complete_title_split_across_adjacent_blocks(monkeypatch):
    import fitz
    with fitz.open() as pdf:
        page=pdf.new_page()
        page.insert_text((50,50),'Journal of Synthetic Examples')
        page.insert_text((50,110),'A complete research title')
        page.insert_text((50,145),'continued across PDF blocks')
        page.insert_text((50,190),'A Fictional Author')
        data=pdf.tobytes()
    monkeypatch.setattr(sources,'public_page',lambda url,**kw:(data,url,'application/pdf'))
    item=paper(locations=[location()]);item.update(title='A complete research title continued across PDF blocks')
    assert sources.acquire(item)[0]==data
    item['title']='A complete research title with a different conclusion'
    with pytest.raises(SourceError,match='RESEARCH_IDENTITY_UNCONFIRMED'):sources.acquire(item)


def test_pdf_identity_accepts_title_lines_in_a_block_with_a_series_heading(monkeypatch):
    import fitz
    with fitz.open() as pdf:
        page=pdf.new_page()
        page.insert_text((50,50),'Example Research Series\nAgent Interoperability and\nConsequential Handoffs')
        data=pdf.tobytes()
    monkeypatch.setattr(sources,'public_page',lambda url,**kw:(data,url,'application/pdf'))
    item=paper(locations=[location()]);item.update(title='Agent Interoperability and Consequential Handoffs',doi=None)
    assert sources.acquire(item)[0]==data
    item['title']='Interoperability and Consequential'
    with pytest.raises(SourceError,match='RESEARCH_IDENTITY_UNCONFIRMED'):sources.acquire(item)


def test_openalex_semantic_search_preserves_meaning_rank_and_same_version_locations(monkeypatch):
    from urllib.parse import urlsplit,parse_qs
    requests=[]
    work={'id':'https://openalex.org/W1','title':'Selective prediction with a reject option',
          'doi':'https://doi.org/10.1234/reject','locations':[
              {'pdf_url':'https://example.org/paper.pdf','license':'cc-by','version':'publishedVersion',
               'landing_page_url':'https://example.org/paper'}],
          'abstract_inverted_index':{'A':[0],'model':[1],'defers':[2],'uncertain':[3],'decisions':[4]}}
    def response(url,**kwargs):
        requests.append(parse_qs(urlsplit(url).query));return {'meta':{'count':50},'results':[work]}
    monkeypatch.setattr(sources,'_json',response)
    question='When should an automated system ask a human instead of acting under uncertainty?'
    rows,page=sources.openalex_search(question,1)
    assert requests[0]['search.semantic']==[question] and 'search' not in requests[0]
    assert requests[0]['filter']==['is_oa:true,has_abstract:true']
    assert rows[0]['description']=='A model defers uncertain decisions'
    assert rows[0]['eligible'] and rows[0]['download_url']=='https://example.org/paper.pdf'
    assert page==2
    assert sources.openalex_search(question,3)[1] is None
