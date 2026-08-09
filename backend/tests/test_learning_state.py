from copy import deepcopy

import pytest

from knowledge_map.artifacts import _with_id, _with_revision, build_initial_learning_path
from learning_state.assessment import canonical_sha256, score_submission
from learning_state.learning_events import normalize_learning_events, validate_learning_event
from learning_state.learning_state import (
    LearningStateError,
    _mastery_band,
    build_learning_state,
)


LEARNER_ID = "learner:trusted"


def _build_knowledge_map() -> dict:
    concepts = [
        {
            "concept_id": concept_id,
            "members": [
                {
                    "candidate_id": f"candidate:{suffix}",
                    "page_number": page,
                    "evidence_ids": [f"evidence:{suffix}"],
                }
            ],
        }
        for concept_id, suffix, page in [
            ("concept:a", "a", 1),
            ("concept:b", "b", 2),
        ]
    ]
    relation_content = {
        "schema": "relation/v1",
        "type": "prerequisite",
        "source_concept_id": "concept:a",
        "target_concept_id": "concept:b",
        "statement": "A 是 B 的前置概念",
        "evidence_ids": ["evidence:a", "evidence:b"],
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "DIRECT_CLUE_ACCEPTED",
    }
    content = {
        "schema": "knowledge-map/v1",
        "source_output_id": "source:test",
        "material_ref": "material:test",
        "pages": [],
        "concepts": concepts,
        "evidence_index": [
            {"evidence_id": "evidence:a"},
            {"evidence_id": "evidence:b"},
        ],
        "relations": [_with_id("relation", "relation_id", relation_content)],
        "review_items": [],
        "known_limitations": [],
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "KNOWLEDGE_MAP_ACCEPTED",
    }
    return _with_revision("knowledge-map", content)


def _build_assessment(knowledge_map: dict, learning_path: dict) -> dict:
    questions = []
    for suffix in ["a", "b"]:
        for index in range(1, 4):
            questions.append(
                {
                    "question_id": f"question:{suffix}:{index}",
                    "concept_id": f"concept:{suffix}",
                    "question_type": "single_choice",
                    "prompt": f"{suffix.upper()} 題目 {index}",
                    "options": [
                        {"option_id": "option:correct", "text": "正確"},
                        {"option_id": "option:wrong", "text": "錯誤"},
                    ],
                    "answer_key_option_id": "option:correct",
                    "source_evidence_ids": [f"evidence:{suffix}"],
                }
            )
    content = {
        "schema": "assessment/v1",
        "assessment_id": "assessment:test",
        "version": "1",
        "knowledge_map_revision": knowledge_map["revision"],
        "learning_path_revision": learning_path["revision"],
        "scoring_rule_version": "single-choice-exact/v1",
        "questions": questions,
        "practice_sets": [
            {
                "practice_set_id": f"practice:{suffix}",
                "concept_id": f"concept:{suffix}",
                "question_ids": [f"question:{suffix}:{index}" for index in range(1, 4)],
            }
            for suffix in ["a", "b"]
        ],
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "ASSESSMENT_ACCEPTED",
    }
    return {"revision": f"assessment:sha256:{canonical_sha256(content)}", **content}


def _build_answer_event(
    assessment: dict,
    question_id: str,
    selected_option_id: str,
    submitted_at: str,
    idempotency_key: str,
) -> dict:
    question = next(
        item for item in assessment["questions"] if item["question_id"] == question_id
    )
    content = {
        "schema": "answer-event/v1",
        "learner_id": LEARNER_ID,
        "assessment_id": assessment["assessment_id"],
        "assessment_revision": assessment["revision"],
        "question_id": question_id,
        "concept_id": question["concept_id"],
        "selected_option_id": selected_option_id,
        "score": int(selected_option_id == question["answer_key_option_id"]),
        "scoring_rule_version": assessment["scoring_rule_version"],
        "knowledge_map_revision": assessment["knowledge_map_revision"],
        "learning_path_revision": assessment["learning_path_revision"],
        "idempotency_key": idempotency_key,
        "submitted_at": submitted_at,
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "ANSWER_SCORED",
    }
    return {
        "answer_event_id": f"answer-event:sha256:{canonical_sha256(content)}",
        **content,
    }


def _build_learning_event(
    knowledge_map: dict,
    learning_path: dict,
    concept_id: str,
    event_type: str,
    event_at: str,
    idempotency_key: str,
) -> dict:
    content = {
        "schema": "learning-event/v1",
        "learner_id": LEARNER_ID,
        "concept_id": concept_id,
        "event_type": event_type,
        "event_at": event_at,
        "idempotency_key": idempotency_key,
        "knowledge_map_revision": knowledge_map["revision"],
        "learning_path_revision": learning_path["revision"],
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "LEARNING_EVENT_ACCEPTED",
    }
    return {
        "learning_event_id": f"learning-event:sha256:{canonical_sha256(content)}",
        **content,
    }


