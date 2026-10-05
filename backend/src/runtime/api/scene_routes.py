from typing import Literal
from uuid import UUID
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel,ConfigDict,Field
from .. import podcast_scenes
from .. import podcasts
from .. import podcast_videos
from ..podcast_timeline import build_timeline, webvtt

class Prepare(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['retry','cancel']
    expected_version:int=Field(ge=1)

def install(app,settings,trusted,query):
    def owner(request):query(request,set());return trusted(request,settings).learner_id
    def timeline(request, identity, index):
        learner = owner(request)
        video = podcast_videos.ready_manifest(learner,identity,index,dsn=settings.dsn)
        if video is not None:
            return video['timeline']
        podcast = podcasts.read_podcast(learner, identity, dsn=settings.dsn)
        if not 0 <= index < len(podcast['episodes']):
            raise HTTPException(404)
        scene = podcast_scenes.read(learner, identity, dsn=settings.dsn)['episodes'][index]
        if scene['status'] != 'ready':
            raise HTTPException(409, detail='PODCAST_TIMELINE_NOT_READY')
        try:
            return build_timeline(identity, index, podcast['episodes'][index], scene['manifest'])
        except ValueError:
            raise HTTPException(409, detail='PODCAST_TIMELINE_SOURCE_MISMATCH') from None
    @app.get('/v1/podcasts/{identity}/episodes/{index}/timeline')
    def read_timeline(request:Request,identity:UUID,index:int):
        return timeline(request, identity, index)
    @app.get('/v1/podcasts/{identity}/episodes/{index}/subtitles')
    def subtitles(request:Request,identity:UUID,index:int):
        return Response(webvtt(timeline(request, identity, index)), media_type='text/vtt')
    @app.get('/v1/podcasts/{identity}/scenes')
    def read(request:Request,identity:UUID):return podcast_scenes.read(owner(request),identity,dsn=settings.dsn)
    @app.post('/v1/podcasts/{identity}/scenes',status_code=202)
    def prepare(request:Request,identity:UUID,body:Prepare):return podcast_scenes.prepare(owner(request),identity,dsn=settings.dsn)
    @app.post('/v1/podcasts/{identity}/scenes/{index}/actions')
    def action(request:Request,identity:UUID,index:int,body:Action):return podcast_scenes.action(owner(request),identity,index,body.action,body.expected_version,dsn=settings.dsn)
