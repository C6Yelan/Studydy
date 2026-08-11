from copy import deepcopy

from knowledge_map.artifacts import _with_revision, build_initial_learning_path
from learning_state.assessment import canonical_sha256, score_submission, validate_assessment


def _build_knowledge_map() -> dict:
    content = {
        "schema": "knowledge-map/v1",
        "source_output_id": "source:test",
        "material_ref": "material:test",
        "pages": [],
        "concepts": [
            {
                "concept_id": "concept:a",
                "members": [
                    {"candidate_id": "candidate:a", "page_number": 1, "evidence_ids": ["evidence:a"]}
                ],
            }
        ],
        "evidence_index": [{"evidence_id": "evidence:a"}],
        "relations": [],
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
    for index in range(1, 4):
        questions.append(
            {
                "question_id": f"question:{index}",
                "concept_id": "concept:a",
                "question_type": "single_choice",
                "prompt": f"題目 {index}",
                "options": [
                    {"option_id": "option:a", "text": "正確"},
                    {"option_id": "option:b", "text": "錯誤"},
                ],
                "answer_key_option_id": "option:a",
                "source_evidence_ids": ["evidence:a"],
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
                "practice_set_id": "practice:a",
                "concept_id": "concept:a",
                "question_ids": ["question:1", "question:2", "question:3"],
            }
        ],
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "ASSESSMENT_ACCEPTED",
    }
    return {
        "revision": f"assessment:sha256:{canonical_sha256(content)}",
        **content,
    }


def _build_submission(assessment: dict, idempotency_key: str = "submission:1") -> dict:
    return {
        "schema": "assessment-submission/v1",
        "assessment_id": assessment["assessment_id"],
        "assessment_revision": assessment["revision"],
        "idempotency_key": idempotency_key,
        "submitted_at": "2026-08-09T10:00:00+08:00",
        "responses": [
            {"question_id": "question:1", "selected_option_id": "option:a"},
            {"question_id": "question:2", "selected_option_id": "option:b"},
            {"question_id": "question:3", "selected_option_id": "option:a"},
        ],
    }


def test_valid_submission_is_server_scored_and_atomic():
    knowledge_map = _build_knowledge_map()
    path = build_initial_learning_path(knowledge_map)
    assessment = _build_assessment(knowledge_map, path)

    scored_submission = score_submission(
        assessment,
        _build_submission(assessment),
        trusted_learner_id="learner:trusted",
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=[],
    )

    assert scored_submission["reason_code"] == "SUBMISSION_SCORED"
    assert [event["score"] for event in scored_submission["answer_events"]] == [1, 0, 1]
    assert {event["learner_id"] for event in scored_submission["answer_events"]} == {
        "learner:trusted"
    }


def test_unknown_empty_or_client_trust_fields_produce_zero_events():
    knowledge_map = _build_knowledge_map()
    path = build_initial_learning_path(knowledge_map)
    assessment = _build_assessment(knowledge_map, path)
    invalid_submissions = []
    empty = _build_submission(assessment)
    empty["responses"] = []
    invalid_submissions.append(empty)
    unknown = _build_submission(assessment)
    unknown["responses"][0]["selected_option_id"] = "option:unknown"
    invalid_submissions.append(unknown)
    untrusted = _build_submission(assessment)
    untrusted["learner_id"] = "learner:client"
    invalid_submissions.append(untrusted)
    wrong_type = _build_submission(assessment)
    wrong_type["responses"][0]["question_id"] = ["question:1"]
    invalid_submissions.append(wrong_type)
    duplicate = _build_submission(assessment)
    duplicate["responses"][1] = deepcopy(duplicate["responses"][0])
    invalid_submissions.append(duplicate)
    stale = _build_submission(assessment)
    stale["assessment_revision"] = "assessment:sha256:stale"
    invalid_submissions.append(stale)

    for submission in invalid_submissions:
        scored_submission = score_submission(
            assessment,
            submission,
            trusted_learner_id="learner:trusted",
            knowledge_map=knowledge_map,
            learning_path_revision=path["revision"],
            existing_events=[],
        )
        assert scored_submission["processing"] == "failed"
        assert scored_submission["answer_events"] == []


def test_nonempty_question_subset_is_scored_without_missing_question_failure():
    knowledge_map = _build_knowledge_map()
    path = build_initial_learning_path(knowledge_map)
    assessment = _build_assessment(knowledge_map, path)
    submission = _build_submission(assessment)
    submission["responses"] = [submission["responses"][1]]

    scored_submission = score_submission(
        assessment,
        submission,
        trusted_learner_id="learner:trusted",
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=[],
    )

    assert scored_submission["reason_code"] == "SUBMISSION_SCORED"
    assert len(scored_submission["answer_events"]) == 1
    assert scored_submission["answer_events"][0]["question_id"] == "question:2"
    assert scored_submission["answer_events"][0]["score"] == 0


