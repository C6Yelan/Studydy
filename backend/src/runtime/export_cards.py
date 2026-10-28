"""整組卡面分批暫存與磁碟打包；不佔用容器的 1 GiB /tmp。"""
from contextlib import closing, contextmanager
from itertools import count, chain
from html import escape
from uuid import uuid4
import fcntl
import hashlib
import json
import shutil
import sqlite3
import time
import zipfile
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import genanki
import pymupdf
from . import card_sets
from .errors import SourceError
from .export_documents import source_text, decode_png
from .storage.artifacts import _root

UPLOAD_TTL = 30 * 60
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_OWNER_UPLOADS = 2
MAX_CONCEPT_PAGES = 16
MAX_IMAGES = 2000


def work_root():
    path = _root() / '.export-work'
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def cleanup_stale_uploads():
    cutoff = time.time() - UPLOAD_TTL
    for directory in work_root().iterdir():
        if not directory.is_dir():
            continue
        try:
            with (directory / 'state.json').open('r+') as stream:
                # 正在上傳、組檔或下載的工作不能被維護程序移除。
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if (directory / 'state.json').stat().st_mtime < cutoff:
                    shutil.rmtree(directory)
        except BlockingIOError:
            continue
        except FileNotFoundError:
            try:
                if directory.stat().st_mtime < cutoff:
                    shutil.rmtree(directory)
            except FileNotFoundError:
                pass


def create(owner, identity, version, format, *, dsn=None):
    view = card_sets.read_card_study(owner, identity, dsn=dsn)
    if view['version'] != version or view['batch'] is None:
        raise SourceError('CARD_SET_CONFLICT')
    cleanup_stale_uploads()
    root = work_root()
    # 以磁碟鎖原子檢查名額並建立工作；並行請求／不同程序不能超收同一 owner。
    with (root / '.quota.lock').open('a') as quota:
        fcntl.flock(quota, fcntl.LOCK_EX)
        active = sum(path.is_dir() for path in root.glob(f'{owner}_*'))
        if active >= MAX_OWNER_UPLOADS:
            raise SourceError('EXPORT_ACTIVE_LIMIT')
        upload_id = uuid4()
        directory = root / f'{owner}_{upload_id}'
        directory.mkdir(mode=0o700)
        state = {'owner': str(owner), 'identity': str(identity), 'version': version, 'format': format,
                 'material_name': view['material_name'], 'concept_ids': [c['concept_id'] for c in view['navigation']], 'images': []}
        (directory / 'state.json').write_text(json.dumps(state))
    return {'upload_id': str(upload_id)}


@contextmanager
def upload(owner, identity, upload_id):
    directory = work_root() / f'{owner}_{upload_id}'
    try:
        stream = (directory / 'state.json').open('r+')
    except FileNotFoundError:
        raise SourceError('RESOURCE_NOT_FOUND') from None
    with stream:
        # 同一工作仍在串流時立即回覆忙碌，不占產檔名額等慢速下載釋鎖。
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SourceError('EXPORT_IN_PROGRESS') from None
        if not directory.exists():
            raise SourceError('RESOURCE_NOT_FOUND')
        state = json.load(stream)
        if state['owner'] != str(owner) or state['identity'] != str(identity):
            raise SourceError('RESOURCE_NOT_FOUND')
        yield directory, state, stream


def append(owner, identity, upload_id, offset, images, *, dsn=None):
    with upload(owner, identity, upload_id) as (directory, state, stream):
        card_sets.read_card_study(owner, identity, expected_version=state['version'], dsn=dsn)
        if offset != len(state['images']):
            raise SourceError('CARD_SET_CONFLICT')
        if offset + len(images) > MAX_IMAGES:
            raise SourceError('EXPORT_PAGE_LIMIT')
        entries = list(state['images'])
        decoded = []
        deck_key = hashlib.sha256(str(identity).encode()).hexdigest()[:12]
        for image in images:
            if image.concept_id not in state['concept_ids'] or image.page != sum(e['concept_id'] == image.concept_id for e in entries):
                raise SourceError('REQUEST_INVALID')
            if image.page >= MAX_CONCEPT_PAGES:
                raise SourceError('EXPORT_PAGE_LIMIT')
            data = decode_png(image.png)
            position = state['concept_ids'].index(image.concept_id) + 1
            name = f'card-{deck_key}-{position:04}-{image.page + 1:03}-{hashlib.sha256(data).hexdigest()[:8]}.png'
            entries.append({'concept_id': image.concept_id, 'page': image.page, 'name': name})
            decoded.append((name, data))
        # 計入實際 PNG（包括中斷寫入留下的檔案），不信任可能落後的記帳值。
        stored = sum(path.stat().st_size for path in directory.glob('*.png'))
        if stored + sum(len(data) for _, data in decoded) > MAX_UPLOAD_BYTES:
            raise SourceError('EXPORT_SIZE_LIMIT')
        for name, data in decoded:
            (directory / name).write_bytes(data)
        state['images'] = entries
        stream.seek(0)
        json.dump(state, stream)
        stream.truncate()
        return {'received': len(entries)}


