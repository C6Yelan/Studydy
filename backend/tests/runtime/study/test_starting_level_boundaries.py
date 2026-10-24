"""舊題組補強／重試入口也須遵守跨概念診斷的活動題組邊界。"""
import pytest
from sqlalchemy import select

from assessment_fixtures import concept_fixture, create, finish, answer, supplement, read
from product_fixtures import closed_loop
from learning_adaptation import assessment_sets, starting_levels
from learning_adaptation.assessment_state import AssessmentSetError
from runtime.storage.database import database_session
from runtime.storage.tables import AssessmentSet, AssessmentSetItem


def test_active_cross_diagnostic_blocks_old_remediation_and_retry_waits_for_active_concept(closed_loop):
    fixture = concept_fixture(closed_loop, 1)
    root = create(fixture)
    finish(fixture)
    answer(fixture, root, wrong={1})
    starting_levels.set_level(fixture["learner"], fixture["source"].material_id, "familiar", dsn=fixture["dsn"])
    launched = starting_levels.start_learning(fixture["learner"], fixture["source"].material_id,
        fixture["document"]["revision"], "cross", fixture["settings"], dsn=fixture["dsn"])
    cross = launched["selected_set_id"]
    assert read(fixture, cross)["kind"] == "material_diagnostic"
    assert read(fixture, root)["cycle"]["can_create_remediation"] is False
    with pytest.raises(AssessmentSetError, match="CONFLICT"):
        supplement(fixture, root)
    with pytest.raises(AssessmentSetError, match="ACTIVE"):
        create(fixture, key="cannot-bypass-cross")

    # 合成 provider 失敗狀態；之後其他概念已開始，不能從舊頁重試跨組而重疊執行。
    with database_session(fixture["dsn"]) as db:
        group = db.get(AssessmentSet, cross)
        group.status = "failed"
        group.set_version += 1
        for item in db.scalars(select(AssessmentSetItem).where(AssessmentSetItem.set_id == cross)):
            item.state, item.failure_reason, item.attempts = "failed", "SEMANTIC_SERVICE_UNAVAILABLE", 1
    other_id = next(concept["concept_id"] for concept in fixture["document"]["concepts"]
                    if concept["concept_id"] != fixture["concept"]["concept_id"])
    assessment_sets.create_set(fixture["learner"], fixture["study"].study_session_id, other_id,
                               "other-concept", fixture["settings"], dsn=fixture["dsn"])
    waiting = read(fixture, cross)
    assert waiting["can_retry"] is False
    with pytest.raises(AssessmentSetError, match="CONFLICT"):
        assessment_sets.change_set(fixture["learner"], fixture["study"].study_session_id, cross,
            "retry", waiting["set_version"], "retry-cross", dsn=fixture["dsn"])


@pytest.mark.parametrize("failed", [False, True])
def test_normal_launch_preserves_existing_remediation_or_retry_entry(closed_loop, failed):
    fixture = concept_fixture(closed_loop, 1)
    root = create(fixture)
    if failed:
        with database_session(fixture["dsn"]) as db:
            group = db.get(AssessmentSet, root)
            group.status = "failed"
            for item in db.scalars(select(AssessmentSetItem).where(AssessmentSetItem.set_id == root)):
                item.state, item.failure_reason, item.attempts = "failed", "SEMANTIC_SERVICE_UNAVAILABLE", 1
    else:
        finish(fixture)
        answer(fixture, root, wrong={1})
    starting_levels.set_level(fixture["learner"], fixture["source"].material_id, "some_knowledge", dsn=fixture["dsn"])
    launched = starting_levels.start_learning(fixture["learner"], fixture["source"].material_id,
        fixture["document"]["revision"], "resume-existing", fixture["settings"],
        current_concept_id=fixture["concept"]["concept_id"], dsn=fixture["dsn"])
    assert launched["selected_set_id"] == root
    with database_session(fixture["dsn"]) as db:
        assert len(db.scalars(select(AssessmentSet).where(
            AssessmentSet.study_session_id == fixture["study"].study_session_id)).all()) == 1
    assert read(fixture, root)["status"] == ("failed" if failed else "completed")