def _build_inputs() -> tuple[dict, dict, dict]:
    knowledge_map = _build_knowledge_map()
    path = build_initial_learning_path(knowledge_map)
    return knowledge_map, path, _build_assessment(knowledge_map, path)


def _build_test_learning_state(
    knowledge_map: dict,
    learning_path: dict,
    assessment: dict,
    answer_events: list[dict],
    learning_events: list[dict] | None,
) -> dict:
    return build_learning_state(
        trusted_learner_id=LEARNER_ID,
        knowledge_map=knowledge_map,
        learning_path=learning_path,
        assessment=assessment,
        answer_events=answer_events,
        learning_event_stream=learning_events,
    )


def _build_subset_submission(
    assessment: dict,
    idempotency_key: str,
    submitted_at: str,
    responses: list[tuple[str, str]],
) -> dict:
    return {
        "schema": "assessment-submission/v1",
        "assessment_id": assessment["assessment_id"],
        "assessment_revision": assessment["revision"],
        "idempotency_key": idempotency_key,
        "submitted_at": submitted_at,
        "responses": [
            {"question_id": question_id, "selected_option_id": option_id}
            for question_id, option_id in responses
        ],
    }


def test_five_subscores_use_latest_distinct_answers_and_all_attempts():
    knowledge_map, path, assessment = _build_inputs()
    answers = [
        _build_answer_event(assessment, "question:a:1", "option:wrong", "2026-08-09T09:00:00+08:00", "a0"),
        _build_answer_event(assessment, "question:a:1", "option:correct", "2026-08-09T11:00:00+08:00", "a1"),
        _build_answer_event(assessment, "question:a:2", "option:correct", "2026-08-09T12:00:00+08:00", "a2"),
        _build_answer_event(assessment, "question:a:3", "option:correct", "2026-08-09T13:00:00+08:00", "a3"),
    ]
    events = [
        _build_learning_event(knowledge_map, path, "concept:a", "concept_completed", "2026-08-09T08:00:00+08:00", "e1"),
        _build_learning_event(knowledge_map, path, "concept:a", "practice_completed", "2026-08-09T08:30:00+08:00", "e2"),
        _build_learning_event(knowledge_map, path, "concept:a", "review_completed", "2026-08-09T10:00:00+08:00", "e3"),
    ]

    state = _build_test_learning_state(knowledge_map, path, assessment, answers, events)
    mastery = state["mastery"][0]

    assert mastery["valid_answer_count"] == 3
    assert mastery["correct_rate"] == 1
    assert mastery["practice_score"] == 1
    assert mastery["review_score"] == 1
    assert mastery["completion_score"] == 1
    assert mastery["recent_error_penalty"] == 0.5
    assert mastery["mastery_score"] == pytest.approx(0.85)
    assert mastery["final_status"] == "mastered"
    assert state["suggestion"]["target_concept_id"] == "concept:b"
    assert state["suggestion"]["prerequisite_score"] == 1


def test_zero_answers_stay_undefined_and_activity_changes_only_status():
    knowledge_map, path, assessment = _build_inputs()
    empty = _build_test_learning_state(knowledge_map, path, assessment, [], [])
    viewed = _build_test_learning_state(
        knowledge_map,
        path,
        assessment,
        [],
        [
            _build_learning_event(
                knowledge_map,
                path,
                "concept:a",
                "concept_viewed",
                "2026-08-09T08:00:00+08:00",
                "viewed",
            )
        ],
    )

    assert empty["mastery"][0]["mastery_score"] is None
    assert empty["mastery"][0]["recent_error_penalty"] is None
    assert empty["mastery"][0]["final_status"] == "not_started"
    assert viewed["mastery"][0]["mastery_score"] is None
    assert viewed["mastery"][0]["final_status"] == "learning"
    assert viewed["mastery"][0]["completion_score"] == 0.5
    assert viewed["suggestion"]["is_personalized"] is False
    assert viewed["suggestion"]["action"] == "follow_initial_path"
    assert viewed["suggestion"]["learning_suggestion_score"] == pytest.approx(0.45)
    assert viewed["suggestion"]["level"] == "low"


