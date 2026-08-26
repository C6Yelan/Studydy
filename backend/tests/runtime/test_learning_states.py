from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from learning_adaptation.answer_events import submit_answer
from learning_adaptation.assessment_items import store_assessment
from learning_adaptation.learning_states import (
    LearningStateError,
    derive_learning_state,
)
from learning_adaptation.study_sessions import (
    StudySessionError,
    complete_study_session,
    create_study_session,
    set_current_study_concept,
)
from runtime.learner_session import TrustedLearner
from runtime.storage.migrations import run_migrations
from test_assessment_items import _documents
from test_study_sessions import (
    _formal_id,
    _insert_material_map,
    _knowledge_map,
    _relation,
)


@pytest.fixture
def state_database_dsn(
    clean_database_dsn: str, migrations_dir: Path
) -> str:
    assert run_migrations(
        clean_database_dsn, migrations_dir=migrations_dir
    ) == tuple(range(1, 12))
    return clean_database_dsn


def _multi_claim_map() -> dict:
    knowledge_map = _knowledge_map()
    concepts = knowledge_map["formal_concepts"]
    concepts[0]["claims"].append(
        {
            "claim_id": "claim:sha256:" + "4" * 64,
            "text": "A second required grounded claim",
            "evidence_ids": concepts[0]["claims"][0]["evidence_ids"],
        }
    )
    concepts[0]["formal_concept_id"] = _formal_id(concepts[0])
    related_source, related_target = sorted(
        concepts[1:], key=lambda concept: concept["formal_concept_id"]
    )
    knowledge_map["relations"] = [
        _relation("prerequisite", concepts[0], concepts[1]),
        _relation("contains", concepts[0], concepts[2]),
        _relation("related", related_source, related_target),
    ]
    knowledge_map["initial_learning_path"] = [
        concept["formal_concept_id"] for concept in concepts
    ]
    return knowledge_map


def _state_session(dsn: str, knowledge_map: dict | None = None):
    learner = TrustedLearner(uuid4())
    stored_map = knowledge_map or _knowledge_map()
    material_id = _insert_material_map(
        dsn, learner.learner_id, stored_map
    )
    study_session = create_study_session(
        learner,
        material_id,
        stored_map["revision"],
        str(uuid4()),
        current_formal_concept_id=stored_map["formal_concepts"][0][
            "formal_concept_id"
        ],
        dsn=dsn,
    )
    return learner, stored_map, material_id, study_session


def _answer(
    dsn: str,
    learner: TrustedLearner,
    knowledge_map: dict,
    study_session,
    *,
    concept_index: int = 0,
    claim_index: int = 0,
    correct: bool,
    sequence: int,
):
    concept = knowledge_map["formal_concepts"][concept_index]
    claim = concept["claims"][claim_index]
    set_current_study_concept(
        learner,
        study_session.study_session_id,
        concept["formal_concept_id"],
        dsn=dsn,
    )
    documents = _documents(
        study_session,
        knowledge_map,
        target_formal_concept_id=concept["formal_concept_id"],
        target_claim_id=claim["claim_id"],
        source_evidence_ids=claim["evidence_ids"],
        prompt=f"Grounded question {concept_index}-{claim_index}-{sequence}?",
    )
    assessment = store_assessment(
        learner,
        documents.public_document,
        documents.private_answer_document,
        dsn=dsn,
    )
    selected_option_id = assessment.private_answer_document.correct_option_id
    if not correct:
        selected_option_id = next(
            option.option_id
            for option in assessment.public_document.options
            if option.option_id != selected_option_id
        )
    return submit_answer(
        learner,
        study_session.study_session_id,
        assessment.assessment_revision,
        assessment.question_id,
        selected_option_id,
        f"answer-{concept_index}-{claim_index}-{sequence}",
        dsn=dsn,
    )


