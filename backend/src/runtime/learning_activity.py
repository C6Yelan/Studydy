"""學習活動：由作答、概念卡標記與 Podcast 收聽紀錄即時彙總，以台灣日期為界。

XP、等級、連續天數、今日任務與徽章都從既有紀錄推導，不另存累計點數、任務或
徽章狀態（規則 xp-rules/v3 與 badge-rules/v6，說明見 docs/learning.md）。刪除教材或卡組時紀錄一併移除，
推導結果隨之改變；沒有紀錄的過去活動不補算。
"""

from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from threading import Lock
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

TIMEZONE = "Asia/Taipei"
RULE_VERSION = "xp-rules/v3"
XP_CORRECT, XP_WRONG = 10, 3
XP_CONCEPT = 30
# 概念卡標記：同一張卡每天第一次標記 +2，單日上限 40；距上一次標記不到 3 秒的不計（連續狂點不加分）。
XP_CARD, XP_CARD_DAILY_CAP = 2, 40
MARK_MIN_GAP = timedelta(seconds=3)
# 每天實際收聽每滿 5 分鐘 +5，單日上限 60。
XP_LISTEN_STEP, XP_LISTEN_SECONDS, XP_LISTEN_DAILY_CAP = 5, 300, 60
# 當天實際收聽滿 1 分鐘才算當天有學習活動。
ACTIVE_LISTEN_SECONDS = 60
MARK_QUEST = 5
# 升級曲線（原神式分段）：(起始等級, 該段第一級升級所需 XP, 段內每級增加)。
# 第 n 級升到 n+1 所需 XP＝所在段的基數＋增量×(n−起始等級)；每進入新的一段需求明顯跳一階。
LEVEL_SEGMENTS = ((1, 100, 50), (5, 500, 100), (10, 1400, 150), (15, 2600, 200), (20, 4500, 300), (30, 9000, 500))

BADGE_RULE_VERSION = "badge-rules/v6"
# 徽章依顯示順序列出：(代號, 計量, 門檻)。同一計量的多個門檻是同一系列的不同層級（由低到高），
# 計量見 derive_badges；修改時一併更新版本字串、API 模型、前端名稱與文件。
BADGES = (
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
    ("listen_30_minutes", "listened_minutes", 30),
    ("listen_180_minutes", "listened_minutes", 180),
    ("listen_600_minutes", "listened_minutes", 600),
    ("listen_1800_minutes", "listened_minutes", 1800),
    ("streak_3", "streak", 3),
    ("streak_7", "streak", 7),
    ("streak_30", "streak", 30),
    ("streak_100", "streak", 100),
    ("streak_365", "streak", 365),
    ("level_5", "level", 5),
    ("level_10", "level", 10),
    ("level_15", "level", 15),
    ("level_20", "level", 20),
    ("level_30", "level", 30),
    # 零失誤題組：至少 3 題的題組，每題第一次作答都答對。
    ("perfect_set_1", "perfect_sets", 1),
    ("perfect_set_10", "perfect_sets", 10),
    ("perfect_set_30", "perfect_sets", 30),
    ("perfect_set_100", "perfect_sets", 100),
    # 累計學習天數（不必連續）：與連續天數相同的活動日。
    ("days_7", "active_days", 7),
    ("days_30", "active_days", 30),
    ("days_100", "active_days", 100),
    ("days_200", "active_days", 200),
    ("days_365", "active_days", 365),
    # 完成的題組（assessment_sets completed）。
    ("first_set", "sets", 1),
    ("sets_10", "sets", 10),
    ("sets_50", "sets", 50),
    ("sets_200", "sets", 200),
    # 聽完的 Podcast 集數（每集第一次聽完）。
    ("first_episode", "episodes", 1),
    ("episodes_10", "episodes", 10),
    ("episodes_50", "episodes", 50),
)
# 特殊成就（畫面上未取得前隱藏條件）：(代號, 門檻)。判定方式見 derive_special_badges。
# 成就鏈（同一條件的更高門檻，例如 combo_10 → combo_25 → combo_50）由畫面依序揭曉；高門檻必然晚於低門檻取得。
SPECIAL_BADGES = (
    ("combo_10", 10),        # 連續答對 10 題
    ("combo_25", 25),        # 連續答對 25 題
    ("combo_50", 50),        # 連續答對 50 題
    ("comeback_20", 20),     # 答錯後下一題就答對，累計 20 次
    ("daily_50", 50),        # 同一天作答 50 題
    ("daily_100", 100),      # 同一天作答 100 題
    ("explorer_5", 5),       # 在 5 份不同教材作答
    ("explorer_10", 10),     # 在 10 份不同教材作答
    ("night_owl", 10),       # 台灣時間 0:00–4:59 作答累計 10 題
    ("early_bird", 10),      # 台灣時間 5:00–7:59 作答累計 10 題
    ("weekend", 1),          # 同一個週末的週六與週日都有學習活動
    ("return", 1),           # 中斷 7 天以上後重新有學習活動
    ("curious_10", 10),      # 在教材問答提出 10 個問題
    ("curious_50", 50),      # 在教材問答提出 50 個問題
    ("researcher", 1),       # 補充研究並把新來源加入教材
    ("creator", 2),          # 建立一組概念卡與一集 Podcast（各一）
    ("corrected_5", 5),      # 答錯過的概念之後又答對，累計 5 個
    ("first_try_material", 1),  # 完成一份教材，且每個作答過的概念第一次作答都答對
    ("perfect_day", 1),      # 同一天作答 20 題以上且全部答對
    ("all_round_day", 1),    # 同一天作答、標記概念卡、收聽 Podcast 滿 1 分鐘、在教材問答提問
    ("thousand_pages", 1),   # 完成一份 500 頁以上的教材
    ("revisit", 1),          # 教材完成 7 天以上後，又標記它的概念卡
    ("xp_day_300", 300),     # 同一天獲得 300 XP
    ("never_give_up", 1),    # 同一個概念答錯 3 次以上後才第一次答對
    ("binge_3", 3),          # 同一天聽完 3 集 Podcast
    ("three_islands", 3),    # 同一天在 3 份不同教材作答
    ("new_year", 1),         # 1 月 1 日有學習活動
)


