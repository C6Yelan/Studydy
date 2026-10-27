"""題組的授權範圍、公開投影與完成狀態；讀取不依賴生成工作。"""

from sqlalchemy import select, text, or_

from runtime.learner_session import TrustedLearner
from runtime.storage.knowledge_structures import _read_verified_document
from runtime.storage.tables import (
    Assessment,
    AssessmentSet,
    AssessmentSetItem,
    AnswerEvent,
    Material,
    StudySession,
    StudySessionMove,
)
from runtime.storage.database import database_session
from . import assessments
from .learning_states import claim_completion
from .map_context import _context_from_validated_document

ACTIVE = ("preparing", "partial_ready", "ready", "in_progress")
MAX_ATTEMPTS = 2


class AssessmentSetError(RuntimeError):
    pass


def _scope(session, learner, sid, *, lock=False):
    if not isinstance(learner, TrustedLearner):
        raise AssessmentSetError("ASSESSMENT_SET_NOT_FOUND")
    material_id = session.scalar(
        select(StudySession.material_id).where(
            StudySession.learner_id == learner.learner_id,
            StudySession.study_session_id == sid,
        )
    )
    material_query = select(Material).where(
        Material.learner_id == learner.learner_id,
        Material.material_id == material_id,
    )
    material = session.scalar(material_query)
    if material is None or material.discard_requested_at is not None:
        raise AssessmentSetError("ASSESSMENT_SET_NOT_FOUND")
    study_query = select(StudySession).where(
        StudySession.learner_id == learner.learner_id,
        StudySession.study_session_id == sid,
    )
    study = session.scalar(study_query)
    if study is None:
        raise AssessmentSetError("ASSESSMENT_SET_NOT_FOUND")
    document = _read_verified_document(
        session,
        learner.learner_id,
        material_id,
        revision=study.knowledge_structure_revision,
    )
    context = _context_from_validated_document(material_id, document)
    if lock:
        # 整份固定版本驗證先完成；寫入鎖只保護後續狀態變更，避免大型教材阻塞其他功能。
        revision = study.knowledge_structure_revision
        material = session.scalar(
            material_query.with_for_update().execution_options(populate_existing=True)
        )
        study = session.scalar(
            study_query.with_for_update().execution_options(populate_existing=True)
        )
        if (
            material is None
            or material.discard_requested_at is not None
            or study is None
            or study.knowledge_structure_revision != revision
        ):
            raise AssessmentSetError("ASSESSMENT_SET_NOT_FOUND")
    return study, context, document


def _read_scope(session, learner, sid):
    from .study_sessions import _validate_plan
    from runtime.storage.knowledge_reads import learning

    if not isinstance(learner, TrustedLearner):
        raise AssessmentSetError("ASSESSMENT_SET_NOT_FOUND")
    study = session.scalar(
        select(StudySession)
        .join(Material, Material.material_id == StudySession.material_id)
        .where(
            StudySession.learner_id == learner.learner_id,
            StudySession.study_session_id == sid,
            Material.learner_id == learner.learner_id,
            Material.discard_requested_at.is_(None),
        )
    )
    if study is None:
        raise AssessmentSetError("ASSESSMENT_SET_NOT_FOUND")
    plan = learning(
        session,
        learner.learner_id,
        study.material_id,
        study.knowledge_structure_revision,
    )
    _validate_plan(study, plan)
    return study, plan


def _round(session, study, set_id, *, lock=False):
    query = select(AssessmentSet).where(
        AssessmentSet.set_id == set_id,
        AssessmentSet.study_session_id == study.study_session_id,
        AssessmentSet.learner_id == study.learner_id,
        AssessmentSet.material_id == study.material_id,
        AssessmentSet.knowledge_structure_revision
        == study.knowledge_structure_revision,
    )
    value = session.scalar(query.with_for_update() if lock else query)
    if value is None:
        raise AssessmentSetError("ASSESSMENT_SET_NOT_FOUND")
    return value


def _items(session, group):
    return list(
        session.scalars(
            select(AssessmentSetItem)
            .where(AssessmentSetItem.set_id == group.set_id)
            .order_by(AssessmentSetItem.ordinal)
        )
    )


