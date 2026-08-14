from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from typing import Any

from knowledge_map.artifacts import validate_knowledge_map


ASSESSMENT_SCHEMA = "assessment/v1"
SUBMISSION_SCHEMA = "assessment-submission/v1"
ANSWER_EVENT_SCHEMA = "answer-event/v1"
SCORING_RULE_VERSION = "single-choice-exact/v1"
ACCEPTED_ASSESSMENT_STATUS = (
    "succeeded",
    "accepted",
    "retain",
    "ASSESSMENT_ACCEPTED",
)
ACCEPTED_ANSWER_STATUS = (
    "succeeded",
    "accepted",
    "retain",
    "ANSWER_SCORED",
)


def build_evidence_grounded_assessment(
    knowledge_map: Any,
    learning_path_revision: Any,
) -> dict[str, Any]:
    """依每個 concept 的 definition 與 Evidence 建立一題 deterministic 題目。"""

    if (
        validate_knowledge_map(knowledge_map) is not None
        or not _is_nonempty_string(learning_path_revision)
    ):
        raise ValueError("ASSESSMENT_BUILD_INVALID")
    questions = []
    practice_sets = []
    for concept in knowledge_map["concepts"]:
        member = concept["members"][0]
        definition = member["definition"]
        evidence_ids = sorted(set(member["evidence_ids"]))
        if not _is_nonempty_string(definition) or not evidence_ids:
            raise ValueError("ASSESSMENT_BUILD_INVALID")
        identity = canonical_sha256(
            {
                "concept_id": concept["concept_id"],
                "definition": definition,
                "evidence_ids": evidence_ids,
            }
        )
        if identity is None:
            raise ValueError("ASSESSMENT_BUILD_INVALID")
        question_id = f"question:sha256:{identity}"
        correct_id = f"option:sha256:{canonical_sha256([question_id, 'correct'])}"
        absent_id = f"option:sha256:{canonical_sha256([question_id, 'absent'])}"
        questions.append(
            {
                "question_id": question_id,
                "concept_id": concept["concept_id"],
                "question_type": "single_choice",
                "prompt": "哪一項敘述由教材 Evidence 直接支持？",
                "options": [
                    {"option_id": absent_id, "text": "教材沒有提供此概念的 Evidence。"},
                    {"option_id": correct_id, "text": definition},
                ],
                "answer_key_option_id": correct_id,
                "source_evidence_ids": evidence_ids,
            }
        )
        practice_sets.append(
            {
                "practice_set_id": f"practice-set:sha256:{identity}",
                "concept_id": concept["concept_id"],
                "question_ids": [question_id],
            }
        )
    questions.sort(key=lambda item: item["question_id"])
    practice_sets.sort(key=lambda item: item["practice_set_id"])
    assessment_id_digest = canonical_sha256(
        {
            "knowledge_map_revision": knowledge_map["revision"],
            "learning_path_revision": learning_path_revision,
        }
    )
    if assessment_id_digest is None:
        raise ValueError("ASSESSMENT_BUILD_INVALID")
    content = {
        "schema": ASSESSMENT_SCHEMA,
        "assessment_id": f"assessment:sha256:{assessment_id_digest}",
        "version": "1",
        "knowledge_map_revision": knowledge_map["revision"],
        "learning_path_revision": learning_path_revision,
        "scoring_rule_version": SCORING_RULE_VERSION,
        "questions": questions,
        "practice_sets": practice_sets,
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "ASSESSMENT_ACCEPTED",
    }
    digest = canonical_sha256(content)
    if digest is None:
        raise ValueError("ASSESSMENT_BUILD_INVALID")
    assessment = {**content, "revision": f"assessment:sha256:{digest}"}
    if validate_assessment(assessment, knowledge_map, learning_path_revision) is not None:
        raise ValueError("ASSESSMENT_BUILD_INVALID")
    return assessment


def canonical_sha256(value: Any) -> str | None:
    """用固定 JSON 表示計算可重現 SHA-256。"""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


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


