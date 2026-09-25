from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
import os
from pathlib import Path
import stat
from typing import BinaryIO
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .tables import Artifact, database_session

ARTIFACT_ROOT_ENV = "STUDYDY_ARTIFACT_ROOT"
_CHUNK = 1024 * 1024


class ArtifactError(RuntimeError):
    """Artifact 操作失敗且不揭露路徑、內容或資料庫細節。"""


@dataclass(frozen=True)
class VerifiedSourcePdf:
    material_id: UUID
    artifact_id: UUID
    sha256: str
    size_bytes: int
    file: BinaryIO = field(repr=False, compare=False)


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
        for directory in (path / "objects", path / ".staging", path / ".trash"):
            directory.mkdir(mode=0o700, exist_ok=True)
            item = directory.stat(follow_symlinks=False)
            if not stat.S_ISDIR(item.st_mode) or stat.S_IMODE(item.st_mode) != 0o700:
                raise ValueError
        return path
    except (OSError, ValueError, UnicodeError):
        raise ArtifactError("ARTIFACT_ROOT_INVALID") from None


def _key_digest(value: str) -> bytes:
    try:
        encoded = value.encode("utf-8")
    except (AttributeError, UnicodeError):
        raise ArtifactError("ARTIFACT_REQUEST_INVALID") from None
    if not 1 <= len(encoded) <= 256:
        raise ArtifactError("ARTIFACT_REQUEST_INVALID")
    return sha256(encoded).digest()


def _object_path(root: Path, artifact_id: UUID) -> Path:
    return root / "objects" / artifact_id.hex


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _lock_source_discard(session: Session, artifact_id: UUID, *, wait: bool = True) -> bool:
    # 核對必須與 rename 所屬 transaction 互斥，不能誤還原未提交的刪除。
    function = "pg_advisory_xact_lock" if wait else "pg_try_advisory_xact_lock"
    acquired = session.scalar(text(f"SELECT {function}(:key)"), {
        "key": int.from_bytes(artifact_id.bytes[:8], "big", signed=True),
    })
    return True if wait else bool(acquired)


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
        raise ArtifactError("ARTIFACT_STORAGE_FAILED") from None


def reconcile_discarded_sources(*, dsn: str | None = None, artifact_id: UUID | None = None) -> None:
    """只處理 discard quarantine：仍有 DB reference 就還原，否則實體刪除。"""
    try:
        root = _root()
        identities = [artifact_id] if artifact_id else [UUID(hex=path.name) for path in (root / ".trash").iterdir()]
        for identity in identities:
            with database_session(dsn) as session:
                # 仍在寫入／刪除的項目留待下次核對，不阻擋其他教材。
                if not _lock_source_discard(session, identity, wait=False):
                    continue
                _reconcile_source(session, root, identity)
    except Exception:
        raise ArtifactError("ARTIFACT_STORAGE_FAILED") from None


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
        raise ArtifactError("ARTIFACT_NOT_AVAILABLE") from None


@contextmanager
def open_verified_source_pdf(
    learner_id: UUID, artifact_id: UUID, *, dsn: str | None = None
) -> Generator[VerifiedSourcePdf, None, None]:
    if not isinstance(learner_id, UUID) or not isinstance(artifact_id, UUID):
        raise ArtifactError("ARTIFACT_NOT_AVAILABLE")
    try:
        with database_session(dsn) as session:
            row = session.execute(
                select(Artifact.material_id, Artifact.sha256, Artifact.size_bytes).where(
                    Artifact.learner_id == learner_id,
                    Artifact.artifact_id == artifact_id,
                    Artifact.kind == "normalized_pdf",
                )
            ).one_or_none()
        if row is None:
            raise ArtifactError("ARTIFACT_NOT_AVAILABLE")
        opened = _verify_file(_object_path(_root(), artifact_id), bytes(row[1]), row[2])
    except ArtifactError:
        raise
    except Exception:
        raise ArtifactError("ARTIFACT_NOT_AVAILABLE") from None
    try:
        yield VerifiedSourcePdf(row[0], artifact_id, bytes(row[1]).hex(), row[2], opened)
    finally:
        try:
            opened.close()
        except Exception:
            pass
