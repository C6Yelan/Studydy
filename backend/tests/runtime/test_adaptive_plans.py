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
    record_no_safe_assessment,
)
from learning_adaptation.study_sessions import (
    complete_study_session,
    create_study_session,
    set_current_study_concept,
)
from learning_adaptation.learning_states import derive_learning_state
from runtime.storage.migrations import run_migrations
from runtime.learner_session import TrustedLearner
from test_learning_states import _answer, _multi_claim_map, _state_session


@pytest.fixture
def adaptive_database_dsn(
    clean_database_dsn: str, migrations_dir: Path
) -> str:
    assert run_migrations(
        clean_database_dsn, migrations_dir=migrations_dir
    ) == tuple(range(1, 16))
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
    replay = apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        remediation.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    )
    assert replay.study_session == applied.study_session

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
    ][0]["formal_concept_id"]


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


def test_apply_rejects_invalid_foreign_wrong_session_and_completed(
    adaptive_database_dsn: str,
):
    learner, knowledge_map, material_id, study_session = _state_session(
        adaptive_database_dsn
    )
    plan = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    with pytest.raises(AdaptivePlanError, match="ADAPTIVE_PLAN_REQUEST_INVALID"):
        apply_adaptive_plan(
            learner,
            study_session.study_session_id,
            "not-a-plan-revision",
            dsn=adaptive_database_dsn,
        )
    foreign_session = create_study_session(
        learner,
        material_id,
        knowledge_map["revision"],
        str(uuid4()),
        current_formal_concept_id=study_session.current_formal_concept_id,
        dsn=adaptive_database_dsn,
    )
    with pytest.raises(AdaptivePlanError, match="ADAPTIVE_PLAN_STALE"):
        apply_adaptive_plan(
            learner,
            foreign_session.study_session_id,
            plan.adaptive_plan_revision,
            dsn=adaptive_database_dsn,
        )
    with pytest.raises(AdaptivePlanError, match="ADAPTIVE_PLAN_UNAVAILABLE"):
        apply_adaptive_plan(
            TrustedLearner(uuid4()),
            study_session.study_session_id,
            plan.adaptive_plan_revision,
            dsn=adaptive_database_dsn,
        )
    apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        plan.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    )
    changed_current_id = knowledge_map["formal_concepts"][1][
        "formal_concept_id"
    ]
    with psycopg.connect(adaptive_database_dsn) as connection:
        connection.execute(
            "UPDATE study_sessions SET current_formal_concept_id=%s "
            "WHERE study_session_id=%s",
            (changed_current_id, study_session.study_session_id),
        )
    with pytest.raises(AdaptivePlanError, match="ADAPTIVE_PLAN_STALE"):
        apply_adaptive_plan(
            learner,
            study_session.study_session_id,
            plan.adaptive_plan_revision,
            dsn=adaptive_database_dsn,
        )
    complete_study_session(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    with pytest.raises(AdaptivePlanError, match="ADAPTIVE_PLAN_STALE"):
        apply_adaptive_plan(
            learner,
            study_session.study_session_id,
            plan.adaptive_plan_revision,
            dsn=adaptive_database_dsn,
        )


def test_no_safe_defer_and_resume_preserve_events_and_canonical_map(
    adaptive_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        adaptive_database_dsn
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
    current = knowledge_map["formal_concepts"][1]
    next_concept = knowledge_map["formal_concepts"][2]
    set_current_study_concept(
        learner,
        study_session.study_session_id,
        current["formal_concept_id"],
        dsn=adaptive_database_dsn,
    )
    with psycopg.connect(adaptive_database_dsn) as connection:
        before = connection.execute(
            "SELECT document FROM knowledge_maps WHERE map_revision=%s",
            (knowledge_map["revision"],),
        ).fetchone()[0]
        event_count = connection.execute(
            "SELECT count(*) FROM answer_events WHERE study_session_id=%s",
            (study_session.study_session_id,),
        ).fetchone()[0]
    before_learning_state = derive_learning_state(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )

    for claim in current["claims"]:
        recorded = record_no_safe_assessment(
            learner,
            study_session.study_session_id,
            claim["claim_id"],
            current["formal_concept_id"],
            2,
            dsn=adaptive_database_dsn,
        )
    assert recorded.status == "active"
    plan = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert plan.primary_step.action == "defer"
    assert plan.primary_step.target_formal_concept_id == next_concept[
        "formal_concept_id"
    ]
    applied = apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        plan.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    )
    assert applied.study_session.last_event_number == 2
    assert derive_learning_state(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    ) == before_learning_state
    assert applied.study_session.no_safe_deferred_formal_concept_ids == (
        current["formal_concept_id"],
    )
    assert apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        plan.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    ).study_session == applied.study_session

    while_next_concept_is_unfinished = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert (
        while_next_concept_is_unfinished.primary_step.target_formal_concept_id
        == next_concept["formal_concept_id"]
    )
    assert while_next_concept_is_unfinished.primary_step.action == (
        "collect_more_data"
    )

    for sequence in (3, 4):
        _answer(
            adaptive_database_dsn,
            learner,
            knowledge_map,
            study_session,
            concept_index=2,
            correct=True,
            sequence=sequence,
        )
    resume = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert resume.primary_step.action == "resume"
    assert resume.primary_step.target_formal_concept_id == current[
        "formal_concept_id"
    ]
    returned = apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        resume.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    )
    assert returned.study_session.no_safe_deferred_formal_concept_ids == ()
    assert returned.study_session.current_formal_concept_id == current[
        "formal_concept_id"
    ]
    with psycopg.connect(adaptive_database_dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM answer_events WHERE study_session_id=%s",
            (study_session.study_session_id,),
        ).fetchone()[0] == event_count + 2
        assert connection.execute(
            "SELECT document FROM knowledge_maps WHERE map_revision=%s",
            (knowledge_map["revision"],),
        ).fetchone()[0] == before


