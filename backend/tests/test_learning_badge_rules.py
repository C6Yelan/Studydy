"""徽章規則 badge-rules/v8：系列徽章的每一層與特殊成就的未達、剛好達標與取得日期。"""
from datetime import date, datetime, timedelta

import pytest

from runtime.learning_activity import (
    BADGES, SPECIAL_BADGES, derive_badges, derive_special_badges, level_for, overcome_difficulties, reached_on,
    run_reached_on, stage_xp, xp_threshold,
)

D1, D2, D3 = date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 20)


def badges(**records):
    values = {
        "answers": {}, "concepts": {}, "materials": {}, "card_marks": {}, "listening": {},
        "xp": {}, "active": set(), "streak": 0, "level": 1, **records,
    }
    view = derive_badges(**values)
    assert view["rule_version"] == "badge-rules/v8"
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
    ("answers_5000", "answers", 5000),
    ("first_concept", "concepts", 1),
    ("concepts_25", "concepts", 25),
    ("concepts_100", "concepts", 100),
    ("concepts_300", "concepts", 300),
    ("concepts_1000", "concepts", 1000),
    ("first_material", "materials", 1),
    ("materials_3", "materials", 3),
    ("materials_10", "materials", 10),
    ("materials_25", "materials", 25),
    ("cards_100", "card_marks", 100),
    ("cards_500", "card_marks", 500),
    ("cards_2000", "card_marks", 2000),
    ("cards_5000", "card_marks", 5000),
    ("first_set", "sets", 1),
    ("sets_10", "sets", 10),
    ("sets_50", "sets", 50),
    ("sets_200", "sets", 200),
    ("first_episode", "episodes", 1),
    ("episodes_10", "episodes", 10),
    ("episodes_50", "episodes", 50),
])
def test_cumulative_badges_are_dated_on_the_day_the_total_reaches_the_target(badge_id, field, target):
    if target > 1:
        below = badges(**{field: {D1: target - 2, D2: 1}})[badge_id]
        assert state(below) == (False, None, target - 1, target)
    # 剛好達標：第二天累計到門檻，取得日期為第二天；之後的紀錄不改變取得日期。
    exact = badges(**{field: {D1: target - 1, D2: 1, D3: 5}} if target > 1 else {field: {D2: 1, D3: 5}})[badge_id]
    assert state(exact) == (True, D2, target, target)


@pytest.mark.parametrize(("badge_id", "minutes"), [
    ("listen_30_minutes", 30), ("listen_180_minutes", 180), ("listen_600_minutes", 600), ("listen_1800_minutes", 1800),
])
def test_listening_badges_need_full_minutes(badge_id, minutes):
    seconds = minutes * 60
    below = badges(listening={D1: 1000, D2: seconds - 1001})[badge_id]
    assert state(below) == (False, None, minutes - 1, minutes)
    exact = badges(listening={D1: 1000, D2: seconds - 1000, D3: 60})[badge_id]
    assert state(exact) == (True, D2, minutes, minutes)


@pytest.mark.parametrize(("badge_id", "length"), [("streak_3", 3), ("streak_7", 7), ("streak_30", 30), ("streak_100", 100), ("streak_365", 365)])
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
    assert xp_threshold(5) == 700 and level_for(699)[0] == 4 and level_for(700)[0] == 5
    below = badges(xp={D1: 600, D2: 99}, level=4)["level_5"]
    assert state(below) == (False, None, 4, 5)
    exact = badges(xp={D1: 600, D2: 100, D3: 50}, level=5)["level_5"]
    assert state(exact) == (True, D2, 5, 5)
    assert badges(xp={D1: 5000}, level=9)["level_5"]["progress"] == 5


def test_active_day_badges_count_days_with_learning_even_without_a_streak():
    start = date(2026, 1, 1)
    every_other = {start + timedelta(days=2 * n) for n in range(7)}
    assert state(badges(active=every_other)["days_7"]) == (True, start + timedelta(days=12), 7, 7)
    assert state(badges(active=set(list(every_other)[:6]))["days_7"])[:3] == (False, None, 6)
    assert badges(active=every_other)["streak_3"]["earned"] is False


@pytest.mark.parametrize(("badge_id", "level"), [("level_10", 10), ("level_15", 15), ("level_20", 20), ("level_30", 30)])
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


