"""大卡組用真 HTTP 請求分批進入磁碟暫存；所有內容均為合成資料。"""
from base64 import b64encode
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
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
    @app.get('/download', dependencies=[Depends(tracked_slot)])
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
        return {'cards': cards[offset:offset + 8]} if expected_version else {'version': 1, 'name': '148概念合成卡組', 'navigation': cards, 'batch': {}}
    monkeypatch.setattr(export_cards.card_sets, 'read_card_study', read)
    monkeypatch.setattr(export_cards, '_root', lambda: tmp_path)
    monkeypatch.setattr(export_routes.request_context, 'trusted_learner', lambda *_: SimpleNamespace(learner_id=owner))
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
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            assert len(archive.namelist()) == 148
            assert archive.testzip() is None
            assert all(archive.read(name) == png for name in archive.namelist())
        assert not (export_cards.work_root() / uid).exists()
    print(json.dumps({'concepts': 148, 'batches': len(sizes), 'total_request_bytes': sum(sizes),
        'max_request_bytes': max(sizes), 'download_bytes': len(response.content), 'seconds': round(time.monotonic() - start, 2)}))


def test_upload_cleanup_excludes_locked_download_and_removes_abandoned_session(monkeypatch, tmp_path):
    monkeypatch.setattr(export_cards, '_root', lambda: tmp_path)
    monkeypatch.setattr(export_cards.card_sets, 'read_card_study', lambda *_args, **_kwargs: {'version': 1, 'batch': {}, 'navigation': [{'concept_id': 'a'}]})
    uid = export_cards.create('owner', 'cards', 1, 'zip')['upload_id']
    path = export_cards.work_root() / uid
    os.utime(path / 'state.json', (0, 0))
    with export_cards.upload('owner', 'cards', uid):
        export_cards.cleanup_stale_uploads()
        assert path.exists()
    export_cards.cleanup_stale_uploads()
    assert not path.exists()
    with pytest.raises(SourceError, match='RESOURCE_NOT_FOUND'):
        with export_cards.upload('owner', 'cards', uid): pass
