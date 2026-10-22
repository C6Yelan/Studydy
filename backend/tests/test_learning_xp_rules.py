"""學習活動規則 xp-rules/v2：等級門檻、收聽與概念卡 XP、連續天數、概念首次完成與收聽採計上限。"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from runtime.podcast_progress import credit_limit
from runtime.storage.learning_activity import card_xp, credited_marks, first_completions, level_for, listening_xp, streak_days

TAIPEI = ZoneInfo("Asia/Taipei")


def test_level_n_needs_100_times_n_xp():
    assert level_for(0) == (1, 0, 100)
    assert level_for(99) == (1, 99, 100)
    assert level_for(100) == (2, 0, 200)
    assert level_for(299) == (2, 199, 200)
    assert level_for(300) == (3, 0, 300)
    assert level_for(600) == (4, 0, 400)


def test_listening_xp_counts_full_five_minutes_up_to_60_a_day():
    assert listening_xp(0) == 0
    assert listening_xp(299) == 0
    assert listening_xp(300) == 5
    assert listening_xp(899) == 10
    assert listening_xp(12 * 300) == 60
    assert listening_xp(86400) == 60


def test_card_marks_count_each_card_once_a_day_skip_rapid_clicks_and_cap_at_40():
    start = datetime(2026, 10, 21, 23, 59, 50, tzinfo=TAIPEI)
    events = [
        (start, "s", "a"),
        (start + timedelta(seconds=1), "s", "b"),     # 不到 3 秒：算標記，不計分
        (start + timedelta(seconds=4), "s", "b"),     # 隔 3 秒：計分
        (start + timedelta(seconds=8), "s", "a"),     # 同一張卡同一天只算一張
        (start + timedelta(seconds=12), "t", "a2"),   # 移到新版的卡沿用原卡身分；已過午夜，算在隔天
    ]
    origins = {("t", "a2"): ("s", "a")}
    assert credited_marks(events, origins, TAIPEI) == {date(2026, 10, 21): (2, 2), date(2026, 10, 22): (1, 1)}
    assert credited_marks(events[:2], {}, TAIPEI) == {date(2026, 10, 21): (2, 1)}
    assert (card_xp(0), card_xp(3), card_xp(20), card_xp(500)) == (0, 6, 40, 40)


def test_streak_keeps_until_today_ends_and_breaks_on_a_gap():
    today = date(2026, 10, 22)
    days = {today - timedelta(days=n) for n in (1, 2, 3, 5)}
    assert streak_days(days, today) == 3
    assert streak_days(days | {today}, today) == 4
    assert streak_days(days, today + timedelta(days=1)) == 0
    assert streak_days(set(), today) == 0


def _event(concept, claim, correct, question, minute):
    return SimpleNamespace(
        target_concept_id=concept, target_claim_id=claim, is_correct=correct,
        semantic_identity=question, created_at=datetime(2026, 10, 22, 9, minute, tzinfo=TAIPEI),
    )


PLAN = {"concepts": [
    {"concept_id": "a", "targets": [
        {"claim_id": "a1", "covered_claim_ids": ["a1", "a1b"]},
        {"claim_id": "a2", "covered_claim_ids": ["a2"]},
    ]},
    {"concept_id": "b", "targets": [{"claim_id": "b1", "covered_claim_ids": ["b1"]}]},
]}


def test_first_completion_follows_the_claim_completion_rule():
    events = [
        _event("a", "a1", True, "q1", 1),
        # 答錯後要連續答對兩道不同題才完成。
        _event("a", "a2", False, "q2", 2),
        _event("a", "a2", True, "q3", 3),
        _event("b", "b1", True, "q4", 4),
        _event("a", "a2", True, "q5", 5),
        # 完成後再答錯不撤銷，也不改變首次完成時間。
        _event("a", "a2", False, "q6", 6),
    ]
    done = first_completions(PLAN, events, set())
    assert done == {"a": events[4].created_at, "b": events[3].created_at}
    # 覆蓋重點（a1b）的作答也算該重點的歷史。
    covered = first_completions(PLAN, [_event("a", "a1b", True, "q", 1), _event("a", "a2", True, "r", 2)], set())
    assert covered["a"].minute == 2


def test_unavailable_points_are_skipped_and_empty_concepts_never_complete():
    events = [_event("a", "a1", True, "q1", 1)]
    assert first_completions(PLAN, events, {"a2"}) == {"a": events[0].created_at}
    assert first_completions(PLAN, [_event("b", "b1", True, "q", 1)], {"b1"}) == {}


def test_listening_credit_is_bounded_by_elapsed_time_report_limit_and_length():
    now = datetime(2026, 10, 22, 9, 0, tzinfo=TAIPEI)
    assert credit_limit(15, 600, now - timedelta(seconds=15), now) == 15
    # 重送或兩個分頁同時回報：只採計實際經過時間（另加 2 秒寬限）。
    assert credit_limit(15, 600, now, now) == 2
    assert credit_limit(15, 600, now - timedelta(seconds=5), now) == 7
    # 第一次回報或久未回報時以單次上限為準。
    assert credit_limit(3000, 600, None, now) == 60
    assert credit_limit(3000, 600, now - timedelta(hours=3), now) == 60
    # 不超過集長的兩倍（0.5 倍速）。
    assert credit_limit(50, 10, None, now) == 22
    assert credit_limit(0, 600, None, now) == 0
