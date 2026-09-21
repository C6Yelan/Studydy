"""原檔／轉換產物共用 private artifact store；未知 commit 結果保留待核對。"""
from contextlib import contextmanager
from datetime import UTC,datetime
from hashlib import sha256
import os
from uuid import UUID,uuid4
from sqlalchemy import select
from .artifacts import _root,_object_path,_verify_file,_sync_directory,_lock_source_discard,ArtifactError,VerifiedSourcePdf
from .tables import Artifact,database_session


def write_blob(session, learner_id,material_id,data:bytes,kind:str,media_type:str):
    if not data or len(data)>104857600:raise ArtifactError('ARTIFACT_TOO_LARGE')
    identity=uuid4();root=_root();_lock_source_discard(session,identity)
    marker=root/'.staging'/f'{identity.hex}.pending';marker.touch(mode=0o600,exist_ok=False)
    destination=_object_path(root,identity)
    with destination.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    destination.chmod(0o400);_sync_directory(destination.parent)
    row=Artifact(artifact_id=identity,learner_id=learner_id,material_id=material_id,kind=kind,media_type=media_type,
                 sha256=sha256(data).digest(),size_bytes=len(data),created_at=datetime.now(UTC))
    session.add(row);session.flush()
    return row


def reconcile_new_artifacts(*,dsn=None):
    root=_root()
    for marker in (root/'.staging').glob('*.pending'):
        identity=UUID(hex=marker.stem)
        with database_session(dsn) as session:
            _lock_source_discard(session,identity)
            if session.get(Artifact,identity) is None:_object_path(root,identity).unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
    _sync_directory(root/'.staging')


@contextmanager
def open_verified_artifact(learner_id,artifact_id,*,dsn=None):
    with database_session(dsn) as session:
        row=session.scalar(select(Artifact).where(Artifact.learner_id==learner_id,Artifact.artifact_id==artifact_id))
        if row is None:raise ArtifactError('ARTIFACT_NOT_AVAILABLE')
        opened=_verify_file(_object_path(_root(),artifact_id),bytes(row.sha256),row.size_bytes)
        result=VerifiedSourcePdf(row.material_id,artifact_id,bytes(row.sha256).hex(),row.size_bytes,opened)
    try:yield result
    finally:opened.close()
