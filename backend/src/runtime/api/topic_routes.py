from typing import Literal
from uuid import UUID
from fastapi import Request
from pydantic import BaseModel,ConfigDict,Field
from .. import topics

class Create(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request:str=Field(min_length=1,max_length=1000)
class Approve(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version:int=Field(ge=1)
    proposal:dict
class Sources(BaseModel):
    model_config=ConfigDict(extra='forbid')
    selected:list[str]=Field(min_length=1)
class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['retry','cancel']
    expected_version:int=Field(ge=1)

def install(app,settings,trusted,key,query):
    def owner(request):query(request,set());return trusted(request,settings).learner_id
    @app.get('/v1/topics')
    def listing(request:Request):return topics.listing(owner(request),dsn=settings.dsn)
    @app.post('/v1/topics',status_code=202)
    def create(request:Request,body:Create):return topics.create(owner(request),body.request,key(request),dsn=settings.dsn)
    @app.get('/v1/topics/{identity}')
    def read(request:Request,identity:UUID):return topics.read(owner(request),identity,dsn=settings.dsn)
    @app.post('/v1/topics/{identity}/approve')
    def approve(request:Request,identity:UUID,body:Approve):return topics.approve(owner(request),identity,body.expected_version,body.proposal,key(request),dsn=settings.dsn)
    @app.post('/v1/topics/{identity}/material',status_code=202)
    def material(request:Request,identity:UUID,body:Sources):return topics.create_material(owner(request),identity,body.selected,dsn=settings.dsn)
    @app.post('/v1/topics/{identity}/actions')
    def action(request:Request,identity:UUID,body:Action):return topics.action(owner(request),identity,body.action,body.expected_version,dsn=settings.dsn)
