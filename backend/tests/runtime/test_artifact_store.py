from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pymupdf
import pytest

from runtime.storage.artifacts import (
    ArtifactError,
    open_verified_source_pdf,
    open_verified_resource_pdf,
    publish_idempotent_source_pdf,
    publish_resource_pdf,
    remove_resource_pdf,
)
from runtime.storage.migrations import run_migrations


@pytest.fixture
def artifact_database_dsn(clean_database_dsn: str, migrations_dir: Path) -> str:
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (1, 2, 3, 4, 5)
    return clean_database_dsn


@pytest.fixture
def artifact_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "private-artifacts"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(root))
    return root


def _pdf(text: str = "Studydy") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def _encrypted_pdf() -> bytes:
    document = pymupdf.open()
    document.new_page()
    content = document.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="user-password",
    )
    document.close()
    return content


def _learner(dsn: str) -> UUID:
    learner_id = uuid4()
    with psycopg.connect(dsn) as connection:
        connection.execute("INSERT INTO learners VALUES (%s,clock_timestamp())", (learner_id,))
    return learner_id


def _publish_source(learner_id: UUID, content: bytes, dsn: str):
    return publish_idempotent_source_pdf(
        learner_id, io.BytesIO(content), f"artifact-test-{uuid4()}", dsn=dsn
    )


def test_source_publish_verified_read_and_owner_isolation(
    artifact_database_dsn: str, artifact_root: Path
) -> None:
    owner = _learner(artifact_database_dsn)
    other = _learner(artifact_database_dsn)
    content = _pdf()
    published = _publish_source(owner, content, artifact_database_dsn)
    assert (artifact_root / "objects" / published.artifact_id.hex).stat().st_mode & 0o777 == 0o400
    with open_verified_source_pdf(owner, published.artifact_id, dsn=artifact_database_dsn) as opened:
        assert opened.file.read() == content
        assert opened.material_id == published.material_id
    with pytest.raises(ArtifactError, match="ARTIFACT_NOT_AVAILABLE"):
        with open_verified_source_pdf(other, published.artifact_id, dsn=artifact_database_dsn):
            pass


def test_source_idempotency_replay_and_conflict(
    artifact_database_dsn: str, artifact_root: Path
) -> None:
    learner = _learner(artifact_database_dsn)
    content = _pdf("one")
    first = publish_idempotent_source_pdf(learner, io.BytesIO(content), "same", dsn=artifact_database_dsn)
    replay = publish_idempotent_source_pdf(learner, io.BytesIO(content), "same", dsn=artifact_database_dsn)
    assert replay == first
    with pytest.raises(ArtifactError, match="ARTIFACT_IDEMPOTENCY_CONFLICT"):
        publish_idempotent_source_pdf(learner, io.BytesIO(_pdf("two")), "same", dsn=artifact_database_dsn)
    with psycopg.connect(artifact_database_dsn) as connection:
        assert connection.execute("SELECT count(*) FROM materials").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM artifacts").fetchone() == (1,)


def test_invalid_pdf_leaves_no_row_or_residue(
    artifact_database_dsn: str, artifact_root: Path
) -> None:
    learner = _learner(artifact_database_dsn)
    with pytest.raises(ArtifactError, match="ARTIFACT_PDF_INVALID"):
        publish_idempotent_source_pdf(
            learner,
            io.BytesIO(b"not-pdf"),
            f"invalid-{uuid4()}",
            dsn=artifact_database_dsn,
        )
    with psycopg.connect(artifact_database_dsn) as connection:
        assert connection.execute("SELECT count(*) FROM materials").fetchone() == (0,)
    assert list((artifact_root / ".staging").iterdir()) == []
    assert list((artifact_root / "objects").iterdir()) == []


def test_source_receipt_rejects_resource_kind(
    artifact_database_dsn: str, artifact_root: Path
) -> None:
    learner = _learner(artifact_database_dsn)
    source_content = _pdf("source")
    source = publish_idempotent_source_pdf(
        learner, io.BytesIO(source_content), "kind-receipt", dsn=artifact_database_dsn
    )
    resource_content = _pdf("resource")
    digest = __import__("hashlib").sha256(resource_content).hexdigest()
    resource_id = uuid4()
    publish_resource_pdf(
        learner,
        source.material_id,
        resource_id,
        io.BytesIO(resource_content),
        digest,
        len(resource_content),
        dsn=artifact_database_dsn,
    )
    with psycopg.connect(artifact_database_dsn) as connection:
        connection.execute(
            "UPDATE materials SET source_artifact_id=%s WHERE material_id=%s",
            (resource_id, source.material_id),
        )
    with pytest.raises(ArtifactError, match="ARTIFACT_PUBLISH_FAILED"):
        publish_idempotent_source_pdf(
            learner,
            io.BytesIO(source_content),
            "kind-receipt",
            dsn=artifact_database_dsn,
        )


def test_verified_read_rejects_changed_object_hash(
    artifact_database_dsn: str, artifact_root: Path
) -> None:
    learner = _learner(artifact_database_dsn)
    published = _publish_source(learner, _pdf(), artifact_database_dsn)
    path = artifact_root / "objects" / published.artifact_id.hex
    path.chmod(0o600)
    changed = bytearray(path.read_bytes())
    changed[-1] ^= 1
    path.write_bytes(changed)
    with pytest.raises(ArtifactError, match="ARTIFACT_NOT_AVAILABLE"):
        with open_verified_source_pdf(learner, published.artifact_id, dsn=artifact_database_dsn):
            pass


def test_resource_publish_read_collision_and_exact_failure_cleanup(
    artifact_database_dsn: str, artifact_root: Path
) -> None:
    learner = _learner(artifact_database_dsn)
    source = _publish_source(learner, _pdf("source"), artifact_database_dsn)
    content = _pdf("resource")
    digest = __import__("hashlib").sha256(content).hexdigest()
    artifact_id = uuid4()
    publish_resource_pdf(
        learner, source.material_id, artifact_id, io.BytesIO(content), digest, len(content), dsn=artifact_database_dsn
    )
    with pytest.raises(ArtifactError, match="RESOURCE_ARTIFACT_CONFLICT"):
        publish_resource_pdf(
            learner, source.material_id, artifact_id, io.BytesIO(content), digest, len(content), dsn=artifact_database_dsn
        )
    with open_verified_resource_pdf(learner, source.material_id, artifact_id, dsn=artifact_database_dsn) as opened:
        assert opened.file.read() == content
    remove_resource_pdf(learner, source.material_id, artifact_id, dsn=artifact_database_dsn)
    assert not (artifact_root / "objects" / artifact_id.hex).exists()
    with psycopg.connect(artifact_database_dsn) as connection:
        assert connection.execute("SELECT count(*) FROM artifacts WHERE kind='resource_pdf'").fetchone() == (0,)


def test_root_must_be_absolute_private_directory(
    artifact_database_dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    learner = _learner(artifact_database_dsn)
    for value in ("relative", str(tmp_path / "missing" / "nested")):
        monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", value)
        with pytest.raises(ArtifactError, match="ARTIFACT_ROOT_INVALID"):
            _publish_source(learner, _pdf(), artifact_database_dsn)
