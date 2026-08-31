from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from learning_adaptation.answer_events import (
    AnswerSubmissionError,
    read_answer_events,
    submit_answer,
)
from learning_adaptation.assessment_items import store_assessment
from learning_adaptation.study_sessions import (
    complete_study_session,
    create_study_session,
)
from runtime.learner_session import TrustedLearner
from runtime.storage.migrations import run_migrations
from test_assessment_items import _documents
from test_study_sessions import _insert_material_map, _knowledge_map


@pytest.fixture
def answer_database_dsn(
    clean_database_dsn: str, migrations_dir: Path
) -> str:
    assert run_migrations(
        clean_database_dsn, migrations_dir=migrations_dir
    ) == tuple(range(1, 15))
    return clean_database_dsn


def _stored_item(dsn: str, *, prompt: str = "Which statement is grounded?"):
    learner = TrustedLearner(uuid4())
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(
        dsn, learner.learner_id, knowledge_map
    )
    target = knowledge_map["formal_concepts"][0]
    study_session = create_study_session(
        learner,
        material_id,
        knowledge_map["revision"],
        str(uuid4()),
        current_formal_concept_id=target["formal_concept_id"],
        dsn=dsn,
    )
    documents = _documents(study_session, knowledge_map, prompt=prompt)
    assessment = store_assessment(
        learner,
        documents.public_document,
        documents.private_answer_document,
        dsn=dsn,
    )
    return learner, knowledge_map, material_id, study_session, assessment


