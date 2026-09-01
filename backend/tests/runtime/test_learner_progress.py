from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from learning_adaptation.learner_progress import (
    LearnerProgressError,
    apply_guidance,
    derive_learner_progress,
    record_no_safe_assessment,
)
from learning_adaptation.study_sessions import (
    complete_study_session,
    create_study_session,
    set_current_study_concept,
)
from runtime.storage.migrations import run_migrations
from runtime.learner_session import TrustedLearner
from test_learning_states import _answer, _multi_claim_map, _state_session


@pytest.fixture
def progress_database_dsn(
    clean_database_dsn: str, migrations_dir: Path
) -> str:
    assert run_migrations(
        clean_database_dsn, migrations_dir=migrations_dir
    ) == tuple(range(1, 18))
    return clean_database_dsn


def test_low_data_has_one_consistent_progress_and_guidance(
    progress_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        progress_database_dsn
    )
    plan = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert plan.next_action.action == "collect_more_data"
    assert plan.next_action.target_formal_concept_id == knowledge_map[
        "formal_concepts"
    ][0]["formal_concept_id"]
    assert plan.fallback_reason == "CURRENT_EVIDENCE_INSUFFICIENT"
    assert plan.event_watermark == 0
    assert plan.material_id == study_session.material_id
    assert all(state.valid_attempts == 0 for state in plan.concept_states)
    assert plan.weakness_findings == []


def test_current_needs_review_then_observed_weak_routes_review_then_practice(
    progress_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        progress_database_dsn
    )
    _answer(
        progress_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=False,
        sequence=1,
    )
    review = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert review.next_action.action == "review"
    assert review.fallback_reason == "CURRENT_NEEDS_REVIEW"
    assert review.next_action.route.resource_promotion_id is not None

    _answer(
        progress_database_dsn,
        learner,
        knowledge_map,
        study_session,
        correct=False,
        sequence=2,
    )
    practice = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert practice.next_action.action == "practice"
    assert practice.fallback_reason == "CURRENT_OBSERVED_WEAKNESS"
    assert practice.guidance_revision != review.guidance_revision


def test_canonical_path_start_no_resource_and_stale_revision(
    progress_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        progress_database_dsn
    )
    stale = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    for sequence in (1, 2):
        _answer(
            progress_database_dsn,
            learner,
            knowledge_map,
            study_session,
            concept_index=0,
            correct=True,
            sequence=sequence,
        )
    with pytest.raises(LearnerProgressError, match="LEARNER_PROGRESS_STALE"):
        apply_guidance(
            learner,
            study_session.study_session_id,
            stale.guidance_revision,
            dsn=progress_database_dsn,
        )

    next_concept = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert next_concept.next_action.action == "start"
    assert next_concept.next_action.reason == (
        "依原先的建議學習順序，前往第一個尚未掌握的概念。"
    )
    assert next_concept.next_action.target_formal_concept_id == knowledge_map[
        "formal_concepts"
    ][1]["formal_concept_id"]
    assert next_concept.next_action.route.resource_promotion_id is None
    applied = apply_guidance(
        learner,
        study_session.study_session_id,
        next_concept.guidance_revision,
        dsn=progress_database_dsn,
    )
    assert applied.current_formal_concept_id == knowledge_map[
        "formal_concepts"
    ][1]["formal_concept_id"]


def test_no_current_target_follows_inline_path(progress_database_dsn: str):
    learner, knowledge_map, material_id, _ = _state_session(
        progress_database_dsn
    )
    study_session = create_study_session(
        learner,
        material_id,
        knowledge_map["revision"],
        str(uuid4()),
        dsn=progress_database_dsn,
    )
    plan = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert plan.next_action.action == "follow_path"
    assert plan.next_action.reason == (
        "目前沒有指定目標，從原先建議學習順序中的第一個未掌握概念開始。"
    )
    assert plan.fallback_reason == "NO_CURRENT_TARGET_FOLLOW_PATH"
    assert plan.next_action.target_formal_concept_id == knowledge_map[
        "initial_learning_path"
    ][0]["formal_concept_id"]


def test_all_mastered_has_no_action(progress_database_dsn: str):
    learner, knowledge_map, _, study_session = _state_session(
        progress_database_dsn
    )
    sequence = 0
    for concept_index in range(len(knowledge_map["formal_concepts"])):
        for _ in range(2):
            sequence += 1
            _answer(
                progress_database_dsn,
                learner,
                knowledge_map,
                study_session,
                concept_index=concept_index,
                correct=True,
                sequence=sequence,
            )
    plan = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert plan.next_action.action == "no_action"
    assert plan.next_action.reason == "本次學習中的所有概念都已符合掌握條件。"
    assert plan.next_action.target_formal_concept_id is None
    assert plan.fallback_reason == "ALL_CONCEPTS_MASTERED"


