from pathlib import Path

import pytest
from sqlmodel import Session, select

from app import db
from app.core import config
from app.models import UserDocument
from tests.conftest import register_user  # type: ignore

pytestmark = pytest.mark.anyio


async def test_upload_invalid_extension_returns_415(client_with_overrides, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)

    client, fake_email_service = client_with_overrides
    await register_user(client, fake_email_service, "materials-invalid@example.com", "correctpassword")

    response = await client.post(
        "/materials/upload",
        files={"file": ("test.txt", b"dummy content", "text/plain")},
    )

    assert response.status_code == 415
    assert list(tmp_path.iterdir()) == []


async def test_upload_pdf_creates_document_and_file(client_with_overrides, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)

    client, fake_email_service = client_with_overrides
    await register_user(client, fake_email_service, "materials-upload@example.com", "correctpassword")

    content = b"%PDF-1.4 dummy pdf content"
    response = await client.post(
        "/materials/upload",
        files={"file": ("test.pdf", content, "application/pdf")},
    )

    assert response.status_code == 201
    data = response.json()
    assert isinstance(data["document_id"], int)
    assert data["original_filename"] == "test.pdf"
    assert data["stored_filename"].endswith(".pdf")

    stored_path = tmp_path / data["stored_filename"]
    assert stored_path.is_file()
    assert stored_path.read_bytes() == content

    with Session(db.engine) as session:
        doc = session.exec(
            select(UserDocument).where(UserDocument.id == data["document_id"])
        ).first()

        assert doc is not None
        assert doc.original_filename == "test.pdf"
        assert doc.stored_filename == data["stored_filename"]
        assert doc.size_bytes == len(content)
        assert Path(doc.stored_path) == stored_path

    me_response = await client.get("/materials/me")
    assert me_response.status_code == 200
    me_data = me_response.json()
    assert me_data["active_doc"]["title"] == "test.pdf"

