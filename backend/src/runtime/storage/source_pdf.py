"""保存並回查 learner 上傳的原始 PDF。"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import os
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from .artifacts import (
    ArtifactError,
    _copy_pdf,
    _error,
    _object_path,
    _root,
    _verify_file,
)
from .tables import Artifact, Material, database_session, deferred_artifact_session


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


def _key_digest(value: str) -> bytes:
    try:
        encoded = value.encode("utf-8")
    except (AttributeError, UnicodeError):
        raise _error("ARTIFACT_REQUEST_INVALID") from None
    if not 1 <= len(encoded) <= 256:
        raise _error("ARTIFACT_REQUEST_INVALID")
    return sha256(encoded).digest()


def _read_source_receipt(session, learner_id: UUID, key: bytes):
    return session.execute(
        select(
            Material.material_id,
            Material.source_artifact_id,
            Artifact.sha256,
            Artifact.size_bytes,
            Artifact.kind,
            Material.upload_request_fingerprint,
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


def publish_idempotent_source_pdf(
    learner_id: UUID,
    source: BinaryIO,
    idempotency_key: str,
    *,
    dsn: str | None = None,
) -> PublishedSourcePdf:
    if not isinstance(learner_id, UUID) or not hasattr(source, "read"):
        raise _error("ARTIFACT_REQUEST_INVALID")
    root = _root()
    staging = root / ".staging" / f"{uuid4().hex}.tmp"
    final = None
    try:
        digest, size = _copy_pdf(source, staging)
        fingerprint = sha256(digest + size.to_bytes(8, "big")).digest()
        key_digest = _key_digest(idempotency_key)
        with database_session(dsn) as session:
            existing = _read_source_receipt(session, learner_id, key_digest)
        if existing is not None:
            if existing[4] != "source_pdf":
                raise _error("ARTIFACT_PUBLISH_FAILED")
            if bytes(existing[5]) != fingerprint:
                raise _error("ARTIFACT_IDEMPOTENCY_CONFLICT")
            opened = _verify_file(_object_path(root, existing[1]), bytes(existing[2]), existing[3])
            opened.close()
            return PublishedSourcePdf(existing[0], existing[1], bytes(existing[2]).hex(), existing[3])

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
        except Exception:
            final.unlink(missing_ok=True)
            raise _error("ARTIFACT_PUBLISH_FAILED") from None
        return PublishedSourcePdf(material_id, artifact_id, digest.hex(), size)
    finally:
        staging.unlink(missing_ok=True)


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
                    Artifact.kind == "source_pdf",
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