def special(**records):
    values = {"events": [], "answers": {}, "active": set(), "questions": {}, "research": {}, "card_sets": [], "podcasts": [], **records}
    items = derive_special_badges(**values)
    assert [item["badge_id"] for item in items] == [badge_id for badge_id, _ in SPECIAL_BADGES]
    return {item["badge_id"]: item for item in items}


def answer(day, hour, correct, material="m1", concept="c1"):
    return (datetime(2026, 10, day, hour), correct, material, concept)


def test_special_badges_start_locked_with_zero_progress():
    for item in special().values():
        assert (item["earned"], item["earned_on"], item["progress"]) == (False, None, 0)


def test_combo_needs_ten_correct_in_a_row_and_keeps_the_best_run_as_progress():
    almost = [answer(16, 12, True)] * 9 + [answer(16, 12, False)] + [answer(17, 12, True)] * 3
    assert state(special(events=almost)["combo_10"]) == (False, None, 9, 10)
    done = almost + [answer(18, 12, True)] * 7
    assert state(special(events=done)["combo_10"]) == (True, date(2026, 10, 18), 10, 10)
    # 成就鏈的下一步：同一段連對延續到 25 題。
    assert state(special(events=done)["combo_25"]) == (False, None, 10, 25)
    longer = done + [answer(19, 12, True)] * 15
    assert state(special(events=longer)["combo_25"]) == (True, date(2026, 10, 19), 25, 25)
    assert special(events=longer)["combo_50"]["progress"] == 25


def test_comeback_counts_a_correct_answer_right_after_a_wrong_one():
    pairs = [answer(16, 12, False), answer(16, 12, True)] * 19 + [answer(16, 12, False), answer(17, 12, False), answer(17, 12, True)]
    assert state(special(events=pairs)["comeback_20"]) == (True, date(2026, 10, 17), 20, 20)
    assert special(events=pairs[:-1])["comeback_20"]["progress"] == 19


def test_daily_fifty_uses_the_first_day_with_fifty_answers():
    assert state(special(answers={D1: 49, D2: 12})["daily_50"]) == (False, None, 49, 50)
    assert state(special(answers={D1: 49, D2: 50, D3: 80})["daily_50"]) == (True, D2, 50, 50)
    assert state(special(answers={D1: 49, D2: 50, D3: 80})["daily_100"]) == (False, None, 80, 100)
    assert state(special(answers={D1: 120, D2: 100})["daily_100"]) == (True, D1, 100, 100)


def test_explorer_counts_distinct_materials_and_dates_the_fifth():
    events = [answer(16, 12, True, material=f"m{n}") for n in range(4)] + [answer(18, 12, False, material="m0"), answer(19, 12, False, material="m9")]
    assert state(special(events=events)["explorer_5"]) == (True, date(2026, 10, 19), 5, 5)
    assert special(events=events)["explorer_10"]["progress"] == 5


def test_night_owl_and_early_bird_use_taiwan_hours():
    events = [answer(16, 4, True)] * 9 + [answer(16, 5, True)] * 10 + [answer(17, 0, False)]
    view = special(events=events)
    assert state(view["night_owl"]) == (True, date(2026, 10, 17), 10, 10)
    assert state(view["early_bird"]) == (True, date(2026, 10, 16), 10, 10)
    # 8 點以後不算早起。
    assert special(events=[answer(16, 8, True)] * 10)["early_bird"]["progress"] == 0


def test_weekend_needs_saturday_and_sunday_of_the_same_weekend():
    friday, saturday, sunday, monday = date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 19)
    assert special(active={friday, sunday, monday})["weekend"]["earned"] is False
    assert state(special(active={saturday, sunday})["weekend"]) == (True, sunday, 1, 1)


def test_return_needs_seven_empty_days_between_activities():
    start = date(2026, 10, 1)
    assert special(active={start, start + timedelta(days=7)})["return"]["earned"] is False
    assert state(special(active={start, start + timedelta(days=8)})["return"]) == (True, start + timedelta(days=8), 1, 1)


def test_curious_researcher_and_creator_follow_their_records():
    view = special(questions={D1: 6, D2: 4}, research={D3: 1}, card_sets=[D2, D1], podcasts=[D3])
    assert state(view["curious_10"]) == (True, D2, 10, 10)
    assert state(view["curious_50"]) == (False, None, 10, 50)
    assert state(view["researcher"]) == (True, D3, 1, 1)
    # 卡組與 Podcast 各一：取兩者第一次完成中較晚的日期。
    assert state(view["creator"]) == (True, D3, 2, 2)
    assert state(special(card_sets=[D1])["creator"]) == (False, None, 1, 2)


