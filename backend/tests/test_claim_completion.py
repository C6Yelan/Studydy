from types import SimpleNamespace
from uuid import uuid4

import pytest

from learning_adaptation.learning_states import claim_completion, derive_learning_states
from learning_adaptation.map_context import ClaimContext, ConceptContext, EvidenceContext, MapContext


def event(correct, identity, claim='a'):
    return SimpleNamespace(is_correct=correct, semantic_identity=identity,
                           target_concept_id='concept', target_claim_id=claim,
                           mastery_qualified=True, assisted=False)


@pytest.mark.parametrize('answers,done,streak', [
    ([], False, 0),
    ([(True, 'a')], True, 1),
    ([(False, 'a'), (True, 'b')], False, 1),
    ([(False, 'a'), (True, 'b'), (True, 'b')], False, 1),
    ([(False, 'a'), (True, 'b'), (True, 'c')], True, 2),
    ([(False, 'a'), (True, 'b'), (False, 'c'), (True, 'd')], False, 1),
    ([(False, 'a'), (True, 'b'), (False, 'c'), (True, 'd'), (True, 'e')], True, 2),
    ([(True, 'a'), (False, 'b')], True, 1),
    ([(False, 'a'), (True, 'b'), (True, 'c'), (False, 'd')], True, 2),
])
def test_claim_completion_rules(answers, done, streak):
    assert claim_completion([event(*answer) for answer in answers]) == (done, streak)


def context():
    evidence = (EvidenceContext('source', 1, '來源原文', {}),)
    return MapContext(uuid4(), 'revision', (ConceptContext('concept', '概念', (
        ClaimContext('a', '重點 A', evidence), ClaimContext('b', '重點 B', evidence),
    ), ()),), ('concept',))


def test_partial_and_zero_assessable_claims_are_system_availability():
    source = context()
    state, = derive_learning_states(source, (event(True, 'q'),), unavailable_claim_ids={'b'})
    assert state.status == 'completed'
    assert state.assessable_claim_ids == ['a']
    assert state.unavailable_claim_ids == ['b']
    state, = derive_learning_states(source, (event(False, 'q'),), unavailable_claim_ids={'a', 'b'})
    assert state.status != 'completed'
    assert not state.weak_claim_ids and not state.assessable_claim_ids


def test_remediation_stays_weak_until_two_distinct_correct_and_practice_does_not_revoke():
    events = [event(False, '1'), event(True, '2'), event(True, '3', 'b')]
    state, = derive_learning_states(context(), tuple(events))
    assert state.status == 'needs_review' and state.weak_claim_ids == ['a']
    events.extend([event(True, '4'), event(False, '5')])
    state, = derive_learning_states(context(), tuple(events))
    assert state.status == 'completed' and not state.weak_claim_ids


def test_identical_grounded_claims_share_completion_without_changing_identity():
    source = context()
    first = source.concepts[0].claims[0]
    source = MapContext(source.material_id, source.knowledge_structure_revision,
                        (ConceptContext('concept', '概念', (first, ClaimContext('b', first.text, first.evidence)), ()),),
                        source.initial_learning_path)
    state, = derive_learning_states(source, (event(True, 'q'),))
    assert state.status == 'completed'
    assert state.completed_claim_ids == ['a', 'b']
