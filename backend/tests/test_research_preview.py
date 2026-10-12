from runtime import research_preview as preview


def test_crossref_abstract_is_used_before_fetching_full_document(monkeypatch):
    monkeypatch.setattr(preview.sources,'_json',lambda url:{'message':{'abstract':'<jats:p>Memory &amp; tools.</jats:p>'}})
    monkeypatch.setattr(preview.sources,'public_page',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Full document was fetched')))
    result=preview.prepare_preview({'kind':'paper','doi':'10.1234/example','url':'https://example.org/paper'})
    assert result['_preview_text']=='Memory & tools.'
    assert result['preview']['kind']=='abstract'


def test_missing_summary_keeps_failure_and_does_not_invent_exclusion(monkeypatch):
    def unavailable(*a,**kw):raise preview.sources.SourceAccessError('RESEARCH_SOURCE_BLOCKED')
    monkeypatch.setattr(preview.sources,'public_page',unavailable)
    result=preview.prepare_preview({'kind':'web','url':'https://example.org/post'})
    assert result['preview']['status']=='missing' and result['_preview_text']==''
    assert result['preview']['attempts'][0]['error_code']=='RESEARCH_SOURCE_BLOCKED'


def test_preview_records_excerpt_and_original_source(monkeypatch):
    result=preview.prepare_preview({'kind':'web','url':'https://example.org/post','description':'a'*(preview.PREVIEW_CHARS+1)})
    assert result['preview']['excerpt'] and result['preview']['url']=='https://example.org/post'
    assert len(result['_preview_text'])==preview.PREVIEW_CHARS