def test_corrected_counts_concepts_answered_wrong_then_right():
    events = []
    for n in range(5):
        events += [answer(16 + n, 12, False, concept=f"c{n}"), answer(16 + n, 13, True, concept=f"c{n}")]
    # 答對後又答錯、之後沒再答對的概念不算；同一概念改正兩次也只算一次。
    events = [answer(15, 9, True, concept="x"), answer(15, 10, False, concept="x"),
              answer(15, 11, False, concept="c0"), answer(15, 12, True, concept="c0")] + events
    view = special(events=events)
    # c0 已在 15 日改正，第 5 個是 20 日的 c4。
    assert state(view["corrected_5"]) == (True, date(2026, 10, 20), 5, 5)
    assert special(events=events[:-2])["corrected_5"]["progress"] == 4


@pytest.mark.parametrize(("badge_id", "target"), [("perfect_set_1", 1), ("perfect_set_10", 10), ("perfect_set_30", 30)])
def test_perfect_set_tiers_count_flawless_sets_by_completion_day(badge_id, target):
    below = badges(perfect_sets={D1: target - 1} if target > 1 else {})[badge_id]
    assert state(below) == (False, None, target - 1, target)
    exact = badges(perfect_sets={D1: target - 1, D2: 1, D3: 4} if target > 1 else {D2: 1, D3: 4})[badge_id]
    assert state(exact) == (True, D2, target, target)


def test_first_try_material_needs_every_concept_right_the_first_time_before_completion():
    finished = date(2026, 10, 17)
    clean = [answer(16, 9, True, material="m", concept="a"), answer(16, 10, True, material="m", concept="b"),
             answer(17, 9, False, material="m", concept="a")]
    assert state(special(events=clean, completed={"m": finished})["first_try_material"]) == (True, finished, 1, 1)
    # 任何一個概念第一次答錯就不算；完成之後的作答不影響判定。
    slip = [answer(16, 9, False, material="m", concept="a"), answer(16, 10, True, material="m", concept="a")]
    assert special(events=slip, completed={"m": finished})["first_try_material"]["earned"] is False
    after = [answer(16, 9, True, material="m", concept="a"), answer(18, 9, False, material="m", concept="b")]
    assert special(events=after, completed={"m": finished})["first_try_material"]["earned"] is True
    # 沒有完成的教材不算。
    assert special(events=clean)["first_try_material"]["earned"] is False


def test_perfect_day_needs_twenty_answers_all_correct():
    assert special(answers={D1: 19}, correct={D1: 19})["perfect_day"]["earned"] is False
    assert special(answers={D1: 25}, correct={D1: 24})["perfect_day"]["earned"] is False
    assert state(special(answers={D1: 25, D2: 20}, correct={D1: 24, D2: 20})["perfect_day"]) == (True, D2, 1, 1)


def test_all_round_day_needs_answers_cards_a_minute_of_listening_and_a_question_on_one_day():
    base = {"answers": {D1: 3, D2: 2}, "card_days": {D1, D2}, "listening": {D1: 60, D2: 59}, "questions": {D1: 1, D2: 1}}
    assert state(special(**base)["all_round_day"]) == (True, D1, 1, 1)
    assert special(**{**base, "questions": {D2: 1}})["all_round_day"]["earned"] is False


def test_thousand_pages_counts_a_completed_material_with_at_least_500_pages():
    assert special(completed={"m": D2}, pages={"m": 499})["thousand_pages"]["earned"] is False
    assert state(special(completed={"m": D2, "n": D1}, pages={"m": 1036, "n": 12})["thousand_pages"]) == (True, D2, 1, 1)
    # 頁數夠但還沒完成也不算。
    assert special(pages={"m": 1036})["thousand_pages"]["earned"] is False