def test_submission_scores_server_side_and_feedback_keeps_answer_private(
    answer_database_dsn: str,
):
    learner, _, material_id, study_session, assessment = _stored_item(
        answer_database_dsn
    )
    public = assessment.public_document
    private = assessment.private_answer_document

    submitted = submit_answer(
        learner,
        study_session.study_session_id,
        assessment.assessment_revision,
        assessment.question_id,
        private.correct_option_id,
        "answer-one",
        dsn=answer_database_dsn,
    )

    assert submitted.event.material_id == material_id
    assert submitted.event.knowledge_map_revision == study_session.knowledge_map_revision
    assert submitted.event.target_formal_concept_id == assessment.target_formal_concept_id
    assert submitted.event.target_claim_id == assessment.target_claim_id
    assert submitted.event.is_correct is True
    assert submitted.event.event_number == 1
    feedback = submitted.feedback.model_dump(mode="json", by_alias=True)
    assert feedback["is_correct"] is True
    assert feedback["rationale"] == private.rationale
    assert feedback["source_evidence_ids"] == private.source_evidence_ids
    assert "correct_option_id" not in json.dumps(feedback)
    assert "private_answer_sha256" not in json.dumps(feedback)
    assert "generation_provenance" not in json.dumps(feedback)
    assert len(read_answer_events(
        learner, study_session.study_session_id, dsn=answer_database_dsn
    )) == 1
    with psycopg.connect(answer_database_dsn) as connection:
        assert connection.execute(
            "SELECT last_event_number FROM study_sessions "
            "WHERE study_session_id=%s",
            (study_session.study_session_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT is_correct FROM answer_events WHERE answer_event_id=%s",
            (submitted.event.answer_event_id,),
        ).fetchone() == (True,)
    assert public.options[0].option_id == private.correct_option_id


def test_wrong_answer_replay_conflict_and_duplicate_are_deterministic(
    answer_database_dsn: str,
):
    learner, _, _, study_session, assessment = _stored_item(
        answer_database_dsn
    )
    wrong_option = next(
        option.option_id
        for option in assessment.public_document.options
        if option.option_id != assessment.private_answer_document.correct_option_id
    )
    first = submit_answer(
        learner,
        study_session.study_session_id,
        assessment.assessment_revision,
        assessment.question_id,
        wrong_option,
        "stable-key",
        dsn=answer_database_dsn,
    )
    replay = submit_answer(
        learner,
        study_session.study_session_id,
        assessment.assessment_revision,
        assessment.question_id,
        wrong_option,
        "stable-key",
        dsn=answer_database_dsn,
    )
    assert replay == first
    assert first.event.is_correct is False

    with pytest.raises(AnswerSubmissionError, match="ANSWER_IDEMPOTENCY_CONFLICT"):
        submit_answer(
            learner,
            study_session.study_session_id,
            assessment.assessment_revision,
            assessment.question_id,
            assessment.private_answer_document.correct_option_id,
            "stable-key",
            dsn=answer_database_dsn,
        )
    with pytest.raises(AnswerSubmissionError, match="ANSWER_ALREADY_SUBMITTED"):
        submit_answer(
            learner,
            study_session.study_session_id,
            assessment.assessment_revision,
            assessment.question_id,
            wrong_option,
            "different-key",
            dsn=answer_database_dsn,
        )


def test_submission_fails_closed_for_owner_session_question_option_and_lifecycle(
    answer_database_dsn: str,
):
    learner, knowledge_map, material_id, study_session, assessment = _stored_item(
        answer_database_dsn
    )
    outsider = TrustedLearner(uuid4())
    _insert_material_map(
        answer_database_dsn, outsider.learner_id, deepcopy(knowledge_map)
    )
    selected = assessment.private_answer_document.correct_option_id

    with pytest.raises(
        AnswerSubmissionError, match="ANSWER_STUDY_SESSION_UNAVAILABLE"
    ):
        submit_answer(
            outsider,
            study_session.study_session_id,
            assessment.assessment_revision,
            assessment.question_id,
            selected,
            "outsider",
            dsn=answer_database_dsn,
        )
    with pytest.raises(AnswerSubmissionError, match="ANSWER_SUBMISSION_STALE"):
        submit_answer(
            learner,
            study_session.study_session_id,
            assessment.assessment_revision,
            "question:sha256:" + "9" * 64,
            selected,
            "wrong-question",
            dsn=answer_database_dsn,
        )
    with pytest.raises(AnswerSubmissionError, match="ANSWER_OPTION_INVALID"):
        submit_answer(
            learner,
            study_session.study_session_id,
            assessment.assessment_revision,
            assessment.question_id,
            "option:sha256:" + "9" * 64,
            "wrong-option",
            dsn=answer_database_dsn,
        )
    complete_study_session(
        learner, study_session.study_session_id, dsn=answer_database_dsn
    )
    with pytest.raises(AnswerSubmissionError, match="ANSWER_SUBMISSION_STALE"):
        submit_answer(
            learner,
            study_session.study_session_id,
            assessment.assessment_revision,
            assessment.question_id,
            selected,
            "completed",
            dsn=answer_database_dsn,
        )

    other_session = create_study_session(
        learner,
        material_id,
        knowledge_map["revision"],
        str(uuid4()),
        current_formal_concept_id=assessment.target_formal_concept_id,
        dsn=answer_database_dsn,
    )
    with pytest.raises(
        AnswerSubmissionError, match="ANSWER_ASSESSMENT_UNAVAILABLE"
    ):
        submit_answer(
            learner,
            other_session.study_session_id,
            assessment.assessment_revision,
            assessment.question_id,
            selected,
            "cross-session",
            dsn=answer_database_dsn,
        )


def test_read_events_detects_scoring_tamper(answer_database_dsn: str):
    learner, _, _, study_session, assessment = _stored_item(
        answer_database_dsn
    )
    submitted = submit_answer(
        learner,
        study_session.study_session_id,
        assessment.assessment_revision,
        assessment.question_id,
        assessment.private_answer_document.correct_option_id,
        "tamper",
        dsn=answer_database_dsn,
    )
    with psycopg.connect(answer_database_dsn) as connection:
        connection.execute(
            "UPDATE answer_events SET is_correct=false WHERE answer_event_id=%s",
            (submitted.event.answer_event_id,),
        )
    with pytest.raises(AnswerSubmissionError, match="ANSWER_EVENT_UNAVAILABLE"):
        read_answer_events(
            learner, study_session.study_session_id, dsn=answer_database_dsn
        )


def test_database_rejects_event_assessment_and_sequence_mismatch(
    answer_database_dsn: str,
):
    learner, _, material_id, study_session, assessment = _stored_item(
        answer_database_dsn
    )
    with psycopg.connect(answer_database_dsn) as connection:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                INSERT INTO answer_events (
                    answer_event_id, study_session_id, material_id,
                    knowledge_map_revision, assessment_revision, question_id,
                    target_formal_concept_id, target_claim_id,
                    selected_option_id, is_correct, event_number,
                    idempotency_key_sha256, request_fingerprint, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, true, 1,
                    decode(%s, 'hex'), decode(%s, 'hex'), statement_timestamp()
                )
                """,
                (
                    uuid4(),
                    study_session.study_session_id,
                    material_id,
                    study_session.knowledge_map_revision,
                    assessment.assessment_revision,
                    "question:sha256:" + "9" * 64,
                    assessment.target_formal_concept_id,
                    assessment.target_claim_id,
                    assessment.private_answer_document.correct_option_id,
                    "1" * 64,
                    "2" * 64,
                ),
            )
