"""來源顯示投影；原檔名稱、來源 hash 與逐字稿保存契約不變。"""
import json

from document_normalization.converter import AUDIO_MIME, VIDEO_MIME, YOUTUBE_MIME
from .errors import SourceError
from .storage.artifacts import open_verified_artifact
from .storage.tables import Artifact


def display_metadata(name, media_type, page_count, mapping=None):
    format = (mapping or {}).get('format')
    kind = ('image' if media_type == 'application/zip' else 'youtube' if media_type == YOUTUBE_MIME
            else 'video' if format == 'video' or media_type in VIDEO_MIME.values()
            else 'audio' if media_type in AUDIO_MIME.values() else 'document')
    media = (mapping or {}).get('media') or {}
    transcript = media.get('transcript_source') if kind in {'audio','video','youtube'} else None
    title = name.removesuffix('.youtube') if kind == 'youtube' else name
    if kind == 'image':
        title = f'{page_count} 張圖片' if page_count and page_count > 1 else name.removesuffix('.zip').removesuffix('（圖片）')
    return {
        'display_name': title, 'source_kind': kind,
        'duration_seconds': media.get('duration_seconds') if kind in {'audio','video','youtube'} else None,
        'image_count': page_count if kind == 'image' else None,
        'transcript_source': transcript,
        'transcript_display_name': {'whisper':'語音辨識','uploader_subtitles':'YouTube 上傳者字幕'}.get(transcript),
    }


def source_display(owner, source, job, session):
    mapping = None
    if job.mapping_artifact_id and source.media_type in {*AUDIO_MIME.values(), *VIDEO_MIME.values(), YOUTUBE_MIME}:
        with open_verified_artifact(owner, job.mapping_artifact_id, session=session) as opened:
            mapping = json.load(opened.file)
        original = session.get(Artifact, source.original_artifact_id)
        normalized = session.get(Artifact, job.normalized_artifact_id)
        if (mapping['original_sha256'] != bytes(original.sha256).hex()
                or mapping['normalized_sha256'] != bytes(normalized.sha256).hex()):
            raise SourceError('SOURCE_BINDING_INVALID')
    return display_metadata(source.original_name, source.media_type, job.page_count, mapping)