def test_one_answer_and_review_without_post_answer_are_needs_review():
    knowledge_map, path, assessment = _build_inputs()
    answers = [
        _build_answer_event(assessment, "question:a:1", "option:correct", "2026-08-09T09:00:00+08:00", "a1")
    ]
    events = [
        _build_learning_event(knowledge_map, path, "concept:a", "concept_viewed", "2026-08-09T08:00:00+08:00", "view"),
        _build_learning_event(knowledge_map, path, "concept:a", "practice_started", "2026-08-09T08:10:00+08:00", "practice"),
        _build_learning_event(knowledge_map, path, "concept:a", "review_completed", "2026-08-09T10:00:00+08:00", "review"),
    ]
    state = _build_test_learning_state(knowledge_map, path, assessment, answers, events)
    mastery = state["mastery"][0]

    assert mastery["valid_answer_count"] == 1
    assert mastery["practice_score"] == 0.5
    assert mastery["review_score"] == 0.5
    assert mastery["needs_review"] is True
    assert "REVIEW_HAS_NO_POST_ANSWER" in mastery["reason_codes"]
    assert mastery["final_status"] != "mastered"
    assert state["weaknesses"] == []
    assert state["suggestion"]["is_personalized"] is False
    assert state["suggestion"]["learning_suggestion_score"] == pytest.approx(0.60)
    assert state["suggestion"]["level"] == "medium"


def test_subset_scorer_reaches_one_two_and_retry_without_increasing_distinct_count():
    knowledge_map, path, assessment = _build_inputs()
    answer_events = []
    submissions = [
        _build_subset_submission(
            assessment,
            "subset:one",
            "2026-08-09T08:00:00+08:00",
            [("question:a:1", "option:correct")],
        ),
        _build_subset_submission(
            assessment,
            "subset:two",
            "2026-08-09T09:00:00+08:00",
            [("question:a:2", "option:correct")],
        ),
        _build_subset_submission(
            assessment,
            "subset:retry",
            "2026-08-09T10:00:00+08:00",
            [("question:a:1", "option:wrong")],
        ),
    ]
    first = score_submission(
        assessment,
        submissions[0],
        trusted_learner_id=LEARNER_ID,
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=answer_events,
    )
    answer_events.extend(first["answer_events"])
    one_answer_state = _build_test_learning_state(knowledge_map, path, assessment, answer_events, [])
    assert one_answer_state["mastery"][0]["valid_answer_count"] == 1
    assert one_answer_state["suggestion"]["learning_suggestion_score"] == pytest.approx(0.60)
    assert one_answer_state["suggestion"]["is_personalized"] is False
    assert one_answer_state["suggestion"]["decision"] == "review"

    second = score_submission(
        assessment,
        submissions[1],
        trusted_learner_id=LEARNER_ID,
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=answer_events,
    )
    answer_events.extend(second["answer_events"])
    two_answer_state = _build_test_learning_state(knowledge_map, path, assessment, answer_events, [])
    assert two_answer_state["mastery"][0]["valid_answer_count"] == 2
    assert two_answer_state["mastery"][0]["final_status"] != "mastered"
    assert two_answer_state["suggestion"]["is_personalized"] is False

    retry = score_submission(
        assessment,
        submissions[2],
        trusted_learner_id=LEARNER_ID,
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=answer_events,
    )
    answer_events.extend(retry["answer_events"])
    retry_state = _build_test_learning_state(knowledge_map, path, assessment, answer_events, [])
    assert retry_state["mastery"][0]["valid_answer_count"] == 2
    assert retry_state["mastery"][0]["correct_rate"] == 0.5
    assert len(retry_state["mastery"][0]["source_answer_event_ids"]) == 3


def test_consecutive_and_post_review_wrong_force_remediation_weakness():
    knowledge_map, path, assessment = _build_inputs()
    answers = [
        _build_answer_event(assessment, "question:a:1", "option:correct", "2026-08-09T09:00:00+08:00", "a1"),
        _build_answer_event(assessment, "question:a:2", "option:wrong", "2026-08-09T11:00:00+08:00", "a2"),
        _build_answer_event(assessment, "question:a:3", "option:wrong", "2026-08-09T12:00:00+08:00", "a3"),
    ]
    events = [
        _build_learning_event(knowledge_map, path, "concept:a", "concept_completed", "2026-08-09T08:00:00+08:00", "complete"),
        _build_learning_event(knowledge_map, path, "concept:a", "practice_started", "2026-08-09T08:30:00+08:00", "practice"),
        _build_learning_event(knowledge_map, path, "concept:a", "review_completed", "2026-08-09T10:00:00+08:00", "review"),
    ]
    state = _build_test_learning_state(knowledge_map, path, assessment, answers, events)

    assert state["mastery"][0]["final_status"] == "review"
    assert state["weaknesses"][0]["kind"] == "remediation_required"
    assert set(state["weaknesses"][0]["reason_codes"]) == {
        "CONSECUTIVE_WRONG_ANSWERS",
        "POST_REVIEW_ANSWER_WRONG",
    }
    assert state["suggestion"]["learning_suggestion_score"] >= 0.75
    assert state["suggestion"]["is_personalized"] is False
    assert state["suggestion"]["needs_review"] is True