def test_no_data_and_one_answer_remain_conservative_and_deterministic(
    state_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        state_database_dsn
    )
    empty = derive_learning_state(
        learner, study_session.study_session_id, dsn=state_database_dsn
    )
    replay = derive_learning_state(
        learner, study_session.study_session_id, dsn=state_database_dsn
    )
    assert replay == empty
    assert empty.event_watermark == 0
    assert empty.all_mastered is False
    assert all(
        state.status == "not_started"
        and state.mastery_band == "no_evidence"
        and state.confidence == "none"
        and state.needs_more_data
        for state in empty.concept_states
    )

    submitted = _answer(
        state_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=True,
        sequence=1,
    )
    one_answer = derive_learning_state(
        learner, study_session.study_session_id, dsn=state_database_dsn
    )
    current = one_answer.concept_states[0]
    assert one_answer.state_revision != empty.state_revision
    assert one_answer.event_watermark == 1
    assert current.status == "learning"
    assert current.mastery_band == "developing"
    assert current.confidence == "limited"
    assert current.needs_more_data is True
    assert current.reason_code == "MORE_ATTEMPTS_REQUIRED"
    assert current.source_answer_event_ids == [submitted.event.answer_event_id]


def test_single_claim_requires_two_distinct_correct_items(
    state_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        state_database_dsn
    )
    _answer(
        state_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=True,
        sequence=1,
    )
    _answer(
        state_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=True,
        sequence=2,
    )
    snapshot = derive_learning_state(
        learner, study_session.study_session_id, dsn=state_database_dsn
    )
    current = snapshot.concept_states[0]
    assert current.status == "mastered"
    assert current.mastery_band == "demonstrated"
    assert current.confidence == "supported"
    assert current.needs_more_data is False
    assert current.valid_attempts == current.correct_attempts == 2
    assert current.distinct_item_attempts == 2
    assert current.reason_code == "MASTERY_DEMONSTRATED"


def test_wrong_mixed_and_post_error_improvement_keep_history(
    state_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        state_database_dsn
    )
    _answer(
        state_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=False,
        sequence=1,
    )
    wrong = derive_learning_state(
        learner, study_session.study_session_id, dsn=state_database_dsn
    ).concept_states[0]
    assert wrong.status == "needs_review"
    assert wrong.recent_result == "incorrect"
    assert wrong.repeated_error is False

    _answer(
        state_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=True,
        sequence=2,
    )
    improved = derive_learning_state(
        learner, study_session.study_session_id, dsn=state_database_dsn
    ).concept_states[0]
    assert improved.status == "learning"
    assert improved.post_error_improvement is True
    assert improved.valid_attempts == 2
    assert improved.correct_attempts == 1
    assert improved.source_event_numbers == [1, 2]
    assert improved.reason_code == "DISTINCT_ITEM_EVIDENCE_REQUIRED"


def test_repeated_wrong_is_needs_review_without_more_data_claim(
    state_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        state_database_dsn
    )
    for sequence in (1, 2):
        _answer(
            state_database_dsn,
            learner,
            knowledge_map,
            study_session,
            correct=False,
            sequence=sequence,
        )
    current = derive_learning_state(
        learner, study_session.study_session_id, dsn=state_database_dsn
    ).concept_states[0]
    assert current.status == "needs_review"
    assert current.repeated_error is True
    assert current.needs_more_data is False
    assert current.confidence == "supported"
    assert current.reason_code == "LATEST_CLAIM_RESULT_INCORRECT"


