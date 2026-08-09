from __future__ import annotations

from datetime import datetime
from typing import Any

from learning_state.assessment import canonical_sha256
from knowledge_map.artifacts import validate_knowledge_map


LEARNING_EVENT_SCHEMA = "learning-event/v1"
LEARNING_EVENT_TYPES = frozenset(
    {
        "concept_completed",
        "concept_viewed",
        "practice_completed",
        "practice_started",
        "review_completed",
    }
)
ACCEPTED_LEARNING_EVENT_STATUS = (
    "succeeded",
    "accepted",
    "retain",
    "LEARNING_EVENT_ACCEPTED",
)


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_timezone_aware_iso_datetime(value: Any) -> bool:
    if not _is_nonempty_string(value):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_learning_event(
    event: Any,
    *,
    trusted_learner_id: Any,
    knowledge_map: Any,
    learning_path_revision: Any,
) -> str | None:
    """驗證完成、練習與複習事件的 trusted bindings。"""
    fields = {
        "schema",
        "learning_event_id",
        "learner_id",
        "concept_id",
        "event_type",
        "event_at",
        "idempotency_key",
        "knowledge_map_revision",
        "learning_path_revision",
        "processing",
        "quality",
        "decision",
        "reason_code",
    }
    if (
        not isinstance(event, dict)
        or set(event) != fields
        or not _is_nonempty_string(trusted_learner_id)
        or validate_knowledge_map(knowledge_map) is not None
    ):
        return "LEARNING_EVENT_INVALID"
    concept_ids = {concept["concept_id"] for concept in knowledge_map["concepts"]}
    if (
        event["schema"] != LEARNING_EVENT_SCHEMA
        or event["learner_id"] != trusted_learner_id
        or not _is_nonempty_string(event["concept_id"])
        or event["concept_id"] not in concept_ids
        or event["event_type"] not in LEARNING_EVENT_TYPES
        or not _is_timezone_aware_iso_datetime(event["event_at"])
        or not _is_nonempty_string(event["idempotency_key"])
        or event["knowledge_map_revision"] != knowledge_map["revision"]
        or event["learning_path_revision"] != learning_path_revision
        or (
            event["processing"],
            event["quality"],
            event["decision"],
            event["reason_code"],
        )
        != ACCEPTED_LEARNING_EVENT_STATUS
    ):
        return "LEARNING_EVENT_INVALID"
    content = {key: value for key, value in event.items() if key != "learning_event_id"}
    digest = canonical_sha256(content)
    if digest is None or event["learning_event_id"] != f"learning-event:sha256:{digest}":
        return "LEARNING_EVENT_INVALID"
    return None


def normalize_learning_events(
    event_stream: Any,
    *,
    trusted_learner_id: Any,
    knowledge_map: Any,
    learning_path_revision: Any,
) -> tuple[list[dict[str, Any]], str | None]:
    """保留完整有效 stream；相同 idempotency replay 只計一次。"""
    if event_stream is None:
        return [], "LEARNING_EVENT_STREAM_MISSING"
    if not isinstance(event_stream, list):
        return [], "LEARNING_EVENT_INVALID"

    event_by_idempotency_key: dict[str, dict[str, Any]] = {}
    for event in event_stream:
        if (
            validate_learning_event(
                event,
                trusted_learner_id=trusted_learner_id,
                knowledge_map=knowledge_map,
                learning_path_revision=learning_path_revision,
            )
            is not None
        ):
            return [], "LEARNING_EVENT_INVALID"
        existing_event = event_by_idempotency_key.get(event["idempotency_key"])
        if existing_event is not None and existing_event != event:
            return [], "LEARNING_EVENT_INVALID"
        event_by_idempotency_key[event["idempotency_key"]] = event

    normalized_events = sorted(
        event_by_idempotency_key.values(),
        key=lambda item: (item["event_at"], item["learning_event_id"]),
    )
    return normalized_events, None
