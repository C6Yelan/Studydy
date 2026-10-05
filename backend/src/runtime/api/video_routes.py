from typing import Literal
from uuid import UUID
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
from .. import podcast_videos
from ..source_normalization import SourceError
from ..storage.source_artifacts import open_verified_artifact
from .media import artifact_response


class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['retry','cancel']
    expected_version:int=Field(ge=1)


def install(app,settings,trusted,query):
    def owner(request):
        query(request,set())
        return trusted(request,settings).learner_id

    @app.get('/v1/podcasts/{identity}/episodes/{index}/video')
    def read(request:Request,identity:UUID,index:int):
        return podcast_videos.read(owner(request),identity,index,dsn=settings.dsn)

    @app.post('/v1/podcasts/{identity}/episodes/{index}/video',status_code=202)
    def prepare(request:Request,identity:UUID,index:int):
        return podcast_videos.prepare(owner(request),identity,index,dsn=settings.dsn)

    @app.post('/v1/podcasts/{identity}/episodes/{index}/video/actions')
    def action(request:Request,identity:UUID,index:int,body:Action):
        return podcast_videos.action(owner(request),identity,index,body.action,body.expected_version,dsn=settings.dsn)

    @app.get('/v1/podcasts/{identity}/episodes/{index}/video/media')
    def media(request:Request,identity:UUID,index:int):
        learner=owner(request)
        manifest=podcast_videos.ready_manifest(learner,identity,index,dsn=settings.dsn)
        if manifest is None:raise SourceError('RESOURCE_NOT_FOUND')
        return artifact_response(request,open_verified_artifact(learner,manifest['artifact_id'],dsn=settings.dsn),'video/mp4')
