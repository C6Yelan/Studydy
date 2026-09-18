from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import os
from pathlib import Path
import stat
from typing import BinaryIO
from uuid import UUID, uuid4

import pymupdf
from sqlalchemy import insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .tables import Artifact, Material, MaterialSource, SourceNormalization, database_session, deferred_artifact_session

ARTIFACT_ROOT_ENV = "STUDYDY_ARTIFACT_ROOT"
SOURCE_LIMIT_BYTES = 104_857_600
_CHUNK = 1024 * 1024


class ArtifactError(RuntimeError):
    """Artifact 操作失敗且不揭露路徑、內容或資料庫細節。"""


@dataclass(frozen=True)
class PublishedSourcePdf:
    material_id: UUID
    artifact_id: UUID
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class VerifiedSourcePdf:
    material_id: UUID
    artifact_id: UUID
    sha256: str
    size_bytes: int
    file: BinaryIO = field(repr=False, compare=False)


def _error(reason: str) -> ArtifactError:
    return ArtifactError(reason)


def _root() -> Path:
    raw = os.environ.get(ARTIFACT_ROOT_ENV)
    try:
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise ValueError
        path = Path(raw)
        if not path.is_absolute():
            raise ValueError
        if path.exists() and path.is_symlink():
            raise ValueError
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
        details = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(details.st_mode)
            or stat.S_IMODE(details.st_mode) != 0o700
            or details.st_uid != os.geteuid()
        ):
            raise ValueError
        objects = path / "objects"
        staging = path / ".staging"
        for directory in (objects, staging, path / ".trash"):
            directory.mkdir(mode=0o700, exist_ok=True)
            item = directory.stat(follow_symlinks=False)
            if not stat.S_ISDIR(item.st_mode) or stat.S_IMODE(item.st_mode) != 0o700:
                raise ValueError
        return path
    except (OSError, ValueError, UnicodeError):
        raise _error("ARTIFACT_ROOT_INVALID") from None


def _key_digest(value: str) -> bytes:
    try:
        encoded = value.encode("utf-8")
    except (AttributeError, UnicodeError):
        raise _error("ARTIFACT_REQUEST_INVALID") from None
    if not 1 <= len(encoded) <= 256:
        raise _error("ARTIFACT_REQUEST_INVALID")
    return sha256(encoded).digest()


def _copy_pdf(source: BinaryIO, destination: Path) -> tuple[bytes, int]:
    digest = sha256()
    size = 0
    try:
        with destination.open("xb") as output:
            while True:
                chunk = source.read(_CHUNK)
                if not isinstance(chunk, bytes):
                    raise OSError
                if not chunk:
                    break
                size += len(chunk)
                if size > SOURCE_LIMIT_BYTES:
                    raise _error("ARTIFACT_TOO_LARGE")
                output.write(chunk)
                digest.update(chunk)
        try:
            with pymupdf.open(destination) as document:
                if document.needs_pass or document.is_encrypted or document.page_count < 1:
                    raise _error("ARTIFACT_PDF_INVALID")
        except ArtifactError:
            raise
        except Exception:
            raise _error("ARTIFACT_PDF_INVALID") from None
        destination.chmod(0o400)
        return digest.digest(), size
    except ArtifactError:
        raise
    except Exception:
        raise _error("ARTIFACT_PUBLISH_FAILED") from None


def _object_path(root: Path, artifact_id: UUID) -> Path:
    return root / "objects" / artifact_id.hex


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _lock_source_discard(session: Session, artifact_id: UUID) -> None:
    # Reconciliation 必須等待 rename 所屬 transaction 結束，不能誤還原未提交的刪除。
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(artifact_id.bytes[:8], "big", signed=True)})


def _reconcile_source(session: Session, root: Path, artifact_id: UUID) -> None:
    trash = root / ".trash" / artifact_id.hex
    if not trash.exists():
        return
    if not stat.S_ISREG(trash.stat(follow_symlinks=False).st_mode):
        raise OSError
    referenced = session.scalar(select(Artifact.artifact_id).where(Artifact.artifact_id == artifact_id))
    if referenced is not None:
        destination = _object_path(root, artifact_id)
        if destination.exists():
            raise OSError
        os.rename(trash, destination)
        _sync_directory(destination.parent)
    else:
        trash.unlink()
    _sync_directory(trash.parent)


