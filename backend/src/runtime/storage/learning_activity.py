"""學習活動：由作答、概念卡複習與 Podcast 收聽紀錄即時彙總，以台灣日期為界。

XP、等級、連續天數、今日任務與徽章都從既有紀錄推導，不另存累計點數、任務或
徽章狀態（規則 xp-rules/v1 與 badge-rules/v1，說明見 docs/learning.md）。刪除教材或卡組時紀錄一併移除，
推導結果隨之改變；沒有紀錄的過去活動不補算。
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text

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

BADGE_RULE_VERSION = "badge-rules/v1"
# 徽章依顯示順序列出：(代號, 計量, 門檻)。計量見 _badge_metrics；修改時一併更新版本字串與文件。
BADGES = (
    ("first_answer", "answers", 1),
    ("answers_100", "answers", 100),
    ("first_concept", "concepts", 1),
    ("concepts_25", "concepts", 25),
    ("first_material", "materials", 1),
    ("cards_100", "card_reviews", 100),
    ("listen_30_minutes", "listened_minutes", 30),
    ("streak_3", "streak", 3),
    ("streak_7", "streak", 7),
    ("level_5", "level", 5),
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


def derive_badges(*, answers, concepts, materials, card_reviews, listening, xp, active, streak, level):
    """由每日紀錄推導徽章（規則 badge-rules/v1）。

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


def _concept_completions(session, owner, zone):
    """每份未刪除教材的每個概念只算一次，取各學習版本中最早的完成時間。"""
    from learning_adaptation.assessment_state import unavailable_claims
    from .knowledge_reads import learning
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
            plan = learning(session, owner, study.material_id, study.knowledge_structure_revision)
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
    """每份未刪除教材第一次有學習版本完成（study session completed）的台灣日期。"""
    days = {}
    for at in session.execute(text(
        """
        SELECT min(s.completed_at) FROM study_sessions s
        JOIN materials m ON m.material_id = s.material_id AND m.learner_id = s.learner_id
        WHERE s.learner_id = :owner AND s.status = 'completed' AND m.discard_requested_at IS NULL
        GROUP BY s.material_id
        """
    ), {"owner": owner}).scalars().all():
        # completed 狀態必有完成時間（資料表約束），因此取得日期一定可推導。
        day = at.astimezone(zone).date()
        days[day] = days.get(day, 0) + 1
    return days


def _card_quest(session, owner, start, now):
    """到期卡優先；沒有到期卡時改為今天複習 5 張新卡。都沒有就不顯示。"""
    from ..card_reviews import _CARDS

    rows = session.execute(text(_CARDS + """
        SELECT r.due_at,
               (SELECT min(v.created_at) FROM card_review_events v
                WHERE v.card_set_id = cards.card_set_id AND v.concept_id = cards.concept_id) AS first_at,
               (SELECT max(v.created_at) FROM card_review_events v
                WHERE v.card_set_id = cards.card_set_id AND v.concept_id = cards.concept_id) AS last_at
        FROM cards
        LEFT JOIN card_review_states r ON r.card_set_id = cards.card_set_id AND r.concept_id = cards.concept_id
    """), {"owner": owner, "card_set": None}).all()
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
        SELECT p.podcast_id, p.material_id, jsonb_array_length(p.episodes) AS episodes,
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
    cards = {
        day: (reviews, distinct) for day, reviews, distinct in session.execute(text(
            """
            SELECT (created_at AT TIME ZONE :zone)::date AS day, count(*),
                   count(DISTINCT (card_set_id, concept_id))
            FROM card_review_events WHERE learner_id = :owner
            GROUP BY day
            """
        ), values).all()
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
    materials = _material_completions(session, owner, zone)
    streak = streak_days(active, today)
    badges = derive_badges(
        answers={day: count for day, (count, _) in answers.items()},
        concepts=concepts,
        materials={day: count for day, count in materials.items() if day <= today},
        card_reviews={day: reviews for day, (reviews, _) in cards.items()},
        listening=listening, xp=daily_xp, active=active,
        streak=streak, level=level,
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
            _card_quest(session, owner, start, now),
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