def test_apply_rejects_invalid_foreign_wrong_session_and_completed(
    progress_database_dsn: str,
):
    learner, knowledge_map, material_id, study_session = _state_session(
        progress_database_dsn
    )
    plan = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    with pytest.raises(LearnerProgressError, match="LEARNER_PROGRESS_REQUEST_INVALID"):
        apply_guidance(
            learner,
            study_session.study_session_id,
            "not-a-guidance-revision",
            dsn=progress_database_dsn,
        )
    foreign_session = create_study_session(
        learner,
        material_id,
        knowledge_map["revision"],
        str(uuid4()),
        current_formal_concept_id=study_session.current_formal_concept_id,
        dsn=progress_database_dsn,
    )
    with pytest.raises(LearnerProgressError, match="LEARNER_PROGRESS_STALE"):
        apply_guidance(
            learner,
            foreign_session.study_session_id,
            plan.guidance_revision,
            dsn=progress_database_dsn,
        )
    with pytest.raises(LearnerProgressError, match="LEARNER_PROGRESS_UNAVAILABLE"):
        apply_guidance(
            TrustedLearner(uuid4()),
            study_session.study_session_id,
            plan.guidance_revision,
            dsn=progress_database_dsn,
        )
    apply_guidance(
        learner,
        study_session.study_session_id,
        plan.guidance_revision,
        dsn=progress_database_dsn,
    )
    changed_current_id = knowledge_map["formal_concepts"][1][
        "formal_concept_id"
    ]
    with psycopg.connect(progress_database_dsn) as connection:
        connection.execute(
            "UPDATE study_sessions SET current_formal_concept_id=%s "
            "WHERE study_session_id=%s",
            (changed_current_id, study_session.study_session_id),
        )
    with pytest.raises(LearnerProgressError, match="LEARNER_PROGRESS_STALE"):
        apply_guidance(
            learner,
            study_session.study_session_id,
            plan.guidance_revision,
            dsn=progress_database_dsn,
        )
    complete_study_session(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    with pytest.raises(LearnerProgressError, match="LEARNER_PROGRESS_STALE"):
        apply_guidance(
            learner,
            study_session.study_session_id,
            plan.guidance_revision,
            dsn=progress_database_dsn,
        )


def test_no_safe_defer_and_resume_preserve_events_and_canonical_map(
    progress_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        progress_database_dsn
    )
    for sequence in (1, 2):
        _answer(
            progress_database_dsn,
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
        dsn=progress_database_dsn,
    )
    with psycopg.connect(progress_database_dsn) as connection:
        before = connection.execute(
            "SELECT document, map_revision, document->'initial_learning_path' "
            "FROM knowledge_maps WHERE map_revision=%s",
            (knowledge_map["revision"],),
        ).fetchone()
        event_count = connection.execute(
            "SELECT count(*) FROM answer_events WHERE study_session_id=%s",
            (study_session.study_session_id,),
        ).fetchone()[0]
    before_progress = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )

    for claim in current["claims"]:
        recorded = record_no_safe_assessment(
            learner,
            study_session.study_session_id,
            claim["claim_id"],
            current["formal_concept_id"],
            2,
            dsn=progress_database_dsn,
        )
    assert recorded.status == "active"
    plan = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert plan.next_action.action == "defer"
    assert plan.next_action.target_formal_concept_id == next_concept[
        "formal_concept_id"
    ]
    applied = apply_guidance(
        learner,
        study_session.study_session_id,
        plan.guidance_revision,
        dsn=progress_database_dsn,
    )
    assert applied.event_watermark == 2
    assert applied.concept_states == before_progress.concept_states
    assert applied.no_safe_deferred_formal_concept_ids == [
        current["formal_concept_id"]
    ]
    assert apply_guidance(
        learner,
        study_session.study_session_id,
        plan.guidance_revision,
        dsn=progress_database_dsn,
    ) == applied

    while_next_concept_is_unfinished = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert (
        while_next_concept_is_unfinished.next_action.target_formal_concept_id
        == next_concept["formal_concept_id"]
    )
    assert while_next_concept_is_unfinished.next_action.action == (
        "collect_more_data"
    )

    for sequence in (3, 4):
        _answer(
            progress_database_dsn,
            learner,
            knowledge_map,
            study_session,
            concept_index=2,
            correct=True,
            sequence=sequence,
        )
    resume = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert resume.next_action.action == "resume"
    assert resume.next_action.target_formal_concept_id == current[
        "formal_concept_id"
    ]
    returned = apply_guidance(
        learner,
        study_session.study_session_id,
        resume.guidance_revision,
        dsn=progress_database_dsn,
    )
    assert returned.no_safe_deferred_formal_concept_ids == []
    assert returned.current_formal_concept_id == current[
        "formal_concept_id"
    ]
    with psycopg.connect(progress_database_dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM answer_events WHERE study_session_id=%s",
            (study_session.study_session_id,),
        ).fetchone()[0] == event_count + 2
        assert connection.execute(
            "SELECT document, map_revision, document->'initial_learning_path' "
            "FROM knowledge_maps WHERE map_revision=%s",
            (knowledge_map["revision"],),
        ).fetchone() == before


