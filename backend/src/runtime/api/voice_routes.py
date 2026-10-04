from typing import Literal
from uuid import UUID
from fastapi import Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from .. import voice
from ..source_normalization import SourceError


class Question(BaseModel):
    model_config=ConfigDict(extra='forbid')
    question:str=Field(min_length=1,max_length=4000)


class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['send','cancel','retry']
    question:str=Field(default='',max_length=4000)


def install(app,settings,trusted,key,query):
    def owner(request):
        query(request,set())
        return trusted(request,settings).learner_id

    @app.get('/v1/materials/{material_id}/voice-conversations')
    def listing(request:Request,material_id:UUID):
        return voice.listing(owner(request),material_id,dsn=settings.dsn)

    @app.post('/v1/materials/{material_id}/voice-conversations',status_code=201)
    def create(request:Request,material_id:UUID):
        return voice.create(owner(request),material_id,key(request),dsn=settings.dsn)

    @app.get('/v1/voice-conversations/{identity}')
    def read(request:Request,identity:UUID):
        return voice.read(owner(request),identity,dsn=settings.dsn)

    @app.delete('/v1/voice-conversations/{identity}')
    def remove(request:Request,identity:UUID):
        return voice.remove(owner(request),identity,dsn=settings.dsn)

    @app.post('/v1/voice-conversations/{identity}/turns',status_code=202)
    def question(request:Request,identity:UUID,body:Question):
        return voice.add_turn(owner(request),identity,key(request),body.question,dsn=settings.dsn)

    @app.post('/v1/voice-conversations/{identity}/recordings',status_code=202)
    async def recording(request:Request,identity:UUID):
        learner=await run_in_threadpool(owner,request)
        request_key=key(request)
        data=bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk)>12*1024*1024:raise SourceError('MATERIAL_TOO_LARGE')
            data.extend(chunk)
        if not data:raise SourceError('REQUEST_INVALID')
        return await run_in_threadpool(voice.add_turn,learner,identity,request_key,recording=bytes(data),dsn=settings.dsn)

    @app.post('/v1/voice-conversations/{identity}/turns/{turn_id}/actions')
    def action(request:Request,identity:UUID,turn_id:UUID,body:Action):
        return voice.action(owner(request),identity,turn_id,body.action,body.question,dsn=settings.dsn)

    @app.get('/v1/voice-conversations/{identity}/turns/{turn_id}/audio')
    def audio(request:Request,identity:UUID,turn_id:UUID):
        return Response(voice.audio(owner(request),identity,turn_id,dsn=settings.dsn),media_type='audio/wav')
