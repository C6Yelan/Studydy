"""逐重點目標計數只使用真正作答，概念級承接只形成手動複習提示。"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from learning_adaptation.goal_metrics import measure_targets


def plan():
    return {"concepts": [
        {"concept_id": "concept", "label": "概念", "targets": [
            {"claim_id": "a", "covered_claim_ids": ["a", "same-a"]},
            {"claim_id": "b", "covered_claim_ids": ["b"]},
        ]},
        {"concept_id": "other", "label": "另一概念", "targets": [
            {"claim_id": "a", "covered_claim_ids": ["a"]},
        ]},
    ]}


def event(correct, identity, at, *, claim="a", concept="concept"):
    return SimpleNamespace(is_correct=correct, semantic_identity=identity, created_at=at,
                           answer_event_id=uuid4(), target_claim_id=claim, target_concept_id=concept)


def test_count_targets_not_claim_aliases_or_other_concepts_sharing_claim_id():
    now = datetime(2026, 10, 24, tzinfo=UTC)
    result = measure_targets(plan(), [event(True, "first", now, claim="same-a")])
    assert result["assessable_points"] == 3
    assert result["completed_points"] == 1
    assert result["concepts"][0]["remaining_points"] == 1
    assert result["concepts"][1]["remaining_points"] == 1


def test_cutoff_uses_existing_distinct_question_completion_rule():
    now = datetime(2026, 10, 24, tzinfo=UTC)
    events = [event(False, "wrong", now), event(True, "same", now + timedelta(hours=1)),
              event(True, "same", now + timedelta(hours=2)), event(True, "different", now + timedelta(hours=3))]
    assert measure_targets(plan(), events, through=now + timedelta(hours=2))["completed_points"] == 0
    assert measure_targets(plan(), events, through=now + timedelta(hours=3))["completed_points"] == 1
    events.append(event(False, "later-wrong", now + timedelta(hours=4)))
    assert measure_targets(plan(), events)["completed_points"] == 1


def test_unavailable_points_are_excluded_and_concept_completion_does_not_invent_answers():
    result = measure_targets(plan(), [], {"b"}, completed_concept_ids={"concept"})
    assert result["assessable_points"] == 2
    assert result["completed_points"] == 0
    assert result["manual_review_points"] == 1
    assert result["concepts"][0]["manual_review_points"] == 1
    assert result["concepts"][1]["manual_review_points"] == 0
    unavailable = measure_targets(plan(), [], {"a", "b"})
    assert unavailable["assessable_points"] == unavailable["completed_points"] == 0