def test_revisit_needs_card_marks_seven_days_after_completing_the_material():
    finished = date(2026, 10, 1)
    early = special(completed={"m": finished}, reviews={"m": [finished + timedelta(days=6)]})
    assert early["revisit"]["earned"] is False
    later = special(completed={"m": finished}, reviews={"m": [finished + timedelta(days=6), finished + timedelta(days=9)]})
    assert state(later["revisit"]) == (True, finished + timedelta(days=9), 1, 1)
    # 標記的是別份教材的卡片不算。
    assert special(completed={"m": finished}, reviews={"n": [finished + timedelta(days=9)]})["revisit"]["earned"] is False


def test_perfect_day_reads_daily_correct_counts_even_with_answer_events():
    # 迴圈變數曾覆蓋 correct（每日答對數），有作答事件時「完美的一天」會讀錯型別。
    events = [answer(16, 12, True)] * 20
    view = special(events=events, answers={D1: 20}, correct={D1: 20})
    assert state(view["perfect_day"]) == (True, D1, 1, 1)


def test_xp_day_needs_three_hundred_xp_on_one_day():
    assert state(special(daily_xp={D1: 299, D2: 120})["xp_day_300"]) == (False, None, 299, 300)
    assert state(special(daily_xp={D1: 299, D2: 300, D3: 500})["xp_day_300"]) == (True, D2, 300, 300)


def test_never_give_up_needs_three_misses_before_the_first_correct_answer():
    two = [answer(16, 9, False), answer(16, 10, False), answer(16, 11, True)]
    assert special(events=two)["never_give_up"]["earned"] is False
    # 已經答對過的概念之後再錯再對不算；別的概念第三次錯之後答對才算。
    three = two + [answer(17, 9, False)] * 3 + [answer(17, 10, True)] + [
        answer(18, 9, False, concept="c2"), answer(18, 10, False, concept="c2"),
        answer(18, 11, False, concept="c2"), answer(19, 9, True, concept="c2")]
    assert state(special(events=three)["never_give_up"]) == (True, date(2026, 10, 19), 1, 1)


def test_binge_needs_three_episodes_finished_on_one_day():
    assert state(special(episodes=[D1, D1, D2])["binge_3"]) == (False, None, 2, 3)
    assert state(special(episodes=[D1, D2, D2, D2, D3])["binge_3"]) == (True, D2, 3, 3)


def test_three_islands_needs_three_materials_answered_on_one_day():
    spread = [answer(16, 9, True, material="a"), answer(16, 10, True, material="b"), answer(17, 9, True, material="c")]
    assert state(special(events=spread)["three_islands"]) == (False, None, 2, 3)
    same_day = spread + [answer(17, 10, False, material="a"), answer(17, 11, True, material="b")]
    assert state(special(events=same_day)["three_islands"]) == (True, date(2026, 10, 17), 3, 3)


def test_new_year_needs_activity_on_january_first():
    assert special(active={date(2026, 1, 2), date(2025, 12, 31)})["new_year"]["earned"] is False
    assert state(special(active={date(2026, 1, 1), date(2027, 1, 1)})["new_year"]) == (True, date(2026, 1, 1), 1, 1)


@pytest.mark.parametrize(("badge_id", "metric", "target"), [
    (badge_id, metric, target) for badge_id, metric, target in BADGES
    if metric in {
        "goals", "stages", "difficulties", "notes", "annotations",
        "image_uploads", "audio_uploads", "video_uploads", "youtube_uploads", "exports", "export_kinds",
        "finished_series", "host_pairs", "all_tools", "media_concepts",
    }
])
def test_v7_cumulative_badges_count_real_records_and_date_the_threshold_day(badge_id, metric, target):
    assert state(badges()[badge_id]) == (False, None, 0, target)
    if target > 1:
        assert state(badges(extra={metric: {D1: target - 1}})[badge_id]) == (False, None, target - 1, target)
    view = badges(extra={metric: {D1: target - 1, D2: 1, D3: 5}})
    assert state(view[badge_id]) == (True, D2, target, target)


def test_stage_streak_needs_seven_consecutive_stage_days_and_keeps_the_longest_run():
    days = {D1 + timedelta(days=offset) for offset in range(6)}
    assert state(badges(stage_days=days)["stage_streak_7"]) == (False, None, 6, 7)
    # 中斷後重新開始：進度是歷史最長連續天數。
    assert state(badges(stage_days=days | {D1 + timedelta(days=9)})["stage_streak_7"]) == (False, None, 6, 7)
    full = days | {D1 + timedelta(days=6)}
    assert state(badges(stage_days=full)["stage_streak_7"]) == (True, D1 + timedelta(days=6), 7, 7)


