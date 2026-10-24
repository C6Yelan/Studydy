"""今日量、原排期落後量與完整概念預覽；沒有模型或持久日程。"""

from copy import deepcopy
from datetime import date, timedelta

from learning_adaptation.goal_planning import build_plan, daily_quota


DAY = date(2026, 10, 25)


def concept(identity, points):
    return {"concept_id": identity, "label": identity, "remaining_points": points}


def inputs():
    return {
        "today": DAY, "target_date": DAY + timedelta(days=4), "target_percent": 100,
        "current": {"assessable_points": 10, "completed_points": 0},
        "baseline": {"date": DAY, "assessable_points": 10, "completed_points": 0,
                     "current_completed_points": 0},
        "daily": {"assessable_points": 10, "completed_at_start": 0,
                  "completed_now": 0, "quota_points": 2},
        "concepts": [concept(f"c{i}", 2) for i in range(5)], "manual_review": [],
    }


def test_quota_rounds_percentage_and_remaining_days_up():
    assert daily_quota(3, 0, 60, 2) == 1
    assert daily_quota(3, 1, 80, 2) == 1
    assert daily_quota(7, 0, 100, 5) == 2
    assert daily_quota(7, 3, 100, 0) == 4
    assert daily_quota(0, 0, 100, 2) == 0


def test_completed_today_does_not_receive_another_batch():
    data = inputs()
    data["current"]["completed_points"] = 2
    data["baseline"]["current_completed_points"] = 2
    data["daily"]["completed_now"] = 2
    data["concepts"] = data["concepts"][1:]
    plan = build_plan(**data)
    assert plan["today_stage"] == {
        "date": DAY.isoformat(), "quota_points": 2, "completed_points": 2,
        "remaining_points": 0, "scheduled_points": 0, "done": True, "concepts": [],
    }
    assert plan["remaining_points"] == 8 and not plan["achieved"]
    assert plan["future_stages"][0]["date"] == (DAY + timedelta(days=1)).isoformat()
    assert plan["future_stages"][0]["concepts"][0]["concept_id"] == "c1"


def test_same_day_availability_does_not_recalculate_saved_quota():
    data = inputs()
    data["current"]["assessable_points"] = 50
    data["current"]["completed_points"] = 2
    data["daily"]["completed_now"] = 2
    data["concepts"] = [concept("remaining", 48)]
    plan = build_plan(**data)
    assert plan["today_stage"]["quota_points"] == 2
    assert plan["today_stage"]["done"] and plan["today_stage"]["concepts"] == []
    assert not plan["achieved"] and plan["remaining_points"] == 48


def test_behind_uses_original_ceil_schedule_through_yesterday_and_catches_up_today():
    data = inputs()
    data.update(today=DAY + timedelta(days=2), current={"assessable_points": 7, "completed_points": 3})
    data["baseline"].update(assessable_points=7, current_completed_points=3)
    data["daily"].update(assessable_points=7, completed_at_start=3, completed_now=3)
    data["concepts"] = [concept(f"c{i}", 1) for i in range(4)]
    assert build_plan(**data)["behind_points"] == 1  # 原計畫前兩日為 2+2，不能另算成 3。
    data["current"]["completed_points"] = 4
    data["baseline"]["current_completed_points"] = 4
    data["daily"]["completed_now"] = 4
    data["concepts"] = data["concepts"][1:]
    plan = build_plan(**data)
    assert plan["behind_points"] == 0
    assert plan["today_stage"]["quota_points"] == 2
    assert plan["today_stage"]["remaining_points"] == 1


def test_whole_concept_can_exceed_quota_and_limit_without_being_cut():
    data = inputs()
    data.update(target_date=DAY + timedelta(days=9), current={"assessable_points": 25, "completed_points": 0})
    data["baseline"]["assessable_points"] = 25
    data["daily"].update(assessable_points=25, quota_points=3)
    data["concepts"] = [concept("large", 25)]
    plan = build_plan(**data)
    assert plan["today_stage"]["quota_points"] == 3
    assert plan["today_stage"]["scheduled_points"] == 25
    assert plan["today_stage"]["concepts"] == [{"concept_id": "large", "label": "large", "remaining_points": 25}]
    assert not plan["quota_over_limit"] and plan["scheduled_over_limit"]
    assert plan["daily_limit"] == 20
    assert plan["target_date"] == data["target_date"].isoformat()
    assert plan["future_stages"] == [] and not plan["achieved"]