def test_partial_no_safe_claim_change_makes_guidance_stale(
    progress_database_dsn: str,
):
    learner, knowledge_map, _, study_session = _state_session(
        progress_database_dsn, _multi_claim_map()
    )
    old_plan = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    current = knowledge_map["formal_concepts"][0]

    record_no_safe_assessment(
        learner,
        study_session.study_session_id,
        current["claims"][0]["claim_id"],
        current["formal_concept_id"],
        0,
        dsn=progress_database_dsn,
    )

    new_plan = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert new_plan.next_action == old_plan.next_action
    assert new_plan.guidance_revision != old_plan.guidance_revision
    with pytest.raises(LearnerProgressError, match="LEARNER_PROGRESS_STALE"):
        apply_guidance(
            learner,
            study_session.study_session_id,
            old_plan.guidance_revision,
            dsn=progress_database_dsn,
        )
    assert apply_guidance(
        learner,
        study_session.study_session_id,
        new_plan.guidance_revision,
        dsn=progress_database_dsn,
    ) == new_plan


def test_multiple_no_safe_defers_follow_path_and_exclude_active_deferred(
    progress_database_dsn: str,
):
    learner, knowledge_map, material_id, study_session = _state_session(
        progress_database_dsn
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
            progress_database_dsn,
            learner,
            knowledge_map,
            study_session,
            concept_index=0,
            correct=True,
            sequence=sequence,
        )
    with psycopg.connect(progress_database_dsn) as connection:
        before_map = connection.execute(
            "SELECT document, map_revision, document->'initial_learning_path' "
            "FROM knowledge_maps WHERE map_revision=%s",
            (knowledge_map["revision"],),
        ).fetchone()

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
                dsn=progress_database_dsn,
            )
        plan = derive_learner_progress(
            learner,
            study_session.study_session_id,
            dsn=progress_database_dsn,
        )
        assert plan.next_action.action == "defer"
        assert plan.next_action.target_formal_concept_id == next_id
        if path_hash is None:
            path_hash = plan.inline_initial_learning_path_sha256
        else:
            assert plan.inline_initial_learning_path_sha256 == path_hash
        applied = apply_guidance(
            learner,
            study_session.study_session_id,
            plan.guidance_revision,
            dsn=progress_database_dsn,
        )
        assert applied.current_formal_concept_id == next_id

    assert applied.no_safe_deferred_formal_concept_ids == path_ids[:2]
    last = concepts[path_ids[2]]
    for claim in last["claims"]:
        record_no_safe_assessment(
            learner,
            study_session.study_session_id,
            claim["claim_id"],
            last["formal_concept_id"],
            2,
            dsn=progress_database_dsn,
        )
    resume = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert resume.next_action.action == "resume"
    assert resume.next_action.target_formal_concept_id == path_ids[0]
    resumed = apply_guidance(
        learner,
        study_session.study_session_id,
        resume.guidance_revision,
        dsn=progress_database_dsn,
    )
    assert resumed.current_formal_concept_id == path_ids[0]
    assert resumed.no_safe_deferred_formal_concept_ids == [
        path_ids[1], path_ids[2]
    ]
    assert resumed.event_watermark == 2
    for claim in concepts[path_ids[0]]["claims"]:
        record_no_safe_assessment(
            learner,
            study_session.study_session_id,
            claim["claim_id"],
            path_ids[0],
            2,
            dsn=progress_database_dsn,
        )
    active_deferred_excluded = derive_learner_progress(
        learner, study_session.study_session_id, dsn=progress_database_dsn
    )
    assert active_deferred_excluded.next_action.action == "resume"
    assert active_deferred_excluded.next_action.target_formal_concept_id == (
        path_ids[1]
    )
    resumed_again = apply_guidance(
        learner,
        study_session.study_session_id,
        active_deferred_excluded.guidance_revision,
        dsn=progress_database_dsn,
    )
    assert resumed_again.no_safe_deferred_formal_concept_ids == [
        path_ids[0], path_ids[2]
    ]

    blocked = create_study_session(
        learner,
        material_id,
        knowledge_map["revision"],
        str(uuid4()),
        current_formal_concept_id=path_ids[0],
        dsn=progress_database_dsn,
    )
    first = concepts[path_ids[0]]
    for claim in first["claims"]:
        blocked = record_no_safe_assessment(
            learner,
            blocked.study_session_id,
            claim["claim_id"],
            first["formal_concept_id"],
            0,
            dsn=progress_database_dsn,
        )
    assert blocked.status == "active"
    blocked_plan = derive_learner_progress(
        learner, blocked.study_session_id, dsn=progress_database_dsn
    )
    assert blocked_plan.fallback_reason == "NO_SAFE_ADVANCE"
    assert blocked_plan.next_action.action == "defer"
    assert blocked_plan.next_action.target_formal_concept_id == path_ids[1]

    with psycopg.connect(progress_database_dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM answer_events WHERE study_session_id=%s",
            (study_session.study_session_id,),
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT document, map_revision, document->'initial_learning_path' "
            "FROM knowledge_maps WHERE map_revision=%s",
            (knowledge_map["revision"],),
        ).fetchone() == before_map
