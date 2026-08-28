from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from learning_adaptation.adaptive_plans import (
    AdaptivePlanError,
    apply_adaptive_plan,
    derive_adaptive_plan,
    project_suggestion,
)
from learning_adaptation.study_sessions import (
    create_study_session,
    set_current_study_concept,
)
from runtime.storage.migrations import run_migrations
from test_learning_states import _answer, _state_session


@pytest.fixture
def adaptive_database_dsn(
    clean_database_dsn: str, migrations_dir: Path
) -> str:
    assert run_migrations(
        clean_database_dsn, migrations_dir=migrations_dir
    ) == tuple(range(1, 14))
    return clean_database_dsn


def test_low_data_has_one_collect_step_and_suggestion_only_projects_it(
    adaptive_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        adaptive_database_dsn
    )
    plan = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert plan.primary_step.action == "collect_more_data"
    assert plan.primary_step.target_formal_concept_id == knowledge_map[
        "formal_concepts"
    ][0]["formal_concept_id"]
    assert plan.fallback_reason == "CURRENT_EVIDENCE_INSUFFICIENT"
    assert plan.deferred_formal_concept_id is None

    suggestion = project_suggestion(plan)
    assert suggestion.adaptive_plan_revision == plan.adaptive_plan_revision
    assert suggestion.action == plan.primary_step.action
    assert suggestion.target_formal_concept_id == (
        plan.primary_step.target_formal_concept_id
    )
    assert suggestion.reason == plan.primary_step.reason
    assert suggestion.confidence == plan.primary_step.confidence
    assert suggestion.route == plan.primary_step.route


def test_current_needs_review_then_observed_weak_routes_review_then_practice(
    adaptive_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        adaptive_database_dsn
    )
    _answer(
        adaptive_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=False,
        sequence=1,
    )
    review = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert review.primary_step.action == "review"
    assert review.fallback_reason == "CURRENT_NEEDS_REVIEW"
    assert review.primary_step.route.resource_promotion_id is not None

    _answer(
        adaptive_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=False,
        sequence=2,
    )
    practice = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert practice.primary_step.action == "practice"
    assert practice.fallback_reason == "CURRENT_OBSERVED_WEAKNESS"
    assert practice.adaptive_plan_revision != review.adaptive_plan_revision


def test_prerequisite_remediation_returns_deferred_target_without_map_mutation(
    adaptive_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        adaptive_database_dsn
    )
    prerequisite = knowledge_map["formal_concepts"][0]
    deferred_target = knowledge_map["formal_concepts"][1]
    set_current_study_concept(
        learner,
        study_session.study_session_id,
        deferred_target["formal_concept_id"],
        dsn=adaptive_database_dsn,
    )
    with psycopg.connect(adaptive_database_dsn) as connection:
        before_map = connection.execute(
            "SELECT document FROM knowledge_maps WHERE map_revision=%s",
            (knowledge_map["revision"],),
        ).fetchone()[0]

    remediation = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert remediation.primary_step.action == "relearn_prerequisite"
    assert remediation.primary_step.reason == (
        "先補強尚未掌握、需要先理解的基礎概念，再回到目前目標。"
    )
    assert remediation.primary_step.target_formal_concept_id == prerequisite[
        "formal_concept_id"
    ]
    assert remediation.current_formal_concept_id == deferred_target[
        "formal_concept_id"
    ]
    assert remediation.primary_step.route.resource_promotion_id is not None
    applied = apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        remediation.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    )
    assert applied.study_session.current_formal_concept_id == prerequisite[
        "formal_concept_id"
    ]
    assert applied.study_session.deferred_formal_concept_id == deferred_target[
        "formal_concept_id"
    ]
    with pytest.raises(AdaptivePlanError, match="ADAPTIVE_PLAN_STALE"):
        apply_adaptive_plan(
            learner,
            study_session.study_session_id,
            remediation.adaptive_plan_revision,
            dsn=adaptive_database_dsn,
        )

    for sequence in (1, 2):
        _answer(
            adaptive_database_dsn,
            learner,
            knowledge_map,
            study_session,
            concept_index=0,
            correct=True,
            sequence=sequence,
        )
    return_plan = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert return_plan.primary_step.action == "continue"
    assert return_plan.fallback_reason == "RETURN_DEFERRED_TARGET"
    assert return_plan.primary_step.target_formal_concept_id == deferred_target[
        "formal_concept_id"
    ]
    returned = apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        return_plan.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    )
    assert returned.study_session.current_formal_concept_id == deferred_target[
        "formal_concept_id"
    ]
    assert returned.study_session.deferred_formal_concept_id is None
    with psycopg.connect(adaptive_database_dsn) as connection:
        after_map = connection.execute(
            "SELECT document FROM knowledge_maps WHERE map_revision=%s",
            (knowledge_map["revision"],),
        ).fetchone()[0]
    assert after_map == before_map


