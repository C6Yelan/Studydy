from copy import deepcopy
from datetime import UTC, datetime
from email.utils import format_datetime
from hashlib import sha256
import json
from urllib.parse import parse_qs

import httpx
import pytest

from runtime import research_sources as sources, research_web as web
from runtime.source_normalization import SourceError


BODY='''<html><head><title>A learning example</title><meta name="author" content="A Teacher">
<link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/"></head><body>
<nav>unrelated navigation</nav><article><h1>A learning example</h1>
<p>This paragraph explains a learning example and its necessary conditions. Do not omit the conditions or replace them with a summary. It provides enough context for learning.</p>
<pre><code>if x &lt; 10:\n    return x != 0</code></pre>
<table><tr><th>Code</th><th>Meaning</th></tr><tr><td>200</td><td>Success</td></tr></table>
</article><footer>unrelated footer</footer></body></html>'''


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    web._search_cache.clear();sources._PUBLIC_CACHE.clear()
    monkeypatch.setattr(web,'_next_search',0)
    for field in ('_rate_limited_until','_host_next'):
        monkeypatch.setattr(sources,field,{})
    monkeypatch.setenv('STUDYDY_SEARXNG_URL','http://search:8080')


def test_search_cache_avoids_repeating_upstream_and_keeps_caller_mutations_private(monkeypatch):
    requests=[]
    def response(request):
        requests.append(request)
        return httpx.Response(200,json={'results':[{'url':'https://example.org/lesson','title':'Lesson','engines':['yahoo']}]})
    client=httpx.Client
    monkeypatch.setattr(web.httpx,'Client',lambda **kwargs:client(transport=httpx.MockTransport(response),**kwargs))
    first=web._searx('機會成本',1)
    first['results'][0]['title']='caller changed'
    second=web._searx('機會成本',1)
    assert len(requests)==1 and second['results'][0]['title']=='Lesson'
    assert requests[0].method=='POST' and requests[0].url.query==b''
    assert parse_qs(requests[0].content.decode())['language']==['zh-TW']


def test_html_body_preserves_literals_structure_and_provenance(monkeypatch):
    monkeypatch.setattr(sources,'public_page',lambda url,**kwargs:(BODY.encode(),url,'text/html'))
    doc=web._web_document('https://example.org/lesson')
    assert doc['authors']=='A Teacher' and doc['license']=='CC-BY-SA-4.0'
    assert 'if x < 10:\n    return x != 0' in doc['text']
    assert '| Code | Meaning |' in doc['text'] and '| 200 | Success |' in doc['text']
    assert '# A learning example' in doc['text']
    assert 'unrelated navigation' not in doc['text'] and 'unrelated footer' not in doc['text']
    candidate={**{k:v for k,v in doc.items() if k!='text'},'download_url':doc['url'],'eligible':True,'kind':'web','id':'stable','version':'web'}
    data,media,suffix,metadata=sources.acquire(candidate)
    assert media=='text/plain' and suffix=='.txt' and b'if x < 10:' in data
    assert metadata['original_sha256']==sha256(BODY.encode()).hexdigest()
    assert metadata['derivative_license']=='CC-BY-SA-4.0'
    assert 'https://example.org/lesson' in data.decode() and 'A Teacher' in data.decode()


@pytest.mark.parametrize('replacement',[
    '<a href="https://creativecommons.org/licenses/by/4.0/">A cited source</a>',
    '<link rel="license" href="https://evil.org/licenses/by/4.0/">',
    '<link rel="license" href="https://creativecommons.org/licenses/by-nc-nd/4.0/">',
    '<figure><a rel="license" href="https://creativecommons.org/licenses/by/4.0/">An image</a></figure>',
])
def test_citation_image_or_unapproved_license_cannot_authorize_the_whole_page(monkeypatch,replacement):
    body=BODY.replace('<link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">','').replace('</article>',replacement+'</article>')
    monkeypatch.setattr(sources,'public_page',lambda url,**kwargs:(body.encode(),url,'text/html'))
    with pytest.raises(SourceError,match='RESEARCH_LICENSE_UNCONFIRMED'):web._web_document('https://example.org/lesson')


def test_acquisition_rechecks_identity_and_license(monkeypatch):
    monkeypatch.setattr(sources,'public_page',lambda url,**kwargs:(BODY.encode(),url,'text/html'))
    doc=web._web_document('https://example.org/lesson')
    row={**doc,'download_url':doc['url'],'id':'one','version':'web'}
    changed={**doc,'page_identity':'different'}
    from runtime import research_reading
    monkeypatch.setattr(research_reading,'read_public_document',lambda url,**kwargs:changed)
    with pytest.raises(SourceError,match='RESEARCH_IDENTITY_UNCONFIRMED'):web.acquire_web(row)
    changed.update(page_identity=doc['page_identity'],license='CC-BY-4.0')
    with pytest.raises(SourceError,match='RESEARCH_LICENSE_UNCONFIRMED'):web.acquire_web(row)


