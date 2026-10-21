"""學習統計：由作答事件即時彙總，以台灣日期為界，不另存累計值。

目前只有作答事件有逐筆時間；概念卡複習與 Podcast 收聽尚未記錄，不計入。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text

TIMEZONE = "Asia/Taipei"


def learning_activity(session, owner, *, now=None):
    today = (now or datetime.now(ZoneInfo(TIMEZONE))).astimezone(ZoneInfo(TIMEZONE)).date()
    rows = session.execute(
        text(
            """
            SELECT (e.created_at AT TIME ZONE :zone)::date AS day,
                   count(*), count(*) FILTER (WHERE e.is_correct)
            FROM answer_events e
            JOIN study_sessions s ON s.study_session_id = e.study_session_id
            WHERE s.learner_id = :owner
            GROUP BY day
            """
        ),
        {"owner": owner, "zone": TIMEZONE},
    ).all()
    days = {day: (answers, correct) for day, answers, correct in rows if day <= today}
    # 今天還沒作答時，連續天數仍從昨天往回算，直到今天結束才中斷。
    cursor = today if today in days else today - timedelta(days=1)
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    monday = today - timedelta(days=today.weekday())
    week = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        answers, correct = days.get(day, (0, 0))
        week.append({"date": day, "answers": answers, "correct": correct})
    return {
        "schema": "learning-activity/v1",
        "timezone": TIMEZONE,
        "today": today,
        "streak_days": streak,
        "active_today": today in days,
        "week": week,
        "total_answers": sum(a for a, _ in days.values()),
        "total_correct": sum(c for _, c in days.values()),
    }
