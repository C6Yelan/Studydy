"""真 API 的匯出權限、固定版本與未發布資料邊界。"""
from base64 import b64encode
from io import BytesIO
from uuid import uuid4
import zipfile

import pymupdf
from fastapi.testclient import TestClient

from assessment_fixtures import concept_fixture
from product_fixtures import ORIGIN, _app, closed_loop
from runtime import material_notes, podcasts
from runtime.learner_session import register_account
from test_note_queue import answer as note_answer
from test_card_generation import create as create_cards, finish as finish_cards, response as card_response
from test_podcasts import create as create_podcast, complete as finish_podcast


def test_notes_maps_and_diagnosis_export_owner_and_revision(closed_loop, tmp_path, monkeypatch):
    f = concept_fixture(closed_loop, 2)
    client = TestClient(_app(f['dsn'], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', f['token'])
    base = f"/v1/materials/{f['source'].material_id}/knowledge-structures/{f['document']['revision']}"
    assert client.get(base + '/notes/summary/export/pdf').status_code == 404
    while material_notes.step(dsn=f['dsn'], call=note_answer): pass
    view = client.get(base + '/notes/summary').json()
    client.put(base + f'/notes/summary/annotations/{uuid4()}', headers={'Origin': ORIGIN}, json={
        'expected_generation': view['generation'], 'paragraph_id': view['paragraphs'][0]['id'], 'body': '匯出私人註記'})
    paths = [base + '/notes/summary/export/' + ext for ext in ('md', 'pdf', 'docx')]
    paths += [base + '/map/export/md', base + '/learning-diagnosis/export/pdf']
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text[:100])
        assert response.headers['cache-control'] == 'private, no-store'
        assert response.headers['content-disposition'].startswith('attachment;')
        if path.endswith('/pdf'):
            with pymupdf.open(stream=response.content, filetype='pdf') as pdf:
                assert len(pdf) >= 1
    assert '匯出私人註記' in client.get(paths[0]).text
    assert client.get(base + '/notes/full/export/md').status_code == 404
    assert client.get(base + '/learning-diagnosis/export/pdf', params={'set_id': str(uuid4())}).status_code == 404
    doc = pymupdf.open(); doc.new_page(width=120, height=76)
    png = b64encode(doc[0].get_pixmap().tobytes('png')).decode(); doc.close()
    ids = [c['concept_id'] for c in client.get(base + '/outline').json()['concepts']]
    map_body = {'images': [{'concept_ids': ids, 'png': png}]}
    assert client.post(base + '/map/export/png', json=map_body, headers={'Origin': ORIGIN}).status_code == 200
    assert client.post(base + '/map/export/png', json={'images': [{'concept_ids': ['fake'], 'png': png}]}, headers={'Origin': ORIGIN}).status_code == 400
    foreign = register_account('exports_foreign@example.com', 'Synthetic test password 42', dsn=f['dsn'])
    client.cookies.set('studydy_session', foreign.raw_token)
    for path in paths:
        assert client.get(path).status_code == 404
    assert client.post(base + '/map/export/png', json=map_body, headers={'Origin': ORIGIN}).status_code == 404
    client.cookies.clear()
    assert client.get(paths[0]).status_code == 401


def test_card_exports_enforce_owner_version_origin_and_complete_selection(closed_loop, tmp_path, monkeypatch):
    owner, _, _, _, dsn, token = closed_loop
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', token)
    identity = create_cards(closed_loop)
    finish_cards(dsn, card_response)
    study = client.get(f'/v1/card-sets/{identity}/study').json()
    doc = pymupdf.open(); doc.new_page(width=120, height=76)
    png = doc[0].get_pixmap().tobytes('png'); doc.close()
    body = {'version': study['version'], 'images': [{'concept_id': c['concept_id'], 'page': 0, 'png': b64encode(png).decode()} for c in study['navigation']]}
    path = f'/v1/card-sets/{identity}/export/apkg'
    assert client.post(path, json=body).status_code == 403
    response = client.post(path, json=body, headers={'Origin': ORIGIN})
    assert response.status_code == 200, response.text[:100]
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert 'collection.anki2' in archive.namelist()
    assert client.post(path, json={**body, 'version': study['version'] + 1}, headers={'Origin': ORIGIN}).status_code == 409
    foreign = register_account('card_export_foreign@example.com', 'Synthetic test password 42', dsn=dsn)
    client.cookies.set('studydy_session', foreign.raw_token)
    assert client.post(path, json=body, headers={'Origin': ORIGIN}).status_code == 404


def test_podcast_mp3_owner_and_missing_video_never_generates(closed_loop, tmp_path, monkeypatch):
    owner, _, _, _, dsn, token = closed_loop
    identity = create_podcast(closed_loop)['podcast_id']
    finish_podcast(dsn)
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', token)
    path = f'/v1/podcasts/{identity}/episodes/0/export/'
    assert client.get(path + 'mp3').status_code == 200
    assert client.get(path + 'mp4').status_code == 404
    assert client.get(path + 'md').status_code == 409
    assert client.get(f'/v1/podcasts/{identity}/episodes/999/export/mp3').status_code == 404
    foreign = register_account('podcast_export_foreign@example.com', 'Synthetic test password 42', dsn=dsn)
    client.cookies.set('studydy_session', foreign.raw_token)
    for ext in ('mp3', 'mp4', 'md'):
        assert client.get(path + ext).status_code == 404


def test_podcast_existing_video_and_transcript_export_exact_saved_content(closed_loop, tmp_path, monkeypatch):
    from base64 import b64decode
    from runtime import podcast_videos
    from test_podcast_videos import bundle
    owner, _, _, _, dsn, token = closed_loop
    identity = create_podcast(closed_loop)['podcast_id']
    finish_podcast(dsn)
    claim = podcast_videos.claim(dsn=dsn)
    payload = bundle(claim)
    assert podcast_videos.finish(claim, bundle=payload, dsn=dsn)
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', token)
    path = f'/v1/podcasts/{identity}/episodes/0/export/'
    response = client.get(path + 'mp4')
    assert response.status_code == 200
    assert response.content == b64decode(payload['video'])
    assert response.headers['content-disposition'].startswith('attachment;')
    assert client.get(path + 'mp4', headers={'Range': 'bytes=0-7'}).content == response.content[:8]
    transcript = client.get(path + 'md')
    assert transcript.status_code == 200, transcript.text[:100]
    view = podcasts.read_podcast(owner.learner_id, identity, dsn=dsn)
    assert view['episodes'][0]['hosts']['host'] in transcript.text
    assert '00:00:00' in transcript.text and '本集重點' in transcript.text
    assert client.get(path + 'mp4', headers={'Range': 'bytes=9999999-'}).status_code == 416
