from typing import Literal
from uuid import UUID
from fastapi import Request
from fastapi import Response
from pydantic import Field, StrictInt, StrictStr, model_validator
from .models import _Closed, MaterialNotesView, NoteHighlightListView, NoteHighlightView
from . import request_context
from .. import material_notes, note_highlights

Kind = Literal["summary", "full"]


class Generate(_Closed):
    expected_generation: int = Field(ge=0)


class Annotation(_Closed):
    expected_generation: int = Field(ge=0)
    paragraph_id: str | None = None
    body: str = Field(max_length=10000)
    block_path: list[StrictStr | StrictInt] | None = Field(default=None, max_length=12)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=1)
    quote: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def anchor_complete(self):
        if len({v is None for v in (self.block_path, self.start, self.end, self.quote)}) != 1:
            raise ValueError("annotation anchor requires block_path, start, end and quote together")
        return self


class Highlight(_Closed):
    expected_generation: int = Field(ge=0)
    paragraph_id: str
    block_path: list[StrictStr | StrictInt] = Field(max_length=12)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)
    style: Literal["yellow", "green", "blue", "pink", "bold", "underline", "strikethrough"]


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
            annotation_id, body.body, body.paragraph_id, body.expected_generation,
            selection=None if body.quote is None else (body.block_path, body.start, body.end, body.quote),
            dsn=settings.dsn)

    @app.get(path + "/highlights", response_model=NoteHighlightListView, tags=["notes"])
    def highlights(request: Request, material_id: UUID, revision: str, kind: Kind):
        return {"highlights": note_highlights.listing(owner(request), material_id, revision, kind, dsn=settings.dsn)}

    @app.post(path + "/highlights", status_code=201, response_model=NoteHighlightView, tags=["notes"])
    def add_highlight(request: Request, material_id: UUID, revision: str, kind: Kind, body: Highlight):
        return note_highlights.add(owner(request), material_id, revision, kind, body.expected_generation,
            body.paragraph_id, body.block_path, body.start, body.end, body.quote, body.style, dsn=settings.dsn)

    @app.delete(path + "/highlights/{highlight_id}", status_code=204, tags=["notes"])
    def delete_highlight(request: Request, material_id: UUID, revision: str, kind: Kind, highlight_id: UUID):
        note_highlights.remove(owner(request), material_id, kind, highlight_id, dsn=settings.dsn)
        return Response(status_code=204)
