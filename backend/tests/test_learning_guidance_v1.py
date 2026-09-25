from datetime import UTC, datetime
from uuid import uuid4

from learning_adaptation.learner_progress import _next_action
from learning_adaptation.learning_states import ConceptLearningState
from learning_adaptation.map_context import ClaimContext, ConceptContext, MapContext
from learning_adaptation.study_sessions import StoredStudySession


A = "concept:sha256:" + "a" * 64
B = "concept:sha256:" + "b" * 64
CLAIM_A = "claim:sha256:" + "1" * 64
CLAIM_B = "claim:sha256:" + "2" * 64


def _context() -> MapContext:
    return MapContext(
        uuid4(),
        "knowledge-structure:sha256:" + "c" * 64,
        (
            ConceptContext(A, "Foundation", (ClaimContext(CLAIM_A, "A", ()),), ()),
            ConceptContext(B, "Application", (ClaimContext(CLAIM_B, "B", ()),), (A,)),
        ),
        (A, B),
    )


def _state(concept_id: str, status: str) -> ConceptLearningState:
    return ConceptLearningState(
        concept_id=concept_id,
        label=concept_id,
        status=status,
        attempts=0,
        correct_answers=0,
        qualified_correct_items=0,
        covered_claim_ids=[],
        mastered_claim_ids=[],
        weak_claim_ids=[],
        latest_is_correct=None,
    )


def _session(context: MapContext, current: str, *, no_safe=(), deferred=()) -> StoredStudySession:
    return StoredStudySession(
        uuid4(), uuid4(), context.material_id, context.knowledge_structure_revision,
        current, tuple(no_safe), tuple(deferred), "active", datetime.now(UTC), None,
        0, b"x" * 32, b"y" * 32,
    )


def test_prerequisite_gap_advises_without_redirecting_current_concept():
    context = _context()
    study = _session(context, B)
    action = _next_action(context, study, [_state(A, "not_started"), _state(B, "not_started")], cycles=[])
    assert study.current_concept_id == B
    assert action.action == "assess"
    assert action.target_concept_id == B
    assert action.target_claim_id == CLAIM_B
    assert action.reason == "canonical_prerequisite_gap"
    assert action.prerequisite_concept_ids == [A]


def test_no_safe_defer_then_resume_never_mutates_canonical_path():
    context = _context()
    before_path = context.initial_learning_path
    deferred = _next_action(
        context,
        _session(context, A, no_safe=(CLAIM_A,)),
        [_state(A, "not_started"), _state(B, "not_started")], cycles=[],
    )
    assert deferred.action == "defer" and deferred.target_concept_id == B
    resumed = _next_action(
        context,
        _session(context, B, no_safe=(CLAIM_A,), deferred=(A,)),
        [_state(A, "not_started"), _state(B, "mastered")], cycles=[],
    )
    assert resumed.action == "resume" and resumed.target_concept_id == A
    assert context.initial_learning_path == before_path


def test_guidance_moves_past_mastered_claim_after_all_claims_are_covered():
    """A 的兩個重點都已答過，第一個已掌握後應繼續第二個。"""
    from learning_adaptation.answer_events import StoredAnswerEvent
    from learning_adaptation.learning_states import derive_learning_states

    context = _context()
    context = MapContext(context.material_id, context.knowledge_structure_revision, (
        ConceptContext(A, "Foundation", (
            ClaimContext(CLAIM_A, "First", ()), ClaimContext(CLAIM_B, "Second", ()),
        ), ()), context.concepts[1],
    ), context.initial_learning_path)
    session = _session(context, A)
    events = tuple(StoredAnswerEvent(
        uuid4(), session.study_session_id, context.material_id,
        context.knowledge_structure_revision, f"assessment-{n}", f"question-{n}",
        f"semantic-{n}", True, A, claim, (), "correct", True, n,
        datetime.now(UTC), b"x" * 32, b"y" * 32,
    ) for n, claim in enumerate((CLAIM_A, CLAIM_A, CLAIM_B), 1))
    states = derive_learning_states(context, events)
    assert states[0].status == "learning"
    assert states[0].qualified_correct_items == 3
    action = _next_action(context, session, list(states), cycles=[])
    assert action.action == "assess"
    assert action.target_claim_id == CLAIM_B


def test_mastered_prerequisite_removes_advisory_without_changing_target():
    context = _context()
    action = _next_action(context, _session(context, B), [_state(A, "mastered"), _state(B, "learning")], cycles=[])
    assert action.action == "assess" and action.target_claim_id == CLAIM_B
    assert action.target_concept_id == B and action.prerequisite_concept_ids == []
    assert action.reason == "current_concept"


def test_multiple_canonical_prerequisites_do_not_include_other_path_steps():
    from dataclasses import replace

    context = _context()
    extra, unrelated = "concept-extra", "concept-unrelated"
    context = replace(
        context,
        concepts=(
            context.concepts[0],
            replace(context.concepts[1], prerequisite_ids=(A, extra)),
            ConceptContext(extra, "Extra", (ClaimContext("claim-extra", "Extra", ()),), ()),
            ConceptContext(unrelated, "Other", (ClaimContext("claim-other", "Other", ()),), ()),
        ),
        initial_learning_path=(A, extra, unrelated, B),
    )
    states = [_state(concept_id, "not_started") for concept_id in context.initial_learning_path]
    action = _next_action(context, _session(context, B), states, cycles=[])
    assert action.action == "assess"
    assert action.target_concept_id == B
    assert action.prerequisite_concept_ids == [A, extra]
    assert context.initial_learning_path == (A, extra, unrelated, B)


def test_no_safe_still_takes_priority_over_prerequisite_advice():
    context = _context()
    states = [_state(A, "not_started"), _state(B, "learning")]
    action = _next_action(context, _session(context, B, no_safe=(CLAIM_B,)), states, cycles=[])
    assert action.action == "defer"
    assert action.target_concept_id == A
    assert action.prerequisite_concept_ids == []
    assert action.reason == "no_safe_assessment"
    blocked = _next_action(
        context, _session(context, B, no_safe=(CLAIM_B,), deferred=(A,)), states, cycles=[],
    )
    assert blocked.action == "no_safe"


def test_mastered_current_still_advances_or_completes():
    context = _context()
    action = _next_action(
        context, _session(context, A),
        [_state(A, "mastered"), _state(B, "not_started")], cycles=[],
    )
    assert action.action == "advance"
    assert action.target_concept_id == B
    action = _next_action(
        context, _session(context, B),
        [_state(A, "mastered"), _state(B, "mastered")], cycles=[],
    )
    assert action.action == "complete"
    assert action.target_concept_id is None
