"""教材目標與當日計量基準；所有排期讀取皆不寫入。"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, text

from runtime.errors import SourceError
from runtime.learner_session import TrustedLearner
from runtime.storage.database import database_session
from runtime.storage.tables import LearningGoal, Material, KnowledgeStructure
from .goal_metrics import load_goal_evidence, measure_targets, read_goal_metrics
from .goal_planning import daily_quota, build_plan, _target
from .starting_levels import route_order, diagnostic_correct_concepts

TIMEZONE = ZoneInfo("Asia/Taipei")


def _now():
    return datetime.now(UTC)


def _material(db, owner, material_id, *, lock=False):
    query = select(Material).where(Material.material_id == material_id, Material.learner_id == owner,
                                   Material.discard_requested_at.is_(None))
    material = db.scalar(query.with_for_update() if lock else query)
    if material is None:
        raise SourceError("RESOURCE_NOT_FOUND")
    return material


def _days_remaining(today, target_date):
    return max(0, (target_date - today).days + 1)


def _day_before(today):
    return datetime.combine(today, time(), TIMEZONE).astimezone(UTC) - timedelta(microseconds=1)


def _rebase(db, goal, revision, now):
    """設定／新版依新範圍的日初進度重排，保留今天已完成的成果。"""
    today = now.astimezone(TIMEZONE).date()
    metrics = read_goal_metrics(db, TrustedLearner(goal.learner_id), goal.material_id, revision,
                                through=_day_before(today))
    # 時刻記實際重排；完成基準與配額均取該日開始前，不能把今天成果歸零。
    goal.baseline_at, goal.baseline_revision = now, revision
    goal.baseline_assessable_points = metrics["assessable_points"]
    goal.baseline_completed_points = metrics["completed_points"]
    goal.baseline_unavailable_claim_ids = list(metrics["unavailable_claim_ids"])
    goal.daily_date = today
    goal.daily_unavailable_claim_ids = list(metrics["unavailable_claim_ids"])
    goal.daily_completed_points = metrics["all_completed_points"]
    goal.daily_quota_points = daily_quota(metrics["assessable_points"], metrics["completed_points"],
        goal.target_percent, _days_remaining(today, goal.target_date))
    return metrics


def rebase_goal_for_revision(db, owner, material_id, revision):
    """publisher 已持有 Material 鎖且新 head 已發布，沿相同交易重排現存目標。"""
    goal = db.scalar(select(LearningGoal).where(LearningGoal.material_id == material_id,
                                              LearningGoal.learner_id == owner).with_for_update())
    material = db.get(Material, material_id)
    if goal is None and material.pending_goal:
        goal = LearningGoal(material_id=material_id, learner_id=owner,
            target_date=date.fromisoformat(material.pending_goal['target_date']),
            target_percent=material.pending_goal['target_percent'], created_at=_now())
        metrics = _rebase(db, goal, revision, _now())
        goal.notice_revision, goal.notice_points = revision, metrics['assessable_points']
        db.add(goal)
        material.pending_goal = None
    elif goal is not None and goal.baseline_revision != revision:
        _rebase(db, goal, revision, _now())


def freeze_goal_day(db, owner, material_id):
    """在會改變可測範圍／答案的寫入前凍結今天；呼叫者已持有 Material 鎖。"""
    now = _now()
    today = now.astimezone(TIMEZONE).date()
    checked = db.info.setdefault("goal_frozen_days", set())
    key = (owner, material_id, today)
    if key in checked:
        return
    goal = db.scalar(select(LearningGoal).where(LearningGoal.material_id == material_id,
                                              LearningGoal.learner_id == owner).with_for_update())
    if goal is None or goal.daily_date == today:
        checked.add(key)
        return
    scope = load_goal_evidence(db, TrustedLearner(owner), material_id, goal.baseline_revision)
    before = measure_targets(scope.plan, scope.events, scope.unavailable, through=_day_before(today))
    goal.daily_date = today
    goal.daily_unavailable_claim_ids = sorted(scope.unavailable)
    all_before = measure_targets(scope.plan, scope.events, through=_day_before(today))
    goal.daily_completed_points = all_before["completed_points"]
    goal.daily_quota_points = daily_quota(before["assessable_points"], before["completed_points"],
        goal.target_percent, _days_remaining(today, goal.target_date))
    checked.add(key)


def _project(db, material, goal, now, *, preview_offset=0, preview_days=7):
    today = now.astimezone(TIMEZONE).date()
    revision = material.head_revision
    if revision is None:
        return {'schema':'learning-goal/v2', 'material_id':str(material.material_id),
                'knowledge_structure_revision':None, 'run_id':None, 'today':today,
                'goal':None, 'pending_goal':material.pending_goal}
    run_id = db.scalar(select(KnowledgeStructure.run_id).where(
        KnowledgeStructure.learner_id == material.learner_id, KnowledgeStructure.material_id == material.material_id,
        KnowledgeStructure.structure_revision == revision))
    result = {"schema": "learning-goal/v2", "pending_goal": None, "material_id": str(material.material_id),
              "knowledge_structure_revision": revision, "run_id": str(run_id), "today": today, "goal": None}
    if goal is None:
        return result
    if goal.baseline_revision != revision:
        raise SourceError("LEARNING_GOAL_STALE")
    scope = load_goal_evidence(db, TrustedLearner(material.learner_id), material.material_id, revision)
    current = measure_targets(scope.plan, scope.events, scope.unavailable,
                              completed_concept_ids=scope.completed_concept_ids)
    if goal.daily_date == today:
        daily_mask = goal.daily_unavailable_claim_ids
        completed_at_start = goal.daily_completed_points
        quota = goal.daily_quota_points
    else:
        # 本日尚未發生相關寫入，現在的可測範圍就是日初範圍；GET 只推算、不保存。
        daily_mask = scope.unavailable
        before = measure_targets(scope.plan, scope.events, daily_mask, through=_day_before(today))
        all_before = measure_targets(scope.plan, scope.events, through=_day_before(today))
        completed_at_start = all_before["completed_points"]
        quota = daily_quota(before["assessable_points"], before["completed_points"], goal.target_percent,
                            _days_remaining(today, goal.target_date))
    day_now = measure_targets(scope.plan, scope.events, daily_mask)
    all_now = measure_targets(scope.plan, scope.events)
    baseline_now = measure_targets(scope.plan, scope.events, goal.baseline_unavailable_claim_ids)
    correct = diagnostic_correct_concepts(db, scope.study) if scope.study is not None else ()
    order = route_order(scope.plan, scope.states, material.starting_level, correct)
    weak = {state.concept_id for state in scope.states if state.weak_claim_ids}
    order.sort(key=lambda identity: identity not in weak)
    by_concept = {concept["concept_id"]: concept for concept in current["concepts"]}
    concepts = [{key: by_concept[identity][key] for key in ("concept_id", "label", "remaining_points")}
                for identity in order if by_concept[identity]["remaining_points"]]
    manual = [{key: concept[key] for key in ("concept_id", "label", "remaining_points")}
              for concept in current["concepts"] if concept["manual_review_points"]]
    value = build_plan(today=today, target_date=goal.target_date, target_percent=goal.target_percent,
        current=current,
        baseline={"date": goal.baseline_at.astimezone(TIMEZONE).date(),
                  "assessable_points": goal.baseline_assessable_points,
                  "completed_points": goal.baseline_completed_points,
                  "current_completed_points": baseline_now["completed_points"]},
        daily={"assessable_points": day_now["assessable_points"], "completed_at_start": completed_at_start,
               "completed_now": all_now["completed_points"], "quota_points": quota},
        concepts=concepts, manual_review=manual, preview_offset=preview_offset, preview_days=preview_days)
    value["update_notice"] = ({"knowledge_structure_revision": revision,
        "previous_points": goal.notice_points, "current_points": current["assessable_points"]}
        if goal.notice_revision != revision else None)
    value['suggestions'] = []
    if value['quota_over_limit'] or value['scheduled_over_limit']:
        before = read_goal_metrics(db, TrustedLearner(material.learner_id), material.material_id,
                                   revision, through=_day_before(today))
        def candidate(target_date, percent):
            quota = daily_quota(before['assessable_points'], before['completed_points'], percent,
                                _days_remaining(today, target_date))
            plan = build_plan(today=today, target_date=target_date, target_percent=percent, current=current,
                baseline={'date':today, 'assessable_points':before['assessable_points'],
                          'completed_points':before['completed_points'], 'current_completed_points':current['completed_points']},
                daily={'assessable_points':current['assessable_points'], 'completed_at_start':before['all_completed_points'],
                       'completed_now':all_now['completed_points'], 'quota_points':quota},
                concepts=concepts, manual_review=manual, preview_days=len(concepts))
            return max([plan['today_stage']['quota_points'], plan['today_stage']['scheduled_points'],
                        *(stage['scheduled_points'] for stage in plan['future_stages']),
                        *(stage['quota_points'] for stage in plan['future_stages'])])
        remaining = max(0, _target(before['assessable_points'], goal.target_percent) - before['completed_points'])
        for days in range(max(1, _days_remaining(today, goal.target_date) + 1, (remaining + 19) // 20), remaining + 1):
            target = today + timedelta(days=days-1)
            daily = candidate(target, goal.target_percent)
            if daily <= 20:
                value['suggestions'].append({'kind':'extend_date', 'target_date':target,
                    'target_percent':goal.target_percent, 'daily_points':daily})
                break
        for percent in (80, 60):
            if percent < goal.target_percent:
                daily = candidate(goal.target_date, percent)
                if daily <= 20:
                    value['suggestions'].append({'kind':'lower_target', 'target_date':goal.target_date,
                        'target_percent':percent, 'daily_points':daily})
    result["goal"] = value
    return result


def record_goal_milestones(db, owner, material_id):
    """交卷後在同一交易記錄今天的分天關卡完成與準時達成目標（成就與 XP 使用）。

    今日配額大於 0 且今天真實完成的重點達到配額才算過關，目標已先達成而配額為 0 的日子不算；
    目標設定（或最近一次修改）那一刻已達標的不算準時達成。每份教材每天一關、準時達成只記一次。
    呼叫者已持有 Material 鎖；目標不存在或尚未跟上最新版本時不記錄。
    """
    goal = db.scalar(select(LearningGoal).where(LearningGoal.material_id == material_id,
                                              LearningGoal.learner_id == owner))
    material = db.get(Material, material_id)
    if goal is None or material is None or goal.baseline_revision != material.head_revision:
        return
    now = _now()
    today = now.astimezone(TIMEZONE).date()
    view = _project(db, material, goal, now, preview_days=0)["goal"]
    stage = view["today_stage"]
    values = {"owner": owner, "material": material_id, "today": today, "now": now, "target": goal.target_date}
    if stage["quota_points"] > 0 and stage["completed_points"] >= stage["quota_points"]:
        db.execute(text(
            """
            INSERT INTO goal_milestones (learner_id, material_id, kind, day, created_at)
            VALUES (:owner, :material, 'stage', :today, :now) ON CONFLICT DO NOTHING
            """
        ), values)
    if not view["achieved"] or today > goal.target_date or db.scalar(text(
        "SELECT 1 FROM goal_milestones WHERE material_id = :material AND kind = 'achieved'"
    ), values):
        return
    # 以設定／修改目標那一刻的真實完成量判斷（含當天稍早的成果），先完成再設目標不算。
    before = read_goal_metrics(db, TrustedLearner(owner), material_id, goal.baseline_revision, through=goal.baseline_at)
    if before["assessable_points"] and before["completed_points"] >= _target(before["assessable_points"], goal.target_percent):
        return
    db.execute(text(
        """
        INSERT INTO goal_milestones (learner_id, material_id, kind, day, target_date, created_at)
        VALUES (:owner, :material, 'achieved', :today, :target, :now) ON CONFLICT DO NOTHING
        """
    ), values)


def read_goal(db, owner, material_id, *, now=None, preview_offset=0, preview_days=7):
    material = _material(db, owner, material_id)
    goal = db.get(LearningGoal, material_id)
    return _project(db, material, goal, now or _now(), preview_offset=preview_offset, preview_days=preview_days)


def set_goal(learner, material_id, target_date, target_percent, *, dsn=None):
    now = _now()
    if target_percent not in (60, 80, 100) or type(target_percent) is not int:
        raise SourceError("REQUEST_INVALID")
    with database_session(dsn) as db:
        material = _material(db, learner.learner_id, material_id, lock=True)
        goal = db.scalar(select(LearningGoal).where(LearningGoal.material_id == material_id,
                                                 LearningGoal.learner_id == learner.learner_id).with_for_update())
        # 過期目標仍可保留原日期調整比例；不能新設或改到另一個過去日期。
        existing_date = goal.target_date if goal else date.fromisoformat(material.pending_goal["target_date"]) if material.pending_goal else None
        if target_date < now.astimezone(TIMEZONE).date() and existing_date != target_date:
            raise SourceError("REQUEST_INVALID")
        if material.head_revision is None:
            material.pending_goal = {'target_date':target_date.isoformat(), 'target_percent':target_percent}
            db.flush()
            return _project(db, material, None, now)
        if goal is None:
            goal = LearningGoal(material_id=material_id, learner_id=learner.learner_id,
                                target_date=target_date, target_percent=target_percent, created_at=now)
            metrics = _rebase(db, goal, material.head_revision, now)
            goal.notice_revision, goal.notice_points = material.head_revision, metrics["assessable_points"]
            db.add(goal)
        elif goal.target_date != target_date or goal.target_percent != target_percent:
            goal.target_date, goal.target_percent = target_date, target_percent
            metrics = _rebase(db, goal, material.head_revision, now)
            goal.notice_revision, goal.notice_points = material.head_revision, metrics["assessable_points"]
        db.flush()
        return _project(db, material, goal, now)


def acknowledge_update(learner, material_id, revision, *, dsn=None):
    with database_session(dsn) as db:
        material = _material(db, learner.learner_id, material_id, lock=True)
        if revision != material.head_revision:
            raise SourceError("LEARNING_GOAL_STALE")
        goal = db.scalar(select(LearningGoal).where(LearningGoal.material_id == material_id,
                                                 LearningGoal.learner_id == learner.learner_id).with_for_update())
        if goal is None:
            raise SourceError("RESOURCE_NOT_FOUND")
        if goal.notice_revision != revision:
            metrics = read_goal_metrics(db, learner, material_id, revision)
            goal.notice_revision, goal.notice_points = revision, metrics["assessable_points"]
        return _project(db, material, goal, _now())


def list_goals(db, owner, *, now=None):
    now = now or _now()
    rows = db.execute(select(Material, LearningGoal).join(LearningGoal,
        (LearningGoal.material_id == Material.material_id) & (LearningGoal.learner_id == Material.learner_id))
        .where(Material.learner_id == owner, Material.discard_requested_at.is_(None))
        .order_by(LearningGoal.target_date, Material.created_at, Material.material_id)).all()
    goals = []
    summary_fields = ("target_date", "target_percent", "assessable_points", "completed_points",
                      "target_points", "remaining_points", "achieved", "days_remaining", "overdue_days",
                      "behind_points", "daily_limit", "quota_over_limit", "scheduled_over_limit", "today_stage")
    for material, goal in rows:
        view = _project(db, material, goal, now, preview_days=0)
        goals.append({"material_id": str(material.material_id), "material_name": material.display_name,
                      "knowledge_structure_revision": view["knowledge_structure_revision"], "run_id": view["run_id"],
                      **{key: view["goal"][key] for key in summary_fields}})
    return {"schema": "learning-goals/v1", "today": now.astimezone(TIMEZONE).date(), "goals": goals}