def test_stable_weakness_can_produce_high_scored_personalized_review():
    knowledge_map, path, assessment = _build_inputs()
    answers = [
        _build_answer_event(assessment, "question:a:1", "option:wrong", "2026-08-09T08:00:00+08:00", "a1"),
        _build_answer_event(assessment, "question:a:2", "option:correct", "2026-08-09T09:00:00+08:00", "a2"),
        _build_answer_event(assessment, "question:a:3", "option:wrong", "2026-08-09T10:00:00+08:00", "a3"),
    ]
    state = _build_test_learning_state(knowledge_map, path, assessment, answers, [])

    assert state["mastery"][0]["final_status"] == "weak"
    assert state["suggestion"]["learning_suggestion_score"] == 1
    assert state["suggestion"]["is_personalized"] is True
    assert state["suggestion"]["action"] == "review_concept"


def test_mastery_formula_clamps_negative_score_to_zero():
    knowledge_map, path, assessment = _build_inputs()
    answers = [
        _build_answer_event(
            assessment,
            f"question:a:{index}",
            "option:wrong",
            f"2026-08-09T{index + 7:02d}:00:00+08:00",
            f"a{index}",
        )
        for index in range(1, 4)
    ]
    state = _build_test_learning_state(knowledge_map, path, assessment, answers, [])

    assert state["mastery"][0]["mastery_score"] == 0
    assert state["mastery"][0]["raw_band"] == "weak"


def test_three_stable_answers_adopt_the_exact_point_seven_five_threshold():
    knowledge_map, path, assessment = _build_inputs()
    answers = [
        _build_answer_event(
            assessment,
            f"question:a:{index}",
            "option:correct",
            f"2026-08-09T{index + 7:02d}:00:00+08:00",
            f"a{index}",
        )
        for index in range(1, 4)
    ]
    events = [
        _build_learning_event(
            knowledge_map,
            path,
            "concept:a",
            "concept_viewed",
            "2026-08-09T07:00:00+08:00",
            "view",
        )
    ]
    state = _build_test_learning_state(knowledge_map, path, assessment, answers, events)

    assert state["mastery"][0]["mastery_score"] == pytest.approx(0.60)
    assert state["suggestion"]["learning_suggestion_score"] == pytest.approx(0.75)
    assert state["suggestion"]["is_personalized"] is True


def test_raw_mastered_with_two_questions_is_forced_to_learning():
    knowledge_map, path, assessment = _build_inputs()
    changed = deepcopy(assessment)
    changed["practice_sets"][0]["question_ids"] = ["question:a:1", "question:a:2"]
    content = {key: value for key, value in changed.items() if key != "revision"}
    changed["revision"] = f"assessment:sha256:{canonical_sha256(content)}"
    answers = [
        _build_answer_event(changed, "question:a:1", "option:wrong", "2026-08-09T08:00:00+08:00", "a0"),
        _build_answer_event(changed, "question:a:1", "option:correct", "2026-08-09T10:00:00+08:00", "a1"),
        _build_answer_event(changed, "question:a:2", "option:correct", "2026-08-09T11:00:00+08:00", "a2"),
    ]
    events = [
        _build_learning_event(knowledge_map, path, "concept:a", "concept_completed", "2026-08-09T07:00:00+08:00", "complete"),
        _build_learning_event(knowledge_map, path, "concept:a", "practice_completed", "2026-08-09T07:30:00+08:00", "practice"),
        _build_learning_event(knowledge_map, path, "concept:a", "review_completed", "2026-08-09T09:00:00+08:00", "review"),
    ]
    state = _build_test_learning_state(knowledge_map, path, changed, answers, events)
    mastery = state["mastery"][0]

    assert mastery["valid_answer_count"] == 2
    assert mastery["raw_band"] == "mastered"
    assert mastery["final_status"] == "learning"
    assert state["weaknesses"] == []
    assert state["suggestion"]["is_personalized"] is False