def quarantine_source_pdf(session: Session, artifact_id: UUID) -> None:
    """Caller 已鎖 Material/runs/artifact；advisory lock 隨 DB commit/rollback 釋放。"""
    try:
        root = _root()
        _lock_source_discard(session, artifact_id)
        _reconcile_source(session, root, artifact_id)
        source = _object_path(root, artifact_id)
        if not stat.S_ISREG(source.stat(follow_symlinks=False).st_mode):
            raise OSError
        os.rename(source, root / ".trash" / artifact_id.hex)
        _sync_directory(source.parent)
        _sync_directory(root / ".trash")
    except Exception:
        raise _error("ARTIFACT_STORAGE_FAILED") from None


def reconcile_discarded_sources(*, dsn: str | None = None, artifact_id: UUID | None = None) -> None:
    """只處理 discard quarantine：仍有 DB reference 就還原，否則實體刪除。"""
    try:
        root = _root()
        identities = [artifact_id] if artifact_id else [UUID(hex=path.name) for path in (root / ".trash").iterdir()]
        for identity in identities:
            with database_session(dsn) as session:
                _lock_source_discard(session, identity)
                _reconcile_source(session, root, identity)
    except Exception:
        raise _error("ARTIFACT_STORAGE_FAILED") from None


def _verify_file(path: Path, expected_digest: bytes, expected_size: int) -> BinaryIO:
    descriptor = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size != expected_size:
            raise OSError
        digest = sha256()
        while True:
            chunk = os.read(descriptor, _CHUNK)
            if not chunk:
                break
            digest.update(chunk)
        if digest.digest() != expected_digest:
            raise OSError
        os.lseek(descriptor, 0, os.SEEK_SET)
        opened = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = None
        return opened
    except Exception:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise _error("ARTIFACT_NOT_AVAILABLE") from None


def _read_source_receipt(session: Session, learner_id: UUID, key: bytes):
    return session.execute(
        select(
            Material.material_id,
            Material.source_artifact_id,
            Artifact.sha256,
            Artifact.size_bytes,
            Artifact.kind,
            Material.upload_request_fingerprint,
            Material.display_name,
        )
        .join(
            Artifact,
            (Artifact.artifact_id == Material.source_artifact_id)
            & (Artifact.learner_id == Material.learner_id)
            & (Artifact.material_id == Material.material_id),
        )
        .where(
            Material.learner_id == learner_id,
            Material.upload_idempotency_key_sha256 == key,
        )
    ).one_or_none()


def _published_source(
    root: Path, existing: Any, fingerprint: bytes, display_name: str | None
) -> PublishedSourcePdf:
    if existing[4] != "source_pdf":
        raise _error("ARTIFACT_PUBLISH_FAILED")
    if bytes(existing[5]) != fingerprint or existing[6] != display_name:
        raise _error("ARTIFACT_IDEMPOTENCY_CONFLICT")
    file = _verify_file(_object_path(root, existing[1]), bytes(existing[2]), existing[3])
    file.close()
    return PublishedSourcePdf(existing[0], existing[1], bytes(existing[2]).hex(), existing[3])


