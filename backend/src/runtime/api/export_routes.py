"""私人匯出入口：沿用各領域讀取的擁有者與固定版本核對。"""
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, Request, Response
from fastapi.responses import FileResponse
import asyncio
from pydantic import Field
from .models import _Closed
from . import request_context
from .. import material_notes, export_documents, export_cards

async def export_slot(request: Request):
    # MP4 只傳既有檔案，不進產檔佇列。
    if request.path_params.get('format') == 'mp4':
        yield
        return
    # 等待不佔用 sync route／FileResponse 共用的工作執行緒。
    async with request.app.state.export_slots:
        yield


MIME = {'md': 'text/markdown; charset=utf-8', 'pdf': 'application/pdf',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'zip': 'application/zip', 'apkg': 'application/octet-stream', 'png': 'image/png',
        'mp3': 'audio/mpeg', 'mp4': 'video/mp4'}


def download(data, filename, format):
    if format == 'pdf':
        data = export_documents.compact_pdf(data)
    return Response(data, media_type=MIME[format], headers={
        'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


class CardImage(_Closed):
    concept_id: str = Field(max_length=200)
    page: int = Field(ge=0)
    png: str = Field(max_length=8000000)


class CardExportCreate(_Closed):
    version: int = Field(ge=1)
    format: Literal['apkg', 'pdf', 'zip']


class CardExportBatch(_Closed):
    offset: int = Field(ge=0)
    images: list[CardImage] = Field(min_length=1, max_length=2)


class MapImage(_Closed):
    concept_ids: list[str] = Field(min_length=1, max_length=24)
    png: str = Field(max_length=8000000)


class MapExport(_Closed):
    images: list[MapImage] = Field(min_length=1, max_length=2000)


def install(app, settings):
    app.state.export_slots = asyncio.Semaphore(2)

    def owner(request, query=set()):
        request_context.require_query(request, query)
        return request_context.trusted_learner(request, settings).learner_id

    @app.get('/v1/materials/{material_id}/knowledge-structures/{revision}/notes/{kind}/export/{format}', tags=['exports'], dependencies=[Depends(export_slot, scope="function")])
    def note(request: Request, material_id: UUID, revision: str,
             kind: Literal['summary', 'full'], format: Literal['md', 'pdf', 'docx']):
        view = material_notes.read(owner(request), material_id, revision, kind, dsn=settings.dsn)
        if not view['paragraphs']:
            raise request_context.ApiFailure('RESOURCE_NOT_FOUND')
        render = {'md': export_documents.note_markdown, 'pdf': export_documents.note_pdf, 'docx': export_documents.note_docx}[format]
        return download(render(view), f'{"重點整理" if kind == "summary" else "完整筆記"}.{format}', format)

    @app.post('/v1/card-sets/{identity}/exports', tags=['exports'], dependencies=[Depends(export_slot, scope="function")])
    def start_cards(request: Request, identity: UUID, body: CardExportCreate):
        return export_cards.create(owner(request), identity, body.version, body.format, dsn=settings.dsn)

    @app.put('/v1/card-sets/{identity}/exports/{upload_id}', tags=['exports'], dependencies=[Depends(export_slot, scope="function")])
    def upload_cards(request: Request, identity: UUID, upload_id: UUID, body: CardExportBatch):
        return export_cards.append(owner(request), identity, upload_id, body.offset, body.images, dsn=settings.dsn)

    @app.delete('/v1/card-sets/{identity}/exports/{upload_id}', tags=['exports'], dependencies=[Depends(export_slot, scope="function")])
    def cancel_cards(request: Request, identity: UUID, upload_id: UUID):
        return export_cards.cancel(owner(request), identity, upload_id)

    def card_download_file(request: Request, identity: UUID, upload_id: UUID):
        with export_cards.finish(owner(request), identity, upload_id, dsn=settings.dsn) as result:
            yield result

    @app.post('/v1/card-sets/{identity}/exports/{upload_id}/download', tags=['exports'], dependencies=[Depends(export_slot, scope="function")])
    def download_cards(file=Depends(card_download_file)):
        path, format = file
        return FileResponse(path, media_type=MIME[format], filename=f'概念卡.{format}',
            headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get('/v1/materials/{material_id}/knowledge-structures/{revision}/map/export/md', tags=['exports'], dependencies=[Depends(export_slot, scope="function")])
    def knowledge_map(request: Request, material_id: UUID, revision: str):
        from ..storage.database import database_session
        from ..storage import knowledge_reads
        from .. import export_map
        from sqlalchemy import text
        learner = owner(request)
        with database_session(settings.dsn) as db:
            db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            view = knowledge_reads.outline(db, learner, material_id, revision)
            regions = knowledge_reads.regions(db, learner, material_id, revision)
        return download(export_map.markdown(view, regions), '概念大綱.md', 'md')

    @app.post('/v1/materials/{material_id}/knowledge-structures/{revision}/map/export/png', tags=['exports'], dependencies=[Depends(export_slot, scope="function")])
    def map_images(request: Request, material_id: UUID, revision: str, body: MapExport):
        from ..storage.database import database_session
        from ..storage import knowledge_reads
        learner = owner(request)
        with database_session(settings.dsn) as db:
            view = knowledge_reads.outline(db, learner, material_id, revision)
        ids = [identity for image in body.images for identity in image.concept_ids]
        if len(ids) != len(set(ids)) or set(ids) != {c['concept_id'] for c in view['concepts']}:
            raise request_context.ApiFailure('REQUEST_INVALID')
        files = [(f'地圖-{i + 1:03}.png', export_documents.decode_png(image.png)) for i, image in enumerate(body.images)]
        data, extension = (files[0][1], 'png') if len(files) == 1 else (export_documents.zip_files(files), 'zip')
        return download(data, f'知識地圖.{extension}', extension)

    @app.get('/v1/materials/{material_id}/knowledge-structures/{revision}/learning-diagnosis/export/pdf', tags=['exports'], dependencies=[Depends(export_slot, scope="function")])
    def diagnosis(request: Request, material_id: UUID, revision: str, set_id: UUID | None = None):
        from ..storage.database import database_session
        from learning_adaptation.learning_insights import read_diagnosis
        from sqlalchemy import text
        learner = owner(request, {'set_id'})
        with database_session(settings.dsn) as db:
            db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            view = read_diagnosis(db, learner, material_id, revision, set_id)
        return download(export_documents.diagnosis_pdf(view), '學習診斷.pdf', 'pdf')

    @app.get('/v1/podcasts/{identity}/episodes/{index}/export/{format}', tags=['exports'], dependencies=[Depends(export_slot, scope="function")])
    def podcast(request: Request, identity: UUID, index: int, format: Literal['md', 'mp3', 'mp4']):
        from .. import podcasts, podcast_videos, export_podcast
        from .scene_routes import load_timeline
        from ..storage.artifacts import open_verified_artifact
        from .media import artifact_response
        learner = owner(request)
        view = podcasts.read_podcast(learner, identity, dsn=settings.dsn)
        if view['status'] != 'ready' or not 0 <= index < len(view['episodes']):
            raise request_context.ApiFailure('RESOURCE_NOT_FOUND')
        episode = view['episodes'][index]
        filename = f'{view["name"]}-第{index + 1}集.{format}'
        if format == 'md':
            try:
                timeline = load_timeline(learner, identity, index, dsn=settings.dsn)
            except request_context.ApiFailure as error:
                if error.reason_code in ('PODCAST_TIMELINE_NOT_READY', 'PODCAST_TIMELINE_UNAVAILABLE', 'PODCAST_TIMELINE_SOURCE_MISMATCH'):
                    raise request_context.ApiFailure('SCENE_CONFLICT') from None
                raise
            return download(export_podcast.transcript(view, index, timeline), filename, format)
        if format == 'mp4':
            manifest = podcast_videos.ready_manifest(learner, identity, index, dsn=settings.dsn)
            if manifest is None:
                raise request_context.ApiFailure('RESOURCE_NOT_FOUND')
            response = artifact_response(request, open_verified_artifact(learner, manifest['artifact_id'], dsn=settings.dsn), MIME[format])
            response.headers['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
            return response
        with open_verified_artifact(learner, UUID(episode['audio']['artifact_id']), dsn=settings.dsn) as source:
            data = export_podcast.mp3(source.file)
        return download(data, filename, format)
