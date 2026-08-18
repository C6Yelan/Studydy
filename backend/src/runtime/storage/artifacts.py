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
from sqlalchemy import delete, select

from .tables import Artifact, database_session

ARTIFACT_ROOT_ENV = "STUDYDY_ARTIFACT_ROOT"
SOURCE_LIMIT_BYTES = 104_857_600
_CHUNK = 1024 * 1024


class ArtifactError(RuntimeError):
    """Artifact 操作失敗且不揭露路徑、內容或資料庫細節。"""


@dataclass(frozen=True)
class VerifiedResourcePdf:
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
        for directory in (objects, staging):
            directory.mkdir(mode=0o700, exist_ok=True)
            item = directory.stat(follow_symlinks=False)
            if not stat.S_ISDIR(item.st_mode) or stat.S_IMODE(item.st_mode) != 0o700:
                raise ValueError
        return path
    except (OSError, ValueError, UnicodeError):
        raise _error("ARTIFACT_ROOT_INVALID") from None


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


def publish_resource_pdf(
    learner_id: UUID,
    material_id: UUID,
    artifact_id: UUID,
    source: BinaryIO,
    expected_sha256: str,
    expected_size_bytes: int,
    *,
    dsn: str | None = None,
) -> None:
    identifiers = (learner_id, material_id, artifact_id)
    if not all(isinstance(value, UUID) for value in identifiers):
        raise _error("RESOURCE_ARTIFACT_PUBLISH_FAILED")
    try:
        expected = bytes.fromhex(expected_sha256)
    except (TypeError, ValueError):
        raise _error("RESOURCE_ARTIFACT_PUBLISH_FAILED") from None
    if len(expected) != 32 or not 1 <= expected_size_bytes <= SOURCE_LIMIT_BYTES:
        raise _error("RESOURCE_ARTIFACT_PUBLISH_FAILED")
    root = _root()
    final = _object_path(root, artifact_id)
    if final.exists():
        raise _error("RESOURCE_ARTIFACT_CONFLICT")

    staging = root / ".staging" / f"{uuid4().hex}.tmp"
    try:
        digest, size = _copy_pdf(source, staging)
        if digest != expected or size != expected_size_bytes:
            raise _error("RESOURCE_ARTIFACT_PUBLISH_FAILED")
        os.rename(staging, final)
        try:
            with database_session(dsn) as session:
                session.add(
                    Artifact(
                        artifact_id=artifact_id,
                        learner_id=learner_id,
                        material_id=material_id,
                        kind="resource_pdf",
                        media_type="application/pdf",
                        sha256=digest,
                        size_bytes=size,
                        created_at=datetime.now(UTC),
                    )
                )
        except Exception:
            final.unlink(missing_ok=True)
            raise _error("RESOURCE_ARTIFACT_PUBLISH_FAILED") from None
    finally:
        staging.unlink(missing_ok=True)


@contextmanager
def open_verified_resource_pdf(
    learner_id: UUID,
    material_id: UUID,
    artifact_id: UUID,
    *,
    dsn: str | None = None,
) -> Generator[VerifiedResourcePdf, None, None]:
    if not all(isinstance(value, UUID) for value in (learner_id, material_id, artifact_id)):
        raise _error("RESOURCE_ARTIFACT_NOT_AVAILABLE")
    try:
        with database_session(dsn) as session:
            row = session.execute(
                select(Artifact.sha256, Artifact.size_bytes).where(
                    Artifact.learner_id == learner_id,
                    Artifact.material_id == material_id,
                    Artifact.artifact_id == artifact_id,
                    Artifact.kind == "resource_pdf",
                )
            ).one_or_none()
        if row is None:
            raise _error("RESOURCE_ARTIFACT_NOT_AVAILABLE")
        opened = _verify_file(_object_path(_root(), artifact_id), bytes(row[0]), row[1])
    except ArtifactError as error:
        if str(error) == "ARTIFACT_NOT_AVAILABLE":
            raise _error("RESOURCE_ARTIFACT_NOT_AVAILABLE") from None
        raise
    except Exception:
        raise _error("RESOURCE_ARTIFACT_NOT_AVAILABLE") from None
    try:
        yield VerifiedResourcePdf(material_id, artifact_id, bytes(row[0]).hex(), row[1], opened)
    finally:
        try:
            opened.close()
        except Exception:
            pass


def remove_resource_pdf(
    learner_id: UUID,
    material_id: UUID,
    artifact_id: UUID,
    *,
    dsn: str | None = None,
) -> None:
    """只供同一次建立失敗時移除 exact resource row 與檔案。"""
    try:
        with database_session(dsn) as session:
            row = session.execute(
                delete(Artifact)
                .where(
                    Artifact.learner_id == learner_id,
                    Artifact.material_id == material_id,
                    Artifact.artifact_id == artifact_id,
                    Artifact.kind == "resource_pdf",
                )
                .returning(Artifact.artifact_id)
            ).one_or_none()
        if row is not None:
            _object_path(_root(), artifact_id).unlink(missing_ok=True)
    except Exception:
        raise _error("RESOURCE_ARTIFACT_CLEANUP_FAILED") from None
