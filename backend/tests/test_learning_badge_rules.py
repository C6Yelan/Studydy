"""徽章規則 badge-rules/v2：每個徽章（各系列的每一層）的未達、剛好達標與取得日期。"""
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
    assert view["rule_version"] == "badge-rules/v2"
    assert [item["badge_id"] for item in view["items"]] == [badge_id for badge_id, _, _ in BADGES]
    return {item["badge_id"]: item for item in view["items"]}


def state(item):
    return item["earned"], item["earned_on"], item["progress"], item["target"]


def test_no_records_earns_nothing_and_shows_zero_progress():
    for badge_id, item in badges().items():
        # 沒有紀錄時為第 1 級，其餘進度為 0。
        assert (item["earned"], item["earned_on"], item["progress"]) == (False, None, int(badge_id.startswith("level_")))


@pytest.mark.parametrize(("badge_id", "field", "target"), [
    ("first_answer", "answers", 1),
    ("answers_100", "answers", 100),
    ("answers_300", "answers", 300),
    ("answers_1000", "answers", 1000),
    ("first_concept", "concepts", 1),
    ("concepts_25", "concepts", 25),
    ("concepts_100", "concepts", 100),
    ("concepts_300", "concepts", 300),
    ("first_material", "materials", 1),
    ("materials_3", "materials", 3),
    ("materials_10", "materials", 10),
    ("cards_100", "card_reviews", 100),
    ("cards_500", "card_reviews", 500),
    ("cards_2000", "card_reviews", 2000),
])
def test_cumulative_badges_are_dated_on_the_day_the_total_reaches_the_target(badge_id, field, target):
    if target > 1:
        below = badges(**{field: {D1: target - 2, D2: 1}})[badge_id]
        assert state(below) == (False, None, target - 1, target)
    # 剛好達標：第二天累計到門檻，取得日期為第二天；之後的紀錄不改變取得日期。
    exact = badges(**{field: {D1: target - 1, D2: 1, D3: 5}} if target > 1 else {field: {D2: 1, D3: 5}})[badge_id]
    assert state(exact) == (True, D2, target, target)


@pytest.mark.parametrize(("badge_id", "minutes"), [
    ("listen_30_minutes", 30), ("listen_180_minutes", 180), ("listen_600_minutes", 600),
])
def test_listening_badges_need_full_minutes(badge_id, minutes):
    seconds = minutes * 60
    below = badges(listening={D1: 1000, D2: seconds - 1001})[badge_id]
    assert state(below) == (False, None, minutes - 1, minutes)
    exact = badges(listening={D1: 1000, D2: seconds - 1000, D3: 60})[badge_id]
    assert state(exact) == (True, D2, minutes, minutes)


@pytest.mark.parametrize(("badge_id", "length"), [("streak_3", 3), ("streak_7", 7), ("streak_30", 30), ("streak_100", 100)])
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


@pytest.mark.parametrize(("badge_id", "level"), [("level_10", 10), ("level_20", 20)])
def test_higher_level_badges_use_the_same_cumulative_xp_threshold(badge_id, level):
    threshold = xp_threshold(level)
    assert level_for(threshold - 1)[0] == level - 1 and level_for(threshold)[0] == level
    below = badges(xp={D1: threshold - 1}, level=level - 1)[badge_id]
    assert state(below) == (False, None, level - 1, level)
    exact = badges(xp={D1: threshold - 1, D2: 1}, level=level)[badge_id]
    assert state(exact) == (True, D2, level, level)


def test_series_tiers_rise_in_order_and_a_higher_tier_implies_the_lower_ones():
    # 同一計量的門檻由低到高排列；取得高層級時，低層級都已取得且日期不晚於高層級。
    series = {}
    for badge_id, metric, target in BADGES:
        series.setdefault(metric, []).append((badge_id, target))
    for tiers in series.values():
        assert [target for _, target in tiers] == sorted(target for _, target in tiers)
    view = badges(answers={D1: 99, D2: 201, D3: 700})
    assert [view[b]["earned_on"] for b in ("first_answer", "answers_100", "answers_300", "answers_1000")] == [D1, D2, D2, D3]

def test_helpers_ignore_order_and_return_none_when_not_reached():
    assert reached_on({D3: 2, D1: 1}, 3) == D3
    assert reached_on({D1: 1}, 2) is None
    assert run_reached_on(set(), 1) is None
    assert run_reached_on({D1}, 1) == D1
