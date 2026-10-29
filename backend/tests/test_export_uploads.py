"""大卡組用真 HTTP 請求分批進入磁碟暫存；所有內容均為合成資料。"""
from base64 import b64encode
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from urllib.parse import unquote
import asyncio
import json
import os
import random
import time
import zipfile

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image

from runtime import export_cards
from runtime.api import export_routes
from runtime.errors import SourceError


def test_export_semaphore_queues_without_starving_file_response_threads(tmp_path):
    import anyio
    import httpx
    from fastapi import Depends
    from fastapi.responses import FileResponse
    app = FastAPI()
    app.state.export_slots = asyncio.Semaphore(2)
    file = tmp_path / 'result.txt'; file.write_text('export')
    active = 0
    peak = 0
    async def tracked_slot(request: Request):
        nonlocal active, peak
        async for _ in export_routes.export_slot(request):
            active += 1; peak = max(peak, active)
            try: yield
            finally: active -= 1
    @app.get('/download', dependencies=[Depends(tracked_slot, scope="function")])
    def download():
        return FileResponse(file)
    async def run():
        # 比正式環境更小的執行緒池；排隊者不得阻止持有席位的 FileResponse 讀檔。
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = 4
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            responses = await asyncio.wait_for(asyncio.gather(*(client.get('/download') for _ in range(44))), 5)
            assert all(r.status_code == 200 and r.text == 'export' for r in responses)
    asyncio.run(run())
    assert peak == 2 and active == 0


def test_148_concept_export_exceeds_old_request_limit_but_downloads_as_one_zip(monkeypatch, tmp_path):
    owner, identity = uuid4(), uuid4()
    cards = [{'concept_id': str(i), 'label': f'合成概念{i}', 'claims': []} for i in range(148)]
    def read(learner, card_set, *, offset=0, expected_version=None, dsn=None):
        assert learner == owner and card_set == identity
        return {'cards': cards[offset:offset + 8]} if expected_version else {'version': 1, 'name': '148概念合成卡組', 'material_name': '01_合成教材.pptx', 'navigation': cards, 'batch': {}}
    monkeypatch.setattr(export_cards.card_sets, 'read_card_study', read)
    monkeypatch.setattr(export_cards, '_root', lambda: tmp_path)
    monkeypatch.setattr(export_routes.request_context, 'trusted_learner', lambda *_: SimpleNamespace(learner_id=owner))
    # 成就用的匯出紀錄寫在 DB；這些案例沒有 DB，只記下呼叫。
    exported = []
    monkeypatch.setattr(export_routes, 'record_export', lambda learner, kind, **_: exported.append((learner, kind)))
    app = FastAPI(); export_routes.install(app, SimpleNamespace(dsn=None))
    # 保持正式卡面尺寸；高細節區域使總 payload 超過舊 nginx 上限，不只測小白圖。
    canvas = Image.new('RGB', (1200, 760), 'white')
    canvas.paste(Image.frombytes('RGB', (600, 400), random.Random(42).randbytes(600 * 400 * 3)), (0, 0))
    output = BytesIO(); canvas.save(output, format='PNG'); png = output.getvalue()
    encoded = b64encode(png).decode()
    base = f'/v1/card-sets/{identity}/exports'
    sizes = []
    start = time.monotonic()
    with TestClient(app) as client:
        uid = client.post(base, json={'version': 1, 'format': 'zip'}).json()['upload_id']
        for offset in range(0, 148, 2):
            body = {'offset': offset, 'images': [{'concept_id': str(i), 'page': 0, 'png': encoded} for i in range(offset, offset + 2)]}
            sizes.append(len(json.dumps(body).encode()))
            response = client.put(f'{base}/{uid}', json=body)
            assert response.status_code == 200
            assert response.json()['received'] == offset + 2
        assert sum(sizes) > 90 * 1024 * 1024
        assert max(sizes) < 16 * 1024 * 1024
        response = client.post(f'{base}/{uid}/download')
        assert response.status_code == 200
        assert unquote(response.headers['content-disposition']).endswith('01_合成教材.zip')
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            assert len(archive.namelist()) == 148
            assert archive.testzip() is None
            assert all(archive.read(name) == png for name in archive.namelist())
        assert not (export_cards.work_root() / f'{owner}_{uid}').exists()
        assert exported == [(owner, 'cards')]
    print(json.dumps({'concepts': 148, 'batches': len(sizes), 'total_request_bytes': sum(sizes),
        'max_request_bytes': max(sizes), 'download_bytes': len(response.content), 'seconds': round(time.monotonic() - start, 2)}))


def test_upload_cleanup_excludes_locked_download_and_removes_abandoned_session(monkeypatch, tmp_path):
    monkeypatch.setattr(export_cards, '_root', lambda: tmp_path)
    monkeypatch.setattr(export_cards.card_sets, 'read_card_study', lambda *_args, **_kwargs: {'version': 1, 'batch': {}, 'material_name': '教材', 'navigation': [{'concept_id': 'a'}]})
    uid = export_cards.create('owner', 'cards', 1, 'zip')['upload_id']
    path = export_cards.work_root() / f'owner_{uid}'
    os.utime(path / 'state.json', (0, 0))
    with export_cards.upload('owner', 'cards', uid):
        export_cards.cleanup_stale_uploads()
        assert path.exists()
    export_cards.cleanup_stale_uploads()
    assert not path.exists()
    with pytest.raises(SourceError, match='RESOURCE_NOT_FOUND'):
        with export_cards.upload('owner', 'cards', uid): pass


