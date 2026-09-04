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
        weak_claim_ids=[],
        latest_is_correct=None,
    )


def _session(context: MapContext, current: str, *, no_safe=(), deferred=()) -> StoredStudySession:
    return StoredStudySession(
        uuid4(), uuid4(), context.material_id, context.knowledge_structure_revision,
        current, tuple(no_safe), tuple(deferred), "active", datetime.now(UTC), None,
        0, b"x" * 32, b"y" * 32,
    )


def test_guidance_uses_only_canonical_prerequisite_for_gap():
    context = _context()
    action = _next_action(context, _session(context, B), [_state(A, "not_started"), _state(B, "not_started")])
    assert action.action == "review_prerequisite"
    assert action.target_concept_id == A
    assert action.prerequisite_concept_ids == [A]


def test_no_safe_defer_then_resume_never_mutates_canonical_path():
    context = _context()
    before_path = context.initial_learning_path
    deferred = _next_action(
        context,
        _session(context, A, no_safe=(CLAIM_A,)),
        [_state(A, "not_started"), _state(B, "not_started")],
    )
    assert deferred.action == "defer" and deferred.target_concept_id == B
    resumed = _next_action(
        context,
        _session(context, B, no_safe=(CLAIM_A,), deferred=(A,)),
        [_state(A, "not_started"), _state(B, "mastered")],
    )
    assert resumed.action == "resume" and resumed.target_concept_id == A
    assert context.initial_learning_path == before_path
