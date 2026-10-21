"""概念卡複習排程：三個評分、固定階梯與台灣日期邊界。"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from runtime.card_reviews import LADDER, schedule

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 21, 23, 30, tzinfo=TAIPEI)


def midnight(day):
    return datetime(2026, 10, day, tzinfo=TAIPEI)


def state(streak, interval):
    return SimpleNamespace(streak=streak, interval_days=interval)


def test_remembered_climbs_the_ladder_and_is_due_at_taipei_midnight():
    # 台灣 23:30 記得，明天 00:00 就到期，而不是 24 小時後。
    assert schedule(None, "remembered", NOW) == (1, 1, midnight(22))
    assert schedule(state(1, 1), "remembered", NOW) == (2, 3, midnight(24))
    assert schedule(state(2, 3), "remembered", NOW) == (3, 7, midnight(28))
    top = schedule(state(40, 240), "remembered", NOW)
    assert top[1] == LADDER[-1] == 240


def test_forgot_resets_and_returns_within_minutes():
    assert schedule(state(5, 60), "forgot", NOW) == (0, 0, NOW + timedelta(minutes=10))
    assert schedule(None, "forgot", NOW) == (0, 0, NOW + timedelta(minutes=10))


def test_fuzzy_halves_the_interval_but_keeps_progress():
    assert schedule(None, "fuzzy", NOW) == (0, 1, midnight(22))
    assert schedule(state(4, 14), "fuzzy", NOW) == (4, 7, midnight(28))
    assert schedule(state(0, 0), "fuzzy", NOW)[1] == 1
    # 模糊後再記得，從原本的連續次數繼續。
    assert schedule(state(4, 7), "remembered", NOW)[:2] == (5, 30)


def test_utc_input_uses_the_taipei_date():
    utc_now = datetime(2026, 10, 21, 16, 30, tzinfo=ZoneInfo("UTC"))  # 台灣 10/22 00:30
    assert schedule(None, "remembered", utc_now)[2] == midnight(23)
