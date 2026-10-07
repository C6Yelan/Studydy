from pathlib import Path
from benchmark import fetch_record, qualify, sources


def test_snapshot_write_failure_cannot_count_as_full_text(tmp_path,monkeypatch):
    sample=dict(id='fixture',title='Fixture',markers=['source marker'],license='PSF-2.0',
        license_url='https://docs.python.org/3/license.html',license_verified=True,license_link_marker='/license.html')
    body=('<title>Fixture</title><a href="/license.html">License</a><main>'+('source marker '*20)+'</main>').encode()
    monkeypatch.setattr(sources,'fetch',lambda *a,**kw:(body,'https://docs.python.org/fixture','text/html'))
    original=Path.write_text
    def fail_text(path,*args,**kwargs):
        if path.suffix=='.txt':raise OSError('fixture storage unavailable')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',fail_text)
    result=fetch_record(sample,'A','https://docs.python.org/fixture',tmp_path)
    qualify([result])
    assert result['outcome']=='failed'
    assert result['failure_reason']=='fixture storage unavailable'
    assert not result['qualified_full_text']


def test_missing_html_title_is_unverified_not_a_claimed_doi_mismatch():
    result=dict(id='xml',method='C',title='',outcome='retrieved',identity_match=False,
        license='unknown',extraction_quality='markers-present',failure_reason='IDENTITY_MISMATCH')
    qualify([result])
    assert result['identity_match'] is None
    assert result['failure_reason']=='LICENSE_OR_FULL_TEXT_UNCONFIRMED'
    assert not result['qualified_full_text']


def test_raw_xml_and_unsupported_license_do_not_inflate_coverage():
    base=dict(method='A',outcome='retrieved',title='Fixture',identity_match=True,license='cc-by',extraction_quality='xml-not-supported-in-production')
    rows=[base.copy(),dict(base,method='B',extraction_quality='xml-body-poc'),
        dict(base,method='B',extraction_quality='xml-body-poc',license='unsupported:cc-by-nc-sa'),
        dict(base,method='B',extraction_quality='xml-body-poc',identity_match=False)]
    qualify(rows)
    assert [r['qualified_full_text'] for r in rows]==[False,True,False,False]