def test_captcha_page_is_not_treated_as_body_and_does_not_block_other_entries(monkeypatch):
    body=BODY.replace('A learning example</title>','Verify you are human</title>')
    monkeypatch.setattr(sources,'public_page',lambda url,**kwargs:(body.encode(),url,'text/html'))
    with pytest.raises(SourceError,match='RESEARCH_CHALLENGE_REQUIRED'):web._web_document('https://example.org/lesson')
    sources._reserve_host('example.org')


def test_web_discovery_preserves_all_candidates_for_background_reading(monkeypatch):
    requests=[];checked=[]
    def search(query,page):
        requests.append((query,page))
        return {'results':[{'url':f'https://site{i//5}.org/lesson{i}?utm_source=test','title':f'Lesson {i}','engines':['yahoo']} for i in range(20)],'unresponsive_engines':[['wikibooks','timeout']]}
    def document(url):
        checked.append(url)
        return {'url':url,'title':'Actual page title','authors':'Author','license':'CC-BY-4.0','license_url':'https://creativecommons.org/licenses/by/4.0/','page_identity':'identity'}
    monkeypatch.setattr(web,'_searx',search);monkeypatch.setattr(web,'_web_document',document)
    rows,next_page,warnings=web.search_web(['topic tutorial','主題 教學'],1)
    assert len(rows)==20 and len(checked)==0 and sum(r['eligible'] for r in rows)==0
    assert next_page==2 and warnings==['SearXNG:wikibooks']
    assert all('utm_' not in row['id'] for row in rows)
    assert requests==[('topic tutorial',1),('主題 教學',1)]
    assert not any('text' in row for row in rows)


def test_free_web_search_joins_existing_search_and_pagination_without_rewriting_query(monkeypatch):
    calls=[]
    monkeypatch.setattr(sources,'openalex_search',lambda query,cursor:(calls.append(('academic',query,cursor)) or [],None))
    monkeypatch.setattr(sources,'official_search',lambda *a:[])
    monkeypatch.setattr(sources,'mdn_search',lambda *a:[])
    monkeypatch.delenv('STUDYDY_SEMANTIC_SCHOLAR_API_KEY',raising=False)
    def search(queries,page):
        calls.append(('web',queries,page));return [],page+1 if page<3 else None,['SearXNG:partial']
    monkeypatch.setattr(web,'search_web',search)
    rows,cursor,warnings=sources.search('network tutorial\n網路 教學',channel='web')
    assert calls==[('web',['network tutorial','網路 教學'],1)]
    assert json.loads(cursor)=={'web':2} and warnings==['SearXNG:partial']
    calls.clear();sources.search('network tutorial\n網路 教學',cursor,channel='web')
    assert calls==[('web',['network tutorial','網路 教學'],2)]


def test_retry_after_is_honored_but_plain_403_does_not_block_a_host(monkeypatch):
    now=1700000000
    monkeypatch.setattr(sources.time,'time',lambda:now)
    future=format_datetime(datetime.fromtimestamp(now+7200,UTC),usegmt=True)
    assert sources._retry_delay(future,60)==7200
    assert sources._retry_delay('7200',60)==7200
    assert sources._retry_delay('invalid',60)==60
    sources._cooldown('blocked.example',403)
    sources._reserve_host('blocked.example')
    sources._cooldown('limited.example',429,future)
    with pytest.raises(SourceError,match='RESEARCH_RATE_LIMITED'):sources._reserve_host('limited.example')


def test_queued_request_rechecks_explicit_rate_limit(monkeypatch):
    clock=[100.0]
    monkeypatch.setattr(sources.time,'monotonic',lambda:clock[0])
    sources._reserve_host('example.org')
    def sleep(delay):
        assert delay==2
        sources._cooldown('example.org',429,'60');clock[0]+=delay
    monkeypatch.setattr(sources.time,'sleep',sleep)
    with pytest.raises(SourceError,match='RESEARCH_RATE_LIMITED'):sources._reserve_host('example.org')


