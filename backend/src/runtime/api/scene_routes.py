from typing import Literal
from uuid import UUID
from fastapi import Request
from pydantic import BaseModel,ConfigDict,Field
from .. import podcast_scenes

class Prepare(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['retry','cancel']
    expected_version:int=Field(ge=1)

def install(app,settings,trusted,query):
    def owner(request):query(request,set());return trusted(request,settings).learner_id
    @app.get('/v1/podcasts/{identity}/scenes')
    def read(request:Request,identity:UUID):return podcast_scenes.read(owner(request),identity,dsn=settings.dsn)
    @app.post('/v1/podcasts/{identity}/scenes',status_code=202)
    def prepare(request:Request,identity:UUID,body:Prepare):return podcast_scenes.prepare(owner(request),identity,dsn=settings.dsn)
    @app.post('/v1/podcasts/{identity}/scenes/{index}/actions')
    def action(request:Request,identity:UUID,index:int,body:Action):return podcast_scenes.action(owner(request),identity,index,body.action,body.expected_version,dsn=settings.dsn)