def has_active_set(session, study_session_id, concept_id=None):
    query = select(AssessmentSet.set_id).where(
        AssessmentSet.study_session_id == study_session_id,
        AssessmentSet.status.in_(ACTIVE),
    )
    if concept_id is not None:
        query = query.where(or_(AssessmentSet.target_concept_id == concept_id,
                                AssessmentSet.kind == "material_diagnostic"))
    return session.scalar(query.limit(1)) is not None


def read_plan(learner, sid, concept_id, *, dsn=None):
    with database_session(dsn) as session:
        session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        study, indexed = _read_scope(session, learner, sid)
        concept = next(
            (c for c in indexed["concepts"] if c["concept_id"] == concept_id), None
        )
        if concept is None:
            raise AssessmentSetError("ASSESSMENT_SET_TARGET_INVALID")
        covered = {
            identity
            for target in concept["targets"]
            for identity in target["covered_claim_ids"]
        }
        return {
            "schema": "assessment-plan/v1",
            "study_session_id": str(sid),
            "knowledge_structure_revision": study.knowledge_structure_revision,
            "policy": "single-concept-grounded-points/v1",
            "concept_id": concept_id,
            "point_count": len(concept["claim_ids"]),
            "targets": concept["targets"],
            "excluded": [
                {"claim_id": identity, "reason": "no_content_evidence"}
                for identity in concept["claim_ids"]
                if identity not in covered
            ],
            "requested_count": len(concept["targets"]),
        }


