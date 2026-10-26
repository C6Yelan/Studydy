from . import request_context
import json
from typing import Literal
from uuid import UUID
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from .. import voice, assessment_presence
from ..errors import SourceError
from .media import bytes_response


class PodcastReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_index: int = Field(ge=0, strict=True)
    turn_index: int = Field(ge=0, strict=True)
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)


class PodcastContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    podcast_id: UUID
    episode_index: int = Field(ge=0, strict=True)
    script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_refs: list[PodcastReference] = Field(min_length=1, max_length=24)


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_structure_revision: str | None = Field(
        default=None, pattern=r"^knowledge-structure:sha256:[0-9a-f]{64}$"
    )


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    mode: Literal["text", "voice"] = "text"
    context: PodcastContext | None = None


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["cancel", "retry"]


class Presence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_: Literal["assessment-presence/v1"] = Field(alias="schema")
    assessment_set_id: UUID
    active: bool = Field(strict=True)


def browser_id(request):
    try:
        return UUID(request.headers.get("X-Studydy-Browser-Id", ""))
    except ValueError:
        raise SourceError("REQUEST_INVALID") from None


def install(app, settings):
    def owner(request):
        request_context.require_query(request, set())
        return request_context.trusted_learner(request, settings).learner_id

    @app.put("/v1/materials/{material_id}/assessment-presence")
    def presence(request: Request, material_id: UUID, body: Presence):
        return assessment_presence.update(
            owner(request), material_id, browser_id(request), body.assessment_set_id,
            body.active, dsn=settings.dsn,
        )

    @app.get("/v1/materials/{material_id}/voice-conversations")
    def listing(request: Request, material_id: UUID):
        return voice.listing(owner(request), material_id, dsn=settings.dsn)

    @app.post("/v1/materials/{material_id}/voice-conversations", status_code=201)
    def create(request: Request, material_id: UUID, body: Create | None = None):
        return voice.create(
            owner(request),
            material_id,
            request_context.idempotency_key(request),
            revision=body.knowledge_structure_revision if body else None,
            dsn=settings.dsn,
        )

    @app.get("/v1/voice-conversations/{identity}")
    def read(request: Request, identity: UUID):
        return voice.read(owner(request), identity, dsn=settings.dsn)

    @app.delete("/v1/voice-conversations/{identity}")
    def remove(request: Request, identity: UUID):
        return voice.remove(owner(request), identity, dsn=settings.dsn)

    @app.post("/v1/voice-conversations/{identity}/turns", status_code=202)
    def question(request: Request, identity: UUID, body: Question):
        learner = owner(request)
        voice.require_question_allowed(learner, identity, browser_id(request), dsn=settings.dsn)
        return voice.add_turn(
            learner,
            identity,
            request_context.idempotency_key(request),
            body.question,
            mode=body.mode,
            context=body.context.model_dump(mode="json") if body.context else None,
            dsn=settings.dsn,
        )

    @app.post("/v1/voice-conversations/{identity}/recordings", status_code=202)
    async def recording(request: Request, identity: UUID):
        learner = await run_in_threadpool(owner, request)
        browser = browser_id(request)
        await run_in_threadpool(voice.require_question_allowed, learner, identity, browser, dsn=settings.dsn)
        request_key = request_context.idempotency_key(request)
        context = None
        raw_context = request.headers.get("X-Studydy-Podcast-Context")
        if raw_context:
            if len(raw_context) > 8192:
                raise SourceError("REQUEST_INVALID")
            try:
                context = PodcastContext.model_validate(
                    json.loads(raw_context)
                ).model_dump(mode="json")
            except ValueError:
                raise SourceError("REQUEST_INVALID") from None
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 12 * 1024 * 1024:
                raise SourceError("MATERIAL_TOO_LARGE")
            data.extend(chunk)
        if not data:
            raise SourceError("REQUEST_INVALID")
        return await run_in_threadpool(
            voice.add_turn,
            learner,
            identity,
            request_key,
            recording=bytes(data),
            context=context,
            dsn=settings.dsn,
        )

    @app.post("/v1/voice-conversations/{identity}/turns/{turn_id}/actions")
    def action(request: Request, identity: UUID, turn_id: UUID, body: Action):
        learner = owner(request)
        if body.action == "retry":
            voice.require_question_allowed(learner, identity, browser_id(request), dsn=settings.dsn)
        return voice.action(
            learner, identity, turn_id, body.action, dsn=settings.dsn
        )

    @app.get("/v1/voice-conversations/{identity}/turns/{turn_id}/audio")
    def audio(request: Request, identity: UUID, turn_id: UUID):
        return bytes_response(
            request,
            voice.audio(owner(request), identity, turn_id, dsn=settings.dsn),
            "audio/wav",
        )
