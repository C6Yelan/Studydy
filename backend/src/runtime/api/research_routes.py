from copy import deepcopy
from typing import Literal
from uuid import UUID
from fastapi import Request
from pydantic import BaseModel,ConfigDict,Field
from .. import research

class Search(BaseModel):
    model_config=ConfigDict(extra='forbid')
    query:str=Field(min_length=1,max_length=1000)
    mode:Literal['review','self-study']
class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['more','acquire','cancel','retry']
    selected:list[str]|None=None
class Confirm(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmed:Literal[True]

def install(app,settings,trusted,key,query):
    def owner(request):query(request,set());return trusted(request,settings).learner_id
    @app.get('/v1/materials/{material_id}/research')
    def listing(request:Request,material_id:UUID):return research.listing(owner(request),material_id,dsn=settings.dsn)
    @app.post('/v1/materials/{material_id}/research',status_code=202)
    def create(request:Request,material_id:UUID,body:Search):return research.create(owner(request),material_id,body.query,body.mode,key(request),dsn=settings.dsn)
    @app.get('/v1/research/{identity}')
    def read(request:Request,identity:UUID):return research.read(owner(request),identity,dsn=settings.dsn)
    @app.post('/v1/research/{identity}/actions')
    def action(request:Request,identity:UUID,body:Action):return research.action(owner(request),identity,body.action,body.selected,dsn=settings.dsn)
    @app.post('/v1/research/{identity}/submit',status_code=202)
    def submit(request:Request,identity:UUID,body:Confirm):return research.submit(owner(request),identity,deepcopy(settings.local_config),dsn=settings.dsn)
