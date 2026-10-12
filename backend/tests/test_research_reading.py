from runtime import research_reading as reading, research_sources as sources
from runtime.source_normalization import SourceError
from test_research_web import BODY


URL='https://medium.com/@teacher/an-agent-123456789abc'
FEED='https://medium.com/feed/@teacher'
RSS=('''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>Other</title><link>https://medium.com/@teacher/other-222222222222</link><content:encoded><![CDATA[<p>Unrelated article.</p>]]></content:encoded></item>
<item><title>An agent</title><link>'''+URL+'''?source=rss-test</link><content:encoded><![CDATA[
<p>An agent plans a task, invokes a tool, reads the observation and decides whether another action is needed. Its memory stores observations from earlier actions.</p>
<pre>result = tool(arguments)</pre>]]></content:encoded></item></channel></rss>''').encode()


def test_proposed_public_feed_recovers_the_same_article_after_403(monkeypatch):
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        if url==URL:raise sources.SourceAccessError('RESEARCH_SOURCE_BLOCKED',url=url,status=403)
        assert url==FEED
        return RSS,url,'application/rss+xml'
    monkeypatch.setattr(sources,'public_page',fetch)
    attempts=[];doc=reading.read_public_document(URL,attempts=attempts,entries=[{'url':FEED,'kind':'rss'}])
    assert calls==[URL,FEED] and doc['access_method']=='rss' and doc['url']==URL
    assert 'result = tool(arguments)' in doc['text'] and 'Unrelated article.' not in doc['text']
    assert attempts[0]['http_status']==403 and attempts[1]['outcome']=='read'
    assert doc['license']=='unknown'


def test_rss_without_matching_article_is_not_a_success(monkeypatch):
    def fetch(url,**kwargs):
        if url==URL:raise sources.SourceAccessError('RESEARCH_SOURCE_BLOCKED',url=url,status=403)
        return RSS.replace(URL.encode(),b'https://medium.com/@teacher/another-333333333333'),url,'text/xml'
    monkeypatch.setattr(sources,'public_page',fetch)
    import pytest
    with pytest.raises(SourceError,match='RESEARCH_FEED_ARTICLE_MISSING'):reading.read_public_document(URL,entries=[{'url':FEED,'kind':'rss'}])


def test_reading_without_explicit_reuse_terms_still_returns_content_but_not_import_permission(monkeypatch):
    html=BODY.replace('<link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">','')
    monkeypatch.setattr(sources,'public_page',lambda url,**kwargs:(html.encode(),url,'text/html'))
    candidate={'id':'one','kind':'web','url':'https://example.org/article','title':'Example','eligible':False}
    result=reading.inspect_candidate(candidate)
    assert result['access']['status']=='read' and result['access']['characters']>0
    assert not result['eligible'] and 'if x < 10:' in result['_reading_text']
    assert 'access' not in candidate


def test_parser_does_not_expand_feed_external_entities():
    malicious=b'<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&xxe;</title><link>https://medium.com/@teacher/an-agent-123456789abc</link></item></channel></rss>'
    import pytest
    with pytest.raises(SourceError,match='RESEARCH_FEED_ARTICLE_MISSING'):reading._feed_article(malicious,FEED,URL)


def test_declared_pdf_link_is_used_when_html_has_no_body(monkeypatch):
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        if url.endswith('/article'):return b'<html><a href="/paper.pdf">PDF</a></html>',url,'text/html'
        return b'%PDF-fixture',url,'application/pdf'
    monkeypatch.setattr(sources,'public_page',fetch)
    monkeypatch.setattr(reading,'_pdf_document',lambda data,url:{'text':'Actual paper body','access_method':'pdf','url':url})
    result=reading.read_public_document('https://example.org/article')
    assert calls==['https://example.org/article','https://example.org/paper.pdf']
    assert result['text']=='Actual paper body'


def test_rechecking_a_paper_html_version_keeps_the_body_hash_consistent(monkeypatch):
    monkeypatch.setattr(sources,'public_page',lambda url,**kwargs:(BODY.encode(),url,'text/html'))
    candidate={'id':'paper','kind':'paper','web_document':True,'url':'https://example.org/article','download_url':'https://example.org/article',
               'title':'A learning example','eligible':True,'license':'CC-BY-SA-4.0','version':'html'}
    checked=reading.inspect_candidate(candidate)
    assert checked['eligible'] and checked['access']['method']=='html'
    data,media,suffix,metadata=sources.acquire(checked)
    assert media=='text/plain' and metadata['access_method']=='html'


def test_unrelated_redirected_html_does_not_replace_a_paper_identity(monkeypatch):
    monkeypatch.setattr(sources,'public_page',lambda url,**kwargs:(BODY.encode(),'https://example.org/different','text/html'))
    candidate={'id':'paper','kind':'paper','url':'https://example.org/expected','title':'A different paper','eligible':False,'license':'unknown'}
    checked=reading.inspect_candidate(candidate)
    assert checked['access']['status']=='read' and not checked['eligible']
    assert checked['title']=='A different paper' and checked['access']['actual_title']=='A learning example'


