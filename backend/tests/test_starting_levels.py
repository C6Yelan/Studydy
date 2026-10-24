"""起始程度的跨概念選材及未完成路線；不呼叫模型。"""
from types import SimpleNamespace

from learning_adaptation.starting_levels import diagnostic_plan, route_order


def sample(count):
    concepts, states = [], []
    for index in range(count):
        identity = f"concept-{index}"
        claims = [f"{identity}-claim-{number}" for number in range(5 if index == count - 1 else 1)]
        concepts.append({"concept_id": identity, "label": identity, "claim_ids": claims,
                         "prerequisite_ids": [], "targets": [{"claim_id": claim,
                         "covered_claim_ids": [claim], "reason": "distinct_grounded_point"} for claim in claims]})
        states.append(SimpleNamespace(concept_id=identity, status="not_started", attempts=0,
            correct_answers=0, completed_claim_ids=[], weak_claim_ids=[], assessable_claim_ids=claims))
    return {"concepts": concepts, "initial_learning_path": [c["concept_id"] for c in concepts]}, states


def test_cross_diagnostic_one_per_concept_max_fifteen_prioritizes_both_difficulties():
    plan, states = sample(18)
    states[-2].attempts, states[-2].correct_answers = 3, 1
    selected = diagnostic_plan(plan, states)
    assert selected["policy"] == "material-diagnostic/v1"
    assert len(selected["targets"]) == 15
    assert len({target["concept_id"] for target in selected["targets"]}) == 15
    assert [target["concept_id"] for target in selected["targets"][:2]] == ["concept-16", "concept-17"]
    assert selected["targets"][1]["claim_id"] == "concept-17-claim-0"


def test_cross_selection_skips_completed_concepts_and_completed_claims():
    plan, states = sample(3)
    states[0].status = "completed"
    states[-1].completed_claim_ids = ["concept-2-claim-0"]
    selected = diagnostic_plan(plan, states)
    assert {target["concept_id"] for target in selected["targets"]} == {"concept-1", "concept-2"}
    assert selected["targets"][0]["claim_id"] == "concept-2-claim-1"
    states[1].assessable_claim_ids = []
    assert [target["concept_id"] for target in diagnostic_plan(plan, states)["targets"]] == ["concept-2"]


def test_diagnostic_correct_partial_concept_follows_wrong_and_untested():
    plan, states = sample(4)
    states[0].attempts = states[0].correct_answers = 1
    states[0].status = "in_progress"
    states[1].attempts, states[1].correct_answers = 1, 0
    states[1].weak_claim_ids = ["concept-1-claim-0"]
    states[1].status = "needs_review"
    assert route_order(plan, states, "familiar", {"concept-0"}) == [
        "concept-1", "concept-2", "concept-3", "concept-0"]
    assert route_order(plan, states, "beginner", {"concept-0"}) == plan["initial_learning_path"]
    assert route_order(plan, states, "some_knowledge", {"concept-0"}) == plan["initial_learning_path"]


def test_gaps_prioritizes_incomplete_weakness_and_difficulty_without_repeating_completed():
    plan, states = sample(5)
    states[1].status, states[1].attempts, states[1].correct_answers = "completed", 4, 1
    states[3].status, states[3].attempts, states[3].correct_answers = "needs_review", 1, 0
    states[3].weak_claim_ids = ["concept-3-claim-0"]
    assert route_order(plan, states, "gaps") == ["concept-3", "concept-4", "concept-0", "concept-2"]
