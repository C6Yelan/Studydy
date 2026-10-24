from typing import Literal
from uuid import UUID
from fastapi import Request
from pydantic import Field
from .models import _Closed, MaterialNotesView
from . import request_context
from .. import material_notes

Kind = Literal["summary", "full"]


class Generate(_Closed):
    expected_generation: int = Field(ge=0)


class Annotation(_Closed):
    expected_generation: int = Field(ge=0)
    paragraph_id: str | None = None
    body: str = Field(max_length=10000)


def install(app, settings):
    def owner(request):
        request_context.require_query(request, set())
        return request_context.trusted_learner(request, settings).learner_id

    path = "/v1/materials/{material_id}/knowledge-structures/{revision}/notes/{kind}"

    @app.get(path, response_model=MaterialNotesView, tags=["notes"])
    def read(request: Request, material_id: UUID, revision: str, kind: Kind):
        return material_notes.read(owner(request), material_id, revision, kind, dsn=settings.dsn)

    @app.post(path, status_code=202, response_model=MaterialNotesView, tags=["notes"])
    def generate(request: Request, material_id: UUID, revision: str, kind: Kind, body: Generate):
        return material_notes.generate(owner(request), material_id, revision, kind,
            body.expected_generation, dsn=settings.dsn)

    @app.put(path + "/annotations/{annotation_id}", response_model=MaterialNotesView, tags=["notes"])
    def annotate(request: Request, material_id: UUID, revision: str, kind: Kind,
                 annotation_id: UUID, body: Annotation):
        return material_notes.annotate(owner(request), material_id, revision, kind,
            annotation_id, body.body, body.paragraph_id, body.expected_generation, dsn=settings.dsn)
