"""概念卡管理、生成進度與閱讀的 HTTP 契約。"""

from __future__ import annotations

from uuid import UUID
from fastapi import Request, Query
from starlette.concurrency import run_in_threadpool
from .. import card_reviews, card_sets
from . import request_context
from .models import (
    CardSetCreate,
    CardSetUpdate,
    CardCountSave,
    CardStudyView,
    CardBatchView,
    CardSetSummary,
    CardSetView,
    CardSetListView,
    CardSetDeletedView,
    CardGenerationView,
    CardReviewsView,
    CardReviewSubmit,
    CardReviewView,
)


def install(app, settings):
    @app.post(
        "/v1/materials/{material_id}/card-sets",
        status_code=201,
        response_model=CardSetSummary,
        operation_id="createCardSet",
        tags=["cards"],
    )
    def create_card_set_route(request: Request, material_id: UUID, body: CardSetCreate):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return card_sets.create_card_set(
            owner,
            material_id,
            body.knowledge_structure_revision,
            body.name,
            body.concept_ids,
            request_context.idempotency_key(request),
            dsn=settings.dsn,
        )

    @app.get(
        "/v1/card-reviews",
        response_model=CardReviewsView,
        operation_id="readCardReviews",
        tags=["cards"],
    )
    def card_reviews_route(request: Request, card_set_id: UUID | None = None):
        request_context.require_query(request, {"card_set_id"})
        owner = request_context.trusted_learner(request, settings).learner_id
        return card_reviews.review_summary(owner, card_set_id=card_set_id, dsn=settings.dsn)

    @app.post(
        "/v1/card-sets/{card_set_id}/reviews",
        response_model=CardReviewView,
        operation_id="submitCardReview",
        tags=["cards"],
    )
    def submit_card_review_route(request: Request, card_set_id: UUID, body: CardReviewSubmit):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return card_reviews.submit_review(
            owner,
            card_set_id,
            body.concept_id,
            body.rating,
            body.expected_review_count,
            request_context.idempotency_key(request),
            dsn=settings.dsn,
        )

    @app.get(
        "/v1/card-sets",
        response_model=CardSetListView,
        operation_id="listCardSets",
        tags=["cards"],
    )
    def list_card_sets_route(request: Request):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return card_sets.list_card_sets(owner, dsn=settings.dsn)

    @app.get(
        "/v1/card-sets/{card_set_id}",
        response_model=CardSetView,
        operation_id="getCardSet",
        tags=["cards"],
    )
    def read_card_set_route(request: Request, card_set_id: UUID):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return card_sets.read_card_set(owner, card_set_id, dsn=settings.dsn)

    @app.post(
        "/v1/card-sets/{card_set_id}/update",
        response_model=CardSetSummary,
        operation_id="updateCardSet",
        tags=["cards"],
    )
    def update_card_set_route(request: Request, card_set_id: UUID, body: CardSetUpdate):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return card_sets.update_card_set(
            owner,
            card_set_id,
            body.name,
            body.expected_version,
            body.concept_ids,
            dsn=settings.dsn,
        )

    @app.post(
        "/v1/card-sets/{card_set_id}/count",
        response_model=CardSetSummary,
        operation_id="saveCardCount",
        tags=["cards"],
    )
    def save_card_count_route(request: Request, card_set_id: UUID, body: CardCountSave):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return card_sets.save_card_count(
            owner,
            card_set_id,
            body.card_count,
            body.expected_version,
            concept_counts=body.concept_counts,
            dsn=settings.dsn,
        )

    @app.get(
        "/v1/card-sets/{card_set_id}/study",
        response_model=CardStudyView,
        operation_id="getCardStudy",
        tags=["cards"],
    )
    def card_study_route(request: Request, card_set_id: UUID):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return card_sets.read_card_study(owner, card_set_id, dsn=settings.dsn)

    @app.get(
        "/v1/card-sets/{card_set_id}/cards",
        response_model=CardBatchView,
        operation_id="getCardBatch",
        tags=["cards"],
    )
    def card_batch_route(
        request: Request,
        card_set_id: UUID,
        offset: int = Query(ge=0),
        version: int = Query(ge=1),
    ):
        request_context.require_query(request, {"offset", "version"})
        owner = request_context.trusted_learner(request, settings).learner_id
        return card_sets.read_card_study(
            owner,
            card_set_id,
            offset=offset,
            expected_version=version,
            dsn=settings.dsn,
        )

    @app.get(
        "/v1/card-sets/{card_set_id}/generation",
        response_model=CardGenerationView,
        operation_id="getCardGeneration",
        tags=["cards"],
    )
    def card_generation_route(request: Request, card_set_id: UUID):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return card_sets.generation_status(owner, card_set_id, dsn=settings.dsn)

    @app.post(
        "/v1/card-sets/{card_set_id}/generation/retry",
        response_model=CardGenerationView,
        operation_id="retryCardGeneration",
        tags=["cards"],
    )
    def retry_card_generation_route(request: Request, card_set_id: UUID):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return card_sets.generation_status(
            owner, card_set_id, retry=True, dsn=settings.dsn
        )

    @app.delete(
        "/v1/card-sets/{card_set_id}",
        response_model=CardSetDeletedView,
        operation_id="deleteCardSet",
        tags=["cards"],
    )
    async def delete_card_set_route(request: Request, card_set_id: UUID):
        request_context.require_query(request, set())
        if await request.body():
            raise request_context.ApiFailure("REQUEST_INVALID")
        owner = (
            await run_in_threadpool(request_context.trusted_learner, request, settings)
        ).learner_id
        return await run_in_threadpool(
            card_sets.delete_card_set, owner, card_set_id, dsn=settings.dsn
        )
