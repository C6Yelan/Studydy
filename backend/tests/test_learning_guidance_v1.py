from datetime import UTC, datetime
from uuid import uuid4

from learning_adaptation.learner_progress import _next_action
from learning_adaptation.learning_states import ConceptLearningState
from learning_adaptation.map_context import ClaimContext, ConceptContext, MapContext, EvidenceContext
from learning_adaptation.study_sessions import StoredStudySession


A = "concept:sha256:" + "a" * 64
B = "concept:sha256:" + "b" * 64
CLAIM_A = "claim:sha256:" + "1" * 64
CLAIM_B = "claim:sha256:" + "2" * 64
EVIDENCE = (EvidenceContext("e", 1, "Grounded content", {}),)


def _context() -> MapContext:
    return MapContext(
        uuid4(),
        "knowledge-structure:sha256:" + "c" * 64,
        (
            ConceptContext(A, "Foundation", (ClaimContext(CLAIM_A, "A", EVIDENCE),), ()),
            ConceptContext(B, "Application", (ClaimContext(CLAIM_B, "B", EVIDENCE),), (A,)),
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
        completed_claim_ids=[],
        assessable_claim_ids=[CLAIM_A if concept_id == A else CLAIM_B],
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


def test_unavailable_concept_can_advance_without_becoming_completed():
    context = _context()
    states = [_state(A, 'not_started').model_copy(update={'assessable_claim_ids': []}),
              _state(B, 'not_started')]
    action = _next_action(context, _session(context, A), states, cycles=[])
    assert action.action == 'advance' and action.target_concept_id == B
    states[1] = _state(B, 'completed')
    action = _next_action(context, _session(context, B), states, cycles=[])
    assert action.action == 'complete'
    assert states[0].status == 'not_started'
    assert context.initial_learning_path == (A, B)


def test_guidance_moves_past_completed_claim_after_all_claims_are_covered():
    """A 的兩個重點都已答過，第一個已完成後應繼續第二個。"""
    from learning_adaptation.answer_events import StoredAnswerEvent
    from learning_adaptation.learning_states import derive_learning_states

    context = _context()
    context = MapContext(context.material_id, context.knowledge_structure_revision, (
        ConceptContext(A, "Foundation", (
            ClaimContext(CLAIM_A, "First", EVIDENCE), ClaimContext(CLAIM_B, "Second", EVIDENCE),
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
    assert states[0].status == "completed"
    assert states[0].qualified_correct_items == 3
    action = _next_action(context, session, list(states), cycles=[])
    assert action.action == "advance"
    assert action.target_concept_id == B


def test_completed_prerequisite_removes_advisory_without_changing_target():
    context = _context()
    action = _next_action(context, _session(context, B), [_state(A, "completed"), _state(B, "in_progress")], cycles=[])
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


def test_practice_cycle_does_not_override_completed_concept():
    context = _context()
    states = [_state(A, 'completed'), _state(B, 'completed')]
    cycle = {'concept_id': B, 'outcome': 'needs_review', 'active_set_id': None}
    action = _next_action(context, _session(context, B), states, [cycle])
    assert action.action == 'complete'
    cycle['active_set_id'] = 'practice'
    assert _next_action(context, _session(context, B), states, [cycle]).action == 'continue_set'


def test_completed_current_still_advances_or_completes():
    context = _context()
    action = _next_action(
        context, _session(context, A),
        [_state(A, "completed"), _state(B, "not_started")], cycles=[],
    )
    assert action.action == "advance"
    assert action.target_concept_id == B
    action = _next_action(
        context, _session(context, B),
        [_state(A, "completed"), _state(B, "completed")], cycles=[],
    )
    assert action.action == "complete"
    assert action.target_concept_id is None