def test_canonical_path_start_no_resource_and_stale_revision(
    adaptive_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        adaptive_database_dsn
    )
    stale = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    for sequence in (1, 2):
        _answer(
            adaptive_database_dsn,
            learner,
            knowledge_map,
            study_session,
            concept_index=0,
            correct=True,
            sequence=sequence,
        )
    with pytest.raises(AdaptivePlanError, match="ADAPTIVE_PLAN_STALE"):
        apply_adaptive_plan(
            learner,
            study_session.study_session_id,
            stale.adaptive_plan_revision,
            dsn=adaptive_database_dsn,
        )

    next_concept = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert next_concept.primary_step.action == "start"
    assert next_concept.primary_step.reason == (
        "依原先的建議學習順序，前往第一個尚未掌握的概念。"
    )
    assert next_concept.primary_step.target_formal_concept_id == knowledge_map[
        "formal_concepts"
    ][1]["formal_concept_id"]
    assert next_concept.primary_step.route.resource_promotion_id is None
    suggestion = project_suggestion(next_concept)
    assert suggestion.fallback_reason == (
        "若目前步驟無法繼續，回到原先的建議學習順序。"
    )
    applied = apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        next_concept.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    )
    assert applied.study_session.current_formal_concept_id == knowledge_map[
        "formal_concepts"
    ][1]["formal_concept_id"]


def test_no_current_target_follows_inline_path(adaptive_database_dsn: str):
    learner, knowledge_map, material_id, _ = _state_session(
        adaptive_database_dsn
    )
    study_session = create_study_session(
        learner,
        material_id,
        knowledge_map["revision"],
        str(uuid4()),
        dsn=adaptive_database_dsn,
    )
    plan = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert plan.primary_step.action == "follow_path"
    assert plan.primary_step.reason == (
        "目前沒有指定目標，從原先建議學習順序中的第一個未掌握概念開始。"
    )
    assert plan.fallback_reason == "NO_CURRENT_TARGET_FOLLOW_PATH"
    assert plan.primary_step.target_formal_concept_id == knowledge_map[
        "initial_learning_path"
    ][0]


def test_nested_immediate_remediation_preserves_original_deferred_target(
    adaptive_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        adaptive_database_dsn
    )
    current = knowledge_map["formal_concepts"][1]["formal_concept_id"]
    original_deferred = knowledge_map["formal_concepts"][2][
        "formal_concept_id"
    ]
    set_current_study_concept(
        learner,
        study_session.study_session_id,
        current,
        dsn=adaptive_database_dsn,
    )
    with psycopg.connect(adaptive_database_dsn) as connection:
        connection.execute(
            "UPDATE study_sessions SET deferred_formal_concept_id=%s "
            "WHERE study_session_id=%s",
            (original_deferred, study_session.study_session_id),
        )
    plan = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert plan.primary_step.action == "relearn_prerequisite"
    applied = apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        plan.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    )
    assert applied.study_session.deferred_formal_concept_id == original_deferred


def test_all_mastered_has_no_action(adaptive_database_dsn: str):
    learner, knowledge_map, _, study_session = _state_session(
        adaptive_database_dsn
    )
    sequence = 0
    for concept_index in range(len(knowledge_map["formal_concepts"])):
        for _ in range(2):
            sequence += 1
            _answer(
                adaptive_database_dsn,
                learner,
                knowledge_map,
                study_session,
                concept_index=concept_index,
                correct=True,
                sequence=sequence,
            )
    plan = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert plan.primary_step.action == "no_action"
    assert plan.primary_step.reason == "本次學習中的所有概念都已符合掌握條件。"
    assert plan.primary_step.target_formal_concept_id is None
    assert plan.fallback_reason == "ALL_CONCEPTS_MASTERED"
    suggestion = project_suggestion(plan)
    assert suggestion.action == "no_action"
    assert suggestion.fallback_action == "no_action"
