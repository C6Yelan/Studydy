"""整組卡面打包；文字與來源由授權的卡組重新讀取。"""
from html import escape
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib

import genanki
import pymupdf
from . import card_sets
from .errors import SourceError
from .export_documents import source_text, zip_files, decode_png


def export(owner, identity, version, images, format, *, dsn=None):
    view = card_sets.read_card_study(owner, identity, dsn=dsn)
    if view['version'] != version or view['batch'] is None:
        raise SourceError('CARD_SET_CONFLICT')
    cards = []
    for offset in range(0, len(view['navigation']), 8):
        batch = card_sets.read_card_study(owner, identity, offset=offset, expected_version=version, dsn=dsn)
        cards.extend(batch['cards'])
    by_id = {c['concept_id']: c for c in cards}
    grouped = {c['concept_id']: [] for c in cards}
    files = []
    deck_key = hashlib.sha256(str(identity).encode()).hexdigest()[:12]
    for image in images:
        if image.concept_id not in grouped or image.page != len(grouped[image.concept_id]):
            raise SourceError('REQUEST_INVALID')
        data = decode_png(image.png)
        name = f'card-{deck_key}-{list(by_id).index(image.concept_id) + 1:04}-{image.page + 1:03}-{hashlib.sha256(data).hexdigest()[:8]}.png'
        grouped[image.concept_id].append((name, data))
        files.append((name, data))
    if any(not pages for pages in grouped.values()):
        raise SourceError('REQUEST_INVALID')
    # 再核對版本，避免跨批讀取期間卡組被編輯。
    card_sets.read_card_study(owner, identity, expected_version=version, dsn=dsn)
    if format == 'zip':
        return zip_files(files)
    if format == 'pdf':
        doc = pymupdf.open()
        # A4 每頁兩張；保持卡面原比例與完整續頁。
        for index, (_, data) in enumerate(files):
            if index % 2 == 0:
                page = doc.new_page(width=595.28, height=841.89)
            y = 28 + (index % 2) * 393
            page.insert_image(pymupdf.Rect(28, y, 567, y + 365), stream=data, keep_proportion=True)
        content = doc.tobytes(garbage=4, deflate=True)
        doc.close()
        return content
    with TemporaryDirectory(prefix='studydy-anki-') as directory:
        model = genanki.Model(1738150123, 'Studydy 概念卡',
            fields=[{'name': n} for n in ('正面', '背面', '卡面', '來源')],
            templates=[{'name': '概念', 'qfmt': '{{正面}}',
                'afmt': '{{FrontSide}}<hr id="answer">{{背面}}<br>{{卡面}}<hr>{{來源}}'}],
            css='.card { font-family: sans-serif; text-align: left; font-size: 20px; } img { max-width: 100%; }')
        deck_id = int(hashlib.sha256(str(identity).encode()).hexdigest()[:8], 16)
        deck = genanki.Deck(deck_id, view['name'])
        for card in cards:
            sources = list(dict.fromkeys(source_text(e) for c in card['claims'] for e in c['evidence']))
            note = genanki.Note(model=model, guid=genanki.guid_for(str(identity), card['concept_id']), fields=[
                escape(card['label']), '<br><br>'.join(escape(c['text']).replace('\n', '<br>') for c in card['claims']),
                ''.join(f'<img src="{name}">' for name, _ in grouped[card['concept_id']]),
                '<br>'.join(escape(s) for s in sources)])
            deck.add_note(note)
        package = genanki.Package(deck)
        for name, data in files:
            path = Path(directory) / name
            path.write_bytes(data)
            package.media_files.append(str(path))
        output = BytesIO()
        package.write_to_file(output)
        return output.getvalue()