def _cycle(session, study, root):
    """同一初篩及其補強的投影；AnswerEvent 是唯一作答事實，不保存閱讀確認或暫緩決定。"""
    snapshot = session.info.get("read_snapshot")
    cache = snapshot["cycles"] if snapshot is not None else None
    key = (study.study_session_id, root.set_id)
    if cache is not None and key in cache:
        return cache[key]
    family = list(
        session.scalars(
            select(AssessmentSet)
            .where(
                (AssessmentSet.set_id == root.set_id)
                | (AssessmentSet.diagnostic_set_id == root.set_id)
            )
            .order_by(AssessmentSet.created_at, AssessmentSet.set_id)
        )
    )
    groups = {group.set_id: group for group in family}
    items = list(
        session.scalars(
            select(AssessmentSetItem).where(AssessmentSetItem.set_id.in_(groups))
        )
    )
    by_revision = {
        item.assessment_revision: item for item in items if item.assessment_revision
    }
    events = (
        list(
            session.scalars(
                select(AnswerEvent)
                .where(
                    AnswerEvent.study_session_id == study.study_session_id,
                    AnswerEvent.assessment_revision.in_(by_revision),
                )
                .order_by(AnswerEvent.event_number)
            )
        )
        if by_revision
        else []
    )
    by_claim = {}
    for event in events:
        by_claim.setdefault(event.target_claim_id, []).append(event)
    prior_events = list(
        session.scalars(
            select(AnswerEvent)
            .where(
                AnswerEvent.study_session_id == study.study_session_id,
                AnswerEvent.target_concept_id == root.target_concept_id,
                AnswerEvent.created_at < root.created_at,
            )
            .order_by(AnswerEvent.event_number)
        )
    )
    # 新版本沿用未變重點的錯題歷史，不能因 revision 改變退回首答規則。
    if session.scalar(
        select(StudySession.study_session_id)
        .where(
            StudySession.learner_id == study.learner_id,
            StudySession.material_id == study.material_id,
            StudySession.started_at < study.started_at,
        )
        .limit(1)
    ):
        from .inherited_progress import inherited_progress
        from runtime.storage.knowledge_reads import learning

        plan = learning(
            session,
            study.learner_id,
            study.material_id,
            study.knowledge_structure_revision,
        )
        inherited, _ = inherited_progress(
            session,
            TrustedLearner(learner_id=study.learner_id),
            study.material_id,
            study.knowledge_structure_revision,
            plan,
            include_cycles=False,
        )
        prior_events = sorted(
            [event for event in inherited if event.created_at < root.created_at]
            + prior_events,
            key=lambda event: (event.created_at, str(event.answer_event_id)),
        )
    initial = {
        item.target_claim_id: item for item in items if item.set_id == root.set_id
    }
    latest_items = {}
    for group in family:
        for candidate in items:
            if candidate.set_id == group.set_id:
                latest_items[candidate.target_claim_id] = candidate
    active = next((group for group in family if group.status in ACTIVE), None)
    other_active = (
        session.scalar(
            select(AssessmentSet.set_id)
            .where(
                AssessmentSet.study_session_id == study.study_session_id,
                AssessmentSet.status.in_(ACTIVE),
                AssessmentSet.set_id.not_in(groups),
                or_(AssessmentSet.target_concept_id == root.target_concept_id,
                    AssessmentSet.kind == "material_diagnostic"),
            )
            .limit(1)
        )
        is not None
    )
    points = []
    for target in root.target_plan["targets"]:
        claim_id = target["claim_id"]
        item = initial[claim_id]
        history = by_claim.get(claim_id, [])
        initial_answer = next(
            (
                event
                for event in history
                if event.assessment_revision == item.assessment_revision
            ),
            None,
        )
        latest = history[-1] if history else None
        prior = [
            event
            for event in prior_events
            if event.target_claim_id in target["covered_claim_ids"]
        ]
        # 尚未完成的錯題不能靠另開初篩跳過雙題補強；已完成者另算本次練習。
        completion_history = history if claim_completion(prior)[0] else prior + history
        latest_item = latest_items[claim_id]
        group = groups[latest_item.set_id]
        unavailable = not latest_item.assessment_revision and (
            latest_item.failure_reason == "NO_SAFE_ASSESSMENT"
            or group.sealed_at is not None
        )
        if unavailable:
            result = "unavailable"
        elif initial_answer is None:
            result = "unanswered" if item.assessment_revision else "unavailable"
        elif initial_answer.is_correct and claim_completion(completion_history)[0]:
            result = "diagnostic_pass"
        elif claim_completion(completion_history)[0]:
            result = "remediation_pass"
        else:
            result = "needs_review"
        points.append(
            {
                "claim_id": claim_id,
                "result": result,
                "latest_answer_event_id": str(latest.answer_event_id)
                if latest
                else None,
                "latest_set_id": (
                    str(by_revision[latest.assessment_revision].set_id)
                    if latest
                    else None
                ),
            }
        )
    counts = {
        name: sum(point["result"] == name for point in points)
        for name in (
            "diagnostic_pass",
            "remediation_pass",
            "needs_review",
            "unanswered",
            "unavailable",
        )
    }
    pending = counts["needs_review"]
    unavailable = counts["unavailable"] + len(root.target_plan["excluded"])
    passed = counts["diagnostic_pass"] + counts["remediation_pass"]
    if active:
        outcome = "in_progress"
    elif pending:
        outcome = "needs_review"
    elif passed > 0 and passed == len(points) - counts["unavailable"]:
        outcome = "passed"
    else:
        outcome = "incomplete"
    can_act = (
        study.status in ("active", "no_safe")
        and root.status == "completed"
        and not active
        and not other_active
    )
    result = {
        "diagnostic_set_id": str(root.set_id),
        "concept_id": root.target_concept_id,
        "set_version": root.set_version,
        "outcome": outcome,
        "active_set_id": str(active.set_id) if active else None,
        "passed_count": passed,
        "remediation_passed_count": counts["remediation_pass"],
        "pending_count": pending,
        "unanswered_count": counts["unanswered"],
        "unavailable_count": unavailable,
        "can_create_remediation": can_act and pending > 0,
        "points": points,
    }
    if cache is not None:
        cache[key] = result
    return result


def _read_cycles(session, study):
    roots = list(
        session.scalars(
            select(AssessmentSet)
            .where(
                AssessmentSet.study_session_id == study.study_session_id,
                AssessmentSet.kind == "diagnostic",
            )
            .order_by(AssessmentSet.created_at.desc(), AssessmentSet.set_id)
        )
    )
    latest = {}
    for root in roots:
        if root.target_concept_id not in latest:
            cycle = _cycle(session, study, root)
            latest[root.target_concept_id] = {
                key: value
                for key, value in cycle.items()
                if key not in ("points", "can_create_remediation")
            }
    return list(latest.values())


