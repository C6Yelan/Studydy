import pymupdf
import pytest

from document_normalization import renderer


def test_html_attributes_in_plain_teaching_text_are_preserved_without_loading_resources(tmp_path):
    source=tmp_path/'example.txt';target=tmp_path/'example.pdf'
    source.write_text('A cache example: <link href="https://example.invalid/style.css">\nAn image example: <img src="https://example.invalid/a.png">',encoding='utf-8')
    renderer.story_pdf(source,target)
    with pymupdf.open(target) as doc:text=''.join(page.get_text() for page in doc)
    assert 'href=' in text and 'src=' in text
    assert '<link' in text and '<img' in text


@pytest.mark.parametrize('markup',['<img src="https://example.invalid/a.png">','<SCRIPT>unsafe()</SCRIPT>',
    '<a title=">" href="https://example.invalid">link</a>'])
def test_real_external_resource_markup_is_still_rejected(tmp_path,monkeypatch,markup):
    monkeypatch.setattr(renderer,'text_html',lambda source:(markup,{}))
    with pytest.raises(ValueError,match='UNSAFE_RENDER_HTML'):
        renderer.story_pdf(tmp_path/'unused.txt',tmp_path/'out.pdf')
