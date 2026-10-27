"""目標點數與分天預覽；只計算，不寫入學習進度或固定日程。"""

from datetime import date, timedelta

DAILY_LIMIT = 20


def _target(points: int, percent: int) -> int:
    return (points * percent + 99) // 100


def _quota(remaining: int, days: int) -> int:
    days = max(1, days)
    return (max(0, remaining) + days - 1) // days


def daily_quota(assessable_points: int, completed_points: int,
                target_percent: int, days_remaining: int) -> int:
    return _quota(_target(assessable_points, target_percent) - completed_points, days_remaining)


def _take_concepts(concepts: list[dict], start: int, needed: int):
    selected, points, index = [], 0, start
    while index < len(concepts) and points < needed:
        concept = concepts[index]
        index += 1
        if concept["remaining_points"] > 0:
            selected.append({key: concept[key] for key in ("concept_id", "label", "remaining_points")})
            points += concept["remaining_points"]
    return selected, points, index


def build_plan(*, today: date, target_date: date, target_percent: int,
               current: dict, baseline: dict, daily: dict,
               concepts: list[dict], manual_review: list[dict],
               preview_offset: int = 0, preview_days: int = 7) -> dict:
    """今日用固定基準，未來假設建議的完整概念順利完成，只作預覽。

    baseline 的完成量使用保存範圍；daily 配額固定，但完成量包含所有當日
    真實完成重點。關卡依地圖編號排序，preview_days=0 省略未來計算。
    """
    total, complete = current["assessable_points"], current["completed_points"]
    target = _target(total, target_percent)
    remaining = max(0, target - complete)
    achieved = total > 0 and remaining == 0
    days_remaining = max(0, (target_date - today).days + 1)

    baseline_days = max(1, (target_date - baseline["date"]).days + 1)
    elapsed = min(baseline_days, max(0, (today - baseline["date"]).days))
    baseline_remaining = max(0, _target(baseline["assessable_points"], target_percent)
                             - baseline["completed_points"])
    ordinary, extra_days = divmod(baseline_remaining, baseline_days)
    # 原每日 ceil(剩餘／剩天) 會先分配餘數；不可改用另一個線性四捨五入公式。
    expected = ordinary * elapsed + min(elapsed, extra_days)
    since_baseline = max(0, baseline["current_completed_points"] - baseline["completed_points"])
    behind = 0 if achieved else min(remaining, max(0, expected - since_baseline))

    quota = daily["quota_points"]
    completed_today = max(0, daily["completed_now"] - daily["completed_at_start"])
    done_today = achieved or ((daily["assessable_points"] > 0 or completed_today > 0)
                              and completed_today >= quota)
    # 整體目標若先達成便不再安排額外工作；原今日 quota 仍如實保留。
    remaining_today = 0 if done_today else min(remaining, max(0, quota - completed_today))
    selected, scheduled, cursor = _take_concepts(concepts, 0, remaining_today)
    today_stage = {
        "date": today.isoformat(), "quota_points": quota,
        "completed_points": completed_today, "remaining_points": remaining_today,
        "scheduled_points": scheduled, "done": done_today, "concepts": selected,
    }

    future = []
    future_remaining = max(0, remaining - scheduled)
    day = today + timedelta(days=1)
    has_more = False
    if preview_days == 0:
        has_more = day <= target_date and future_remaining > 0 and cursor < len(concepts)
    else:
        position = 0
        while day <= target_date and future_remaining > 0 and cursor < len(concepts):
            future_quota = _quota(future_remaining, (target_date - day).days + 1)
            selected, assigned, cursor = _take_concepts(concepts, cursor, future_quota)
            if not selected:
                break
            if position >= preview_offset + preview_days:
                has_more = True
                break
            if position >= preview_offset:
                future.append({
                    "date": day.isoformat(), "quota_points": future_quota,
                    "completed_points": 0, "remaining_points": future_quota,
                    "scheduled_points": assigned, "done": False, "concepts": selected,
                })
            future_remaining = max(0, future_remaining - assigned)
            position += 1
            day += timedelta(days=1)

    return {
        "target_date": target_date.isoformat(), "target_percent": target_percent,
        "assessable_points": total, "completed_points": complete,
        "target_points": target, "remaining_points": remaining, "achieved": achieved,
        "days_remaining": days_remaining, "overdue_days": max(0, (today - target_date).days),
        "behind_points": behind, "daily_limit": DAILY_LIMIT,
        "quota_over_limit": not done_today and quota > DAILY_LIMIT,
        "scheduled_over_limit": scheduled > DAILY_LIMIT,
        "today_stage": today_stage, "future_stages": future,
        "preview_offset": preview_offset, "preview_has_more": has_more,
        "manual_review": manual_review,
    }