def test_owner_quota_is_atomic_and_cancel_releases_it(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(export_cards, '_root', lambda: tmp_path)
    monkeypatch.setattr(export_cards.card_sets, 'read_card_study', lambda *_args, **_kwargs: {'version': 1, 'batch': {}, 'material_name': '教材', 'navigation': [{'concept_id': 'a'}]})
    def create():
        try:
            return export_cards.create('owner', 'cards', 1, 'zip')['upload_id']
        except SourceError as error:
            assert str(error) == 'EXPORT_ACTIVE_LIMIT'
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: create(), range(8)))
    created = [uid for uid in results if uid]
    assert len(created) == export_cards.MAX_OWNER_UPLOADS == 2
    other = export_cards.create('other-owner', 'cards', 1, 'zip')['upload_id']
    export_cards.cancel('owner', 'cards', created[0])
    replacement = create()
    assert replacement is not None
    for owner, uid in [('owner', created[1]), ('owner', replacement), ('other-owner', other)]:
        export_cards.cancel(owner, 'cards', uid)


def test_byte_and_concept_page_limits_reject_before_writing_batch(monkeypatch, tmp_path):
    monkeypatch.setattr(export_cards, '_root', lambda: tmp_path)
    monkeypatch.setattr(export_cards.card_sets, 'read_card_study', lambda *_args, **_kwargs: {'version': 1, 'batch': {}, 'material_name': '教材', 'navigation': [{'concept_id': 'a'}]})
    output = BytesIO(); Image.new('RGB', (8, 8)).save(output, format='PNG')
    png = output.getvalue()
    def image(page):
        return export_routes.CardImage(concept_id='a', page=page, png=b64encode(png).decode())
    monkeypatch.setattr(export_cards, 'MAX_UPLOAD_BYTES', len(png) * 2)
    uid = export_cards.create('owner', 'cards', 1, 'zip')['upload_id']
    directory = export_cards.work_root() / f'owner_{uid}'
    export_cards.append('owner', 'cards', uid, 0, [image(0)])
    with pytest.raises(SourceError, match='EXPORT_SIZE_LIMIT'):
        export_cards.append('owner', 'cards', uid, 1, [image(1), image(2)])
    assert len(list(directory.glob('*.png'))) == 1
    assert len(json.loads((directory / 'state.json').read_text())['images']) == 1
    export_cards.append('owner', 'cards', uid, 1, [image(1)])
    assert sum(p.stat().st_size for p in directory.glob('*.png')) == export_cards.MAX_UPLOAD_BYTES
    monkeypatch.setattr(export_cards, 'MAX_CONCEPT_PAGES', 2)
    with pytest.raises(SourceError, match='EXPORT_PAGE_LIMIT'):
        export_cards.append('owner', 'cards', uid, 2, [image(2)])
    assert len(list(directory.glob('*.png'))) == 2
    export_cards.cancel('owner', 'cards', uid)


async def asgi_request(app, path, *, method='GET', send_body=None):
    messages = []
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
             'http_version': '1.1', 'scheme': 'http', 'method': method, 'path': path,
             'raw_path': path.encode(), 'root_path': '', 'query_string': b'', 'headers': [],
             'client': ('127.0.0.1', 1), 'server': ('test', 80)}
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def send(message):
        messages.append(message)
        if send_body and message['type'] == 'http.response.body' and message.get('body'):
            await send_body()
    await app(scope, receive, send)
    assert messages[0]['status'] == 200
    return b''.join(m.get('body', b'') for m in messages)


