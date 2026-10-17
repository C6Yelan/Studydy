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
        if url.endswith('/article'):return b'<html><title>Actual paper</title><a href="/paper.pdf">PDF</a></html>',url,'text/html'
        return b'%PDF-fixture',url,'application/pdf'
    monkeypatch.setattr(sources,'public_page',fetch)
    monkeypatch.setattr(reading,'_pdf_document',lambda data,url:{'text':'Actual paper body','title_page_text':'Actual paper body','access_method':'pdf','url':url})
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
    html=BODY.replace('</head>','<meta name="citation_title" content="A learning example"><meta name="citation_pdf_url" content="https://example.org/full.pdf"></head>')
    html=html.replace('</article>','<h2>Discussion</h2><p>The simulation explains the conditions of the observed memory effect.</p></article>')
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        return (b'%PDF-fixture' if url.endswith('.pdf') else html.encode()),url,'application/pdf' if url.endswith('.pdf') else 'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    monkeypatch.setattr(reading,'_pdf_document',lambda data,url:{'text':'Full paper including the methods and limitations.','title_page_text':'A learning example','access_method':'pdf','url':url})
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


def test_complete_paper_html_is_read_even_when_its_pdf_is_blocked(monkeypatch):
    html=BODY.replace('</head>','<meta name="citation_title" content="A learning example"><meta name="citation_pdf_url" content="https://example.org/full.pdf"></head>')
    html=html.replace('</article>','<h2>Discussion</h2><p>This is a simulation of memory, not an experiment on human participants. Its predicted effect depends on the stated learning rule.</p></article>')
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        if url.endswith('.pdf'):raise sources.SourceAccessError('RESEARCH_SOURCE_BLOCKED',url=url,status=403)
        return html.encode(),url,'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    checked=reading.inspect_candidate({'id':'paper','kind':'paper','url':'https://example.org/article','title':'A learning example','eligible':False,'license':'unknown'})
    assert checked['eligible'] and checked['access']['method']=='html'
    assert 'not an experiment on human participants' in checked['_reading_text']
    assert calls==['https://example.org/article','https://example.org/full.pdf']
    data,metadata=checked['_acquired_document']
    def forbidden(*args,**kwargs):raise AssertionError('snapshot must not download again')
    monkeypatch.setattr(sources,'public_page',forbidden)
    reread=reading.inspect_candidate(checked,document=(data,'text/plain','.txt',metadata))
    assert reread['_reading_text']==checked['_reading_text']
    assert reread['inspection_sha256']==checked['inspection_sha256']
    assert reread['inspection_text_sha256']==checked['inspection_text_sha256']


def test_indexed_paper_license_applies_to_its_verified_html_body_after_pdf_failure(monkeypatch):
    html=BODY.replace('<link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">','')
    html=html.replace('</head>','<meta name="citation_title" content="A learning example"><meta name="citation_pdf_url" content="https://example.org/full.pdf"></head>')
    html=html.replace('</article>','<h2>Discussion</h2><p>The model predicts memory benefits only under these learning conditions.</p></article>')
    def fetch(url,**kwargs):
        if url.endswith('.pdf'):raise sources.SourceAccessError('RESEARCH_SOURCE_BLOCKED',url=url,status=403)
        return html.encode(),url,'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    monkeypatch.setattr(sources,'_unpaywall_locations',lambda c:[])
    for entry in ('https://example.org/full.pdf','https://example.org/article'):
        checked=reading.inspect_candidate({'id':'paper','kind':'paper','url':'https://example.org/article',
            'download_url':entry,'title':'A learning example','eligible':True,
            'license':'cc-by','license_url':'https://creativecommons.org/licenses/by/4.0/','version':'publishedVersion'})
        assert checked['eligible'] and checked['access']['method']=='html'
        assert checked['license']=='cc-by' and checked['_acquired_document'][1]['version']=='publishedVersion'
        assert any(a.get('http_status')==403 for a in checked['access']['attempts'])


def test_pdf_acquisition_format_failure_does_not_hide_a_valid_licensed_html_article(monkeypatch):
    monkeypatch.setattr(sources,'public_page',lambda url,**kw:(BODY.encode(),url,'text/html'))
    monkeypatch.setattr(sources,'_unpaywall_locations',lambda c:[])
    checked=reading.inspect_candidate({'id':'paper','kind':'paper','url':'https://example.org/article',
        'download_url':'https://example.org/article','title':'A learning example','eligible':True,
        'license':'cc-by','license_url':'https://creativecommons.org/licenses/by/4.0/','version':'publishedVersion'})
    assert checked['eligible'] and checked['access']['method']=='html'
    assert 'if x < 10:' in checked['_reading_text']


