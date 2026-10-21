"""徽章規則 badge-rules/v1：每個徽章的未達、剛好達標與取得日期。"""
from datetime import date, timedelta

import pytest

from runtime.storage.learning_activity import (
    BADGES, derive_badges, level_for, reached_on, run_reached_on, xp_threshold,
)

D1, D2, D3 = date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 20)


def badges(**records):
    values = {
        "answers": {}, "concepts": {}, "materials": {}, "card_reviews": {}, "listening": {},
        "xp": {}, "active": set(), "streak": 0, "level": 1, **records,
    }
    view = derive_badges(**values)
    assert view["rule_version"] == "badge-rules/v1"
    assert [item["badge_id"] for item in view["items"]] == [badge_id for badge_id, _, _ in BADGES]
    return {item["badge_id"]: item for item in view["items"]}


def state(item):
    return item["earned"], item["earned_on"], item["progress"], item["target"]


def test_no_records_earns_nothing_and_shows_zero_progress():
    for badge_id, item in badges().items():
        # 沒有紀錄時為第 1 級，其餘進度為 0。
        assert (item["earned"], item["earned_on"], item["progress"]) == (False, None, int(badge_id == "level_5"))


@pytest.mark.parametrize(("badge_id", "field", "target"), [
    ("first_answer", "answers", 1),
    ("answers_100", "answers", 100),
    ("first_concept", "concepts", 1),
    ("concepts_25", "concepts", 25),
    ("first_material", "materials", 1),
    ("cards_100", "card_reviews", 100),
])
def test_cumulative_badges_are_dated_on_the_day_the_total_reaches_the_target(badge_id, field, target):
    if target > 1:
        below = badges(**{field: {D1: target - 2, D2: 1}})[badge_id]
        assert state(below) == (False, None, target - 1, target)
    # 剛好達標：第二天累計到門檻，取得日期為第二天；之後的紀錄不改變取得日期。
    exact = badges(**{field: {D1: target - 1, D2: 1, D3: 5}} if target > 1 else {field: {D2: 1, D3: 5}})[badge_id]
    assert state(exact) == (True, D2, target, target)


def test_listening_badge_needs_30_full_minutes():
    below = badges(listening={D1: 1000, D2: 799})["listen_30_minutes"]
    assert state(below) == (False, None, 29, 30)
    exact = badges(listening={D1: 1000, D2: 800, D3: 60})["listen_30_minutes"]
    assert state(exact) == (True, D2, 30, 30)


@pytest.mark.parametrize(("badge_id", "length"), [("streak_3", 3), ("streak_7", 7)])
def test_streak_badges_are_dated_on_the_first_run_that_reaches_the_length(badge_id, length):
    start = date(2026, 10, 1)
    # 中間斷一天：前一段只有 length-1 天，不算達標；未達時進度為目前連續天數。
    broken = {start + timedelta(days=n) for n in range(length - 1)}
    later = {start + timedelta(days=length + n) for n in range(2)}
    assert state(badges(active=broken | later, streak=2)[badge_id]) == (False, None, 2, length)
    reached = {start + timedelta(days=n) for n in range(length)}
    # 取得後即使目前已中斷（streak=0）仍保留，日期為第一次達到的那天。
    assert state(badges(active=reached | later, streak=0)[badge_id]) == (True, start + timedelta(days=length - 1), length, length)


def test_level_badge_uses_the_day_cumulative_xp_reaches_level_5():
    assert xp_threshold(5) == 1000 and level_for(999)[0] == 4 and level_for(1000)[0] == 5
    below = badges(xp={D1: 600, D2: 399}, level=4)["level_5"]
    assert state(below) == (False, None, 4, 5)
    exact = badges(xp={D1: 600, D2: 400, D3: 50}, level=5)["level_5"]
    assert state(exact) == (True, D2, 5, 5)
    assert badges(xp={D1: 5000}, level=9)["level_5"]["progress"] == 5


def test_helpers_ignore_order_and_return_none_when_not_reached():
    assert reached_on({D3: 2, D1: 1}, 3) == D3
    assert reached_on({D1: 1}, 2) is None
    assert run_reached_on(set(), 1) is None
    assert run_reached_on({D1}, 1) == D1