def test_all_mastered_produces_deterministic_no_action():
    knowledge_map, path, assessment = _build_inputs()
    answers = []
    events = []
    for suffix in ["a", "b"]:
        answers.extend(
            [
                _build_answer_event(assessment, f"question:{suffix}:1", "option:wrong", "2026-08-09T08:00:00+08:00", f"{suffix}0"),
                _build_answer_event(assessment, f"question:{suffix}:1", "option:correct", "2026-08-09T10:00:00+08:00", f"{suffix}1"),
                _build_answer_event(assessment, f"question:{suffix}:2", "option:correct", "2026-08-09T11:00:00+08:00", f"{suffix}2"),
                _build_answer_event(assessment, f"question:{suffix}:3", "option:correct", "2026-08-09T12:00:00+08:00", f"{suffix}3"),
            ]
        )
        events.extend(
            [
                _build_learning_event(knowledge_map, path, f"concept:{suffix}", "concept_completed", "2026-08-09T07:00:00+08:00", f"{suffix}-complete"),
                _build_learning_event(knowledge_map, path, f"concept:{suffix}", "practice_completed", "2026-08-09T07:30:00+08:00", f"{suffix}-practice"),
                _build_learning_event(knowledge_map, path, f"concept:{suffix}", "review_completed", "2026-08-09T09:00:00+08:00", f"{suffix}-review"),
            ]
        )
    first = _build_test_learning_state(knowledge_map, path, assessment, answers, events)
    second = _build_test_learning_state(knowledge_map, path, assessment, answers, events)

    assert {item["final_status"] for item in first["mastery"]} == {"mastered"}
    assert first["suggestion"]["action"] == "no_action"
    assert first["suggestion"]["target_concept_id"] is None
    assert first["suggestion"]["learning_suggestion_score"] is None
    assert first == second


@pytest.mark.parametrize(
    "event_type",
    [
        "concept_completed",
        "concept_viewed",
        "practice_completed",
        "practice_started",
        "review_completed",
    ],
)
def test_exact_five_learning_event_types_are_accepted(event_type):
    knowledge_map, path, _ = _build_inputs()
    event = _build_learning_event(
        knowledge_map,
        path,
        "concept:a",
        event_type,
        "2026-08-09T08:00:00+08:00",
        event_type,
    )

    assert (
        validate_learning_event(
            event,
            trusted_learner_id=LEARNER_ID,
            knowledge_map=knowledge_map,
            learning_path_revision=path["revision"],
        )
        is None
    )


def test_cycle_path_fails_before_state_is_produced():
    knowledge_map = _build_knowledge_map()
    reverse_content = {
        **{
            key: value
            for key, value in knowledge_map["relations"][0].items()
            if key != "relation_id"
        },
        "source_concept_id": "concept:b",
        "target_concept_id": "concept:a",
    }
    changed = deepcopy(knowledge_map)
    changed["relations"] = sorted(
        changed["relations"]
        + [_with_id("relation", "relation_id", reverse_content)],
        key=lambda item: item["relation_id"],
    )
    changed = _with_revision(
        "knowledge-map",
        {key: value for key, value in changed.items() if key != "revision"},
    )
    cycle_path = build_initial_learning_path(changed)
    assessment = _build_assessment(changed, cycle_path)

    with pytest.raises(LearningStateError, match="PREREQUISITE_CYCLE"):
        _build_test_learning_state(changed, cycle_path, assessment, [], [])


def test_missing_stream_and_conflicting_learning_event_replay_fail_closed():
    knowledge_map, path, assessment = _build_inputs()
    with pytest.raises(LearningStateError, match="LEARNING_EVENT_STREAM_MISSING"):
        _build_test_learning_state(knowledge_map, path, assessment, [], None)

    first = _build_learning_event(
        knowledge_map,
        path,
        "concept:a",
        "concept_viewed",
        "2026-08-09T08:00:00+08:00",
        "same-key",
    )
    changed = deepcopy(first)
    changed["event_type"] = "concept_completed"
    content = {key: value for key, value in changed.items() if key != "learning_event_id"}
    changed["learning_event_id"] = f"learning-event:sha256:{canonical_sha256(content)}"
    normalized, error = normalize_learning_events(
        [first, changed],
        trusted_learner_id=LEARNER_ID,
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
    )
    assert normalized == []
    assert error == "LEARNING_EVENT_INVALID"


def test_mastery_band_boundaries_are_exact():
    assert _mastery_band(0.80) == "mastered"
    assert _mastery_band(0.79) == "learning"
    assert _mastery_band(0.50) == "learning"
    assert _mastery_band(0.49) == "review"
    assert _mastery_band(0.30) == "review"
    assert _mastery_band(0.29) == "weak"
