from __future__ import annotations

import json
from pathlib import Path

from runtime.api.models import (
    AdaptiveResponseView,
    AnswerFeedbackView,
    ApiErrorView,
    AssessmentView,
    LearningStateView,
    StudySessionView,
    WeaknessView,
)


def test_phase06_public_fixtures_are_strict_and_contain_no_private_fields():
    path = Path(__file__).parent / "fixtures" / "phase06-public-fixtures-v1.json"
    fixtures = json.loads(path.read_text(encoding="utf-8"))
    assert fixtures["schema"] == "phase06-public-fixtures/v1"
    validators = {
        "success": AssessmentView,
        "low_data": LearningStateView,
        "weakness": WeaknessView,
        "prerequisite_gap": AdaptiveResponseView,
        "reassessment": AnswerFeedbackView,
        "completed": StudySessionView,
        "stale": ApiErrorView,
        "failure": ApiErrorView,
    }
    for name, model in validators.items():
        model.model_validate_json(
            json.dumps(fixtures[name], ensure_ascii=False)
        )
    encoded = json.dumps(fixtures, ensure_ascii=False)
    for private_field in (
        "correct_option_id",
        "private_answer",
        "generation_provenance",
        "source_answer_event_ids",
        "supporting_answer_event_ids",
        "entailment",
    ):
        assert private_field not in encoded
