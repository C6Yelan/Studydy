from . import request_context
from typing import Literal
from uuid import UUID
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from .. import podcast_scenes
from .. import podcasts
from .. import podcast_videos
from ..podcast_timeline import build_timeline, webvtt, with_source_ranges


def load_timeline(learner, identity, index, *, dsn=None):
    video = podcast_videos.ready_manifest(
        learner, identity, index, dsn=dsn
    )
    if video is not None:
        podcast = podcasts.read_podcast(learner, identity, dsn=dsn)
        try:
            return with_source_ranges(video["timeline"], podcast["episodes"][index])
        except (ValueError, IndexError):
            raise request_context.ApiFailure("PODCAST_TIMELINE_SOURCE_MISMATCH") from None
    podcast = podcasts.read_podcast(learner, identity, dsn=dsn)
    if not 0 <= index < len(podcast["episodes"]):
        raise HTTPException(404)
    scene = podcast_scenes.read(learner, identity, dsn=dsn)["episodes"][
        index
    ]
    if scene["status"] != "ready":
        # 音訊完成後仍要等場景對齊寫入；這段期間是可重試的處理中狀態。
        raise request_context.ApiFailure("PODCAST_TIMELINE_NOT_READY")
    try:
        episode = podcast["episodes"][index]
        return with_source_ranges(
            build_timeline(identity, index, episode, scene["manifest"]), episode
        )
    except ValueError:
        raise request_context.ApiFailure("PODCAST_TIMELINE_SOURCE_MISMATCH") from None


class Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["retry", "cancel"]
    expected_version: int = Field(ge=1)


def install(app, settings):
    def owner(request):
        request_context.require_query(request, set())
        return request_context.trusted_learner(request, settings).learner_id

    def timeline(request, identity, index):
        return load_timeline(owner(request), identity, index, dsn=settings.dsn)

    @app.get("/v1/podcasts/{identity}/episodes/{index}/timeline")
    def read_timeline(request: Request, identity: UUID, index: int):
        return timeline(request, identity, index)

    @app.get("/v1/podcasts/{identity}/episodes/{index}/subtitles")
    def subtitles(request: Request, identity: UUID, index: int):
        return Response(
            webvtt(timeline(request, identity, index)), media_type="text/vtt"
        )

    @app.get("/v1/podcasts/{identity}/scenes")
    def read(request: Request, identity: UUID):
        return podcast_scenes.read(owner(request), identity, dsn=settings.dsn)

    @app.post("/v1/podcasts/{identity}/scenes", status_code=202)
    def prepare(request: Request, identity: UUID, body: Prepare):
        return podcast_scenes.prepare(owner(request), identity, dsn=settings.dsn)

    @app.post("/v1/podcasts/{identity}/scenes/{index}/actions")
    def action(request: Request, identity: UUID, index: int, body: Action):
        return podcast_scenes.action(
            owner(request),
            identity,
            index,
            body.action,
            body.expected_version,
            dsn=settings.dsn,
        )
