from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from learning_adaptation.study_sessions import set_current_study_concept
from learning_adaptation.weaknesses import (
    WeaknessError,
    derive_weakness,
)
from knowledge_map.prerequisites import build_prerequisite_constraints
from runtime.learner_session import TrustedLearner
from runtime.storage.migrations import run_migrations
from test_learning_states import _answer, _state_session
from test_study_sessions import _knowledge_map


@pytest.fixture
def weakness_database_dsn(
    clean_database_dsn: str, migrations_dir: Path
) -> str:
    assert run_migrations(
        clean_database_dsn, migrations_dir=migrations_dir
) == tuple(range(1, 17))
    return clean_database_dsn


def test_no_data_stays_in_learning_state_without_guessing_weakness_cards(
    weakness_database_dsn: str,
):
    learner, _, _, study_session = _state_session(weakness_database_dsn)
    snapshot = derive_weakness(
        learner, study_session.study_session_id, dsn=weakness_database_dsn
    )
    assert snapshot.immediate_prerequisite_gaps == []
    assert snapshot == derive_weakness(
        learner, study_session.study_session_id, dsn=weakness_database_dsn
    )
    assert snapshot.findings == []


def test_positive_constraint_drives_gap_until_prerequisite_is_mastered(
    weakness_database_dsn: str,
):
    knowledge_map = _knowledge_map()
    prerequisite, target = knowledge_map["formal_concepts"][:2]
    claim = prerequisite["claims"][0]
    constraints, _ = build_prerequisite_constraints(
        [{
            "proposal_id": "agent4-positive",
            "source_formal_concept_id": prerequisite["formal_concept_id"],
            "target_formal_concept_id": target["formal_concept_id"],
            "evidence_bindings": [{
                "owner_formal_concept_id": prerequisite["formal_concept_id"],
                "claim_id": claim["claim_id"],
                "evidence_ids": sorted(claim["evidence_ids"]),
            }],
        }],
        knowledge_map["formal_concepts"],
        {"model_id": "local-nli", "revision": "fixed", "policy": "positive-only/v1"},
        lambda _: True,
    )
    knowledge_map["prerequisite_constraints"] = constraints
    learner, knowledge_map, _, study_session = _state_session(
        weakness_database_dsn, knowledge_map
    )
    set_current_study_concept(
        learner,
        study_session.study_session_id,
        target["formal_concept_id"],
        dsn=weakness_database_dsn,
    )
    before = derive_weakness(
        learner, study_session.study_session_id, dsn=weakness_database_dsn
    )
    assert [
        gap.prerequisite_constraint_id
        for gap in before.immediate_prerequisite_gaps
    ] == [constraints[0]["prerequisite_constraint_id"]]
    prerequisite_index = knowledge_map["formal_concepts"].index(prerequisite)
    for sequence in (1, 2):
        _answer(
            weakness_database_dsn,
            learner,
            knowledge_map,
            study_session,
            concept_index=prerequisite_index,
            correct=True,
            sequence=sequence,
        )
    set_current_study_concept(
        learner,
        study_session.study_session_id,
        target["formal_concept_id"],
        dsn=weakness_database_dsn,
    )
    after = derive_weakness(
        learner, study_session.study_session_id, dsn=weakness_database_dsn
    )
    assert after.immediate_prerequisite_gaps == []


def test_single_wrong_is_needs_review_and_repeated_wrong_is_observed_weak(
    weakness_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        weakness_database_dsn
    )
    first = _answer(
        weakness_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=False,
        sequence=1,
    )
    one_wrong = derive_weakness(
        learner, study_session.study_session_id, dsn=weakness_database_dsn
    )
    current = next(
        finding
        for finding in one_wrong.findings
        if finding.target_formal_concept_id
        == study_session.current_formal_concept_id
    )
    assert current.category == "needs_review"
    assert current.remediation_intent == "review"
    assert current.supporting_answer_event_ids == [
        first.event.answer_event_id
    ]

    second = _answer(
        weakness_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=False,
        sequence=2,
    )
    repeated = derive_weakness(
        learner, study_session.study_session_id, dsn=weakness_database_dsn
    )
    current = next(
        finding
        for finding in repeated.findings
        if finding.target_formal_concept_id
        == study_session.current_formal_concept_id
    )
    assert current.category == "observed_weak"
    assert current.confidence == "supported"
    assert current.remediation_intent == "practice"
    assert current.supporting_answer_event_ids == [
        first.event.answer_event_id,
        second.event.answer_event_id,
    ]
    assert repeated.weakness_revision != one_wrong.weakness_revision


def test_no_prerequisite_constraints_produces_no_synthetic_gap(
    weakness_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        weakness_database_dsn
    )
    target = knowledge_map["formal_concepts"][1]
    set_current_study_concept(
        learner,
        study_session.study_session_id,
        target["formal_concept_id"],
        dsn=weakness_database_dsn,
    )
    snapshot = derive_weakness(
        learner, study_session.study_session_id, dsn=weakness_database_dsn
    )


def test_wrong_owner_cannot_read_weakness(weakness_database_dsn: str):
    _, _, _, study_session = _state_session(weakness_database_dsn)
    with pytest.raises(WeaknessError, match="WEAKNESS_UNAVAILABLE"):
        derive_weakness(
            TrustedLearner(uuid4()),
            study_session.study_session_id,
            dsn=weakness_database_dsn,
        )
