"""從已驗證的時間映射取得逐字稿來源，不以副檔名或模型猜測辨識方式。"""

import json
from uuid import UUID

from document_normalization.converter import AUDIO_MIME, VIDEO_MIME, YOUTUBE_MIME
from .errors import SourceError
from .storage.artifacts import open_verified_artifact


def transcript_metadata(owner, item, session):
    media_type = item["media_type"]
    if media_type not in {*AUDIO_MIME.values(), *VIDEO_MIME.values(), YOUTUBE_MIME}:
        return None
    with open_verified_artifact(
        owner, UUID(item["mapping_artifact_id"]), session=session
    ) as artifact:
        mapping = json.load(artifact.file)
        media = mapping.get("media", {})
        if (
            artifact.sha256 != item["mapping_sha256"]
            or mapping.get("schema") != "source-mapping/v1"
            or mapping.get("original_sha256") != item["original_sha256"]
            or mapping.get("normalized_sha256") != item["normalized_sha256"]
            or mapping.get("format") not in {"audio", "video", "youtube"}
            or media.get("transcript_source") not in {"whisper", "uploader_subtitles"}
            or not isinstance(media.get("language"), str)
        ):
            raise SourceError("SOURCE_BINDING_INVALID")
    return {
        "format": mapping["format"],
        "transcript_source": media["transcript_source"],
        "language": media["language"],
    }


def read_transcript(owner, material_id, normalization_id, *, dsn=None):
    import pymupdf
    from sqlalchemy import select
    from .storage.database import database_session
    from .storage.materials import require_material
    from .storage.tables import MaterialSource, SourceNormalization

    with database_session(dsn) as session:
        require_material(session, owner, material_id, read=True)
        job = session.scalar(
            select(SourceNormalization)
            .join(MaterialSource, MaterialSource.source_id == SourceNormalization.source_id)
            .where(
                MaterialSource.learner_id == owner,
                MaterialSource.material_id == material_id,
                MaterialSource.removed_at.is_(None),
                MaterialSource.media_type.in_([*AUDIO_MIME.values(), *VIDEO_MIME.values(), YOUTUBE_MIME]),
                SourceNormalization.normalization_id == normalization_id,
                SourceNormalization.status == "ready",
            )
        )
        if job is None:
            raise SourceError("RESOURCE_NOT_FOUND")
        with open_verified_artifact(owner, job.normalized_artifact_id, session=session) as artifact:
            with pymupdf.open(stream=artifact.file.read(), filetype="pdf") as document:
                return {"schema": "source-transcript/v1", "text": "\n\n".join(page.get_text().strip() for page in document)}
