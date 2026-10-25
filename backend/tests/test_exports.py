"""合成內容的離線格式完整性；不呼叫模型。"""
from base64 import b64encode
from io import BytesIO
import json
import sqlite3
import wave
import unicodedata
import zipfile

import av
import pymupdf
import pytest
from docx import Document

from runtime import export_documents as documents, export_map, export_podcast, export_cards
from runtime.api.export_routes import CardImage


def note():
    evidence = {'page': 13, 'normalized_page': 3, 'source_name': '教材.pdf', 'block_order': 2}
    p = {'id': 'p', 'heading': '繁體中文粗體標題', 'text': '完整條件：x < 3，且不可省略。',
         'formula': 'a = b + 1', 'table': {'headers': ['條件', '結果'], 'rows': [['甲', '乙']]}, 'evidence': [evidence]}
    return {'material_id': 'm', 'knowledge_structure_revision': 'revision', 'kind': 'summary',
            'source_status': {'quality': 'accepted', 'processing': 'succeeded', 'decision': 'retain'},
            'paragraphs': [p], 'annotations': [
                {'paragraph_id': 'p', 'body': '我的想法'},
                {'paragraph_id': None, 'body': '舊註記', 'original_revision': 'old', 'original_paragraph': {**p, 'heading': '原段落'}}]}