def at(day, minute=0):
    return datetime(2026, 10, day, 10, minute)


def ask(day, minute, correct, question, material="m", concept="c"):
    """一筆作答：(台灣時間, 是否答對, 教材, 概念, 題目身分)。"""
    return (at(day, minute), correct, material, concept, question)


def test_difficulty_is_overcome_once_when_error_rate_drops_below_half():
    # 2 錯 1 對 → 難點（3 題、67%）；再對 1 題 2/4 仍是 50%；再對 1 題 2/5 → 克服。
    events = [ask(16, 0, False, "q1"), ask(16, 1, False, "q2"), ask(16, 2, True, "q3"),
              ask(17, 0, True, "q4"), ask(18, 0, True, "q5"),
              # 克服後再錯、再對都不重算。
              ask(19, 0, False, "q6"), ask(19, 1, False, "q7"), ask(20, 0, True, "q8")]
    assert overcome_difficulties(events) == [(date(2026, 10, 18), False)]
    # 少於 3 題時錯誤率再高也不是難點；從未成為難點的概念不算克服。
    assert overcome_difficulties([ask(16, 0, False, "q1"), ask(16, 1, True, "q2"), ask(16, 2, True, "q3")]) == []
    # 同名概念在不同教材分開計算；沒有概念的作答忽略。
    other = [(t, ok, "m2", c, q) for t, ok, _, c, q in events]
    assert len(overcome_difficulties(sorted(events + other))) == 2
    assert overcome_difficulties([ask(16, i, False, f"q{i}", concept=None) for i in range(4)]) == []


def test_repeated_practice_of_the_same_question_cannot_farm_difficulties():
    # 先答對 3 題；再次練習同一題（題目身分相同）故意答錯 3 次再答對：只採計每題第一次作答，不成為難點。
    honest = [ask(16, 0, True, "q1"), ask(16, 1, True, "q2"), ask(16, 2, True, "q3")]
    farmed = [ask(17, i, False, "q1") for i in range(3)] + [ask(17, i + 3, True, "q2") for i in range(3)]
    assert overcome_difficulties(honest + farmed) == []
    # 重複題同樣不能用來「克服」：真正成為難點後，只答對重複題不算克服。
    hard = [ask(16, 0, False, "q1"), ask(16, 1, False, "q2"), ask(16, 2, True, "q3")]
    assert overcome_difficulties(hard + [ask(17, i, True, "q3") for i in range(5)]) == []
    # 宿敵也只看第一次作答：同一題錯 5 次不構成宿敵。
    repeated_wrong = [ask(16, i, False, "q1") for i in range(5)]
    assert overcome_difficulties(repeated_wrong + [ask(17, i, True, f"r{i}") for i in range(3)]) == []


def test_nemesis_requires_seventy_percent_errors_on_at_least_five_answers():
    wrong4_right1 = [ask(16, i, i == 4, f"w{i}") for i in range(5)]  # 5 題錯 4（80%）
    recovery = [ask(17, i, True, f"r{i}") for i in range(4)]         # 4/9 → 克服
    assert overcome_difficulties(wrong4_right1 + recovery) == [(date(2026, 10, 17), True)]
    # 4 題錯 3（75%）但未滿 5 題：克服但不是宿敵。
    short = [ask(16, i, i == 3, f"w{i}") for i in range(4)] + [ask(17, i, True, f"r{i}") for i in range(3)]
    assert overcome_difficulties(short) == [(date(2026, 10, 17), False)]


def test_stage_xp_has_a_daily_cap():
    assert [stage_xp(n) for n in (0, 1, 3, 10)] == [0, 15, 45, 45]


def test_v7_special_badges_use_feature_dates():
    view = special(features={"goal_on_time": [D2, D1], "all_media": [D1, D2, D3], "nemesis": [D3]})
    assert state(view["goal_on_time"]) == (True, D1, 1, 1)
    assert state(view["all_media"]) == (False, None, 3, 4)
    assert state(view["nemesis"]) == (True, D3, 1, 1)
    full = special(features={"all_media": [D3, D1, D2, D2]})
    assert state(full["all_media"]) == (True, D3, 4, 4)
    for badge_id in ("last_minute", "night_listener", "same_day_learner"):
        assert state(view[badge_id]) == (False, None, 0, 1)