def test_multi_claim_coverage_and_latest_result_gate_mastery(
    state_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        state_database_dsn, _multi_claim_map()
    )
    _answer(
        state_database_dsn,
        learner,
        knowledge_map,
        study_session,
        claim_index=0,
        correct=True,
        sequence=1,
    )
    _answer(
        state_database_dsn,
        learner,
        knowledge_map,
        study_session,
        claim_index=0,
        correct=True,
        sequence=2,
    )
    incomplete = derive_learning_state(
        learner, study_session.study_session_id, dsn=state_database_dsn
    ).concept_states[0]
    assert incomplete.status == "learning"
    assert incomplete.claim_coverage_complete is False
    assert incomplete.reason_code == "CLAIM_COVERAGE_INCOMPLETE"

    _answer(
        state_database_dsn,
        learner,
        knowledge_map,
        study_session,
        claim_index=1,
        correct=True,
        sequence=3,
    )
    mastered = derive_learning_state(
        learner, study_session.study_session_id, dsn=state_database_dsn
    ).concept_states[0]
    assert mastered.status == "mastered"
    assert len(mastered.latest_correct_claim_ids) == 2

    _answer(
        state_database_dsn,
        learner,
        knowledge_map,
        study_session,
        claim_index=0,
        correct=False,
        sequence=4,
    )
    regressed = derive_learning_state(
        learner, study_session.study_session_id, dsn=state_database_dsn
    ).concept_states[0]
    assert regressed.status == "needs_review"
    assert regressed.reason_code == "LATEST_CLAIM_RESULT_INCORRECT"
    assert regressed.latest_correct_claim_ids == [
        knowledge_map["formal_concepts"][0]["claims"][1]["claim_id"]
    ]


def test_all_mastered_and_new_session_are_isolated(state_database_dsn: str):
    learner, knowledge_map, material_id, study_session = _state_session(
        state_database_dsn
    )
    sequence = 0
    for concept_index in range(len(knowledge_map["formal_concepts"])):
        for _ in range(2):
            sequence += 1
            _answer(
                state_database_dsn,
                learner,
                knowledge_map,
                study_session,
                concept_index=concept_index,
                correct=True,
                sequence=sequence,
            )
    complete = derive_learning_state(
        learner, study_session.study_session_id, dsn=state_database_dsn
    )
    assert complete.all_mastered is True
    assert complete.event_watermark == 6
    assert all(state.status == "mastered" for state in complete.concept_states)

    new_session = create_study_session(
        learner,
        material_id,
        knowledge_map["revision"],
        str(uuid4()),
        current_formal_concept_id=knowledge_map["formal_concepts"][0][
            "formal_concept_id"
        ],
        dsn=state_database_dsn,
    )
    isolated = derive_learning_state(
        learner, new_session.study_session_id, dsn=state_database_dsn
    )
    assert isolated.event_watermark == 0
    assert isolated.all_mastered is False
    assert all(
        state.status == "not_started" for state in isolated.concept_states
    )


def test_wrong_owner_and_tampered_event_binding_fail_closed(
    state_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        state_database_dsn
    )
    outsider = TrustedLearner(uuid4())
    with pytest.raises(LearningStateError, match="LEARNING_STATE_UNAVAILABLE"):
        derive_learning_state(
            outsider, study_session.study_session_id, dsn=state_database_dsn
        )

    submitted = _answer(
        state_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=True,
        sequence=1,
    )
    with psycopg.connect(state_database_dsn) as connection:
        connection.execute(
            "UPDATE answer_events SET target_claim_id=%s "
            "WHERE answer_event_id=%s",
            ("claim:sha256:" + "9" * 64, submitted.event.answer_event_id),
        )
    with pytest.raises(LearningStateError, match="LEARNING_STATE_UNAVAILABLE"):
        derive_learning_state(
            learner, study_session.study_session_id, dsn=state_database_dsn
        )


def test_current_concept_change_is_map_validated_and_active_only(
    state_database_dsn: str,
):
    learner, _, _, study_session = _state_session(state_database_dsn)
    with pytest.raises(StudySessionError, match="STUDY_SESSION_TARGET_INVALID"):
        set_current_study_concept(
            learner,
            study_session.study_session_id,
            "formal-concept:sha256:" + "9" * 64,
            dsn=state_database_dsn,
        )
    complete_study_session(
        learner, study_session.study_session_id, dsn=state_database_dsn
    )
    with pytest.raises(
        StudySessionError, match="STUDY_SESSION_LIFECYCLE_CONFLICT"
    ):
        set_current_study_concept(
            learner,
            study_session.study_session_id,
            study_session.current_formal_concept_id,
            dsn=state_database_dsn,
        )
