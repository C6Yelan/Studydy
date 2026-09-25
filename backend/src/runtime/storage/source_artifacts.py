"""原檔與轉換產物共用私人 artifact store；未確認的 commit 留待核對。"""

from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
import os
from uuid import UUID, uuid4

from sqlalchemy import select

from .artifacts import (
    ArtifactError,
    VerifiedSourcePdf,
    _lock_source_discard,
    _object_path,
    _root,
    _sync_directory,
    _verify_file,
)
from .tables import Artifact, database_session


def write_blob(session, learner_id, material_id, data: bytes, kind: str, media_type: str):
    if not data or len(data) > 100 * 1024 * 1024:
        raise ArtifactError("ARTIFACT_TOO_LARGE")

    artifact_id = uuid4()
    root = _root()
    _lock_source_discard(session, artifact_id)
    marker = root / ".staging" / f"{artifact_id.hex}.pending"
    marker.touch(mode=0o600, exist_ok=False)
    destination = _object_path(root, artifact_id)
    with destination.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    destination.chmod(0o400)
    _sync_directory(destination.parent)

    row = Artifact(
        artifact_id=artifact_id,
        learner_id=learner_id,
        material_id=material_id,
        kind=kind,
        media_type=media_type,
        sha256=sha256(data).digest(),
        size_bytes=len(data),
        created_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def reconcile_new_artifacts(*, dsn=None):
    root = _root()
    for marker in (root / ".staging").glob("*.pending"):
        artifact_id = UUID(hex=marker.stem)
        with database_session(dsn) as session:
            if not _lock_source_discard(session, artifact_id, wait=False):
                continue
            if session.get(Artifact, artifact_id) is None:
                _object_path(root, artifact_id).unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
    _sync_directory(root / ".staging")


@contextmanager
def open_verified_artifact(learner_id, artifact_id, *, dsn=None):
    with database_session(dsn) as session:
        row = session.scalar(select(Artifact).where(
            Artifact.learner_id == learner_id,
            Artifact.artifact_id == artifact_id,
        ))
        if row is None:
            raise ArtifactError("ARTIFACT_NOT_AVAILABLE")
        digest = bytes(row.sha256)
        opened = _verify_file(_object_path(_root(), artifact_id), digest, row.size_bytes)
        result = VerifiedSourcePdf(row.material_id, artifact_id, digest.hex(), row.size_bytes, opened)
    try:
        yield result
    finally:
        opened.close()