def test_notes_preserve_body_table_formula_sources_and_orphaned_annotations():
    view = note()
    markdown = documents.note_markdown(view)
    assert all(text in markdown for text in ('我的想法', '舊註記', '原段落', '第 3 頁', '區塊 2', '| 甲 | 乙 |', 'old'))
    word = Document(BytesIO(documents.note_docx(view)))
    assert word.tables[0].cell(1, 0).text == '甲'
    assert '舊註記' in '\n'.join(p.text for p in word.paragraphs)
    with pymupdf.open(stream=documents.note_pdf(view), filetype='pdf') as pdf:
        assert len(pdf) >= 2
        text = unicodedata.normalize('NFKC', ''.join(page.get_text() for page in pdf))
        assert all(t in text for t in ('我的想法', '舊註記', '原段落', '第 3 頁', 'a = b + 1'))
        spans = [s for b in pdf[0].get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
        assert any('Bold' in s['font'] and '繁體中文' in s['text'] for s in spans)
        assert not any('Droid' in s['font'] and s['size'] == 14 for s in spans)


def test_diagnosis_overflow_is_explicit_and_stays_one_page():
    point = {'title': '需要複習的內容' * 8, 'correct_count': 1, 'answer_count': 3, 'evidence': [{'page': 2, 'source_name': '教材'}]}
    view = {'scope': 'material', 'strengths': [], 'weaknesses': [{'label': '概念', 'points': [point] * 80}],
            'recommendations': [{'label': '概念', 'reason': '80 個重點尚待加強。'}]}
    with pymupdf.open(stream=documents.diagnosis_pdf(view), filetype='pdf') as pdf:
        assert len(pdf) == 1
        assert '未列於摘要' in unicodedata.normalize('NFKC', pdf[0].get_text()) and '80' in pdf[0].get_text()


def test_mp3_is_decodable_and_transcript_uses_saved_names_and_time():
    source = BytesIO()
    with wave.open(source, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b'\x00\x01' * 24000)
    source.seek(0)
    mp3 = export_podcast.mp3(source)
    with av.open(BytesIO(mp3)) as audio:
        frames = list(audio.decode(audio=0))
        assert sum(f.samples for f in frames) >= 44000
    episode = {'hosts': {'host': '小安', 'guest': '小禾'}, 'claims': [{'label': '主題', 'text': '本集重點', 'evidence': []}]}
    text = export_podcast.transcript({'name': '節目', 'episodes': [episode]}, 0, {'segments': [
        {'start': 12, 'end': 24, 'turns': [{'speaker': 'host', 'text': '甲'}, {'speaker': 'guest', 'text': '乙'}]}]})
    assert all(s in text for s in ('小安', '小禾', '00:00:12', '00:00:24', '本集重點'))


def test_map_covers_all_regions_and_cross_region_relations():
    view = {'material_id': 'm', 'knowledge_structure_revision': 'r',
            'concepts': [{'concept_id': str(i), 'label': f'概念{i}', 'claim_count': 1} for i in range(30)],
            'relations': [{'source_concept_id': '0', 'target_concept_id': '29', 'type': 'prerequisite'}]}
    regions = {'regions': [{'label': '區域一', 'concept_ids': [str(i) for i in range(15)]},
                           {'label': '區域二', 'concept_ids': [str(i) for i in range(15, 30)]}]}
    md = export_map.markdown(view, regions)
    assert all(f'概念{i}' in md for i in range(30))


def test_card_package_has_all_images_full_text_sources_and_stable_ids(monkeypatch, tmp_path):
    doc = pymupdf.open(); page = doc.new_page(width=120, height=76)
    png = page.get_pixmap().tobytes('png'); doc.close()
    cards = [{'concept_id': str(i), 'label': f'概念{i}', 'claims': [{'text': f'完整背面{i}', 'evidence': [{'page': i + 1, 'source_name': '教材'}]}]} for i in range(9)]
    def read(owner, identity, *, offset=0, expected_version=None, dsn=None):
        if expected_version is not None:
            return {'cards': cards[offset:offset + 8]}
        return {'version': 1, 'name': '整組', 'navigation': cards, 'batch': {}}
    monkeypatch.setattr(export_cards.card_sets, 'read_card_study', read)
    images = [CardImage(concept_id=c['concept_id'], page=p, png=b64encode(png).decode()) for c in cards for p in range(2)]
    package = export_cards.export('owner', 'deck', 1, images, 'apkg')
    with zipfile.ZipFile(BytesIO(package)) as archive:
        assert len(json.loads(archive.read('media'))) == 18
        path = tmp_path / 'anki.db'; path.write_bytes(archive.read('collection.anki2'))
    with sqlite3.connect(path) as db:
        notes = db.execute('select guid, flds from notes').fetchall()
        assert len(notes) == 9
        assert all('完整背面' in fields and '第 ' in fields and '<img src=' in fields for _, fields in notes)
    with pymupdf.open(stream=export_cards.export('owner', 'deck', 1, images, 'pdf'), filetype='pdf') as pdf:
        assert len(pdf) == 9
        assert all(len(page.get_images()) == 2 for page in pdf)


def test_summary_bold_chinese_headings_preserve_the_published_one_page_body_layout():
    from runtime.note_content import summary_pdf, summary_layout
    paragraphs = [{'heading': '繁體中文標題 English 123',
                   'text': 'important physiological phenomenon in learning ' * 62,
                   'formula': None, 'table': None}]
    assert summary_layout(paragraphs)['fits']
    with pymupdf.open(stream=summary_pdf(paragraphs), filetype='pdf') as before, pymupdf.open(
            stream=documents.summary_with_bold_headings(paragraphs), filetype='pdf') as after:
        assert len(after) == 1 and after[0].rect == pymupdf.paper_rect('a4')
        def spans(page):
            return [s for b in page.get_text('dict')['blocks'] for line in b.get('lines', []) for s in line['spans']]
        original = [s for s in spans(before[0]) if s['size'] == 12]
        result = [s for s in spans(after[0]) if s['size'] == 12]
        assert [(s['text'], s['font']) for s in result] == [(s['text'], s['font']) for s in original]
        for old, new in zip(original, result):
            # PDF 重寫座標的浮點序列化誤差，不容許重新換行或可見的位置移動。
            assert new['origin'] == pytest.approx(old['origin'], abs=.001)
        assert unicodedata.normalize('NFKC', after[0].get_text(sort=True)) == unicodedata.normalize('NFKC', before[0].get_text(sort=True))
        assert any('Bold' in s['font'] and '繁體中文' in s['text'] for s in spans(after[0]))
        assert any(s['font'] == 'NimbusSans-Bold' and 'English 123' in s['text'] for s in spans(after[0]))