def _publish_source(
    learner_id: UUID,
    source: BinaryIO,
    *,
    key_digest: bytes,
    display_name: str | None,
    dsn: str | None,
) -> PublishedSourcePdf:
    if not isinstance(learner_id, UUID) or not hasattr(source, "read"):
        raise _error("ARTIFACT_REQUEST_INVALID")
    if display_name is not None and (
        not isinstance(display_name, str) or not 1 <= len(display_name) <= 200
        or not display_name.strip()
        or any(ord(char) < 32 or ord(char) == 127 or char in "/\\" for char in display_name)
    ):
        raise _error("ARTIFACT_REQUEST_INVALID")
    root = _root()
    staging = root / ".staging" / f"{uuid4().hex}.tmp"
    final: Path | None = None
    try:
        digest, size = _copy_pdf(source, staging)
        fingerprint = sha256(digest + size.to_bytes(8, "big")).digest()
        with database_session(dsn) as session:
            existing = _read_source_receipt(session, learner_id, key_digest)
        if existing is not None:
            return _published_source(root, existing, fingerprint, display_name)

        material_id = uuid4()
        artifact_id = uuid4()
        final = _object_path(root, artifact_id)
        os.rename(staging, final)
        try:
            with deferred_artifact_session(dsn) as session:
                session.execute(
                    insert(Material).values(
                        material_id=material_id,
                        learner_id=learner_id,
                        display_name=display_name,
                        source_artifact_id=artifact_id,
                        upload_idempotency_key_sha256=key_digest,
                        upload_request_fingerprint=fingerprint,
                        created_at=datetime.now(UTC),
                    )
                )
                session.execute(
                    insert(Artifact).values(
                        artifact_id=artifact_id,
                        learner_id=learner_id,
                        material_id=material_id,
                        kind="source_pdf",
                        media_type="application/pdf",
                        sha256=digest,
                        size_bytes=size,
                        created_at=datetime.now(UTC),
                    )
                )
                # 新 v1 PDF 也保存 identity normalization，與 migration 的既有 PDF backfill 一致。
                now=datetime.now(UTC)
                session.execute(insert(MaterialSource).values(source_id=artifact_id,learner_id=learner_id,material_id=material_id,
                    original_artifact_id=artifact_id,original_name=display_name or "material.pdf",media_type="application/pdf",
                    idempotency_key_sha256=key_digest,request_fingerprint=fingerprint,created_at=now))
                session.execute(insert(SourceNormalization).values(normalization_id=artifact_id,learner_id=learner_id,material_id=material_id,
                    source_id=artifact_id,policy={"schema":"normalization-policy/v1","renderer":"pdf-identity"},status="ready",
                    normalized_artifact_id=artifact_id,created_at=now,updated_at=now))
        except IntegrityError:
            final.unlink(missing_ok=True)
            final = None
            with database_session(dsn) as session:
                winner = _read_source_receipt(session, learner_id, key_digest)
            if winner is None:
                raise _error("ARTIFACT_PUBLISH_FAILED") from None
            return _published_source(root, winner, fingerprint, display_name)
        except Exception:
            final.unlink(missing_ok=True)
            raise _error("ARTIFACT_PUBLISH_FAILED") from None
        return PublishedSourcePdf(material_id, artifact_id, digest.hex(), size)
    finally:
        staging.unlink(missing_ok=True)


def publish_idempotent_source_pdf(
    learner_id: UUID,
    source: BinaryIO,
    idempotency_key: str,
    *,
    dsn: str | None = None,
    display_name: str | None = None,
) -> PublishedSourcePdf:
    return _publish_source(
        learner_id,
        source,
        key_digest=_key_digest(idempotency_key),
        display_name=display_name,
        dsn=dsn,
    )


@contextmanager
def open_verified_source_pdf(
    learner_id: UUID, artifact_id: UUID, *, dsn: str | None = None
) -> Generator[VerifiedSourcePdf, None, None]:
    if not isinstance(learner_id, UUID) or not isinstance(artifact_id, UUID):
        raise _error("ARTIFACT_NOT_AVAILABLE")
    try:
        with database_session(dsn) as session:
            row = session.execute(
                select(Artifact.material_id, Artifact.sha256, Artifact.size_bytes).where(
                    Artifact.learner_id == learner_id,
                    Artifact.artifact_id == artifact_id,
                    Artifact.kind.in_(("source_pdf","normalized_pdf")),
                )
            ).one_or_none()
        if row is None:
            raise _error("ARTIFACT_NOT_AVAILABLE")
        opened = _verify_file(_object_path(_root(), artifact_id), bytes(row[1]), row[2])
    except ArtifactError:
        raise
    except Exception:
        raise _error("ARTIFACT_NOT_AVAILABLE") from None
    try:
        yield VerifiedSourcePdf(row[0], artifact_id, bytes(row[1]).hex(), row[2], opened)
    finally:
        try:
            opened.close()
        except Exception:
            pass