def _artifact_status_values(item: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (
        item.get("processing"),
        item.get("quality"),
        item.get("decision"),
        item.get("reason_code"),
    )


def _evidence_ids_by_concept(knowledge_map: dict[str, Any]) -> dict[str, set[str]]:
    return {
        concept["concept_id"]: {
            evidence_id
            for member in concept["members"]
            for evidence_id in member["evidence_ids"]
        }
        for concept in knowledge_map["concepts"]
    }


def validate_assessment(
    assessment: Any,
    knowledge_map: Any,
    learning_path_revision: Any,
) -> str | None:
    """驗證 single-choice 題目、Evidence、practice set 與 exact revisions。"""
    root_fields = {
        "schema",
        "assessment_id",
        "version",
        "revision",
        "knowledge_map_revision",
        "learning_path_revision",
        "scoring_rule_version",
        "questions",
        "practice_sets",
        "processing",
        "quality",
        "decision",
        "reason_code",
    }
    if not isinstance(assessment, dict) or set(assessment) != root_fields:
        return "ASSESSMENT_INVALID"
    if (
        validate_knowledge_map(knowledge_map) is not None
        or assessment["schema"] != ASSESSMENT_SCHEMA
        or not _is_nonempty_string(assessment["assessment_id"])
        or not _is_nonempty_string(assessment["version"])
        or assessment["knowledge_map_revision"] != knowledge_map["revision"]
        or assessment["learning_path_revision"] != learning_path_revision
        or assessment["scoring_rule_version"] != SCORING_RULE_VERSION
        or _artifact_status_values(assessment) != ACCEPTED_ASSESSMENT_STATUS
    ):
        return "ASSESSMENT_INVALID"
    content = {key: value for key, value in assessment.items() if key != "revision"}
    digest = canonical_sha256(content)
    if digest is None or assessment["revision"] != f"assessment:sha256:{digest}":
        return "ASSESSMENT_INVALID"

    questions = assessment["questions"]
    if not isinstance(questions, list) or not questions:
        return "ASSESSMENT_INVALID"
    question_fields = {
        "question_id",
        "concept_id",
        "question_type",
        "prompt",
        "options",
        "answer_key_option_id",
        "source_evidence_ids",
    }
    option_fields = {"option_id", "text"}
    evidence_by_concept = _evidence_ids_by_concept(knowledge_map)
    question_by_id: dict[str, dict[str, Any]] = {}
    for question in questions:
        if not isinstance(question, dict) or set(question) != question_fields:
            return "ASSESSMENT_INVALID"
        question_id = question["question_id"]
        concept_id = question["concept_id"]
        options = question["options"]
        evidence_ids = question["source_evidence_ids"]
        if (
            not _is_nonempty_string(question_id)
            or question_id in question_by_id
            or not _is_nonempty_string(concept_id)
            or concept_id not in evidence_by_concept
            or question["question_type"] != "single_choice"
            or not _is_nonempty_string(question["prompt"])
            or not isinstance(options, list)
            or len(options) < 2
            or not isinstance(evidence_ids, list)
            or not evidence_ids
            or any(not _is_nonempty_string(evidence_id) for evidence_id in evidence_ids)
            or len(evidence_ids) != len(set(evidence_ids))
            or not set(evidence_ids).issubset(evidence_by_concept[concept_id])
        ):
            return "ASSESSMENT_INVALID"
        option_ids = []
        option_texts = []
        for option in options:
            if (
                not isinstance(option, dict)
                or set(option) != option_fields
                or not _is_nonempty_string(option["option_id"])
                or not _is_nonempty_string(option["text"])
            ):
                return "ASSESSMENT_INVALID"
            option_ids.append(option["option_id"])
            option_texts.append(option["text"])
        if (
            len(option_ids) != len(set(option_ids))
            or len(option_texts) != len(set(option_texts))
            or question["answer_key_option_id"] not in option_ids
        ):
            return "ASSESSMENT_INVALID"
        question_by_id[question_id] = question
    if questions != sorted(questions, key=lambda item: item["question_id"]):
        return "ASSESSMENT_INVALID"

    practice_sets = assessment["practice_sets"]
    if not isinstance(practice_sets, list):
        return "ASSESSMENT_INVALID"
    practice_fields = {"practice_set_id", "concept_id", "question_ids"}
    practice_set_ids = set()
    assigned_question_ids = set()
    for practice_set in practice_sets:
        if not isinstance(practice_set, dict) or set(practice_set) != practice_fields:
            return "ASSESSMENT_INVALID"
        practice_id = practice_set["practice_set_id"]
        concept_id = practice_set["concept_id"]
        question_ids = practice_set["question_ids"]
        if (
            not _is_nonempty_string(practice_id)
            or practice_id in practice_set_ids
            or not _is_nonempty_string(concept_id)
            or concept_id not in evidence_by_concept
            or not isinstance(question_ids, list)
            or not question_ids
            or any(not _is_nonempty_string(question_id) for question_id in question_ids)
            or len(question_ids) != len(set(question_ids))
            or any(question_id not in question_by_id for question_id in question_ids)
            or any(
                question_by_id[question_id]["concept_id"] != concept_id
                for question_id in question_ids
            )
            or assigned_question_ids.intersection(question_ids)
        ):
            return "ASSESSMENT_INVALID"
        practice_set_ids.add(practice_id)
        assigned_question_ids.update(question_ids)
    if practice_sets != sorted(
        practice_sets, key=lambda item: item["practice_set_id"]
    ):
        return "ASSESSMENT_INVALID"
    return None


def validate_answer_event(
    event: Any,
    assessment: dict[str, Any],
    trusted_learner_id: str,
) -> str | None:
    """驗證 server-scored AnswerEvent identity 與 exact bindings。"""
    fields = {
        "schema",
        "answer_event_id",
        "learner_id",
        "assessment_id",
        "assessment_revision",
        "question_id",
        "concept_id",
        "selected_option_id",
        "score",
        "scoring_rule_version",
        "knowledge_map_revision",
        "learning_path_revision",
        "idempotency_key",
        "submitted_at",
        "processing",
        "quality",
        "decision",
        "reason_code",
    }
    if not isinstance(event, dict) or set(event) != fields:
        return "ANSWER_EVENT_INVALID"
    question_by_id = {item["question_id"]: item for item in assessment["questions"]}
    if not _is_nonempty_string(event["question_id"]):
        return "ANSWER_EVENT_INVALID"
    question = question_by_id.get(event["question_id"])
    if (
        event["schema"] != ANSWER_EVENT_SCHEMA
        or event["learner_id"] != trusted_learner_id
        or event["assessment_id"] != assessment["assessment_id"]
        or event["assessment_revision"] != assessment["revision"]
        or event["scoring_rule_version"] != assessment["scoring_rule_version"]
        or event["knowledge_map_revision"] != assessment["knowledge_map_revision"]
        or event["learning_path_revision"] != assessment["learning_path_revision"]
        or question is None
        or event["concept_id"] != question["concept_id"]
        or not _is_nonempty_string(event["selected_option_id"])
        or event["selected_option_id"]
        not in {option["option_id"] for option in question["options"]}
        or type(event["score"]) is not int
        or event["score"] not in {0, 1}
        or event["score"]
        != int(event["selected_option_id"] == question["answer_key_option_id"])
        or not _is_nonempty_string(event["idempotency_key"])
        or not _is_timezone_aware_iso_datetime(event["submitted_at"])
        or _artifact_status_values(event) != ACCEPTED_ANSWER_STATUS
    ):
        return "ANSWER_EVENT_INVALID"
    content = {key: value for key, value in event.items() if key != "answer_event_id"}
    digest = canonical_sha256(content)
    if digest is None or event["answer_event_id"] != f"answer-event:sha256:{digest}":
        return "ANSWER_EVENT_INVALID"
    return None


def _submission_failure(reason_code: str) -> dict[str, Any]:
    return {
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": reason_code,
        "answer_events": [],
        "replayed": False,
    }


def score_submission(
    assessment: Any,
    submission: Any,
    *,
    trusted_learner_id: Any,
    knowledge_map: Any,
    learning_path_revision: Any,
    existing_events: Any,
) -> dict[str, Any]:
    """以 trusted learner 與 answer key 原子地建立 0/1 AnswerEvents。"""
    if (
        not _is_nonempty_string(trusted_learner_id)
        or validate_assessment(
            assessment, knowledge_map, learning_path_revision
        )
        is not None
    ):
        return _submission_failure("ASSESSMENT_INVALID")
    submission_fields = {
        "schema",
        "assessment_id",
        "assessment_revision",
        "idempotency_key",
        "submitted_at",
        "responses",
    }
    if not isinstance(submission, dict) or set(submission) != submission_fields:
        return _submission_failure("SUBMISSION_INVALID")
    if (
        submission["schema"] != SUBMISSION_SCHEMA
        or submission["assessment_id"] != assessment["assessment_id"]
        or submission["assessment_revision"] != assessment["revision"]
        or not _is_nonempty_string(submission["idempotency_key"])
        or not _is_timezone_aware_iso_datetime(submission["submitted_at"])
    ):
        return _submission_failure("SUBMISSION_INVALID")
    question_by_id = {item["question_id"]: item for item in assessment["questions"]}
    responses = submission["responses"]
    if not isinstance(responses, list) or not responses:
        return _submission_failure("SUBMISSION_INVALID")
    submitted_response_by_question = {}
    for response in responses:
        if (
            not isinstance(response, dict)
            or set(response) != {"question_id", "selected_option_id"}
        ):
            return _submission_failure("SUBMISSION_INVALID")
        question_id = response["question_id"]
        option_id = response["selected_option_id"]
        if (
            not _is_nonempty_string(question_id)
            or question_id not in question_by_id
            or question_id in submitted_response_by_question
            or not _is_nonempty_string(option_id)
            or option_id
            not in {option["option_id"] for option in question_by_id[question_id]["options"]}
        ):
            return _submission_failure("SUBMISSION_INVALID")
        submitted_response_by_question[question_id] = response
    if not isinstance(existing_events, list):
        return _submission_failure("ANSWER_EVENT_INVALID")
    for event in existing_events:
        if validate_answer_event(event, assessment, trusted_learner_id) is not None:
            return _submission_failure("ANSWER_EVENT_INVALID")
    events_with_idempotency_key = [
        event
        for event in existing_events
        if event["idempotency_key"] == submission["idempotency_key"]
    ]
    if events_with_idempotency_key:
        existing_option_id_by_question = {
            event["question_id"]: event["selected_option_id"] for event in events_with_idempotency_key
        }
        if (
            len(events_with_idempotency_key) != len(submitted_response_by_question)
            or existing_option_id_by_question
            != {
                question_id: response["selected_option_id"]
                for question_id, response in submitted_response_by_question.items()
            }
        ):
            return _submission_failure("IDEMPOTENCY_CONFLICT")
        return {
            "processing": "succeeded",
            "quality": "accepted",
            "decision": "retain",
            "reason_code": "SUBMISSION_REPLAYED",
            "answer_events": sorted(
                deepcopy(events_with_idempotency_key), key=lambda item: item["question_id"]
            ),
            "replayed": True,
        }

    answer_events = []
    for question_id in sorted(submitted_response_by_question):
        question = question_by_id[question_id]
        response = submitted_response_by_question[question_id]
        content = {
            "schema": ANSWER_EVENT_SCHEMA,
            "learner_id": trusted_learner_id,
            "assessment_id": assessment["assessment_id"],
            "assessment_revision": assessment["revision"],
            "question_id": question_id,
            "concept_id": question["concept_id"],
            "selected_option_id": response["selected_option_id"],
            "score": int(
                response["selected_option_id"] == question["answer_key_option_id"]
            ),
            "scoring_rule_version": assessment["scoring_rule_version"],
            "knowledge_map_revision": assessment["knowledge_map_revision"],
            "learning_path_revision": assessment["learning_path_revision"],
            "idempotency_key": submission["idempotency_key"],
            "submitted_at": submission["submitted_at"],
            "processing": "succeeded",
            "quality": "accepted",
            "decision": "retain",
            "reason_code": "ANSWER_SCORED",
        }
        answer_events.append(
            {
                "answer_event_id": (
                    "answer-event:sha256:" + canonical_sha256(content)
                ),
                **content,
            }
        )
    if any(
        validate_answer_event(event, assessment, trusted_learner_id) is not None
        for event in answer_events
    ):
        return _submission_failure("ANSWER_EVENT_INVALID")
    return {
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "SUBMISSION_SCORED",
        "answer_events": answer_events,
        "replayed": False,
    }
