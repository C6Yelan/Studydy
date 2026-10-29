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


@pytest.mark.parametrize('prefix,token', [('x ' * 34, r'\theta'), ('x ' * 34, 'monotonically'), ('x ' * 35, '0.8')])
def test_plain_pdf_wrap_preserves_formula_commands_words_and_numbers(tmp_path, prefix, token):
    source = tmp_path / 'formula.txt'
    target = tmp_path / 'formula.pdf'
    source.write_text(prefix + token + ' remains intact.', encoding='utf-8')
    renderer.story_pdf(source, target)
    with pymupdf.open(target) as document:
        text = ' '.join(page.get_text() for page in document)
    assert token in text
    assert 'remains intact.' in text


def test_plain_wrap_keeps_chinese_width_and_all_original_characters():
    text = '先驗機率與 ' + r'\displaystyle P(G|T)=0.25' + ' 保留原文。' * 30
    parts = renderer.wrap_text(text)
    assert ''.join(parts) == text
    assert all(sum(2 if renderer.unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in part) <= 72 for part in parts)
