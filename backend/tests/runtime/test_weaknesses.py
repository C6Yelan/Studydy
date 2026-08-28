from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from learning_adaptation.learning_states import derive_learning_state
from learning_adaptation.map_context import read_map_context
from learning_adaptation.study_sessions import set_current_study_concept
from learning_adaptation.weaknesses import (
    WeaknessError,
    _immediate_prerequisite_gaps,
    derive_weakness,
)
from runtime.learner_session import TrustedLearner
from runtime.storage.migrations import run_migrations
from test_learning_states import _answer, _state_session


@pytest.fixture
def weakness_database_dsn(
    clean_database_dsn: str, migrations_dir: Path
) -> str:
    assert run_migrations(
        clean_database_dsn, migrations_dir=migrations_dir
    ) == tuple(range(1, 14))
    return clean_database_dsn


def test_no_data_stays_in_learning_state_without_guessing_weakness_cards(
    weakness_database_dsn: str,
):
    learner, _, _, study_session = _state_session(weakness_database_dsn)
    snapshot = derive_weakness(
        learner, study_session.study_session_id, dsn=weakness_database_dsn
    )
    assert snapshot == derive_weakness(
        learner, study_session.study_session_id, dsn=weakness_database_dsn
    )
    assert snapshot.findings == []
    assert snapshot.immediate_prerequisite_gaps == []


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


def test_only_unmastered_published_immediate_prerequisite_forms_gap(
    weakness_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        weakness_database_dsn
    )
    prerequisite = knowledge_map["formal_concepts"][0]
    target = knowledge_map["formal_concepts"][1]
    set_current_study_concept(
        learner,
        study_session.study_session_id,
        target["formal_concept_id"],
        dsn=weakness_database_dsn,
    )
    before = derive_weakness(
        learner, study_session.study_session_id, dsn=weakness_database_dsn
    )
    assert len(before.immediate_prerequisite_gaps) == 1
    gap = before.immediate_prerequisite_gaps[0]
    assert gap.target_formal_concept_id == target["formal_concept_id"]
    assert gap.prerequisite_formal_concept_id == prerequisite[
        "formal_concept_id"
    ]
    assert gap.prerequisite_status == "not_started"
    assert gap.supporting_answer_event_ids == []
    assert gap.reason == "目前目標有一個尚未掌握、需要先理解的基礎概念。"

    for sequence in (1, 2):
        _answer(
            weakness_database_dsn,
            learner,
            knowledge_map,
            study_session,
            concept_index=0,
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


def test_contains_related_and_cycle_edges_never_form_prerequisite_gap(
    weakness_database_dsn: str,
):
    learner, knowledge_map, material_id, study_session = _state_session(
        weakness_database_dsn
    )
    context = read_map_context(
        learner.learner_id,
        material_id,
        knowledge_map["revision"],
        dsn=weakness_database_dsn,
    )
    learning_state = derive_learning_state(
        learner, study_session.study_session_id, dsn=weakness_database_dsn
    )
    prerequisite = next(
        relation
        for relation in context.relations
        if relation.relation_type == "prerequisite"
    )
    target_id = prerequisite.target_formal_concept_id
    ignored_context = replace(
        context,
        relations=tuple(
            replace(relation, is_in_prerequisite_cycle=True)
            if relation.relation_id == prerequisite.relation_id
            else relation
            for relation in context.relations
        ),
    )
    assert _immediate_prerequisite_gaps(
        ignored_context, learning_state, target_id
    ) == []

    non_prerequisite_context = replace(
        context,
        relations=tuple(
            replace(
                relation,
                target_formal_concept_id=target_id,
            )
            for relation in context.relations
            if relation.relation_type in {"contains", "related"}
        ),
    )
    assert _immediate_prerequisite_gaps(
        non_prerequisite_context, learning_state, target_id
    ) == []


def test_wrong_owner_cannot_read_weakness(weakness_database_dsn: str):
    _, _, _, study_session = _state_session(weakness_database_dsn)
    with pytest.raises(WeaknessError, match="WEAKNESS_UNAVAILABLE"):
        derive_weakness(
            TrustedLearner(uuid4()),
            study_session.study_session_id,
            dsn=weakness_database_dsn,
        )