def unavailable_claims(session, study, context):
    unavailable = set(study.no_safe_claim_ids)
    roots = list(
        session.scalars(
            select(AssessmentSet)
            .where(
                AssessmentSet.study_session_id == study.study_session_id,
                AssessmentSet.kind == "diagnostic",
            )
            .order_by(AssessmentSet.created_at.desc(), AssessmentSet.set_id)
        )
    )
    seen = set()
    for root in roots:
        if root.target_concept_id in seen:
            continue
        seen.add(root.target_concept_id)
        latest = {}
        for item, sealed_at in session.execute(
            select(AssessmentSetItem, AssessmentSet.sealed_at)
            .join(AssessmentSet, AssessmentSet.set_id == AssessmentSetItem.set_id)
            .where(
                (AssessmentSet.set_id == root.set_id)
                | (AssessmentSet.diagnostic_set_id == root.set_id)
            )
            .order_by(AssessmentSet.created_at, AssessmentSet.set_id)
        ):
            latest[item.target_claim_id] = (item, sealed_at)
        for target in root.target_plan["targets"]:
            item, sealed_at = latest[target["claim_id"]]
            # 尚未準備好或暫時 provider 失敗不等於已確認無安全題。
            if item.assessment_revision:
                unavailable.difference_update(target["covered_claim_ids"])
            elif item.failure_reason == "NO_SAFE_ASSESSMENT" or sealed_at is not None:
                unavailable.update(target["covered_claim_ids"])
    return unavailable


def completed_concepts(session, study):
    """重建曾完成的概念，避免後續練習或可出題範圍改變撤銷完成。"""
    roots = session.scalars(
        select(AssessmentSet).where(
            AssessmentSet.study_session_id == study.study_session_id,
            AssessmentSet.kind == "diagnostic",
            AssessmentSet.status == "completed",
        )
    )
    # 學生確認「移到新版」時依概念對應承接的完成（runtime.version_moves）；不產生作答事件。
    moved = session.scalar(
        select(StudySessionMove.completed_concept_ids).where(
            StudySessionMove.target_study_session_id == study.study_session_id
        )
    )
    return {
        root.target_concept_id
        for root in roots
        if _cycle(session, study, root)["outcome"] == "passed"
    } | set(moved or ())


def _summary(group, rows, answers):
    revisions = [item.assessment_revision for item in rows if item.assessment_revision]
    return {
        "set_id": str(group.set_id),
        "target_concept_id": group.target_concept_id,
        "kind": group.kind,
        "diagnostic_set_id": (
            str(group.diagnostic_set_id) if group.diagnostic_set_id else None
        ),
        "status": group.status,
        "set_version": group.set_version,
        "requested_count": group.requested_count,
        "published_count": len(revisions),
        "answered_count": len(answers),
        "passed_count": sum(item.is_correct for item in answers),
        "assessment_revisions": revisions,
        "created_at": group.created_at,
        "completed_at": group.completed_at,
    }


def list_sets(learner, sid, *, dsn=None):
    with database_session(dsn) as session:
        session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        study, _, _ = _scope(session, learner, sid)
        return _list_sets(session, study)


def _list_sets(session, study):
    groups = list(
        session.scalars(
            select(AssessmentSet)
            .where(AssessmentSet.study_session_id == study.study_session_id)
            .order_by(AssessmentSet.created_at.desc(), AssessmentSet.set_id)
        )
    )
    items_by_set = {group.set_id: [] for group in groups}
    answers_by_set = {group.set_id: [] for group in groups}
    if groups:
        for item, answer in session.execute(
            select(AssessmentSetItem, AnswerEvent)
            .outerjoin(
                AnswerEvent,
                (AnswerEvent.study_session_id == study.study_session_id)
                & (
                    AnswerEvent.assessment_revision
                    == AssessmentSetItem.assessment_revision
                ),
            )
            .where(AssessmentSetItem.set_id.in_(items_by_set))
            .order_by(AssessmentSetItem.ordinal)
        ):
            items_by_set[item.set_id].append(item)
            if answer is not None:
                answers_by_set[item.set_id].append(answer)
    return {
        "schema": "assessment-set-list/v1",
        "study_session_id": str(study.study_session_id),
        "knowledge_structure_revision": study.knowledge_structure_revision,
        "active_set_ids": [
            str(item.set_id) for item in groups if item.status in ACTIVE
        ],
        "sets": [
            _summary(group, items_by_set[group.set_id], answers_by_set[group.set_id])
            for group in groups
        ],
    }


