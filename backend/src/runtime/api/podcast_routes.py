"""Podcast 管理與音訊的 HTTP 契約。"""

from __future__ import annotations

from uuid import UUID
from fastapi import Request
from starlette.concurrency import run_in_threadpool
from .. import podcasts
from ..storage.artifacts import open_verified_artifact
from . import request_context
from .media import artifact_response
from .models import (
    PodcastCreate,
    PodcastAction,
    PodcastSummary,
    PodcastView,
    PodcastListView,
    PodcastDeletedView,
)


def install(app, settings):
    @app.post(
        "/v1/materials/{material_id}/podcasts",
        status_code=201,
        response_model=PodcastSummary,
        tags=["podcasts"],
    )
    def create_podcast_route(request: Request, material_id: UUID, body: PodcastCreate):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return podcasts.create_podcast(
            owner,
            material_id,
            body.knowledge_structure_revision,
            body.name,
            body.concept_ids,
            request_context.idempotency_key(request),
            delivery=body.delivery,
            dsn=settings.dsn,
        )

    @app.get("/v1/podcasts", response_model=PodcastListView, tags=["podcasts"])
    def list_podcasts_route(request: Request):
        request_context.require_query(request, set())
        return podcasts.list_podcasts(
            request_context.trusted_learner(request, settings).learner_id,
            dsn=settings.dsn,
        )

    @app.get("/v1/podcasts/{podcast_id}", response_model=PodcastView, tags=["podcasts"])
    def read_podcast_route(request: Request, podcast_id: UUID):
        request_context.require_query(request, set())
        return podcasts.read_podcast(
            request_context.trusted_learner(request, settings).learner_id,
            podcast_id,
            dsn=settings.dsn,
        )

    @app.post(
        "/v1/podcasts/{podcast_id}/actions",
        response_model=PodcastSummary | PodcastDeletedView,
        tags=["podcasts"],
    )
    def podcast_action_route(request: Request, podcast_id: UUID, body: PodcastAction):
        request_context.require_query(request, set())
        return podcasts.change_podcast(
            request_context.trusted_learner(request, settings).learner_id,
            podcast_id,
            body.action,
            body.expected_version,
            body.name,
            dsn=settings.dsn,
        )

    @app.delete(
        "/v1/podcasts/{podcast_id}",
        response_model=PodcastDeletedView,
        tags=["podcasts"],
    )
    async def delete_podcast_route(request: Request, podcast_id: UUID):
        request_context.require_query(request, set())
        if await request.body():
            raise request_context.ApiFailure("REQUEST_INVALID")
        owner = (
            await run_in_threadpool(request_context.trusted_learner, request, settings)
        ).learner_id
        return await run_in_threadpool(
            podcasts.delete_podcast, owner, podcast_id, dsn=settings.dsn
        )

    @app.get(
        "/v1/podcasts/{podcast_id}/episodes/{episode_index}/audio", tags=["podcasts"]
    )
    def podcast_audio_route(request: Request, podcast_id: UUID, episode_index: int):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        view = podcasts.read_podcast(owner, podcast_id, dsn=settings.dsn)
        if view["status"] != "ready" or not 0 <= episode_index < len(view["episodes"]):
            raise request_context.ApiFailure("RESOURCE_NOT_FOUND")
        artifact_id = UUID(view["episodes"][episode_index]["audio"]["artifact_id"])
        return artifact_response(
            request,
            open_verified_artifact(owner, artifact_id, dsn=settings.dsn),
            "audio/wav",
        )
