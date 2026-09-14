from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from runtime.storage.materials import rename_material, read_material_library, MaterialLibraryError
from test_material_library import library_materials, product_snapshot
from test_closed_loop_v1 import closed_loop
from test_accounts import _app, ORIGIN, HEADERS


def test_owner_rename_is_a_unicode_setter_without_identity_changes(library_materials, tmp_path, monkeypatch):
    f = library_materials
    identity, material, dsn = f['learner'].learner_id, f['first'].material_id, f['dsn']
    before = read_material_library(identity, material_id=material, dsn=dsn)[0]
    snapshot = product_snapshot(dsn)
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', f['token'])
    path = f'/v1/materials/{material}/rename'
    response = client.post(path, headers=HEADERS, json={'schema': 'material-rename/v1', 'display_name': '  作業系統 第五章_2026 🐍  '})
    assert response.status_code == 200
    assert response.json()['display_name'] == '作業系統 第五章_2026 🐍'
    updated = rename_material(identity, material, response.json()['display_name'], dsn=dsn)
    assert updated == {**before, 'display_name': response.json()['display_name']}
    assert client.get(f'/v1/materials/{material}').json()['display_name'] == response.json()['display_name']
    assert next(item for item in client.get('/v1/materials').json()['materials'] if item['material_id'] == str(material))['display_name'] == response.json()['display_name']
    after = product_snapshot(dsn)
    assert {k:v for k,v in snapshot.items() if k != 'materials'} == {k:v for k,v in after.items() if k != 'materials'}
    assert client.post(path, json={'schema':'material-rename/v1', 'display_name':'Valid'}).status_code == 403
    client.cookies.set('studydy_session', f['foreign'].raw_token)
    assert client.post(path, headers=HEADERS, json={'schema':'material-rename/v1','display_name':'Foreign'}).status_code == 404
    assert client.post(f'/v1/materials/{uuid4()}/rename', headers=HEADERS, json={'schema':'material-rename/v1','display_name':'Missing'}).status_code == 404
    with psycopg.connect(dsn) as db:
        db.execute('UPDATE materials SET discard_requested_at=now() WHERE learner_id=%s AND material_id=%s', (identity, material))
    with pytest.raises(MaterialLibraryError, match='MATERIAL_NOT_DISCARDABLE'):
        rename_material(identity, material, 'No longer mutable', dsn=dsn)


@pytest.mark.parametrize('name', ['', '   ', '字'*201, '\x00title', 'title\n', '\tName', 'x\x7f', 'x\u0085', '\ud800'])
def test_rename_rejects_invalid_names_before_storage(name):
    with pytest.raises(MaterialLibraryError, match='REQUEST_INVALID'):
        rename_material(uuid4(), uuid4(), name)
