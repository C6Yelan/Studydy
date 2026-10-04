import pytest
from runtime import research_sources as sources
from runtime.source_normalization import SourceError

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
