"""學習活動：由作答、概念卡複習與 Podcast 收聽紀錄即時彙總，以台灣日期為界。

XP、等級、連續天數、今日任務與徽章都從既有紀錄推導，不另存累計點數、任務或
徽章狀態（規則 xp-rules/v1 與 badge-rules/v4，說明見 docs/learning.md）。刪除教材或卡組時紀錄一併移除，
推導結果隨之改變；沒有紀錄的過去活動不補算。
"""

from collections import OrderedDict
from datetime import datetime, time, timedelta
from threading import Lock
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

TIMEZONE = "Asia/Taipei"
RULE_VERSION = "xp-rules/v1"
XP_CORRECT, XP_WRONG = 10, 3
XP_CONCEPT = 30
XP_CARD = 2
# 每天實際收聽每滿 5 分鐘 +5，單日上限 60。
XP_LISTEN_STEP, XP_LISTEN_SECONDS, XP_LISTEN_DAILY_CAP = 5, 300, 60
# 當天實際收聽滿 1 分鐘才算當天有學習活動。
ACTIVE_LISTEN_SECONDS = 60
NEW_CARD_QUEST = 5

BADGE_RULE_VERSION = "badge-rules/v4"
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
    ("first_material", "materials", 1),
    ("materials_3", "materials", 3),
    ("materials_10", "materials", 10),
    ("cards_100", "card_reviews", 100),
    ("cards_500", "card_reviews", 500),
    ("cards_2000", "card_reviews", 2000),
    ("listen_30_minutes", "listened_minutes", 30),
    ("listen_180_minutes", "listened_minutes", 180),
    ("listen_600_minutes", "listened_minutes", 600),
    ("streak_3", "streak", 3),
    ("streak_7", "streak", 7),
    ("streak_30", "streak", 30),
    ("streak_100", "streak", 100),
    ("streak_365", "streak", 365),
    ("level_5", "level", 5),
    ("level_10", "level", 10),
    ("level_20", "level", 20),
    ("level_30", "level", 30),
    # 零失誤題組：至少 3 題的題組，每題第一次作答都答對。
    ("perfect_set_1", "perfect_sets", 1),
    ("perfect_set_10", "perfect_sets", 10),
    ("perfect_set_30", "perfect_sets", 30),
)
# 特殊成就（畫面上未取得前隱藏條件）：(代號, 門檻)。判定方式見 derive_special_badges。
SPECIAL_BADGES = (
    ("combo_10", 10),        # 連續答對 10 題
    ("comeback_20", 20),     # 答錯後下一題就答對，累計 20 次
    ("daily_50", 50),        # 同一天作答 50 題
    ("explorer_5", 5),       # 在 5 份不同教材作答
    ("night_owl", 10),       # 台灣時間 0:00–4:59 作答累計 10 題
    ("early_bird", 10),      # 台灣時間 5:00–7:59 作答累計 10 題
    ("weekend", 1),          # 同一個週末的週六與週日都有學習活動
    ("return", 1),           # 中斷 7 天以上後重新有學習活動
    ("curious_10", 10),      # 在教材問答提出 10 個問題
    ("researcher", 1),       # 補充研究並把新來源加入教材
    ("creator", 2),          # 建立一組概念卡與一集 Podcast（各一）
    ("corrected_5", 5),      # 答錯過的概念之後又答對，累計 5 個
    ("first_try_material", 1),  # 完成一份教材，且每個作答過的概念第一次作答都答對
    ("perfect_day", 1),      # 同一天作答 20 題以上且全部答對
    ("all_round_day", 1),    # 同一天作答、複習概念卡、收聽 Podcast 滿 1 分鐘、在教材問答提問
    ("thousand_pages", 1),   # 完成一份 500 頁以上的教材
    ("revisit", 1),          # 教材完成 7 天以上後，又複習它的概念卡
)


def level_for(total):
    """第 n 級升到 n+1 需 100×n XP；回傳 (等級, 本級已得, 升級所需)。"""
    level, floor = 1, 0
    while total >= floor + 100 * level:
        floor += 100 * level
        level += 1
    return level, total - floor, 100 * level