def test_public_page_cache_has_a_ttl_and_does_not_wrap_authenticated_fetches(monkeypatch):
    calls=[];clock=[10.0]
    monkeypatch.setattr(sources.time,'monotonic',lambda:clock[0])
    def fetch(url,**kwargs):calls.append((url,kwargs));return b'public',url,'text/html'
    monkeypatch.setattr(sources,'fetch',fetch)
    sources.public_page('https://example.org/a');sources.public_page('https://example.org/a')
    assert len(calls)==1
    clock[0]+=601;sources.public_page('https://example.org/a');assert len(calls)==2
    sources.fetch('https://example.org/a',headers={'Authorization':'test-only'})
    assert len(calls)==3 and calls[-1][1]['headers']=={'Authorization':'test-only'}


def test_canonical_web_urls_preserve_meaningful_query_and_reject_credentials():
    assert web.canonical_url('http://EXAMPLE.org/中文?utm_source=x&id=3#section')=='https://example.org/%E4%B8%AD%E6%96%87?id=3'
    assert web.canonical_url('https://user:secret@example.org/') is None
    assert web.canonical_url('https://example.org:8443/') is None
    assert web.canonical_url('javascript:alert(1)') is None


def test_later_verified_web_result_preserves_id_and_its_identity_binding():
    old={'id':'stable','doi':None,'kind':'web','url':'https://example.org/a','title':'Search title','authors':'Unknown',
         'eligible':False,'state':'candidate','license':'unknown','license_url':None,'download_url':None,'version':'web','reason':'not checked'}
    new={**old,'eligible':True,'title':'Actual title','download_url':old['url'],'license':'CC-BY-4.0',
         'license_url':'https://creativecommons.org/licenses/by/4.0/','page_identity':'verified'}
    result=sources.merge_candidates([old],[new])[0]
    assert result['id']=='stable' and result['page_identity']=='verified'
    assert result['eligible'] and result['title']=='Actual title'
    assert old['eligible'] is False


def test_search_service_retry_time_reaches_the_background_worker(monkeypatch):
    def limited(*args):raise sources.SourceAccessError('RESEARCH_RATE_LIMITED',status=429,retry_at=1700007200)
    monkeypatch.setattr(web,'_searx',limited)
    with pytest.raises(sources.SourceAccessError) as caught:web.search_web(['generic topic'],1)
    assert caught.value.retry_at==1700007200


def test_title_only_login_page_is_not_accepted_as_article_body():
    html=b'<html><head><title>Publication Library Login</title></head><body><h1>Publication Library Login</h1></body></html>'
    with pytest.raises(SourceError,match='RESEARCH_AUTH_REQUIRED'):
        web.html_document(html,'https://example.org/login','text/html')


def test_article_teaching_login_is_still_readable():
    html=BODY.replace('A learning example','Login with secure sessions')
    doc=web.html_document(html.encode(),'https://example.org/tutorial','text/html')
    assert 'necessary conditions' in doc['text']


def test_publication_title_without_body_is_unavailable():
    html=b'<html><head><title>An agent paper | Conference Library</title></head><body><h1>An agent paper | Conference Library</h1></body></html>'
    with pytest.raises(SourceError,match='RESEARCH_BODY_UNAVAILABLE'):
        web.html_document(html,'https://example.org/paper','text/html')


def test_official_linked_policy_must_explicitly_cover_documentation(monkeypatch):
    html=BODY.replace('<link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">','').replace(
        '<footer>unrelated footer</footer>','<footer><a href="/licensing">Content licensing</a></footer>')
    policy=b'<html><p>All documentation is licensed under <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>.</p></html>'
    monkeypatch.setattr(sources,'public_page',lambda url,**kwargs:(policy,url,'text/html'))
    doc=web.html_document(html.encode(),'https://example.org/lesson','text/html')
    assert doc['license']=='CC-BY-4.0'
    assert doc['license_snapshot_sha256']==sha256(policy).hexdigest()
    assert 'https://example.org/licensing' in doc['license_text']
    unrelated=b'<html><p>Code examples use <a rel="license" href="https://creativecommons.org/licenses/by/4.0/">CC BY</a>.</p></html>'
    monkeypatch.setattr(sources,'public_page',lambda url,**kwargs:(unrelated,url,'text/html'))
    assert web.html_document(html.encode(),'https://example.org/lesson','text/html')['license']=='unknown'


def test_later_search_does_not_reactivate_an_exhausted_source():
    old={'id':'same','url':'https://example.org/a','eligible':False,'state':'candidate',
         'access':{'status':'unavailable','exhausted':True},'reason':'Unavailable'}
    new={**old,'eligible':True,'reason':'New search result','access':{'status':'not_reviewed'}}
    merged=sources.merge_candidates([old],[new])[0]
    assert not merged['eligible'] and merged['access']['exhausted']
