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
    evidence = {'page': 13, 'normalized_page': 3, 'source_name': '錄音.MP3', 'block_order': 2}
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
    assert all(text in markdown for text in ('我的想法', '舊註記', '原段落', '第 3 段', '區塊 2', '| 甲 | 乙 |', 'old'))
    word = Document(BytesIO(documents.note_docx(view)))
    assert word.tables[0].cell(1, 0).text == '甲'
    assert '舊註記' in '\n'.join(p.text for p in word.paragraphs)
    with pymupdf.open(stream=documents.note_pdf(view), filetype='pdf') as pdf:
        assert len(pdf) >= 2
        text = unicodedata.normalize('NFKC', ''.join(page.get_text() for page in pdf))
        assert all(t in text for t in ('我的想法', '舊註記', '原段落', '第 3 段', 'a = b + 1'))
        spans = [s for b in pdf[0].get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
        assert any('Bold' in s['font'] and '繁體中文' in s['text'] for s in spans)
        assert not any('Droid' in s['font'] and s['size'] == 14 for s in spans)


def test_diagnosis_overflow_is_explicit_and_stays_one_page():
    point = {'title': '需要複習的內容' * 8, 'correct_count': 1, 'answer_count': 3, 'evidence': [{'page': 2, 'source_name': '課程.youtube'}]}
    view = {'scope': 'material', 'strengths': [], 'weaknesses': [{'label': '概念', 'points': [point] * 80}],
            'recommendations': [{'label': '概念', 'reason': '80 個重點尚待加強。'}]}
    with pymupdf.open(stream=documents.diagnosis_pdf(view), filetype='pdf') as pdf:
        assert len(pdf) == 1
        assert '未列於摘要' in unicodedata.normalize('NFKC', pdf[0].get_text()) and '80' in pdf[0].get_text()
        assert '第 2 段' in unicodedata.normalize('NFKC', pdf[0].get_text())


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
    episode = {'hosts': {'host': '小安', 'guest': '小禾'}, 'claims': [{'label': '主題', 'text': '本集重點', 'evidence': [{'page': 9, 'normalized_page': 4, 'source_name': '錄音.wav'}]}]}
    text = export_podcast.transcript({'name': '節目', 'episodes': [episode]}, 0, {'segments': [
        {'start': 12, 'end': 24, 'turns': [{'speaker': 'host', 'text': '甲'}, {'speaker': 'guest', 'text': '乙'}]}]})
    assert all(s in text for s in ('小安', '小禾', '00:00:12', '00:00:24', '本集重點', '第 4 段'))


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
    cards = [{'concept_id': str(i), 'label': f'概念{i}', 'claims': [{'text': f'完整背面{i}', 'evidence': [{'page': i + 1, 'source_name': '錄音.mp3'}]}]} for i in range(9)]
    def read(owner, identity, *, offset=0, expected_version=None, dsn=None):
        if expected_version is not None:
            return {'cards': cards[offset:offset + 8]}
        return {'version': 1, 'name': '整組', 'navigation': cards, 'batch': {}}
    monkeypatch.setattr(export_cards.card_sets, 'read_card_study', read)
    monkeypatch.setattr(export_cards, '_root', lambda: tmp_path)
    images = [CardImage(concept_id=c['concept_id'], page=p, png=b64encode(png).decode()) for c in cards for p in range(2)]
    def exported(format):
        upload_id = export_cards.create('owner', 'deck', 1, format)['upload_id']
        for offset in range(0, len(images), 2):
            export_cards.append('owner', 'deck', upload_id, offset, images[offset:offset + 2])
        with export_cards.finish('owner', 'deck', upload_id) as (path, _):
            if format == 'apkg':
                assert (path.parent / 'collection.anki2').is_file()
            content = path.read_bytes()
        assert not (export_cards.work_root() / f'owner_{upload_id}').exists()
        return content
    with monkeypatch.context() as guard:
        def outside_temp(*args, **kwargs):
            raise AssertionError('Anki private SQLite must stay inside the upload workspace')
        guard.setattr('tempfile.mkstemp', outside_temp)
        package = exported('apkg')
    with zipfile.ZipFile(BytesIO(package)) as archive:
        assert len(json.loads(archive.read('media'))) == 18
        path = tmp_path / 'anki.db'; path.write_bytes(archive.read('collection.anki2'))
    with sqlite3.connect(path) as db:
        notes = db.execute('select guid, flds from notes').fetchall()
        assert len(notes) == 9
        assert all('完整背面' in fields and ' 段' in fields and '<img src=' in fields for _, fields in notes)
    with zipfile.ZipFile(BytesIO(exported('zip'))) as archive:
        assert len(archive.namelist()) == 18 and archive.testzip() is None
    with pymupdf.open(stream=exported('pdf'), filetype='pdf') as pdf:
        assert len(pdf) == 9
        assert all(len(page.get_images()) == 2 for page in pdf)


def test_long_summary_pdf_flows_onto_more_pages_without_shrinking_or_dropping_text():
    view = note()
    first = view['paragraphs'][0]
    view['paragraphs'] = [{**first, 'id': str(i), 'heading': f'繁體中文標題 {i}',
                           'text': f'第 {i} 段完整正文，不裁切也不縮字。' * 40} for i in range(12)]
    view['annotations'] = []
    with pymupdf.open(stream=documents.note_pdf(view), filetype='pdf') as pdf:
        body = [page for page in pdf if '來源與個人註記' not in page.get_text()]
        assert len(body) >= 3 and all(page.rect == pymupdf.paper_rect('a4') for page in body)
        text = unicodedata.normalize('NFKC', ''.join(page.get_text() for page in pdf))
        assert all(f'繁體中文標題 {i}' in text and f'第 {i} 段完整正文' in text for i in range(12))
        spans = [s for page in body for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
        assert min(s['size'] for s in spans) >= 12
        assert any('Bold' in s['font'] and '繁體中文' in s['text'] for s in spans)


@pytest.mark.parametrize('name', ['來源.mp3', '來源.M4A', '來源.wav', '來源.mp4', '來源.youtube'])
def test_media_sources_use_transcript_segments(name):
    assert documents.source_text({'source_name': name, 'page': 14, 'normalized_page': 4}) == f'{name} · 第 4 段'
    assert documents.source_text({'source_name': '來源.pdf', 'page': 14, 'normalized_page': 4}) == '來源.pdf · 第 4 頁'


def test_docx_cleans_xml_controls_without_changing_the_note_and_sets_author():
    view = note()
    p = view['paragraphs'][0]
    p['heading'] = '標題\x0c'; p['text'] = '正文\x00\x0b\x0c\x1f\ud800\uffff\t保留\n換行'
    p['formula'] = 'x = 1\x0c'
    p['table']['headers'][0] += '\x01'
    p['table']['rows'][0][0] += '\x0c'
    p['evidence'][0]['source_name'] = '錄音\x0c.mp3'
    view['annotations'][0]['body'] = '註記\x0c'
    doc = Document(BytesIO(documents.note_docx(view)))
    assert '正文\t保留\n換行' in [p.text for p in doc.paragraphs]
    assert doc.tables[0].cell(1, 0).text == '甲'
    assert '\x0c' in view['paragraphs'][0]['text']
    assert doc.core_properties.author == doc.core_properties.last_modified_by == 'Studydy'


def test_empty_diagnosis_has_no_empty_recommendations_or_zero_omissions():
    view = {'scope': 'material', 'strengths': [], 'weaknesses': [], 'recommendations': []}
    with pymupdf.open(stream=documents.diagnosis_pdf(view), filetype='pdf') as doc:
        text = unicodedata.normalize('NFKC', doc[0].get_text())
        assert '建議先複習' not in text and '另有' not in text
        assert len(doc) == 1
