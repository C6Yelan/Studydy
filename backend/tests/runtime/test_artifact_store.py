from __future__ import annotations

from product_fixtures import seed_pdf

import io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
from uuid import UUID, uuid4

import psycopg
import pymupdf
import pytest
import runtime.storage.artifacts as artifact_storage

from runtime.storage.artifacts import ArtifactError, open_verified_source_pdf
from runtime.source_normalization import SourceError
from runtime.storage.migrations import run_migrations


@pytest.fixture
def artifact_database_dsn(clean_database_dsn: str, migrations_dir: Path) -> str:
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (1, 2, 3, 4)
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
    return seed_pdf(
        learner_id, io.BytesIO(content), f"artifact-test-{uuid4()}", dsn=dsn
    )


@pytest.mark.parametrize('commit', [True, False])
def test_reconciliation_skips_active_writer_then_obeys_commit(
    artifact_database_dsn, artifact_root, commit,
):
    from runtime.source_normalization import create_draft
    from runtime.storage.source_artifacts import write_blob, reconcile_new_artifacts
    from runtime.storage.tables import database_session
    dsn = artifact_database_dsn
    owner = _learner(dsn)
    material = create_draft(owner, 'Synthetic', 'lock-test', dsn=dsn)

    class Rollback(Exception):
        pass

    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with database_session(dsn) as session:
                artifact = write_blob(session, owner, material, b'synthetic', 'original', 'text/plain')
                marker = artifact_root / '.staging' / f'{artifact.artifact_id.hex}.pending'
                path = artifact_root / 'objects' / artifact.artifact_id.hex
                pool.submit(reconcile_new_artifacts, dsn=dsn).result(timeout=2)
                assert marker.exists() and path.read_bytes() == b'synthetic'
                if not commit:
                    raise Rollback()
        except Rollback:
            pass
    reconcile_new_artifacts(dsn=dsn)
    assert not marker.exists()
    assert path.exists() is commit


def test_reconciliation_skips_active_discard_then_restores_rollback(artifact_database_dsn, artifact_root):
    from runtime.source_normalization import create_draft
    from runtime.storage.source_artifacts import write_blob
    from runtime.storage.tables import database_session
    dsn = artifact_database_dsn
    owner = _learner(dsn)
    material = create_draft(owner, 'Synthetic', 'discard-lock-test', dsn=dsn)
    with database_session(dsn) as session:
        artifact = write_blob(session, owner, material, b'synthetic', 'original', 'text/plain')
    path = artifact_root / 'objects' / artifact.artifact_id.hex
    trash = artifact_root / '.trash' / artifact.artifact_id.hex

    class Rollback(Exception):
        pass

    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(Rollback):
            with database_session(dsn) as session:
                artifact_storage.quarantine_source_pdf(session, artifact.artifact_id)
                pool.submit(artifact_storage.reconcile_discarded_sources, dsn=dsn).result(timeout=2)
                assert trash.exists() and not path.exists()
                raise Rollback()
    artifact_storage.reconcile_discarded_sources(dsn=dsn)
    assert path.read_bytes() == b'synthetic'
    assert not trash.exists()


def test_database_lock_timeout_rolls_back_and_next_transaction_can_write(artifact_database_dsn):
    from sqlalchemy.exc import OperationalError
    from runtime.source_normalization import create_draft
    from runtime.storage.tables import database_session, Material
    dsn = artifact_database_dsn
    owner = _learner(dsn)
    material = create_draft(owner, 'Before', 'timeout-test', dsn=dsn)
    identity = uuid4()

    def blocked():
        with database_session(dsn) as session:
            row = session.get(Material, material)
            row.display_name = 'Must roll back'
            session.flush()
            artifact_storage._lock_source_discard(session, identity)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database_session(dsn) as session:
            artifact_storage._lock_source_discard(session, identity)
            with pytest.raises(OperationalError) as caught:
                pool.submit(blocked).result(timeout=8)
            assert caught.value.orig.sqlstate == '55P03'
    with database_session(dsn) as session:
        row = session.get(Material, material)
        assert row.display_name == 'Before'
        row.display_name = 'After'
    with database_session(dsn) as session:
        assert session.get(Material, material).display_name == 'After'


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
    first = seed_pdf(learner, io.BytesIO(content), "same", dsn=artifact_database_dsn)
    replay = seed_pdf(learner, io.BytesIO(content), "same", dsn=artifact_database_dsn)
    assert replay == first
    with pytest.raises(SourceError, match="IDEMPOTENCY_CONFLICT"):
        seed_pdf(learner, io.BytesIO(_pdf("two")), "same", dsn=artifact_database_dsn)
    with psycopg.connect(artifact_database_dsn) as connection:
        assert connection.execute("SELECT count(*) FROM materials").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM artifacts").fetchone() == (3,)






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


def test_root_must_be_absolute_private_directory(
    artifact_database_dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    learner = _learner(artifact_database_dsn)
    for value in ("relative", str(tmp_path / "missing" / "nested")):
        monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", value)
        with pytest.raises(ArtifactError, match="ARTIFACT_ROOT_INVALID"):
            _publish_source(learner, _pdf(), artifact_database_dsn)

def test_concurrent_source_upload_replays_one_receipt(artifact_database_dsn, artifact_root, monkeypatch):
    from runtime import source_normalization as sources
    owner=_learner(artifact_database_dsn)
    material=sources.create_draft(owner,'Synthetic.pdf','draft',dsn=artifact_database_dsn)
    monkeypatch.setattr(sources,'conversion_policy',lambda:{'schema':'normalization-policy/v1','renderer':'fixture'})
    data=_pdf()
    def upload(content,key):
        try:return sources.upload_source(owner,material,content,'Synthetic.pdf','application/pdf',key,dsn=artifact_database_dsn)
        except SourceError as error:return str(error)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(upload,data,'same-source') for _ in range(2)]
        outcomes=[future.result(timeout=10) for future in futures]
    assert outcomes[0]==outcomes[1] and isinstance(outcomes[0],UUID)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(upload,_pdf(str(i)),'different-source') for i in range(2)]
        outcomes=[future.result(timeout=10) for future in futures]
    assert sum(isinstance(value,UUID) for value in outcomes)==1
    assert 'IDEMPOTENCY_CONFLICT' in outcomes


def test_failed_normalization_retains_private_source_and_no_public_pdf(artifact_database_dsn,artifact_root,monkeypatch):
    from runtime import source_normalization as sources
    from document_normalization.converter import NormalizationError
    owner=_learner(artifact_database_dsn)
    material=sources.create_draft(owner,'Invalid.pdf','draft',dsn=artifact_database_dsn)
    monkeypatch.setattr(sources,'conversion_policy',lambda:{'schema':'normalization-policy/v1','renderer':'fixture'})
    sources.upload_source(owner,material,b'not-pdf','Invalid.pdf','application/pdf','upload',dsn=artifact_database_dsn)
    def reject(*args):raise NormalizationError('PDF_DAMAGED')
    monkeypatch.setattr(sources,'convert',reject)
    assert sources.normalize_next(dsn=artifact_database_dsn)
    item=sources.read_sources(owner,material,dsn=artifact_database_dsn)[0]
    assert item['status']=='failed' and item['normalized_artifact_id'] is None
    assert (artifact_root/'objects'/item['original_artifact_id'].hex).is_file()
