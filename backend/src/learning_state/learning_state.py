from __future__ import annotations

from datetime import datetime
from typing import Any

from knowledge_map.artifacts import validate_knowledge_map
from learning_state.assessment import (
    SCORING_RULE_VERSION,
    canonical_sha256,
    validate_answer_event,
    validate_assessment,
)
from learning_state.learning_events import normalize_learning_events


LEARNING_STATE_SCHEMA = "learning-state/v1"
INITIAL_PATH_SCHEMA = "initial-learning-path/v1"


class LearningStateError(ValueError):
    """提供穩定、可辨識的學習狀態失敗原因。"""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _canonical_revision(prefix: str, content: dict[str, Any]) -> str:
    digest = canonical_sha256(content)
    if digest is None:
        raise LearningStateError("LEARNING_INPUT_INVALID")
    return f"{prefix}:sha256:{digest}"


def validate_initial_learning_path(
    learning_path: Any, knowledge_map: Any
) -> str | None:
    """檢查 Initial Path identity、完整 Concept 順序與 prerequisite。"""
    fields = {
        "schema",
        "revision",
        "knowledge_map_revision",
        "material_ref",
        "ordered_concept_ids",
        "processing",
        "quality",
        "decision",
        "reason_code",
    }
    if (
        not isinstance(learning_path, dict)
        or set(learning_path) != fields
        or validate_knowledge_map(knowledge_map) is not None
    ):
        return "INITIAL_PATH_INVALID"
    if learning_path.get("reason_code") == "PREREQUISITE_CYCLE":
        return "PREREQUISITE_CYCLE"
    if (
        learning_path["schema"] != INITIAL_PATH_SCHEMA
        or learning_path["knowledge_map_revision"] != knowledge_map["revision"]
        or learning_path["material_ref"] != knowledge_map["material_ref"]
        or (
            learning_path["processing"],
            learning_path["quality"],
            learning_path["decision"],
        )
        not in {
            ("succeeded", "accepted", "retain"),
            ("succeeded", "needs_review", "review"),
        }
    ):
        return "INITIAL_PATH_INVALID"
    content = {key: value for key, value in learning_path.items() if key != "revision"}
    digest = canonical_sha256(content)
    if digest is None or learning_path[
        "revision"
    ] != f"initial-learning-path:sha256:{digest}":
        return "INITIAL_PATH_INVALID"
    ordered = learning_path["ordered_concept_ids"]
    concept_ids = {concept["concept_id"] for concept in knowledge_map["concepts"]}
    if (
        not isinstance(ordered, list)
        or any(not isinstance(concept_id, str) for concept_id in ordered)
        or len(ordered) != len(set(ordered))
        or set(ordered) != concept_ids
    ):
        return "INITIAL_PATH_INVALID"
    positions = {concept_id: index for index, concept_id in enumerate(ordered)}
    for relation in knowledge_map["relations"]:
        if relation["type"] == "prerequisite" and positions[
            relation["source_concept_id"]
        ] >= positions[relation["target_concept_id"]]:
            return "INITIAL_PATH_INVALID"
    return None


def _parse_event_datetime(event: dict[str, Any], field: str) -> datetime:
    return datetime.fromisoformat(event[field])