def test_partial_no_safe_claim_change_makes_adaptive_plan_stale(
    adaptive_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        adaptive_database_dsn, _multi_claim_map()
    )
    old_plan = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    current = knowledge_map["formal_concepts"][0]

    record_no_safe_assessment(
        learner,
        study_session.study_session_id,
        current["claims"][0]["claim_id"],
        current["formal_concept_id"],
        0,
        dsn=adaptive_database_dsn,
    )

    new_plan = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert new_plan.primary_step == old_plan.primary_step
    assert new_plan.adaptive_plan_revision != old_plan.adaptive_plan_revision
    with pytest.raises(AdaptivePlanError, match="ADAPTIVE_PLAN_STALE"):
        apply_adaptive_plan(
            learner,
            study_session.study_session_id,
            old_plan.adaptive_plan_revision,
            dsn=adaptive_database_dsn,
        )
    assert apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        new_plan.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    ).plan == new_plan


def test_multiple_no_safe_defers_follow_path_and_exclude_active_deferred(
    adaptive_database_dsn: str,
):
    learner, knowledge_map, material_id, study_session = _state_session(
        adaptive_database_dsn
    )
    path_ids = [
        step["formal_concept_id"]
        for step in knowledge_map["initial_learning_path"]
    ]
    concepts = {
        concept["formal_concept_id"]: concept
        for concept in knowledge_map["formal_concepts"]
    }
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
    with psycopg.connect(adaptive_database_dsn) as connection:
        before_map = connection.execute(
            "SELECT document FROM knowledge_maps WHERE map_revision=%s",
            (knowledge_map["revision"],),
        ).fetchone()[0]

    path_hash = None
    for current_id, next_id in zip(path_ids[:2], path_ids[1:]):
        current = concepts[current_id]
        for claim in current["claims"]:
            record_no_safe_assessment(
                learner,
                study_session.study_session_id,
                claim["claim_id"],
                current_id,
                2,
                dsn=adaptive_database_dsn,
            )
        plan = derive_adaptive_plan(
            learner,
            study_session.study_session_id,
            dsn=adaptive_database_dsn,
        )
        assert plan.primary_step.action == "defer"
        assert plan.primary_step.target_formal_concept_id == next_id
        if path_hash is None:
            path_hash = plan.inline_initial_learning_path_sha256
        else:
            assert plan.inline_initial_learning_path_sha256 == path_hash
        applied = apply_adaptive_plan(
            learner,
            study_session.study_session_id,
            plan.adaptive_plan_revision,
            dsn=adaptive_database_dsn,
        )
        assert applied.study_session.current_formal_concept_id == next_id

    assert applied.study_session.no_safe_deferred_formal_concept_ids == tuple(
        path_ids[:2]
    )
    last = concepts[path_ids[2]]
    for claim in last["claims"]:
        record_no_safe_assessment(
            learner,
            study_session.study_session_id,
            claim["claim_id"],
            last["formal_concept_id"],
            2,
            dsn=adaptive_database_dsn,
        )
    resume = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert resume.primary_step.action == "resume"
    assert resume.primary_step.target_formal_concept_id == path_ids[0]
    resumed = apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        resume.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    )
    assert resumed.study_session.current_formal_concept_id == path_ids[0]
    assert resumed.study_session.no_safe_deferred_formal_concept_ids == (
        path_ids[1],
        path_ids[2],
    )
    assert resumed.study_session.last_event_number == 2
    for claim in concepts[path_ids[0]]["claims"]:
        record_no_safe_assessment(
            learner,
            study_session.study_session_id,
            claim["claim_id"],
            path_ids[0],
            2,
            dsn=adaptive_database_dsn,
        )
    active_deferred_excluded = derive_adaptive_plan(
        learner, study_session.study_session_id, dsn=adaptive_database_dsn
    )
    assert active_deferred_excluded.primary_step.action == "resume"
    assert active_deferred_excluded.primary_step.target_formal_concept_id == (
        path_ids[1]
    )
    resumed_again = apply_adaptive_plan(
        learner,
        study_session.study_session_id,
        active_deferred_excluded.adaptive_plan_revision,
        dsn=adaptive_database_dsn,
    )
    assert resumed_again.study_session.no_safe_deferred_formal_concept_ids == (
        path_ids[0],
        path_ids[2],
    )

    blocked = create_study_session(
        learner,
        material_id,
        knowledge_map["revision"],
        str(uuid4()),
        current_formal_concept_id=path_ids[0],
        dsn=adaptive_database_dsn,
    )
    first = concepts[path_ids[0]]
    for claim in first["claims"]:
        blocked = record_no_safe_assessment(
            learner,
            blocked.study_session_id,
            claim["claim_id"],
            first["formal_concept_id"],
            0,
            dsn=adaptive_database_dsn,
        )
    assert blocked.status == "no_safe"
    blocked_plan = derive_adaptive_plan(
        learner, blocked.study_session_id, dsn=adaptive_database_dsn
    )
    assert blocked_plan.fallback_reason == "NO_SAFE_PREREQUISITE_BLOCKED"
    assert blocked_plan.primary_step.action == "no_action"
    assert blocked_plan.primary_step.target_formal_concept_id is None

    with psycopg.connect(adaptive_database_dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM answer_events WHERE study_session_id=%s",
            (study_session.study_session_id,),
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT document FROM knowledge_maps WHERE map_revision=%s",
            (knowledge_map["revision"],),
        ).fetchone()[0] == before_map