def test_structured_abstract_does_not_become_full_body_when_pdf_is_unavailable(monkeypatch):
    html=BODY.replace('</head>','<meta name="citation_title" content="A learning example"><meta name="citation_pdf_url" content="https://example.org/full.pdf"></head>')
    html=html.replace('</article>','<section id="abstract"><h2>Methods</h2><p>This abstract reports a simulation of memory with a learning rule.</p><h2>Results</h2><p>The abstract reports a positive result.</p></section></article>')
    def fetch(url,**kwargs):
        if url.endswith('.pdf'):raise sources.SourceAccessError('RESEARCH_SOURCE_BLOCKED',url=url,status=403)
        return html.encode(),url,'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    doc=reading.read_public_document('https://example.org/article')
    assert doc['fulltext_unavailable'] and doc['access_method']=='landing_page'


def test_flat_structured_abstract_is_not_mistaken_for_body_sections(monkeypatch):
    html=BODY.replace('</head>','<meta name="citation_title" content="A learning example"><meta name="citation_pdf_url" content="https://example.org/full.pdf"></head>')
    html=html.replace('</article>','<h2>Abstract</h2><h3>Methods</h3><p>This abstract reports a simulation of memory.</p><h3>Results</h3><p>The abstract reports a positive result.</p></article>')
    def fetch(url,**kwargs):
        if url.endswith('.pdf'):raise sources.SourceAccessError('RESEARCH_SOURCE_BLOCKED',url=url,status=403)
        return html.encode(),url,'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    assert reading.read_public_document('https://example.org/article')['fulltext_unavailable']


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
    monkeypatch.setattr(reading,'_pdf_document',lambda data,url:{'text':'The actual full paper.','title_page_text':'A learning example','access_method':'pdf','url':url})
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
    monkeypatch.setattr(reading,'_pdf_document',lambda data,url:{'text':'Actual complete paper.','title_page_text':'a research paper','access_method':'pdf','url':url})
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


def test_arxiv_declared_license_binds_the_same_versioned_pdf_and_is_rechecked(monkeypatch):
    import fitz,pytest
    from hashlib import sha256
    abstract='https://arxiv.org/abs/2401.00001v2';pdf_url='https://arxiv.org/pdf/2401.00001v2'
    html=b'''<html><head><meta name="citation_title" content="A sampling example">
    <meta name="citation_author" content="Fixture Author">
    <meta name="citation_pdf_url" content="https://arxiv.org/pdf/2401.00001"></head>
    <body><div class="abs-license"><a title="Rights to this article" href="https://creativecommons.org/licenses/by/4.0/">CC BY</a></div></body></html>'''
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72,72),'A sampling example\nSampling chooses a token using the probability distribution.')
        pdf.set_metadata({'title':'A sampling example'});data=pdf.tobytes()
    calls=[];revoked=False
    def fetch(url,**kwargs):
        calls.append(url)
        if url==abstract:
            body=html.replace(b'https://creativecommons.org/licenses/by/4.0/',b'https://arxiv.org/licenses/nonexclusive-distrib/1.0/') if revoked else html
            return body,url,'text/html'
        assert url==pdf_url
        return data,url,'application/pdf'
    monkeypatch.setattr(sources,'public_page',fetch)
    candidate={'id':'found','kind':'web','url':'https://arxiv.org/html/2401.00001v2','title':'Search result title',
        'eligible':False,'license':'unknown','screening':{'decision':'reference','basis':'importability','reason':'Unchecked'}}
    checked=reading.inspect_candidate(candidate)
    assert checked['eligible'] and checked['kind']=='paper' and checked['screening']['decision']=='read'
    assert checked['download_url']==pdf_url and checked['version']=='arxiv:2401.00001v2'
    assert checked['inspection_sha256']==sha256(data).hexdigest()
    assert checked['license_snapshot_sha256']==sha256(html).hexdigest()
    acquired,media,suffix,metadata=sources.acquire(checked)
    assert acquired==data and media=='application/pdf' and suffix=='.pdf'
    assert abstract in metadata['license_text'] and metadata['license_snapshot_sha256']==sha256(html).hexdigest()
    revoked=True
    with pytest.raises(SourceError,match='RESEARCH_LICENSE_UNCONFIRMED'):sources.acquire(checked)
    assert calls[-1]==abstract


def test_arxiv_license_does_not_apply_to_another_paper_or_incidental_cc_link(monkeypatch):
    import pytest
    abstract='https://arxiv.org/abs/2401.00001v1'
    template='''<html><head><meta name="citation_title" content="Fixture">
    <meta name="citation_pdf_url" content="PDF"></head><body>LICENSE</body></html>'''
    license='<div class="abs-license"><a title="Rights to this article" href="https://creativecommons.org/licenses/by/4.0/">CC BY</a></div>'
    for pdf,terms,error in [('https://arxiv.org/pdf/2401.00002',license,'RESEARCH_IDENTITY_UNCONFIRMED'),
                            ('https://arxiv.org/pdf/2401.00001','<a href="https://creativecommons.org/licenses/by/4.0/">A cited source</a>','RESEARCH_LICENSE_UNCONFIRMED')]:
        html=template.replace('PDF',pdf).replace('LICENSE',terms).encode()
        monkeypatch.setattr(sources,'public_page',lambda url,**kwargs:(html,url,'text/html'))
        with pytest.raises(SourceError,match=error):sources.arxiv_metadata(abstract)
    assert sources.arxiv_abstract_url('https://arxiv.org.attacker.example/pdf/2401.00001') is None
    assert sources.arxiv_abstract_url('https://example.org/pdf/2401.00001') is None