def _latest_answer_by_question(
    answer_events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest_answer_by_question: dict[str, dict[str, Any]] = {}
    for event in sorted(
        answer_events,
        key=lambda item: (_parse_event_datetime(item, "submitted_at"), item["answer_event_id"]),
    ):
        latest_answer_by_question[event["question_id"]] = event
    return latest_answer_by_question


def _mastery_band(score: float) -> str:
    if score >= 0.80:
        return "mastered"
    if score >= 0.50:
        return "learning"
    if score >= 0.30:
        return "review"
    return "weak"


def _completion_score(learning_events: list[dict[str, Any]]) -> float:
    learning_event_types = {event["event_type"] for event in learning_events}
    if "concept_completed" in learning_event_types:
        return 1.0
    if "concept_viewed" in learning_event_types:
        return 0.5
    return 0.0


def _practice_score(
    concept_id: str,
    learning_events: list[dict[str, Any]],
    latest_answer_by_question: dict[str, dict[str, Any]],
    assessment: dict[str, Any],
) -> float:
    learning_event_types = {event["event_type"] for event in learning_events}
    practice_question_sets = [
        item["question_ids"]
        for item in assessment["practice_sets"]
        if item["concept_id"] == concept_id
    ]
    has_fully_correct_practice_set = any(
        all(question_id in latest_answer_by_question and latest_answer_by_question[question_id]["score"] == 1 for question_id in question_ids)
        for question_ids in practice_question_sets
    )
    if "practice_completed" in learning_event_types and has_fully_correct_practice_set:
        return 1.0
    prescribed_question_ids = {
        question_id for question_ids in practice_question_sets for question_id in question_ids
    }
    if (
        learning_event_types.intersection({"practice_started", "practice_completed"})
        or prescribed_question_ids.intersection(latest_answer_by_question)
    ):
        return 0.5
    return 0.0


def _review_score(
    answer_events: list[dict[str, Any]], learning_events: list[dict[str, Any]]
) -> tuple[float, bool, bool]:
    review_events = [event for event in learning_events if event["event_type"] == "review_completed"]
    if not review_events:
        return 0.0, False, False
    ordered_answers = sorted(
        answer_events,
        key=lambda item: (_parse_event_datetime(item, "submitted_at"), item["answer_event_id"]),
    )
    has_post_review_wrong = False
    has_review_without_post_answer = False
    has_proven_improvement = False
    for review in review_events:
        review_at = _parse_event_datetime(review, "event_at")
        answers_before_review = [item for item in ordered_answers if _parse_event_datetime(item, "submitted_at") < review_at]
        answers_after_review = [item for item in ordered_answers if _parse_event_datetime(item, "submitted_at") > review_at]
        if not answers_after_review:
            has_review_without_post_answer = True
            continue
        first_answer_after_review = answers_after_review[0]
        has_post_review_wrong = has_post_review_wrong or first_answer_after_review["score"] == 0
        if answers_before_review and answers_before_review[-1]["score"] == 0 and first_answer_after_review["score"] == 1:
            has_proven_improvement = True
    return (
        1.0 if has_proven_improvement else 0.5,
        has_review_without_post_answer,
        has_post_review_wrong,
    )


def _recent_penalty(answer_events: list[dict[str, Any]]) -> float | None:
    if not answer_events:
        return None
    ordered = sorted(
        answer_events,
        key=lambda item: (_parse_event_datetime(item, "submitted_at"), item["answer_event_id"]),
    )
    if ordered[-1]["score"] == 0:
        return 1.0
    if any(event["score"] == 0 for event in ordered[:-1]):
        return 0.5
    return 0.0


def _build_mastery_item(
    concept_id: str,
    answer_events: list[dict[str, Any]],
    learning_events: list[dict[str, Any]],
    assessment: dict[str, Any],
) -> dict[str, Any]:
    latest_answer_by_question = _latest_answer_by_question(answer_events)
    valid_answer_count = len(latest_answer_by_question)
    completion_score = _completion_score(learning_events)
    practice_score = _practice_score(concept_id, learning_events, latest_answer_by_question, assessment)
    review_score, review_without_post, post_review_wrong = _review_score(answer_events, learning_events)
    recent_error_penalty = _recent_penalty(answer_events)
    reason_codes: list[str] = []
    needs_review = review_without_post
    if valid_answer_count == 0:
        final_status = "learning" if learning_events else "not_started"
        raw_band = None
        mastery_score = None
        correct_rate = None
        reason_codes.append(
            "NO_ANSWERS_WITH_ACTIVITY" if learning_events else "NO_LEARNING_ACTIVITY"
        )
    else:
        correct_rate = sum(event["score"] for event in latest_answer_by_question.values()) / valid_answer_count
        mastery_score = max(
            0.0,
            min(
                1.0,
                0.45 * correct_rate
                + 0.20 * practice_score
                + 0.15 * review_score
                + 0.10 * completion_score
                - 0.10 * recent_error_penalty,
            ),
        )
        raw_band = _mastery_band(mastery_score)
        final_status = raw_band
        if valid_answer_count < 3:
            reason_codes.append("INSUFFICIENT_DISTINCT_QUESTIONS")
            needs_review = True
            if final_status == "mastered":
                final_status = "learning"
        ordered_answers = sorted(
            answer_events,
            key=lambda item: (_parse_event_datetime(item, "submitted_at"), item["answer_event_id"]),
        )
        consecutive_wrong = (
            len(ordered_answers) >= 2
            and ordered_answers[-1]["score"] == 0
            and ordered_answers[-2]["score"] == 0
        )
        if consecutive_wrong:
            reason_codes.append("CONSECUTIVE_WRONG_ANSWERS")
        if post_review_wrong:
            reason_codes.append("POST_REVIEW_ANSWER_WRONG")
        if consecutive_wrong or post_review_wrong:
            final_status = "weak" if raw_band == "weak" else "review"
            needs_review = True
        elif recent_error_penalty == 1.0 and final_status == "mastered":
            final_status = "learning"
            reason_codes.append("LATEST_ANSWER_WRONG")
    if review_without_post:
        reason_codes.append("REVIEW_HAS_NO_POST_ANSWER")
    if not reason_codes:
        reason_codes.append("MASTERY_CALCULATED")
    return {
        "concept_id": concept_id,
        "valid_answer_count": valid_answer_count,
        "correct_rate": correct_rate,
        "practice_score": practice_score,
        "review_score": review_score,
        "completion_score": completion_score,
        "recent_error_penalty": recent_error_penalty,
        "mastery_score": mastery_score,
        "raw_band": raw_band,
        "final_status": final_status,
        "needs_review": needs_review,
        "source_answer_event_ids": sorted(event["answer_event_id"] for event in answer_events),
        "source_learning_event_ids": sorted(event["learning_event_id"] for event in learning_events),
        "reason_codes": reason_codes,
    }


def _build_weaknesses(mastery: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weaknesses = []
    for item in mastery:
        forced = {
            "CONSECUTIVE_WRONG_ANSWERS",
            "POST_REVIEW_ANSWER_WRONG",
        }.intersection(item["reason_codes"])
        if item["final_status"] != "weak" and not forced:
            continue
        weaknesses.append(
            {
                "concept_id": item["concept_id"],
                "kind": "remediation_required" if forced else "weak_mastery",
                "reason_codes": sorted(forced) or ["MASTERY_SCORE_WEAK"],
                "source_answer_event_ids": item["source_answer_event_ids"],
                "source_learning_event_ids": item["source_learning_event_ids"],
            }
        )
    return weaknesses


def _collect_prerequisite_ids_by_concept(knowledge_map: dict[str, Any]) -> dict[str, set[str]]:
    prerequisite_ids_by_concept = {concept["concept_id"]: set() for concept in knowledge_map["concepts"]}
    for relation in knowledge_map["relations"]:
        if relation["type"] == "prerequisite":
            prerequisite_ids_by_concept[relation["target_concept_id"]].add(relation["source_concept_id"])
    return prerequisite_ids_by_concept


def _build_learning_suggestion(
    mastery: list[dict[str, Any]],
    weaknesses: list[dict[str, Any]],
    learning_path: dict[str, Any],
    knowledge_map: dict[str, Any],
) -> dict[str, Any]:
    mastery_by_concept_id = {item["concept_id"]: item for item in mastery}
    weak_concept_ids = {item["concept_id"] for item in weaknesses}
    first_unmastered_concept_id = next(
        (
            concept_id
            for concept_id in learning_path["ordered_concept_ids"]
            if mastery_by_concept_id[concept_id]["final_status"] != "mastered"
        ),
        None,
    )
    source_answer_event_ids = sorted(
        event_id for item in mastery for event_id in item["source_answer_event_ids"]
    )
    source_learning_event_ids = sorted(
        event_id for item in mastery for event_id in item["source_learning_event_ids"]
    )
    if first_unmastered_concept_id is None:
        return {
            "is_personalized": False,
            "action": "no_action",
            "target_concept_id": None,
            "mastery_data_score": None,
            "weakness_score": None,
            "path_alignment_score": None,
            "prerequisite_score": None,
            "action_clarity_score": None,
            "learning_suggestion_score": None,
            "level": "no_action",
            "fallback_action": None,
            "fallback_target_concept_id": None,
            "needs_review": False,
            "decision": "retain",
            "reason_code": "ALL_CONCEPTS_MASTERED",
            "source_answer_event_ids": source_answer_event_ids,
            "source_learning_event_ids": source_learning_event_ids,
        }

    target_concept_id = first_unmastered_concept_id
    target_mastery = mastery_by_concept_id[target_concept_id]
    mastery_data_score = 1.0 if target_mastery["valid_answer_count"] >= 3 else (
        0.5 if target_mastery["valid_answer_count"] else 0.0
    )
    if target_concept_id in weak_concept_ids:
        weakness_score = 1.0
    elif target_mastery["final_status"] == "review" or (
        target_mastery["recent_error_penalty"] is not None
        and target_mastery["recent_error_penalty"] > 0
    ):
        weakness_score = 0.5
    else:
        weakness_score = 0.0
    target_prerequisite_ids = _collect_prerequisite_ids_by_concept(knowledge_map)[target_concept_id]
    if any(
        mastery_by_concept_id[concept_id]["final_status"] != "mastered"
        for concept_id in target_prerequisite_ids
    ):
        raise LearningStateError("LEARNING_INPUT_INVALID")
    prerequisite_score = 1.0
    has_forced_weakness = bool(
        {
            "CONSECUTIVE_WRONG_ANSWERS",
            "POST_REVIEW_ANSWER_WRONG",
        }.intersection(target_mastery["reason_codes"])
    )
    if target_mastery["final_status"] in {"weak", "review"}:
        action = "review_concept"
        action_reason_code = "REVIEW_CURRENT_CONCEPT"
    elif target_mastery["final_status"] == "learning":
        action = "practice_concept"
        action_reason_code = "CONTINUE_CURRENT_CONCEPT"
    else:
        action = "start_concept"
        action_reason_code = "START_NEXT_CONCEPT"
    path_alignment_score = 1.0
    action_clarity_score = 1.0
    learning_suggestion_score = (
        0.30 * mastery_data_score
        + 0.25 * weakness_score
        + 0.20 * path_alignment_score
        + 0.15 * prerequisite_score
        + 0.10 * action_clarity_score
    )
    has_insufficient_answers = mastery_data_score == 0 or target_mastery["valid_answer_count"] < 3
    suggestion_level = "high" if learning_suggestion_score >= 0.75 else "medium" if learning_suggestion_score >= 0.50 else "low"
    if has_insufficient_answers:
        is_personalized = False
        needs_review = target_mastery["valid_answer_count"] > 0
        decision = "review" if needs_review else "retain"
        reason_code = "INSUFFICIENT_DATA_USE_INITIAL_PATH"
    elif has_forced_weakness:
        is_personalized = False
        needs_review = True
        decision = "review"
        reason_code = "SUGGESTION_NEEDS_REVIEW"
    elif learning_suggestion_score >= 0.75:
        is_personalized = True
        needs_review = False
        decision = "retain"
        reason_code = action_reason_code
    elif learning_suggestion_score >= 0.50:
        is_personalized = False
        needs_review = True
        decision = "review"
        reason_code = "SUGGESTION_NEEDS_REVIEW"
    else:
        is_personalized = False
        needs_review = False
        decision = "reject"
        reason_code = "LOW_SCORE_USE_INITIAL_PATH"
    return {
        "is_personalized": is_personalized,
        "action": action if is_personalized else "follow_initial_path",
        "target_concept_id": target_concept_id,
        "mastery_data_score": mastery_data_score,
        "weakness_score": weakness_score,
        "path_alignment_score": path_alignment_score,
        "prerequisite_score": prerequisite_score,
        "action_clarity_score": action_clarity_score,
        "learning_suggestion_score": learning_suggestion_score,
        "level": suggestion_level,
        "fallback_action": "follow_initial_path",
        "fallback_target_concept_id": target_concept_id,
        "needs_review": needs_review,
        "decision": decision,
        "reason_code": reason_code,
        "source_answer_event_ids": source_answer_event_ids,
        "source_learning_event_ids": source_learning_event_ids,
    }


def build_learning_state(
    *,
    trusted_learner_id: Any,
    knowledge_map: Any,
    learning_path: Any,
    assessment: Any,
    answer_events: Any,
    learning_event_stream: Any,
) -> dict[str, Any]:
    """由已驗證事件建立 canonical Mastery、Weakness 與單一 Suggestion。"""
    if not isinstance(trusted_learner_id, str) or not trusted_learner_id.strip():
        raise LearningStateError("LEARNING_INPUT_INVALID")
    if validate_knowledge_map(knowledge_map) is not None:
        raise LearningStateError("LEARNING_INPUT_INVALID")
    path_reason_code = validate_initial_learning_path(learning_path, knowledge_map)
    if path_reason_code is not None:
        raise LearningStateError(path_reason_code)
    if validate_assessment(assessment, knowledge_map, learning_path["revision"]) is not None:
        raise LearningStateError("ASSESSMENT_INVALID")
    if not isinstance(answer_events, list) or any(
        validate_answer_event(event, assessment, trusted_learner_id) is not None
        for event in answer_events
    ):
        raise LearningStateError("ANSWER_EVENT_INVALID")
    answer_event_ids = [event["answer_event_id"] for event in answer_events]
    if len(answer_event_ids) != len(set(answer_event_ids)):
        raise LearningStateError("ANSWER_EVENT_INVALID")
    learning_events, learning_event_reason_code = normalize_learning_events(
        learning_event_stream,
        trusted_learner_id=trusted_learner_id,
        knowledge_map=knowledge_map,
        learning_path_revision=learning_path["revision"],
    )
    if learning_event_reason_code is not None:
        raise LearningStateError(learning_event_reason_code)

    mastery = []
    for concept_id in learning_path["ordered_concept_ids"]:
        concept_answer_events = [
            event for event in answer_events if event["concept_id"] == concept_id
        ]
        concept_learning_events = [
            event for event in learning_events if event["concept_id"] == concept_id
        ]
        mastery.append(
            _build_mastery_item(concept_id, concept_answer_events, concept_learning_events, assessment)
        )
    weaknesses = _build_weaknesses(mastery)
    suggestion = _build_learning_suggestion(mastery, weaknesses, learning_path, knowledge_map)
    needs_review = (
        any(item["needs_review"] for item in mastery)
        or any(item["valid_answer_count"] < 3 for item in mastery)
        or suggestion["needs_review"]
    )
    status = (
        {
            "processing": "partial",
            "quality": "needs_review",
            "decision": "review",
            "reason_code": "LEARNING_STATE_NEEDS_REVIEW",
        }
        if needs_review
        else {
            "processing": "succeeded",
            "quality": "accepted",
            "decision": "retain",
            "reason_code": "LEARNING_STATE_ACCEPTED",
        }
    )
    content = {
        "schema": LEARNING_STATE_SCHEMA,
        "learner_id": trusted_learner_id,
        "knowledge_map_revision": knowledge_map["revision"],
        "learning_path_revision": learning_path["revision"],
        "assessment_id": assessment["assessment_id"],
        "assessment_revision": assessment["revision"],
        "scoring_rule_version": SCORING_RULE_VERSION,
        "source_answer_event_ids": sorted(answer_event_ids),
        "source_learning_event_ids": sorted(
            event["learning_event_id"] for event in learning_events
        ),
        "mastery": mastery,
        "weaknesses": weaknesses,
        "suggestion": suggestion,
        **status,
    }
    return {"revision": _canonical_revision("learning-state", content), **content}