def test_same_submission_replays_and_changed_response_conflicts():
    knowledge_map = _build_knowledge_map()
    path = build_initial_learning_path(knowledge_map)
    assessment = _build_assessment(knowledge_map, path)
    submission = _build_submission(assessment)
    first = score_submission(
        assessment,
        submission,
        trusted_learner_id="learner:trusted",
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=[],
    )
    replay = score_submission(
        assessment,
        submission,
        trusted_learner_id="learner:trusted",
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=first["answer_events"],
    )
    changed = deepcopy(submission)
    changed["responses"][0]["selected_option_id"] = "option:b"
    conflict = score_submission(
        assessment,
        changed,
        trusted_learner_id="learner:trusted",
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=first["answer_events"],
    )

    assert replay["replayed"] is True
    assert replay["answer_events"] == first["answer_events"]
    assert conflict["reason_code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict["answer_events"] == []


def test_subset_replay_is_order_independent_but_question_set_change_conflicts():
    knowledge_map = _build_knowledge_map()
    path = build_initial_learning_path(knowledge_map)
    assessment = _build_assessment(knowledge_map, path)
    submission = _build_submission(assessment)
    submission["responses"] = submission["responses"][:2]
    first = score_submission(
        assessment,
        submission,
        trusted_learner_id="learner:trusted",
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=[],
    )
    reordered = deepcopy(submission)
    reordered["responses"].reverse()
    replay = score_submission(
        assessment,
        reordered,
        trusted_learner_id="learner:trusted",
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=first["answer_events"],
    )
    changed_set = deepcopy(submission)
    changed_set["responses"].pop()
    conflict = score_submission(
        assessment,
        changed_set,
        trusted_learner_id="learner:trusted",
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=first["answer_events"],
    )

    assert replay["replayed"] is True
    assert replay["answer_events"] == first["answer_events"]
    assert conflict["reason_code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict["answer_events"] == []


def test_different_keys_accumulate_questions_and_allow_same_question_retry():
    knowledge_map = _build_knowledge_map()
    path = build_initial_learning_path(knowledge_map)
    assessment = _build_assessment(knowledge_map, path)
    accepted_events = []
    cases = [
        ("submission:one", "question:1", "option:a"),
        ("submission:two", "question:2", "option:a"),
        ("submission:retry", "question:1", "option:b"),
    ]
    for key, question_id, option_id in cases:
        submission = _build_submission(assessment, key)
        submission["responses"] = [
            {"question_id": question_id, "selected_option_id": option_id}
        ]
        scored_submission = score_submission(
            assessment,
            submission,
            trusted_learner_id="learner:trusted",
            knowledge_map=knowledge_map,
            learning_path_revision=path["revision"],
            existing_events=accepted_events,
        )
        assert scored_submission["processing"] == "succeeded"
        accepted_events.extend(scored_submission["answer_events"])

    assert len(accepted_events) == 3
    assert {event["question_id"] for event in accepted_events} == {
        "question:1",
        "question:2",
    }
    assert [
        event["score"]
        for event in accepted_events
        if event["question_id"] == "question:1"
    ] == [1, 0]


def test_invalid_practice_membership_and_tampered_existing_event_fail_closed():
    knowledge_map = _build_knowledge_map()
    path = build_initial_learning_path(knowledge_map)
    assessment = _build_assessment(knowledge_map, path)
    invalid = deepcopy(assessment)
    invalid["practice_sets"][0]["question_ids"] = ["question:unknown"]
    content = {key: value for key, value in invalid.items() if key != "revision"}
    invalid["revision"] = f"assessment:sha256:{canonical_sha256(content)}"
    assert validate_assessment(invalid, knowledge_map, path["revision"]) == "ASSESSMENT_INVALID"

    first = score_submission(
        assessment,
        _build_submission(assessment),
        trusted_learner_id="learner:trusted",
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=[],
    )
    tampered = deepcopy(first["answer_events"])
    tampered[0]["score"] = 0
    scored_submission = score_submission(
        assessment,
        _build_submission(assessment, "submission:2"),
        trusted_learner_id="learner:trusted",
        knowledge_map=knowledge_map,
        learning_path_revision=path["revision"],
        existing_events=tampered,
    )
    assert scored_submission["reason_code"] == "ANSWER_EVENT_INVALID"
    assert scored_submission["answer_events"] == []