def test_overdue_goal_keeps_real_remaining_work_without_future_dates():
    data = inputs()
    data.update(target_date=DAY - timedelta(days=1), current={"assessable_points": 4, "completed_points": 1})
    data["baseline"].update(date=DAY - timedelta(days=4), assessable_points=4, current_completed_points=1)
    data["daily"].update(assessable_points=4, completed_at_start=1, completed_now=1, quota_points=3)
    data["concepts"] = [concept("left", 3)]
    plan = build_plan(**data)
    assert (plan["days_remaining"], plan["overdue_days"], plan["behind_points"]) == (0, 1, 3)
    assert plan["today_stage"]["scheduled_points"] == 3 and plan["future_stages"] == []


def test_zero_assessable_points_are_not_an_achievement():
    data = inputs()
    data["current"] = {"assessable_points": 0, "completed_points": 0}
    data["baseline"].update(assessable_points=0)
    data["daily"].update(assessable_points=0, quota_points=0)
    data["concepts"] = []
    plan = build_plan(**data)
    assert plan["target_points"] == 0 and not plan["achieved"]
    assert not plan["today_stage"]["done"] and plan["future_stages"] == []


def test_manual_gap_is_visible_without_inventing_an_automatic_stage():
    data = inputs()
    data["current"] = {"assessable_points": 2, "completed_points": 0}
    data["daily"].update(assessable_points=2, quota_points=1)
    data["concepts"] = []
    data["manual_review"] = [{"concept_id": "carried-without-point-evidence", "label": "carried", "remaining_points": 2}]
    plan = build_plan(**data)
    assert plan["remaining_points"] == 2 and not plan["achieved"]
    assert plan["manual_review"] == data["manual_review"]
    assert plan["today_stage"]["remaining_points"] == 1
    assert plan["today_stage"]["scheduled_points"] == 0 and plan["future_stages"] == []


def test_preview_pages_preserve_order_and_do_not_change_actual_progress():
    data = inputs()
    before = deepcopy(data)
    full = build_plan(**data, preview_days=31)
    first = build_plan(**data, preview_days=2)
    second = build_plan(**data, preview_offset=2, preview_days=2)
    summary = build_plan(**data, preview_days=0)
    assert first["future_stages"] + second["future_stages"] == full["future_stages"]
    assert first["preview_has_more"] and not second["preview_has_more"]
    assert summary["future_stages"] == [] and summary["preview_has_more"]
    assert all(stage["completed_points"] == 0 and not stage["done"] for stage in full["future_stages"])
    assert full["completed_points"] == 0 and not full["achieved"]
    assert data == before


def test_newly_assessable_points_can_fill_fixed_today_quota():
    data = inputs()
    data["current"] = {"assessable_points": 5, "completed_points": 0}
    data["daily"].update(assessable_points=4, quota_points=4)
    data["concepts"] = [concept("restored-extra-point", 4), concept("one-more", 1)]
    plan = build_plan(**data)
    assert plan["today_stage"]["quota_points"] == 4
    assert plan["today_stage"]["scheduled_points"] == 4
    assert [item["concept_id"] for item in plan["today_stage"]["concepts"]] == ["restored-extra-point"]
    data["current"]["completed_points"] = 4
    data["daily"]["completed_now"] = 4
    data["concepts"] = data["concepts"][1:]
    after = build_plan(**data)
    assert after["today_stage"]["quota_points"] == 4
    assert after["today_stage"]["completed_points"] == 4 and after["today_stage"]["done"]
    assert after["today_stage"]["concepts"] == [] and not after["achieved"]


def test_real_completion_is_counted_even_when_nothing_was_assessable_at_day_start():
    data = inputs()
    data["current"] = {"assessable_points": 2, "completed_points": 1}
    data["daily"].update(assessable_points=0, quota_points=0, completed_now=1)
    data["concepts"] = [concept("left", 1)]
    plan = build_plan(**data)
    assert plan["today_stage"]["quota_points"] == 0
    assert plan["today_stage"]["completed_points"] == 1 and plan["today_stage"]["done"]
    assert not plan["achieved"]


def test_current_zero_measurement_preserves_real_today_completion():
    data = inputs()
    data["current"] = {"assessable_points": 0, "completed_points": 0}
    data["daily"].update(assessable_points=4, quota_points=4, completed_now=4)
    data["concepts"] = []
    plan = build_plan(**data)
    assert not plan["achieved"]
    assert plan["today_stage"]["completed_points"] == 4
    assert plan["today_stage"]["done"] and plan["today_stage"]["remaining_points"] == 0