def test_atom_xhtml_content_uses_the_same_article_identity_check():
    url='https://learning.example/articles/loop'
    data=('''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>An observation loop</title><link href="'''+url+'''"/>
    <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>The agent receives an observation from the tool and uses that result to choose its next action. It stops when the requested result is complete.</p><pre>result = tool(arguments)</pre></div></content></entry></feed>''').encode()
    doc=reading._feed_article(data,'https://learning.example/atom.xml',url)
    assert doc['url']==url and 'result = tool(arguments)' in doc['text']


def test_declared_fulltext_is_read_instead_of_stopping_at_a_readable_landing_page(monkeypatch):
    html=BODY.replace('</head>','<meta name="citation_pdf_url" content="https://example.org/full.pdf"></head>')
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        return (b'%PDF-fixture' if url.endswith('.pdf') else html.encode()),url,'application/pdf' if url.endswith('.pdf') else 'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    monkeypatch.setattr(reading,'_pdf_document',lambda data,url:{'text':'Full paper including the methods and limitations.','access_method':'pdf','url':url})
    doc=reading.read_public_document('https://example.org/article')
    assert calls==['https://example.org/article','https://example.org/full.pdf']
    assert doc['access_method']=='pdf' and 'methods and limitations' in doc['text']


def test_unavailable_declared_fulltext_is_distinguished_from_readable_page(monkeypatch):
    html=BODY.replace('</head>','<meta name="citation_pdf_url" content="https://example.org/full.pdf"></head>')
    def fetch(url,**kwargs):
        if url.endswith('.pdf'):raise sources.SourceAccessError('RESEARCH_SOURCE_BLOCKED',url=url,status=403)
        return html.encode(),url,'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    attempts=[];doc=reading.read_public_document('https://example.org/article',attempts=attempts)
    assert doc['access_method']=='landing_page' and doc['fulltext_unavailable']
    assert attempts[-1]['http_status']==403


def test_limited_fulltext_stays_pending_instead_of_accepting_the_landing_page(monkeypatch):
    html=BODY.replace('</head>','<meta name="citation_pdf_url" content="https://example.org/full.pdf"></head>')
    retry_at=reading.time.time()+300
    def fetch(url,**kwargs):
        if url.endswith('.pdf'):raise sources.SourceAccessError('RESEARCH_RATE_LIMITED',url=url,status=429,retry_at=retry_at)
        return html.encode(),url,'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    checked=reading.inspect_candidate({'id':'paper','kind':'paper','url':'https://example.org/article','title':'A learning example','eligible':False,'license':'unknown'})
    assert checked['access']['retry_at']==retry_at and checked['access']['status']=='unavailable'
    assert not checked['eligible'] and not checked['_reading_text']


def test_pdf_viewer_follows_embedded_document_instead_of_reviewing_its_toolbar(monkeypatch):
    html=BODY.replace('</body>','<iframe src="/files/full.pdf?download=1"></iframe></body>')
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        return (b'%PDF-fixture' if '/files/' in url else html.encode()),url,'application/pdf' if '/files/' in url else 'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    monkeypatch.setattr(reading,'_pdf_document',lambda data,url:{'text':'The actual full paper.','access_method':'pdf','url':url})
    doc=reading.read_public_document('https://example.org/viewer')
    assert calls==['https://example.org/viewer','https://example.org/files/full.pdf?download=1']
    assert doc['text']=='The actual full paper.'


def test_download_pdf_label_follows_extensionless_viewer_document(monkeypatch):
    html=b'<html><head><title>View of a research paper</title></head><body><a href="/article/download/45/24/142">Download <span>Download PDF</span></a><iframe src="" title="Article PDF"></iframe></body></html>'
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        return (b'%PDF-fixture' if '/download/' in url else html),url,'application/pdf' if '/download/' in url else 'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    monkeypatch.setattr(reading,'_pdf_document',lambda data,url:{'text':'Actual complete paper.','access_method':'pdf','url':url})
    doc=reading.read_public_document('https://example.org/article/view/45/24')
    assert calls==['https://example.org/article/view/45/24','https://example.org/article/download/45/24/142']
    assert doc['access_method']=='pdf' and doc['text']=='Actual complete paper.'


def test_pdf_paper_identity_checks_the_title_page_beyond_the_journal_header(monkeypatch):
    import fitz
    with fitz.open() as pdf:
        page=pdf.new_page();page.insert_text((50,50),'Journal of Computer Systems\nA learning example\nActual paper body about agent tool loops.')
        data=pdf.tobytes()
    monkeypatch.setattr(sources,'public_page',lambda url,**kwargs:(data,url,'application/pdf'))
    row=reading.inspect_candidate({'id':'paper','kind':'paper','title':'A learning example','url':'https://example.org/paper','eligible':False,'license':'unknown'})
    assert row['access']['method']=='pdf' and row['access']['status']=='read'
    assert row['inspection_sha256']==reading.sha256(data).hexdigest()
    assert not any(a.get('error_code')=='RESEARCH_IDENTITY_UNCONFIRMED' for a in row['access']['attempts'])
