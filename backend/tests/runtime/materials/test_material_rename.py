from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

import runtime.material_discard as material_discard
import runtime.storage.materials as material_storage
from runtime.storage.materials import MaterialLibraryError, read_material_library, rename_material
from product_fixtures import HEADERS, ORIGIN, _app, closed_loop, library_materials, product_snapshot
from test_material_discard import claim, create, ordered_race, request, unused


def test_owner_rename_is_a_unicode_setter_without_identity_changes(library_materials, tmp_path, monkeypatch):
    fixture = library_materials
    owner = fixture["learner"].learner_id
    material_id = fixture["first"].material_id
    dsn = fixture["dsn"]
    before = read_material_library(owner, material_id=material_id, dsn=dsn)[0]
    snapshot = product_snapshot(dsn)
    client = TestClient(_app(dsn, tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set("studydy_session", fixture["token"])
    path = f"/v1/materials/{material_id}/rename"
    response = client.post(path, headers=HEADERS, json={
        "schema": "material-rename/v1",
        "display_name": "  作業系統 第五章_2026 🐍  ",
    })
    assert response.status_code == 200
    display_name = response.json()["display_name"]
    assert display_name == "作業系統 第五章_2026 🐍"
    updated = rename_material(owner, material_id, display_name, dsn=dsn)
    assert updated == {**before, "display_name": display_name}
    assert client.get(f"/v1/materials/{material_id}").json()["display_name"] == display_name
    materials = client.get("/v1/materials").json()["materials"]
    assert next(item for item in materials if item["material_id"] == str(material_id))["display_name"] == display_name
    after = product_snapshot(dsn)
    assert {k: v for k, v in snapshot.items() if k != "materials"} == {
        k: v for k, v in after.items() if k != "materials"
    }
    assert client.post(path, json={"schema": "material-rename/v1", "display_name": "Valid"}).status_code == 403
    client.cookies.set("studydy_session", fixture["foreign"].raw_token)
    assert client.post(
        path, headers=HEADERS, json={"schema": "material-rename/v1", "display_name": "Foreign"},
    ).status_code == 404
    assert client.post(
        f"/v1/materials/{uuid4()}/rename", headers=HEADERS,
        json={"schema": "material-rename/v1", "display_name": "Missing"},
    ).status_code == 404
    with psycopg.connect(dsn) as db:
        db.execute(
            "UPDATE materials SET discard_requested_at=now() WHERE learner_id=%s AND material_id=%s",
            (owner, material_id),
        )
    with pytest.raises(MaterialLibraryError, match="MATERIAL_NOT_DISCARDABLE"):
        rename_material(owner, material_id, "No longer mutable", dsn=dsn)


@pytest.mark.parametrize("name", [
    "   ", "字" * 201, "title\n", "x\u0085", "\ud800",
], ids=[
    "whitespace", "too-long", "newline", "c1-control", "surrogate",
])
def test_rename_rejects_invalid_names_before_storage(name, monkeypatch):
    # 空白涵蓋 strip 後空值；換行保護先拒絕控制字元再 strip，另保留非 ASCII Cc 與 Cs。
    monkeypatch.setattr(material_storage, "database_session", lambda *_: pytest.fail("Invalid name must not reach storage"))
    with pytest.raises(MaterialLibraryError, match="REQUEST_INVALID"):
        rename_material(uuid4(), uuid4(), name)


@pytest.mark.parametrize("delete_first", [True, False])
def test_rename_and_delete_serialize_on_the_same_material(unused, monkeypatch, delete_first):
    run = create(unused)
    claim(unused)
    original_name = material_storage.read_material_library(
        unused.learner.learner_id, material_id=unused.source.material_id, dsn=unused.dsn,
    )[0]["display_name"]

    def rename():
        try:
            return rename_material(
                unused.learner.learner_id, unused.source.material_id, "改名後教材", dsn=unused.dsn,
            )
        except MaterialLibraryError as error:
            return type(error), str(error)

    remove = lambda: request(unused)
    first, second = ordered_race(
        monkeypatch, unused, run,
        remove if delete_first else rename,
        rename if delete_first else remove,
        material_discard if delete_first else material_storage,
        material_storage if delete_first else material_discard,
        lock_material=True,
    )
    if delete_first:
        assert first == "removing"
        assert second == (MaterialLibraryError, "MATERIAL_NOT_DISCARDABLE")
    else:
        assert first["display_name"] == "改名後教材"
        assert second == "removing"
    with psycopg.connect(unused.dsn) as db:
        name, deleting = db.execute(
            "SELECT display_name, discard_requested_at IS NOT NULL FROM materials "
            "WHERE learner_id=%s AND material_id=%s",
            (unused.learner.learner_id, unused.source.material_id),
        ).fetchone()
        assert deleting
        assert name == (original_name if delete_first else "改名後教材")