def test_card_stream_releases_generation_slot_but_keeps_temporary_files_locked(monkeypatch, tmp_path):
    import fcntl
    owner, identity = uuid4(), uuid4()
    card = {'concept_id': 'a', 'label': '合成', 'claims': []}
    monkeypatch.setattr(export_cards, '_root', lambda: tmp_path)
    monkeypatch.setattr(export_cards.card_sets, 'read_card_study', lambda *_args, expected_version=None, **_kwargs:
        {'cards': [card]} if expected_version else {'version': 1, 'batch': {}, 'material_name': '教材', 'name': '合成', 'navigation': [card]})
    monkeypatch.setattr(export_routes.request_context, 'trusted_learner', lambda *_: SimpleNamespace(learner_id=owner))
    # 成就用的匯出紀錄寫在 DB；這些案例沒有 DB，只記下呼叫。
    exported = []
    monkeypatch.setattr(export_routes, 'record_export', lambda learner, kind, **_: exported.append((learner, kind)))
    monkeypatch.setattr(export_routes.material_notes, 'read', lambda *_args, **_kwargs: {'paragraphs': [{}], 'content_revision': 1})
    monkeypatch.setattr(export_routes.note_highlights, 'listing', lambda *_args, **_kwargs: [])
    from contextlib import nullcontext
    from runtime.storage import database
    monkeypatch.setattr(database, 'database_session', lambda *_: nullcontext())
    monkeypatch.setattr(export_routes, 'material_name', lambda *_: '合成')
    monkeypatch.setattr(export_routes.export_documents, 'note_markdown', lambda _: '# note')
    app = FastAPI(); export_routes.install(app, SimpleNamespace(dsn=None))
    original = export_cards.render
    def render(*args, **kwargs):
        assert app.state.export_slots._value == 1
        return original(*args, **kwargs)
    monkeypatch.setattr(export_cards, 'render', render)
    uid = export_cards.create(owner, identity, 1, 'zip')['upload_id']
    output = BytesIO(); Image.new('RGB', (8, 8)).save(output, format='PNG')
    export_cards.append(owner, identity, uid, 0, [export_routes.CardImage(concept_id='a', page=0, png=b64encode(output.getvalue()).decode())])
    directory = export_cards.work_root() / f'{owner}_{uid}'
    async def run():
        sending, release = asyncio.Event(), asyncio.Event()
        async def slow():
            sending.set(); await release.wait()
        task = asyncio.create_task(asgi_request(app, f'/v1/card-sets/{identity}/exports/{uid}/download', method='POST', send_body=slow))
        try:
            await asyncio.wait_for(sending.wait(), 3)
            assert app.state.export_slots._value == 2
            assert directory.exists()
            with (directory / 'state.json').open() as stream:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for method, path in [('POST', f'/v1/card-sets/{identity}/exports/{uid}/download'),
                                 ('DELETE', f'/v1/card-sets/{identity}/exports/{uid}')]:
                with pytest.raises(SourceError, match='EXPORT_IN_PROGRESS'):
                    await asyncio.wait_for(asgi_request(app, path, method=method), 1)
            assert app.state.export_slots._value == 2
            assert await asyncio.wait_for(asgi_request(app, f'/v1/materials/{uuid4()}/knowledge-structures/r/notes/summary/export/md'), 2) == b'# note'
        finally:
            release.set()
            await task
        assert not directory.exists()
    asyncio.run(run())


def test_mp4_stream_bypasses_even_a_full_generation_queue(monkeypatch):
    from contextlib import contextmanager
    from runtime import podcasts, podcast_videos
    from runtime.storage import artifacts
    owner, identity, artifact = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(export_routes.request_context, 'trusted_learner', lambda *_: SimpleNamespace(learner_id=owner))
    # 成就用的匯出紀錄寫在 DB；這些案例沒有 DB，只記下呼叫。
    exported = []
    monkeypatch.setattr(export_routes, 'record_export', lambda learner, kind, **_: exported.append((learner, kind)))
    monkeypatch.setattr(podcasts, 'read_podcast', lambda *_args, **_kwargs: {'status': 'ready', 'name': '合成', 'episodes': [{}]})
    monkeypatch.setattr(podcast_videos, 'ready_manifest', lambda *_args, **_kwargs: {'artifact_id': artifact})
    @contextmanager
    def opened(*_args, **_kwargs):
        yield SimpleNamespace(file=BytesIO(b'video'), size_bytes=5)
    monkeypatch.setattr(artifacts, 'open_verified_artifact', opened)
    app = FastAPI(); export_routes.install(app, SimpleNamespace(dsn=None))
    async def run():
        await app.state.export_slots.acquire(); await app.state.export_slots.acquire()
        try:
            assert await asyncio.wait_for(asgi_request(app, f'/v1/podcasts/{identity}/episodes/0/export/mp4'), 2) == b'video'
            assert app.state.export_slots._value == 0
        finally:
            app.state.export_slots.release(); app.state.export_slots.release()
    asyncio.run(run())


def test_export_record_failure_does_not_fail_the_download(monkeypatch, caplog):
    """成就用的匯出紀錄寫不進 DB（這裡沒有 DB）時，下載照常完成。"""
    from contextlib import contextmanager
    from runtime import podcasts, podcast_videos
    from runtime.storage import artifacts
    owner, identity = uuid4(), uuid4()
    monkeypatch.setattr(export_routes.request_context, 'trusted_learner', lambda *_: SimpleNamespace(learner_id=owner))
    monkeypatch.setattr(podcasts, 'read_podcast', lambda *_args, **_kwargs: {'status': 'ready', 'name': '合成', 'episodes': [{}]})
    monkeypatch.setattr(podcast_videos, 'ready_manifest', lambda *_args, **_kwargs: {'artifact_id': uuid4()})
    @contextmanager
    def opened(*_args, **_kwargs):
        yield SimpleNamespace(file=BytesIO(b'video'), size_bytes=5)
    monkeypatch.setattr(artifacts, 'open_verified_artifact', opened)
    app = FastAPI(); export_routes.install(app, SimpleNamespace(dsn=None))
    assert asyncio.run(asgi_request(app, f'/v1/podcasts/{identity}/episodes/0/export/mp4')) == b'video'
    assert 'EXPORT_RECORD_FAILED kind=podcast' in caplog.text
