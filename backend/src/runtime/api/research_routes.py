from . import request_context
from copy import deepcopy
from typing import Literal
from uuid import UUID
from fastapi import Request
from pydantic import BaseModel, ConfigDict
from .. import research


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["acquire", "cancel", "retry"]
    selected: list[str] | None = None


class Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]


def install(app, settings):
    def owner(request):
        request_context.require_query(request, set())
        return request_context.trusted_learner(request, settings).learner_id

    @app.get("/v1/materials/{material_id}/research")
    def listing(request: Request, material_id: UUID):
        return research.listing(owner(request), material_id, dsn=settings.dsn)

    @app.get("/v1/research/{identity}")
    def read(request: Request, identity: UUID):
        return research.read(owner(request), identity, dsn=settings.dsn)

    @app.delete("/v1/research/{identity}")
    def delete(request: Request, identity: UUID):
        return research.delete(owner(request), identity, dsn=settings.dsn)

    @app.post("/v1/research/{identity}/actions")
    def action(request: Request, identity: UUID, body: Action):
        return research.action(
            owner(request),
            identity,
            body.action,
            body.selected,
            dsn=settings.dsn,
        )

    @app.post("/v1/research/{identity}/submit", status_code=202)
    def submit(request: Request, identity: UUID, body: Confirm):
        return research.submit(
            owner(request), identity, deepcopy(settings.local_config), dsn=settings.dsn
        )