def listening_xp(seconds):
    return min(XP_LISTEN_DAILY_CAP, seconds // XP_LISTEN_SECONDS * XP_LISTEN_STEP)


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
    """到達第 level 級所需的總 XP（第 n 級升到 n+1 需 100×n）。"""
    return sum(100 * n for n in range(1, level))


def derive_badges(*, answers, concepts, materials, card_reviews, listening, xp, active, streak, level, perfect_sets=None):
    """由每日紀錄推導系列徽章（規則 badge-rules/v4）。

    answers／concepts／materials／card_reviews／listening（秒）／xp 皆為 {台灣日期: 當天數量}；
    active 為有學習活動的日期集合。進度一律來自這些紀錄：累計型徽章的取得日期是累計
    第一次達標的那天，連續天數是第一次連續達標的那天，等級是累計 XP 第一次達到門檻的
    那天；未取得的連續天數徽章以目前連續天數作為進度。
    """
    listened = sum(listening.values())
    metrics = {
        "answers": (sum(answers.values()), lambda target: reached_on(answers, target)),
        "concepts": (sum(concepts.values()), lambda target: reached_on(concepts, target)),
        "materials": (sum(materials.values()), lambda target: reached_on(materials, target)),
        "card_reviews": (sum(card_reviews.values()), lambda target: reached_on(card_reviews, target)),
        "listened_minutes": (listened // 60, lambda target: reached_on(listening, target * 60)),
        "level": (level, lambda target: reached_on(xp, xp_threshold(target))),
        "perfect_sets": (sum((perfect_sets or {}).values()), lambda target: reached_on(perfect_sets or {}, target)),
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
                          correct=None, card_days=(), listening=None, completed=None, pages=None, reviews=None):
    """特殊與挑戰成就（規則 badge-rules/v4）。

    events 為依時間排序的作答 (台灣時間 datetime, 是否答對, 教材, 目標概念)；answers 為 {日期: 作答數}；
    active 為有學習活動的日期；questions、research 為 {日期: 次數}；card_sets、podcasts 為完成日期清單。
    挑戰成就另用：correct {日期: 答對數}、card_days 有複習卡片的日期、listening {日期: 收聽秒數}、
    completed {教材: 完成日期}、pages {教材: 頁數}、reviews {教材: 複習該教材卡片的日期清單}。
    取得日期是第一次達到門檻的那天；進度不超過門檻。
    """
    correct, listening = correct or {}, listening or {}
    completed, pages, reviews = completed or {}, pages or {}, reviews or {}
    combo, run, best_run = [], 0, 0
    comeback, previous = [], None
    explored, seen = [], set()
    night, early = [], []
    corrected, wrong, fixed = [], set(), set()
    for at, correct, material, concept in events:
        day = at.date()
        run = run + 1 if correct else 0
        best_run = max(best_run, run)
        if run == 10 and not combo:
            combo.append(day)
        if previous is False and correct:
            comeback.append(day)
        previous = correct
        if material not in seen:
            seen.add(material)
            explored.append(day)
        if at.hour < 5:
            night.append(day)
        elif at.hour < 8:
            early.append(day)
        key = (material, concept)
        if concept is not None and key not in fixed:
            if not correct:
                wrong.add(key)
            elif key in wrong:
                fixed.add(key)
                corrected.append(day)
    busy = sorted(day for day, count in answers.items() if count >= 50)
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
    values = {
        "combo_10": (min(best_run, 10), combo[0] if combo else None),
        "comeback_20": _reached(comeback, 20),
        "daily_50": (min(max(answers.values(), default=0), 50), busy[0] if busy else None),
        "explorer_5": _reached(explored, 5),
        "night_owl": _reached(night, 10),
        "early_bird": _reached(early, 10),
        "weekend": _reached(weekends, 1),
        "return": _reached(returns, 1),
        "curious_10": _reached(asked, 10),
        "researcher": _reached(added, 1),
        "creator": (len(made), max(made) if len(made) == 2 else None),
        "corrected_5": _reached(corrected, 5),
        "first_try_material": _reached(sorted(first_tries), 1),
        "perfect_day": _reached(perfect_days, 1),
        "all_round_day": _reached(all_round, 1),
        "thousand_pages": _reached(big, 1),
        "revisit": _reached(revisits, 1),
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
    from .knowledge_reads import learning
    from .tables import KnowledgeReadIndex

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
    from .tables import StudySession

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


def _card_quest(session, owner, start, now, origins):
    """到期卡優先；沒有到期卡時改為今天複習 5 張新卡。都沒有就不顯示。

    移到新版的卡片沿用舊卡的複習紀錄（origins），不會被當成沒學過的新卡。
    """
    from types import SimpleNamespace
    from ..card_reviews import _CARDS

    times = {}
    for card_set, concept, first_at, last_at in session.execute(text(
        """
        SELECT card_set_id, concept_id, min(created_at), max(created_at)
        FROM card_review_events WHERE learner_id = :owner
        GROUP BY card_set_id, concept_id
        """
    ), {"owner": owner}).all():
        key = origins.get((card_set, concept), (card_set, concept))
        earliest, latest = times.get(key, (first_at, last_at))
        times[key] = (min(earliest, first_at), max(latest, last_at))
    rows = []
    for card_set, concept, due_at in session.execute(text(_CARDS + """
        SELECT cards.card_set_id, cards.concept_id, r.due_at
        FROM cards
        LEFT JOIN card_review_states r ON r.card_set_id = cards.card_set_id AND r.concept_id = cards.concept_id
    """), {"owner": owner, "card_set": None}).all():
        first_at, last_at = times.get(origins.get((card_set, concept), (card_set, concept)), (None, None))
        rows.append(SimpleNamespace(due_at=due_at, first_at=first_at, last_at=last_at))
    # 今天以前就學過的卡：今天到期或今天已複習的都算進「到期卡」任務。
    learned = [row for row in rows if row.first_at is not None and row.first_at < start]
    due = sum(row.due_at is not None and row.due_at <= now for row in learned)
    target = sum(row.last_at >= start or (row.due_at is not None and row.due_at <= now) for row in learned)
    if target:
        # 「忘了」的卡 10 分鐘後再到期，評到不再到期才算完成。
        return {"kind": "review_due_cards", "progress": target - due, "target": target, "done": not due}
    new_today = sum(row.first_at is not None and row.first_at >= start for row in rows)
    remaining = sum(row.first_at is None for row in rows)
    target = min(NEW_CARD_QUEST, new_today + remaining)
    if not target:
        return None
    progress = min(new_today, target)
    return {"kind": "review_new_cards", "progress": progress, "target": target, "done": progress >= target}


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


def learning_activity(session, owner, *, now=None):
    zone = ZoneInfo(TIMEZONE)
    now = (now or datetime.now(zone)).astimezone(zone)
    today = now.date()
    start = datetime.combine(today, time(), zone)
    end = start + timedelta(days=1)
    values = {"owner": owner, "zone": TIMEZONE}
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
    # 卡片以「卡組＋概念」識別，同一張卡同一天只計一次 XP；複習數仍逐次計算。
    # 移到新版的卡片沿用最初那張卡的身分，同一天在新舊卡組各評一次仍只算一次。
    from ..version_moves import card_origins

    origins = card_origins(session, owner)
    reviewed = {}
    for day, card_set, concept, reviews in session.execute(text(
        """
        SELECT (created_at AT TIME ZONE :zone)::date AS day, card_set_id, concept_id, count(*)
        FROM card_review_events WHERE learner_id = :owner
        GROUP BY day, card_set_id, concept_id
        """
    ), values).all():
        if day <= today:
            count, distinct = reviewed.setdefault(day, [0, set()])
            reviewed[day][0] = count + reviews
            distinct.add(origins.get((card_set, concept), (card_set, concept)))
    cards = {day: (count, len(distinct)) for day, (count, distinct) in reviewed.items()}
    listening = {
        day: int(seconds) for day, seconds in session.execute(text(
            """
            SELECT listened_on, sum(listened_seconds) FROM podcast_listening_days
            WHERE learner_id = :owner GROUP BY listened_on
            """
        ), values).all()
        if day <= today
    }
    concepts = {day: count for day, count in _concept_completions(session, owner, zone).items() if day <= today}

    active = (
        set(answers) | set(cards)
        | {day for day, seconds in listening.items() if seconds >= ACTIVE_LISTEN_SECONDS}
    )
    sources = {
        "answers": sum(answer_xp.values()),
        "concepts": sum(concepts.values()) * XP_CONCEPT,
        "cards": sum(distinct for _, distinct in cards.values()) * XP_CARD,
        "podcast": sum(listening_xp(seconds) for seconds in listening.values()),
    }
    total = sum(sources.values())
    today_xp = (
        answer_xp.get(today, 0) + concepts.get(today, 0) * XP_CONCEPT
        + cards.get(today, (0, 0))[1] * XP_CARD + listening_xp(listening.get(today, 0))
    )
    level, level_xp, level_target = level_for(total)
    daily_xp = {}
    for day, value in (
        list(answer_xp.items())
        + [(day, count * XP_CONCEPT) for day, count in concepts.items()]
        + [(day, distinct * XP_CARD) for day, (_, distinct) in cards.items()]
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
        card_reviews={day: reviews for day, (reviews, _) in cards.items()},
        listening=listening, xp=daily_xp, active=active,
        streak=streak, level=level,
    )
    badges["items"] += derive_special_badges(
        events=events, answers={day: count for day, (count, _) in answers.items()}, active=active,
        questions={day: count for day, count in questions.items() if day <= today},
        research={day: count for day, count in research.items() if day <= today},
        card_sets=[day for day in card_sets if day <= today], podcasts=[day for day in podcasts if day <= today],
        correct={day: right for day, (_, right) in answers.items()}, card_days=set(cards),
        listening=listening, completed=completed, pages=pages, reviews=reviews,
    )
    monday = today - timedelta(days=today.weekday())
    week = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        count, correct = answers.get(day, (0, 0))
        week.append({
            "date": day, "answers": count, "correct": correct,
            "card_reviews": cards.get(day, (0, 0))[0],
            "listened_seconds": listening.get(day, 0),
            "active": day in active,
        })
    quests = [
        quest for quest in (
            _card_quest(session, owner, start, now, origins),
            _study_quest(session, owner, start, end),
            _podcast_quest(session, owner, start, end),
        )
        if quest is not None
    ]
    return {
        "schema": "learning-activity/v2",
        "timezone": TIMEZONE,
        "rule_version": RULE_VERSION,
        "today": today,
        "streak_days": streak,
        "active_today": today in active,
        "week": week,
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
