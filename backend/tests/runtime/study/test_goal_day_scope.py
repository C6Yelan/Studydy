"""當日可測範圍由真寫入前凍結，NO_SAFE／重試不重寫已看見的今日量。"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from assessment_fixtures import concept_fixture, create, finish, answer, change, read
from product_fixtures import closed_loop
from learning_adaptation import assessment_sets as sets, answer_events, learning_goals as goals
from runtime.storage.database import database_session
from runtime.storage.tables import LearningGoal, AssessmentSetItem


def _clock(monkeypatch):
    current = [datetime(2030, 1, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(goals, "_now", lambda: current[0])
    monkeypatch.setattr(sets, "_now", lambda: current[0])
    class ClockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz) if tz is not None else current[0].replace(tzinfo=None)
    monkeypatch.setattr(answer_events, "datetime", ClockDatetime)
    return current


def _view(fixture):
    with database_session(fixture["dsn"]) as db:
        return goals.read_goal(db, fixture["learner"].learner_id, fixture["source"].material_id)["goal"]


def _save(fixture, current):
    return goals.set_goal(fixture["learner"], fixture["source"].material_id,
                          (current[0] + timedelta(days=2)).date(), 100, dsn=fixture["dsn"])


def test_no_goal_does_not_replay_goal_metrics_on_assessment_writes(closed_loop, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("NO_GOAL_MUST_NOT_REPLAY_METRICS")
    monkeypatch.setattr(goals, "load_goal_evidence", forbidden)
    fixture = concept_fixture(closed_loop, 1)
    group = create(fixture)
    finish(fixture)
    answer(fixture, group)


def test_no_safe_and_retry_freeze_before_availability_changes(closed_loop, monkeypatch):
    fixture = concept_fixture(closed_loop, 4)
    current = _clock(monkeypatch)
    _save(fixture, current)
    group = create(fixture)
    initial_day = current[0].date()
    current[0] += timedelta(days=1)
    assert _view(fixture)["today_stage"]["quota_points"] == 3
    with database_session(fixture["dsn"]) as db:
        assert db.get(LearningGoal, fixture["source"].material_id).daily_date == initial_day
    work = sets.claim_set_work(dsn=fixture["dsn"])
    assert work is not None and work.set_id == group
    with database_session(fixture["dsn"]) as db:
        rejected_claim = db.get(AssessmentSetItem, (group, work.ordinal)).target_claim_id
    sets._commit_prepared(work, None, "NO_SAFE_ASSESSMENT", dsn=fixture["dsn"])
    after = _view(fixture)
    assert after["assessable_points"] == 4
    assert after["today_stage"]["quota_points"] == 3
    with database_session(fixture["dsn"]) as db:
        goal = db.get(LearningGoal, fixture["source"].material_id)
        assert goal.daily_date == current[0].date()
        assert goal.daily_unavailable_claim_ids == []
    finish(fixture)
    assert read(fixture, group)["status"] == "partial_ready"
    previous_day = current[0].date()
    current[0] += timedelta(days=1)
    assert _view(fixture)["today_stage"]["quota_points"] == 4
    with database_session(fixture["dsn"]) as db:
        assert db.get(LearningGoal, fixture["source"].material_id).daily_date == previous_day
    change(fixture, group, "retry")
    restored = _view(fixture)
    assert restored["assessable_points"] == 5
    assert restored["today_stage"]["quota_points"] == 4
    # 目標量固定四點，但恢復可測重點的真完成也計入今日成果。
    assert restored["today_stage"]["scheduled_points"] == 4
    with database_session(fixture["dsn"]) as db:
        assert db.get(LearningGoal, fixture["source"].material_id).daily_unavailable_claim_ids == [rejected_claim]
    finish(fixture, variant="recovered")
    answer(fixture, group)
    completed = _view(fixture)
    assert completed["today_stage"]["quota_points"] == 4
    assert completed["today_stage"]["completed_points"] == 4
    assert completed["today_stage"]["done"] is True
    assert completed["today_stage"]["concepts"] == []
    # 之後同日某點變成不可測，已真實完成的今日成果也不能倒退。
    create(fixture, key="new-practice-after-completion")
    work = sets.claim_set_work(dsn=fixture["dsn"])
    sets._commit_prepared(work, None, "NO_SAFE_ASSESSMENT", dsn=fixture["dsn"])
    later = _view(fixture)
    assert later["assessable_points"] == 4
    assert later["today_stage"]["completed_points"] == 4
    assert later["today_stage"]["done"] is True


def test_partial_publish_and_new_round_preserve_daily_scope(closed_loop, monkeypatch):
    fixture = concept_fixture(closed_loop, 4)
    current = _clock(monkeypatch)
    _save(fixture, current)
    group = create(fixture)
    finish(fixture, fail={0})
    assert read(fixture, group)["status"] == "partial_ready"
    current[0] += timedelta(days=1)
    before = _view(fixture)
    assert before["assessable_points"] == 5 and before["today_stage"]["quota_points"] == 3
    change(fixture, group, "publish-partial")
    assert _view(fixture)["assessable_points"] == 4
    assert _view(fixture)["today_stage"]["quota_points"] == 3
    answer(fixture, group)
    assert _view(fixture)["today_stage"]["completed_points"] == 3
    assert _view(fixture)["today_stage"]["done"] is True
    current[0] += timedelta(days=1)
    assert _view(fixture)["today_stage"]["quota_points"] == 1
    create(fixture, key="manual-practice")
    restored = _view(fixture)
    assert restored["assessable_points"] == 5
    assert restored["today_stage"]["quota_points"] == 1
    assert restored["today_stage"]["completed_points"] == 0
    assert restored["manual_review"] == [{"concept_id": fixture["concept"]["concept_id"],
        "label": fixture["concept"]["label"], "remaining_points": 1}]