def test_declared_http_fulltext_is_upgraded_before_downloading(monkeypatch):
    html=BODY.replace('</head>','<meta name="citation_pdf_url" content="http://example.org/full.pdf"></head>')
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        assert url.startswith('https://')
        return (b'%PDF-fixture' if url.endswith('.pdf') else html.encode()),url,'application/pdf' if url.endswith('.pdf') else 'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    monkeypatch.setattr(reading,'_pdf_document',lambda data,url:{'text':'Actual full paper.','title_page_text':'A learning example','access_method':'pdf','url':url})
    doc=reading.read_public_document('https://example.org/article')
    assert calls==['https://example.org/article','https://example.org/full.pdf']
    assert doc['access_method']=='pdf'


def test_licensed_landing_page_never_becomes_body_when_fulltext_is_blocked(monkeypatch):
    html=BODY.replace('</head>','<meta name="citation_pdf_url" content="https://example.org/full.pdf"></head>')
    def fetch(url,**kwargs):
        if url.endswith('.pdf'):raise sources.SourceAccessError('RESEARCH_SOURCE_BLOCKED',url=url,status=403)
        return html.encode(),url,'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    checked=reading.inspect_candidate({'id':'paper','kind':'paper','url':'https://example.org/article','title':'A learning example','eligible':False,'license':'unknown'})
    assert checked['access']['fulltext_unavailable'] and checked['access']['status']=='unavailable'
    assert not checked['eligible'] and not checked['_reading_text']
    assert checked['access']['attempts'][-1]['http_status']==403


def test_paper_metadata_license_survives_a_landing_page_without_pdf_url(monkeypatch):
    import fitz
    landing='https://example.org/article';pdf_url='https://example.org/paper.pdf'
    html=b'<html><head><meta name="citation_pdf_url" content="http://example.org/paper.pdf"></head><body><h1>A sampling example</h1></body></html>'
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72,72),'A sampling example\nThe methods and results of the actual full paper.')
        pdf.set_metadata({'title':'A sampling example'});data=pdf.tobytes()
    def fetch(url,**kwargs):
        assert url in (landing,pdf_url)
        return (data if url==pdf_url else html),url,'application/pdf' if url==pdf_url else 'text/html'
    monkeypatch.setattr(sources,'public_page',fetch)
    location=sources._location(None,'cc-by','publishedVersion',landing)
    candidate=sources._paper('paper','A sampling example','Author',2026,None,landing,[location],'OpenAlex')
    assert candidate['eligible'] and candidate['download_url']==landing
    acquired,media,suffix,metadata=sources.acquire(candidate)
    assert acquired==data and media=='application/pdf' and metadata['download_url']==pdf_url
    checked=reading.inspect_candidate(candidate)
    assert checked['eligible'] and 'actual full paper' in checked['_reading_text']


def test_declared_download_cannot_substitute_another_article(monkeypatch):
    def fetch(url, **kwargs):
        if url.endswith('/article'):
            return b'<html><title>Expected article</title><a href="/other.pdf">PDF</a></html>', url, 'text/html'
        return b'%PDF-fixture', url, 'application/pdf'
    monkeypatch.setattr(sources, 'public_page', fetch)
    monkeypatch.setattr(reading, '_pdf_document', lambda data,url: {
        'text':'Different article body', 'title_page_text':'Unrelated article', 'url':url, 'access_method':'pdf'})
    import pytest
    attempts=[]
    doc=reading.read_public_document('https://example.org/article',attempts=attempts)
    assert doc.get('fulltext_unavailable') and doc['access_method']=='landing_page'
    assert any(a.get('error_code')=='RESEARCH_IDENTITY_UNCONFIRMED' for a in attempts)


def test_real_repeated_failure_stops_but_unsent_host_cooldown_does_not(monkeypatch):
    c={'id':'one','kind':'web','url':'https://example.org/article','title':'Example','eligible':False}
    def blocked(url, **kwargs):
        raise sources.SourceAccessError('RESEARCH_RATE_LIMITED', url=url, status=429, retry_at=9999999999)
    monkeypatch.setattr(sources,'public_page',blocked)
    first=reading.inspect_candidate(c)
    assert first['access']['failed_acquisitions']==1 and not first['access'].get('exhausted')
    second=reading.inspect_candidate(first)
    assert second['access']['exhausted'] and 'retry_at' not in second['access']
    def cooldown(url, **kwargs):
        raise sources.SourceAccessError('RESEARCH_RATE_LIMITED', retry_at=9999999999)
    monkeypatch.setattr(sources,'public_page',cooldown)
    cooling=reading.inspect_candidate(first)
    assert cooling['access']['failed_acquisitions']==1 and not cooling['access'].get('exhausted')