def read_set(learner, sid, set_id, *, dsn=None):
    with database_session(dsn) as session:
        session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        session.info["read_snapshot"] = {"documents": {}, "inherited": {}, "cycles": {}}
        study, _ = _read_scope(session, learner, sid)
        return _read_set(session, study, set_id)


def _read_set(session, study, set_id):
    """由已驗證教材的同一 snapshot 投影題組；仍只輸出公開題目及本人作答回饋。"""
    from .answer_events import _event, _feedback

    sid = study.study_session_id
    group = _round(session, study, set_id)
    rows = _items(session, group)
    expected_provenance = assessments._provenance(group)
    revisions = [item.assessment_revision for item in rows if item.assessment_revision]
    published = {}
    if revisions:
        published = {
            assessment.assessment_revision: (assessment, event)
            for assessment, event in session.execute(
                select(Assessment, AnswerEvent)
                .outerjoin(
                    AnswerEvent,
                    (AnswerEvent.study_session_id == sid)
                    & (AnswerEvent.assessment_revision == Assessment.assessment_revision),
                )
                .where(Assessment.assessment_revision.in_(revisions))
            )
        }
    projected = []
    for item in rows:
        public = feedback = created = None
        can_submit = False
        if item.assessment_revision is not None:
            assessment, event = published[item.assessment_revision]
            stored = assessments._stored(assessment, expected_provenance)
            public, created = stored.public_document, assessment.created_at
            if event is not None:
                feedback = _feedback(
                    _event(event, assessment, study), assessment,
                    include_answer_key=(group.status == "completed"),
                ).model_dump(by_alias=True)
            else:
                # _round 已驗證題組 scope；每題隸屬此組且 assessment_revision 在 DB 中唯一。
                can_submit = (
                    study.status in ("active", "no_safe")
                    and group.status in ("ready", "in_progress")
                    and group.sealed_at is not None
                    and item.state == "published"
                )
        projected.append(
            {
                "ordinal": item.ordinal,
                "target_concept_id": item.target_concept_id,
                "target_claim_id": item.target_claim_id,
                "state": item.state,
                "attempts": item.attempts,
                "failure_reason": item.failure_reason,
                "assessment": public,
                "feedback": feedback,
                "created_at": created,
                "can_submit": can_submit,
            }
        )
    summary = _summary(
        group, rows, [event for _, event in published.values() if event is not None]
    )
    other_active = (
        session.scalar(
            select(AssessmentSet.set_id)
            .where(
                AssessmentSet.study_session_id == sid,
                AssessmentSet.set_id != set_id,
                AssessmentSet.status.in_(ACTIVE),
                (True if group.kind == "material_diagnostic" else or_(
                    AssessmentSet.target_concept_id == group.target_concept_id,
                    AssessmentSet.kind == "material_diagnostic")),
            )
            .limit(1)
        )
        is not None
    )
    cycle_root = (
        session.get(AssessmentSet, group.diagnostic_set_id)
        if group.diagnostic_set_id
        else group
    )
    return {
        "schema": "assessment-set/v1",
        "study_session_id": str(sid),
        "material_id": str(study.material_id),
        "knowledge_structure_revision": study.knowledge_structure_revision,
        **summary,
        "kind": group.kind,
        "selection_policy": group.target_plan["policy"],
        "point_count": group.target_plan["point_count"],
        "excluded_count": len(group.target_plan["excluded"]),
        "verified_count": sum(item.state in ("verified", "published") for item in rows),
        "can_retry": (
            group.status in ("partial_ready", "failed")
            and not other_active
            and study.status in ("active", "no_safe")
            and any(
                item.state == "failed" and item.attempts < MAX_ATTEMPTS for item in rows
            )
        ),
        "can_publish_partial": (
            group.status == "partial_ready"
            and any(item.state == "verified" for item in rows)
        ),
        "can_complete": group.status in ("ready", "in_progress"),
        "items": projected,
        "cycle": None if group.kind == "material_diagnostic" else _cycle(session, study, cycle_root),
    }