def level_requirement(level):
    """第 level 級升到下一級所需的 XP（分段曲線 LEVEL_SEGMENTS）。"""
    start, base, step = next(segment for segment in reversed(LEVEL_SEGMENTS) if segment[0] <= level)
    return base + step * (level - start)


def level_for(total):
    """依累計 XP 回傳 (等級, 本級已得, 升級所需)。"""
    level, floor = 1, 0
    while total >= floor + level_requirement(level):
        floor += level_requirement(level)
        level += 1
    return level, total - floor, level_requirement(level)


def listening_xp(seconds):
    return min(XP_LISTEN_DAILY_CAP, seconds // XP_LISTEN_SECONDS * XP_LISTEN_STEP)


def card_xp(credited):
    return min(XP_CARD_DAILY_CAP, credited * XP_CARD)


def credited_marks(events, origins, zone):
    """依時間排序的標記事件 (時間, 卡組, 概念) → {台灣日期: (標記張數, 計分張數)}。

    同一張卡同一天只算一張；距上一次標記（任何卡）不到 MARK_MIN_GAP 的不計分。
    移到新版的卡片沿用最初那張卡的身分（origins），在新舊卡組各標一次仍只算一張。
    """
    marked, credited, previous = {}, {}, None
    for at, card_set, concept in events:
        day = at.astimezone(zone).date()
        card = origins.get((card_set, concept), (card_set, concept))
        marked.setdefault(day, set()).add(card)
        if previous is None or at - previous >= MARK_MIN_GAP:
            credited.setdefault(day, set()).add(card)
        previous = at
    return {day: (len(cards), len(credited.get(day, ()))) for day, cards in marked.items()}


def streak_days(active, today):
    """今天還沒有活動時，連續天數仍從昨天往回算，直到今天結束才中斷。"""
    cursor = today if today in active else today - timedelta(days=1)
    streak = 0
    while cursor in active:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def reached_on(per_day, target):
    """依日期累加，回傳累計第一次達到 target 的日期；沒有達到回傳 None。"""
    total = 0
    for day in sorted(per_day):
        total += per_day[day]
        if total >= target:
            return day
    return None


def run_reached_on(active, length):
    """回傳第一次連續 length 天有學習活動的那一天；沒有達到回傳 None。"""
    run, previous = 0, None
    for day in sorted(active):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        previous = day
        if run >= length:
            return day
    return None


def xp_threshold(level):
    """到達第 level 級所需的總 XP。"""
    return sum(level_requirement(n) for n in range(1, level))


def derive_badges(*, answers, concepts, materials, card_marks, listening, xp, active, streak, level,
                  perfect_sets=None, sets=None, episodes=None):
    """由每日紀錄推導系列徽章（規則 badge-rules/v6）。

    answers／concepts／materials／card_marks（計分的標記張數）／listening（秒）／xp／sets（完成題組）／
    episodes（聽完的集數）皆為 {台灣日期: 當天數量}；
    active 為有學習活動的日期集合。進度一律來自這些紀錄：累計型徽章的取得日期是累計
    第一次達標的那天，連續天數是第一次連續達標的那天，等級是累計 XP 第一次達到門檻的
    那天；未取得的連續天數徽章以目前連續天數作為進度。
    """
    listened = sum(listening.values())
    metrics = {
        "answers": (sum(answers.values()), lambda target: reached_on(answers, target)),
        "concepts": (sum(concepts.values()), lambda target: reached_on(concepts, target)),
        "materials": (sum(materials.values()), lambda target: reached_on(materials, target)),
        "card_marks": (sum(card_marks.values()), lambda target: reached_on(card_marks, target)),
        "listened_minutes": (listened // 60, lambda target: reached_on(listening, target * 60)),
        "level": (level, lambda target: reached_on(xp, xp_threshold(target))),
        "perfect_sets": (sum((perfect_sets or {}).values()), lambda target: reached_on(perfect_sets or {}, target)),
        "active_days": (len(active), lambda target: reached_on(dict.fromkeys(active, 1), target)),
        "sets": (sum((sets or {}).values()), lambda target: reached_on(sets or {}, target)),
        "episodes": (sum((episodes or {}).values()), lambda target: reached_on(episodes or {}, target)),
    }
    items = []
    for badge_id, metric, target in BADGES:
        if metric == "streak":
            earned_on = run_reached_on(active, target)
            earned = earned_on is not None
            value = target if earned else streak
        else:
            value, dated = metrics[metric]
            earned = value >= target
            earned_on = dated(target) if earned else None
        items.append({
            "badge_id": badge_id, "earned": earned, "earned_on": earned_on,
            "progress": min(value, target), "target": target,
        })
    return {"rule_version": BADGE_RULE_VERSION, "items": items}


def _reached(dates, target):
    """dates 為依時間排序、每次達成一次的日期；回傳 (進度, 第 target 次的日期)。"""
    return min(len(dates), target), (dates[target - 1] if len(dates) >= target else None)


def derive_special_badges(*, events, answers, active, questions, research, card_sets, podcasts,
                          correct=None, card_days=(), listening=None, completed=None, pages=None, reviews=None,
                          daily_xp=None, episodes=()):
    """特殊與挑戰成就（規則 badge-rules/v6）。

    events 為依時間排序的作答 (台灣時間 datetime, 是否答對, 教材, 目標概念)；answers 為 {日期: 作答數}；
    active 為有學習活動的日期；questions、research 為 {日期: 次數}；card_sets、podcasts 為完成日期清單。
    挑戰成就另用：correct {日期: 答對數}、card_days 有標記卡片的日期、listening {日期: 收聽秒數}、
    completed {教材: 完成日期}、pages {教材: 頁數}、reviews {教材: 標記該教材卡片的日期清單}、
    daily_xp {日期: 當天 XP}、episodes 每集第一次聽完的日期清單。
    取得日期是第一次達到門檻的那天；進度不超過門檻。
    """
    correct, listening = correct or {}, listening or {}
    completed, pages, reviews, daily_xp = completed or {}, pages or {}, reviews or {}, daily_xp or {}
    combos, run, best_run = {}, 0, 0
    comeback, previous = [], None
    explored, seen = [], set()
    night, early = [], []
    corrected, wrong, fixed = [], set(), set()
    # 永不放棄：同一概念第一次答對之前已答錯至少 3 次。
    misses, persisted, settled = {}, [], set()
    # 一日三島：每天作答過的教材。
    islands = {}
    # 迴圈變數不可沿用 correct（那是 {日期: 答對數}），否則之後的「完美的一天」判定會讀到布林值。
    for at, is_correct, material, concept in events:
        day = at.date()
        run = run + 1 if is_correct else 0
        best_run = max(best_run, run)
        if run in (10, 25, 50):
            combos.setdefault(run, day)
        islands.setdefault(day, set()).add(material)
        key = (material, concept)
        if concept is not None and key not in settled:
            if is_correct:
                settled.add(key)
                if misses.get(key, 0) >= 3:
                    persisted.append(day)
            else:
                misses[key] = misses.get(key, 0) + 1
        if previous is False and is_correct:
            comeback.append(day)
        previous = is_correct
        if material not in seen:
            seen.add(material)
            explored.append(day)
        if at.hour < 5:
            night.append(day)
        elif at.hour < 8:
            early.append(day)
        key = (material, concept)
        if concept is not None and key not in fixed:
            if not is_correct:
                wrong.add(key)
            elif key in wrong:
                fixed.add(key)
                corrected.append(day)
    busy = {target: sorted(day for day, count in answers.items() if count >= target) for target in (50, 100)}
    days = sorted(active)
    weekends = [day for day in days if day.weekday() == 6 and day - timedelta(days=1) in active]
    returns = [later for earlier, later in zip(days, days[1:]) if (later - earlier).days > 7]
    asked = [day for day in sorted(questions) for _ in range(questions[day])]
    added = [day for day in sorted(research) for _ in range(research[day])]
    # 卡組與 Podcast 各自第一次完成的日期；兩者都有時取較晚的一天。
    made = [min(dates) for dates in (card_sets, podcasts) if dates]
    # 一次就懂：完成的教材中，每個作答過的概念第一次作答都答對（只看完成那天以前的作答）。
    first_tries = []
    for material, finished in completed.items():
        firsts = {}
        for at, is_correct, event_material, concept in events:
            if event_material == material and concept is not None and at.date() <= finished:
                firsts.setdefault(concept, is_correct)
        if firsts and all(firsts.values()):
            first_tries.append(finished)
    perfect_days = [day for day in sorted(answers) if answers[day] >= 20 and correct.get(day, 0) == answers[day]]
    card_set_days = set(card_days)
    all_round = [day for day in sorted(answers) if day in card_set_days
                 and listening.get(day, 0) >= ACTIVE_LISTEN_SECONDS and questions.get(day, 0) > 0]
    big = sorted(day for material, day in completed.items() if pages.get(material, 0) >= 500)
    revisits = sorted(
        day for material, finished in completed.items() for day in reviews.get(material, ())
        if (day - finished).days >= 7
    )
    most_answers = max(answers.values(), default=0)
    heavy = sorted(day for day, value in daily_xp.items() if value >= 300)
    finished_per_day = {}
    for day in episodes:
        finished_per_day[day] = finished_per_day.get(day, 0) + 1
    binges = sorted(day for day, count in finished_per_day.items() if count >= 3)
    hopping = sorted(day for day, materials in islands.items() if len(materials) >= 3)
    new_years = sorted(day for day in active if (day.month, day.day) == (1, 1))
    values = {
        **{
            f"combo_{target}": (min(best_run, target), combos.get(target)) for target in (10, 25, 50)
        },
        "comeback_20": _reached(comeback, 20),
        **{
            f"daily_{target}": (min(most_answers, target), busy[target][0] if busy[target] else None)
            for target in (50, 100)
        },
        "explorer_5": _reached(explored, 5),
        "explorer_10": _reached(explored, 10),
        "night_owl": _reached(night, 10),
        "early_bird": _reached(early, 10),
        "weekend": _reached(weekends, 1),
        "return": _reached(returns, 1),
        "curious_10": _reached(asked, 10),
        "curious_50": _reached(asked, 50),
        "researcher": _reached(added, 1),
        "creator": (len(made), max(made) if len(made) == 2 else None),
        "corrected_5": _reached(corrected, 5),
        "first_try_material": _reached(sorted(first_tries), 1),
        "perfect_day": _reached(perfect_days, 1),
        "all_round_day": _reached(all_round, 1),
        "thousand_pages": _reached(big, 1),
        "revisit": _reached(revisits, 1),
        "xp_day_300": (min(max(daily_xp.values(), default=0), 300), heavy[0] if heavy else None),
        "never_give_up": _reached(persisted, 1),
        "binge_3": (min(max(finished_per_day.values(), default=0), 3), binges[0] if binges else None),
        "three_islands": (min(max((len(m) for m in islands.values()), default=0), 3), hopping[0] if hopping else None),
        "new_year": _reached(new_years, 1),
    }
    items = []
    for badge_id, target in SPECIAL_BADGES:
        progress, earned_on = values[badge_id]
        items.append({
            "badge_id": badge_id, "earned": earned_on is not None, "earned_on": earned_on,
            "progress": target if earned_on is not None else min(progress, target), "target": target,
        })
    return items


def first_completions(plan, events, unavailable):
    """重播作答，回傳 {概念: 可出題重點全部完成的那一筆作答時間}。

    與 derive_learning_states 使用同一個重點完成規則與目前無法出題的重點；
    events 須依時間排序。完成後不撤銷，因此第一次達成即為首次完成。
    """
    from learning_adaptation.learning_states import claim_completion

    targets, pending = {}, {}
    for concept in plan["concepts"]:
        assessable = [
            target for target in concept["targets"]
            if not set(target["covered_claim_ids"]) & unavailable
        ]
        if not assessable:
            continue
        pending[concept["concept_id"]] = {target["claim_id"] for target in assessable}
        for target in assessable:
            for claim_id in target["covered_claim_ids"]:
                targets[(concept["concept_id"], claim_id)] = target["claim_id"]
    histories, completed = {}, {}
    for event in events:
        target = targets.get((event.target_concept_id, event.target_claim_id))
        concept = event.target_concept_id
        if target is None or concept in completed or target not in pending[concept]:
            continue
        history = histories.setdefault((concept, target), [])
        history.append(event)
        if claim_completion(history)[0]:
            pending[concept].discard(target)
            if not pending[concept]:
                completed[concept] = event.created_at
    return completed


# 學習計畫以內容雜湊驗證且版本不可變；每次首頁與頁首都重讀並驗證大型教材的完整計畫
# 會拖慢學習活動。第一次完整讀取後，在本程序以「使用者、教材、版本、計畫雜湊」保留
# 重播所需的概念與重點對應（不含作答），作答與無法出題的重點仍每次即時讀取。
_PLAN_LIMIT = 16
_plans = OrderedDict()
_plans_lock = Lock()


def _completion_plan(session, owner, material_id, revision):
    from .storage.knowledge_reads import learning
    from .storage.tables import KnowledgeReadIndex

    digest = session.scalar(select(KnowledgeReadIndex.learning_sha256).where(
        KnowledgeReadIndex.learner_id == owner,
        KnowledgeReadIndex.material_id == material_id,
        KnowledgeReadIndex.structure_revision == revision,
    ))
    key = (owner, material_id, revision, digest)
    if digest is not None:
        with _plans_lock:
            if key in _plans:
                _plans.move_to_end(key)
                return _plans[key]
    plan = learning(session, owner, material_id, revision)
    compact = {"concepts": [
        {
            "concept_id": concept["concept_id"],
            "targets": [
                {"claim_id": target["claim_id"], "covered_claim_ids": list(target["covered_claim_ids"])}
                for target in concept["targets"]
            ],
        }
        for concept in plan["concepts"]
    ]}
    if digest is not None:
        with _plans_lock:
            _plans[key] = compact
            while len(_plans) > _PLAN_LIMIT:
                _plans.popitem(last=False)
    return compact


def _concept_completions(session, owner, zone):
    """每份未刪除教材的每個概念只算一次，取各學習版本中最早的完成時間。"""
    from learning_adaptation.assessment_state import unavailable_claims
    from .storage.tables import StudySession

    sessions = session.execute(text(
        """
        SELECT s.study_session_id FROM study_sessions s
        JOIN materials m ON m.material_id = s.material_id AND m.learner_id = s.learner_id
        WHERE s.learner_id = :owner AND m.discard_requested_at IS NULL
        """
    ), {"owner": owner}).scalars().all()
    events_by_material, earliest = {}, {}
    for identity in sessions:
        study = session.get(StudySession, identity)
        if study.material_id not in events_by_material:
            # 新版本會承接舊版本未變重點的作答，因此重播同一教材的全部作答。
            events_by_material[study.material_id] = session.execute(text(
                """
                SELECT e.target_concept_id, e.target_claim_id, e.is_correct,
                       e.semantic_identity, e.created_at
                FROM answer_events e
                JOIN study_sessions s ON s.study_session_id = e.study_session_id
                WHERE s.learner_id = :owner AND e.material_id = :material
                ORDER BY e.created_at, e.answer_event_id
                """
            ), {"owner": owner, "material": study.material_id}).all()
        try:
            plan = _completion_plan(session, owner, study.material_id, study.knowledge_structure_revision)
        except Exception:
            # 版本無法通過驗證時不推測完成狀態。
            continue
        unavailable = unavailable_claims(session, study, plan)
        for concept, at in first_completions(plan, events_by_material[study.material_id], unavailable).items():
            key = (study.material_id, concept)
            if key not in earliest or at < earliest[key]:
                earliest[key] = at
    days = {}
    for at in earliest.values():
        day = at.astimezone(zone).date()
        days[day] = days.get(day, 0) + 1
    return days


def _material_completions(session, owner, zone):
    """每份未刪除教材第一次有學習版本完成（study session completed）的台灣日期：{教材: 日期}。"""
    return {
        # completed 狀態必有完成時間（資料表約束），因此取得日期一定可推導。
        material: at.astimezone(zone).date()
        for material, at in session.execute(text(
            """
            SELECT s.material_id, min(s.completed_at) FROM study_sessions s
            JOIN materials m ON m.material_id = s.material_id AND m.learner_id = s.learner_id
            WHERE s.learner_id = :owner AND s.status = 'completed' AND m.discard_requested_at IS NULL
            GROUP BY s.material_id
            """
        ), {"owner": owner}).all()
    }


def _card_quest(session, owner, credited):
    """今天標記 5 張概念卡（卡片不足 5 張時以全部張數為目標）；沒有卡片就不顯示。

    credited 是今天計分的標記張數，與 XP 相同規則（credited_marks）：同一張卡算一張、連續狂點不算。
    """
    from .card_reviews import _CARDS

    total = session.execute(text(_CARDS + "SELECT count(*) FROM cards"), {"owner": owner, "card_set": None, "material": None}).scalar()
    target = min(MARK_QUEST, total)
    if not target:
        return None
    progress = min(credited, target)
    return {"kind": "mark_cards", "progress": progress, "target": target, "done": progress >= target}


def _study_quest(session, owner, start, end):
    """今天完成 1 次題組作答；沒有進行中的學習且今天也沒完成題組時不顯示。"""
    done = session.execute(text(
        """
        SELECT count(*) FROM assessment_sets
        WHERE learner_id = :owner AND status = 'completed'
          AND completed_at >= :start AND completed_at < :end
        """
    ), {"owner": owner, "start": start, "end": end}).scalar_one()
    # 最近有作答（或最近開始）的進行中學習排第一，作為「繼續學習」的入口。
    current = session.execute(text(
        """
        SELECT s.study_session_id, s.material_id, s.knowledge_structure_revision
        FROM study_sessions s
        JOIN materials m ON m.material_id = s.material_id AND m.learner_id = s.learner_id
        WHERE s.learner_id = :owner AND s.status IN ('active', 'no_safe')
          AND m.discard_requested_at IS NULL AND m.head_revision = s.knowledge_structure_revision
        ORDER BY greatest(s.started_at, (SELECT max(e.created_at) FROM answer_events e
                                         WHERE e.study_session_id = s.study_session_id)) DESC NULLS LAST,
                 s.study_session_id
        LIMIT 1
        """
    ), {"owner": owner}).first()
    if current is None and not done:
        return None
    return {
        "kind": "continue_study", "progress": min(done, 1), "target": 1, "done": done > 0,
        "material_id": current.material_id if current else None,
        "study_session_id": current.study_session_id if current else None,
    }


def _podcast_quest(session, owner, start, end):
    """聽完 1 集還沒聽完的 Podcast；沒有未聽完的集數且今天也沒聽完任何一集時不顯示。"""
    rows = session.execute(text(
        """
        SELECT p.podcast_id, p.material_id,
               -- 含 U+0000 的清單以 {"items": [...]} 保存（storage.evidence_json）。
               jsonb_array_length(CASE WHEN jsonb_typeof(p.episodes) = 'array'
                                       THEN p.episodes ELSE p.episodes -> 'items' END) AS episodes,
               coalesce(array_agg(g.episode_index) FILTER (WHERE g.completed_at IS NOT NULL), '{}') AS finished,
               max(g.updated_at) AS touched,
               count(*) FILTER (WHERE g.completed_at >= :start AND g.completed_at < :end) AS finished_today,
               p.created_at
        FROM podcasts p
        JOIN materials m ON m.material_id = p.material_id AND m.learner_id = p.learner_id
        LEFT JOIN podcast_episode_progress g ON g.podcast_id = p.podcast_id
        WHERE p.learner_id = :owner AND p.status = 'ready' AND m.discard_requested_at IS NULL
        GROUP BY p.podcast_id
        ORDER BY max(g.updated_at) DESC NULLS LAST, p.created_at DESC
        """
    ), {"owner": owner, "start": start, "end": end}).all()
    done = sum(row.finished_today for row in rows) > 0
    pick = next((row for row in rows if len(set(row.finished)) < row.episodes), None)
    if pick is None and not done:
        return None
    episode = None
    if pick is not None:
        current = session.execute(text(
            """
            SELECT episode_index, completed_at FROM podcast_episode_progress
            WHERE podcast_id = :podcast ORDER BY updated_at DESC LIMIT 1
            """
        ), {"podcast": pick.podcast_id}).first()
        finished = set(pick.finished)
        if current is not None and current.completed_at is None:
            episode = current.episode_index
        else:
            episode = next(index for index in range(pick.episodes) if index not in finished)
    return {
        "kind": "finish_podcast_episode", "progress": int(done), "target": 1, "done": done,
        "material_id": pick.material_id if pick else None,
        "podcast_id": pick.podcast_id if pick else None,
        "episode_index": episode,
    }


def _daily_records(session, owner, zone, today):
    """每日作答 {日期: (題數, 答對)}、概念卡標記 {日期: (張數, 計分張數)}、收聽 {日期: 秒數} 與活動日集合。

    學習活動與學習節奏共用；晚於 today 的日期不列入。
    """
    from .storage.card_moves import card_origins

    values = {"owner": owner, "zone": zone.key}
    answers = {
        day: (count, correct) for day, count, correct in session.execute(text(
            """
            SELECT (e.created_at AT TIME ZONE :zone)::date AS day,
                   count(*), count(*) FILTER (WHERE e.is_correct)
            FROM answer_events e
            JOIN study_sessions s ON s.study_session_id = e.study_session_id
            WHERE s.learner_id = :owner
            GROUP BY day
            """
        ), values).all()
        if day <= today
    }
    # 概念卡標記：{日期: (標記張數, 計分張數)}，規則見 credited_marks。
    origins = card_origins(session, owner)
    cards = {
        day: value for day, value in credited_marks(session.execute(text(
            """
            SELECT created_at, card_set_id, concept_id FROM card_review_events
            WHERE learner_id = :owner ORDER BY created_at, review_event_id
            """
        ), values).all(), origins, zone).items()
        if day <= today
    }
    listening = {
        day: int(seconds) for day, seconds in session.execute(text(
            """
            SELECT listened_on, sum(listened_seconds) FROM podcast_listening_days
            WHERE learner_id = :owner GROUP BY listened_on
            """
        ), values).all()
        if day <= today
    }
    active = (
        set(answers) | set(cards)
        | {day for day, seconds in listening.items() if seconds >= ACTIVE_LISTEN_SECONDS}
    )
    return answers, cards, listening, active


def learning_activity(session, owner, *, now=None):
    zone = ZoneInfo(TIMEZONE)
    now = (now or datetime.now(zone)).astimezone(zone)
    today = now.date()
    start = datetime.combine(today, time(), zone)
    end = start + timedelta(days=1)
    values = {"owner": owner, "zone": TIMEZONE}
    answers, cards, listening, active = _daily_records(session, owner, zone, today)
    # 同一份教材的同一道題（題目與正解相同）只算第一次作答；重送與重做不重複得分。
    answer_xp = {}
    for day, correct in session.execute(text(
        """
        SELECT DISTINCT ON (e.material_id, e.semantic_identity)
               (e.created_at AT TIME ZONE :zone)::date AS day, e.is_correct
        FROM answer_events e
        JOIN study_sessions s ON s.study_session_id = e.study_session_id
        WHERE s.learner_id = :owner
        ORDER BY e.material_id, e.semantic_identity, e.created_at, e.event_number
        """
    ), values).all():
        if day <= today:
            answer_xp[day] = answer_xp.get(day, 0) + (XP_CORRECT if correct else XP_WRONG)
    concepts = {day: count for day, count in _concept_completions(session, owner, zone).items() if day <= today}
    sources = {
        "answers": sum(answer_xp.values()),
        "concepts": sum(concepts.values()) * XP_CONCEPT,
        "cards": sum(card_xp(credited) for _, credited in cards.values()),
        "podcast": sum(listening_xp(seconds) for seconds in listening.values()),
    }
    total = sum(sources.values())
    today_xp = (
        answer_xp.get(today, 0) + concepts.get(today, 0) * XP_CONCEPT
        + card_xp(cards.get(today, (0, 0))[1]) + listening_xp(listening.get(today, 0))
    )
    level, level_xp, level_target = level_for(total)
    daily_xp = {}
    for day, value in (
        list(answer_xp.items())
        + [(day, count * XP_CONCEPT) for day, count in concepts.items()]
        + [(day, card_xp(credited)) for day, (_, credited) in cards.items()]
        + [(day, listening_xp(seconds)) for day, seconds in listening.items()]
    ):
        daily_xp[day] = daily_xp.get(day, 0) + value
    completed = {material: day for material, day in _material_completions(session, owner, zone).items() if day <= today}
    materials = {}
    for day in completed.values():
        materials[day] = materials.get(day, 0) + 1
    streak = streak_days(active, today)
    # 零失誤題組：至少 3 題、每題第一次作答都答對的完成題組，依完成日期計數。
    perfect_sets = {}
    for (day,) in session.execute(text(
        """
        SELECT (s.completed_at AT TIME ZONE :zone)::date
        FROM assessment_sets s
        JOIN assessment_set_items i ON i.set_id = s.set_id AND i.assessment_revision IS NOT NULL
        LEFT JOIN LATERAL (
            SELECT e.is_correct FROM answer_events e
            WHERE e.assessment_revision = i.assessment_revision AND e.study_session_id = s.study_session_id
            ORDER BY e.created_at, e.event_number LIMIT 1
        ) f ON true
        WHERE s.learner_id = :owner AND s.status = 'completed'
        GROUP BY s.set_id, s.completed_at
        HAVING count(*) >= 3 AND bool_and(coalesce(f.is_correct, false))
        """
    ), values).all():
        if day <= today:
            perfect_sets[day] = perfect_sets.get(day, 0) + 1
    sets = {
        day: count for day, count in session.execute(text(
            """
            SELECT (completed_at AT TIME ZONE :zone)::date AS day, count(*) FROM assessment_sets
            WHERE learner_id = :owner AND status = 'completed'
            GROUP BY day
            """
        ), values).all()
        if day <= today
    }
    # 每集第一次聽完的日期（刪除 Podcast 時播放進度一併移除）。
    episodes = sorted(day for (day,) in session.execute(text(
        """
        SELECT (completed_at AT TIME ZONE :zone)::date FROM podcast_episode_progress
        WHERE learner_id = :owner AND completed_at IS NOT NULL
        """
    ), values).all() if day <= today)
    episodes_per_day = {}
    for day in episodes:
        episodes_per_day[day] = episodes_per_day.get(day, 0) + 1
    pages = dict(session.execute(text(
        """
        SELECT material_id, max(total_pages) FROM material_processing_runs
        -- partial 也會發布可學習的知識地圖（例如千頁教材部分頁面失敗）。
        WHERE learner_id = :owner AND status IN ('succeeded', 'partial') AND total_pages IS NOT NULL
        GROUP BY material_id
        """
    ), values).all())
    reviews = {}
    for material, day in session.execute(text(
        """
        SELECT DISTINCT c.material_id, (r.created_at AT TIME ZONE :zone)::date
        FROM card_review_events r JOIN card_sets c ON c.card_set_id = r.card_set_id
        WHERE r.learner_id = :owner
        """
    ), values).all():
        if day <= today:
            reviews.setdefault(material, []).append(day)
    events = [
        (at, correct, material, concept) for at, correct, material, concept in session.execute(text(
            """
            SELECT e.created_at AT TIME ZONE :zone, e.is_correct, e.material_id, e.target_concept_id
            FROM answer_events e
            JOIN study_sessions s ON s.study_session_id = e.study_session_id
            WHERE s.learner_id = :owner
            ORDER BY e.created_at, e.event_number
            """
        ), values).all()
        if at.date() <= today
    ]
    questions = dict(session.execute(text(
        """
        SELECT (t.created_at AT TIME ZONE :zone)::date AS day, count(*)
        FROM voice_turns t JOIN voice_conversations c ON c.conversation_id = t.conversation_id
        WHERE c.learner_id = :owner AND c.deleted_at IS NULL AND t.status = 'ready'
        GROUP BY day
        """
    ), values).all())
    research = dict(session.execute(text(
        """
        SELECT (created_at AT TIME ZONE :zone)::date AS day, count(*) FROM material_research
        WHERE learner_id = :owner AND deleted_at IS NULL AND status = 'submitted'
        GROUP BY day
        """
    ), values).all())
    card_sets = [day for (day,) in session.execute(text(
        """
        SELECT (created_at AT TIME ZONE :zone)::date FROM card_sets
        WHERE learner_id = :owner AND deleted_at IS NULL AND status = 'ready'
        """
    ), values).all()]
    podcasts = [day for (day,) in session.execute(text(
        """
        SELECT (created_at AT TIME ZONE :zone)::date FROM podcasts
        WHERE learner_id = :owner AND status = 'ready'
        """
    ), values).all()]
    badges = derive_badges(
        answers={day: count for day, (count, _) in answers.items()},
        concepts=concepts,
        materials=materials, perfect_sets=perfect_sets,
        card_marks={day: credited for day, (_, credited) in cards.items()},
        listening=listening, xp=daily_xp, active=active,
        streak=streak, level=level, sets=sets, episodes=episodes_per_day,
    )
    badges["items"] += derive_special_badges(
        events=events, answers={day: count for day, (count, _) in answers.items()}, active=active,
        questions={day: count for day, count in questions.items() if day <= today},
        research={day: count for day, count in research.items() if day <= today},
        card_sets=[day for day in card_sets if day <= today], podcasts=[day for day in podcasts if day <= today],
        correct={day: right for day, (_, right) in answers.items()}, card_days=set(cards),
        listening=listening, completed=completed, pages=pages, reviews=reviews,
        daily_xp=daily_xp, episodes=episodes,
    )
    quests = [
        quest for quest in (
            _card_quest(session, owner, cards.get(today, (0, 0))[1]),
            _study_quest(session, owner, start, end),
            _podcast_quest(session, owner, start, end),
        )
        if quest is not None
    ]
    return {
        "schema": "learning-activity/v3",
        "timezone": TIMEZONE,
        "rule_version": RULE_VERSION,
        "today": today,
        "streak_days": streak,
        "active_today": today in active,
        "total_answers": sum(a for a, _ in answers.values()),
        "total_correct": sum(c for _, c in answers.values()),
        "xp": {
            "total": total, "today": today_xp, "level": level,
            "level_xp": level_xp, "level_target": level_target, "sources": sources,
        },
        "quests": [
            {"material_id": None, "study_session_id": None, "podcast_id": None, "episode_index": None, **quest}
            for quest in quests
        ],
        "badges": badges,
    }


RHYTHM_PERIODS = ("week", "month", "quarter", "year")
# 各區間最多往前翻的頁數，都約十年；前端翻頁鈕（features/profile/LearningRhythm.tsx）用同一組上限。
RHYTHM_MAX_OFFSET = {"week": 520, "month": 120, "quarter": 40, "year": 10}


def rhythm_window(period, offset, today):
    """學習節奏的日期區間 (起, 迄)，offset 為往前翻的頁數（0 是目前這一頁）。

    週：週一到週日；月：自然月；近 3 個月：13 週、近一年：52 週，都以本週日為最後一天，往前翻一頁就整段往前移。
    """
    monday = today - timedelta(days=today.weekday())
    if period == "week":
        start = monday - timedelta(weeks=offset)
        return start, start + timedelta(days=6)
    if period == "month":
        index = today.year * 12 + today.month - 1 - offset
        start = date(index // 12, index % 12 + 1, 1)
        following = date((index + 1) // 12, (index + 1) % 12 + 1, 1)
        return start, following - timedelta(days=1)
    weeks = 13 if period == "quarter" else 52
    end = monday + timedelta(days=6) - timedelta(weeks=weeks * offset)
    return end - timedelta(weeks=weeks) + timedelta(days=1), end


def learning_rhythm(session, owner, *, period, offset=0, now=None):
    """指定區間每天的作答、答對、標記卡片張數、收聽秒數與是否有學習；每次最多 364 天。

    與學習活動同一份每日彙總（原始紀錄沒有定期清除，刪除教材時相關紀錄移除）；
    first_day 是最早有紀錄的日期，畫面據此決定能否再往前翻。
    """
    zone = ZoneInfo(TIMEZONE)
    today = (now or datetime.now(zone)).astimezone(zone).date()
    start, end = rhythm_window(period, offset, today)
    answers, cards, listening, active = _daily_records(session, owner, zone, today)
    recorded = set(answers) | set(cards) | set(listening)
    days = []
    day = start
    while day <= end:
        count, correct = answers.get(day, (0, 0))
        days.append({
            "date": day, "answers": count, "correct": correct,
            "card_reviews": cards.get(day, (0, 0))[0],
            "listened_seconds": listening.get(day, 0),
            "active": day in active,
        })
        day += timedelta(days=1)
    return {
        "schema": "learning-rhythm/v1",
        "timezone": TIMEZONE,
        "today": today,
        "period": period,
        "offset": offset,
        "start": start,
        "end": end,
        "first_day": min(recorded) if recorded else None,
        "streak_days": streak_days(active, today),
        "days": days,
    }