def cancel(owner, identity, upload_id):
    with upload(owner, identity, upload_id) as (directory, _, __):
        shutil.rmtree(directory)
    return {'cancelled': True}


@contextmanager
def finish(owner, identity, upload_id, *, dsn=None):
    with upload(owner, identity, upload_id) as (directory, state, _):
        try:
            result = render(owner, identity, state, directory, dsn=dsn)
            # HTTP dependency 保持此 lease 到串流結束，取消連線與例外也會清理。
            yield result, state['format'], state['material_name']
        finally:
            shutil.rmtree(directory)


def render(owner, identity, state, directory, *, dsn=None):
    version, format = state['version'], state['format']
    view = card_sets.read_card_study(owner, identity, dsn=dsn)
    if view['version'] != version or view['batch'] is None:
        raise SourceError('CARD_SET_CONFLICT')
    cards = []
    for offset in range(0, len(view['navigation']), 8):
        cards.extend(card_sets.read_card_study(owner, identity, offset=offset, expected_version=version, dsn=dsn)['cards'])
    grouped = {c['concept_id']: [] for c in cards}
    for item in state['images']:
        grouped[item['concept_id']].append(item['name'])
    if any(not pages for pages in grouped.values()):
        raise SourceError('REQUEST_INVALID')
    card_sets.read_card_study(owner, identity, expected_version=version, dsn=dsn)
    files = [directory / name for card in cards for name in grouped[card['concept_id']]]
    result = directory / f'export.{format}'
    if format == 'zip':
        used = set()
        with zipfile.ZipFile(result, 'w', zipfile.ZIP_STORED) as archive:
            for card in cards:
                base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', card['label']).strip(' .') or '概念卡'
                names = grouped[card['concept_id']]
                for page, name in enumerate(names, 1):
                    stem = base if len(names) == 1 else f'{base}-{page}'
                    output, suffix = f'{stem}.png', 2
                    while output.casefold() in used:
                        output = f'{stem}-{suffix}.png'
                        suffix += 1
                    used.add(output.casefold())
                    info = zipfile.ZipInfo(output, datetime.now(ZoneInfo('Asia/Taipei')).timetuple()[:6])
                    archive.writestr(info, (directory/name).read_bytes())
    elif format == 'pdf':
        with pymupdf.open() as doc:
            for index, path in enumerate(files):
                if index % 2 == 0:
                    page = doc.new_page(width=595.28, height=841.89)
                y = 28 + (index % 2) * 393
                page.insert_image(pymupdf.Rect(28, y, 567, y + 365), filename=str(path), keep_proportion=True)
            stamp = datetime.now(ZoneInfo('Asia/Taipei')).strftime("D:%Y%m%d%H%M%S+08'00'")
            doc.set_metadata({'creationDate':stamp, 'modDate':stamp})
            doc.save(result, garbage=4, deflate=True)
    else:
        model = genanki.Model(1738150123, 'Studydy 概念卡',
            fields=[{'name': n} for n in ('正面', '背面', '卡面', '來源')],
            templates=[{'name': '概念', 'qfmt': '{{正面}}',
                'afmt': '{{FrontSide}}<hr id="answer">{{背面}}<br>{{卡面}}<hr>{{來源}}'}],
            css='.card { font-family: sans-serif; text-align: left; font-size: 20px; } img { max-width: 100%; }')
        deck_id = int(hashlib.sha256(str(identity).encode()).hexdigest()[:8], 16)
        deck = genanki.Deck(deck_id, view['name'])
        for card in cards:
            sources = list(dict.fromkeys(source_text(e) for c in card['claims'] for e in c['evidence']))
            deck.add_note(genanki.Note(model=model, guid=genanki.guid_for(str(identity), card['concept_id']), fields=[
                escape(card['label']), '<br><br>'.join(escape(c['text']).replace('\n', '<br>') for c in card['claims']),
                ''.join(f'<img src="{name}">' for name in grouped[card['concept_id']]),
                '<br>'.join(escape(s) for s in sources)]))
        # genanki.write_to_file 會留下 /tmp SQLite；使用它的 DB writer，讓全部副本同生共滅。
        database = directory / 'collection.anki2'
        timestamp = time.time()
        with closing(sqlite3.connect(database)) as connection:
            genanki.Package(deck).write_to_db(connection.cursor(), timestamp, count(int(timestamp * 1000)))
            connection.commit()
        with zipfile.ZipFile(result, 'w', zipfile.ZIP_STORED) as archive:
            for name, data in chain([('collection.anki2', database.read_bytes()),
                                     ('media', json.dumps({i: path.name for i, path in enumerate(files)}).encode())],
                                    ((str(i), path.read_bytes()) for i, path in enumerate(files))):
                archive.writestr(zipfile.ZipInfo(name, datetime.now(ZoneInfo('Asia/Taipei')).timetuple()[:6]), data)
    return result
