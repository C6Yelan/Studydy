"""教材起始程度、跨概念診斷選材與單一開始／接續入口。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from content_identity import canonical_sha256
from runtime.errors import SourceError
from runtime.storage.database import database_session
from runtime.storage.tables import Material, StudySession, AssessmentSet, AssessmentSetItem, AnswerEvent
from runtime.storage import knowledge_reads
from runtime.material_runtime import validate_runtime_lock
from .learning_insights import _material_states, structural_difficulty, is_personal_difficulty
from .study_sessions import ensure_study_session_in_transaction, _stored

LEVELS = ("beginner", "some_knowledge", "familiar", "gaps")
MATERIAL_DIAGNOSTIC = "material_diagnostic"


def _material(db, owner, material_id, *, lock=False):
    query = select(Material).where(Material.material_id == material_id, Material.learner_id == owner,
                                   Material.discard_requested_at.is_(None))
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise SourceError("RESOURCE_NOT_FOUND")
    return row


def read_level(db, owner, material_id):
    material = _material(db, owner, material_id)
    return {"schema": "starting-level/v1", "material_id": str(material_id),
            "starting_level": material.starting_level}


def set_level(learner, material_id, level, *, dsn=None):
    if level not in LEVELS:
        raise SourceError("REQUEST_INVALID")
    with database_session(dsn) as db:
        material = _material(db, learner.learner_id, material_id, lock=True)
        material.starting_level = level
        # 程度只改後續導覽，不取消現有題組或重設任何完成事實。
        return {"schema": "starting-level/v1", "material_id": str(material_id), "starting_level": level}


def _hard_ids(plan, states):
    return {item["concept_id"] for item in structural_difficulty(plan) if item["level"] == "hard"} | {
        state.concept_id for state in states if is_personal_difficulty(state)}


def route_order(plan, states, level, diagnostic_correct=()):
    """只排未完成概念；診斷答對但尚有剩餘重點者排在錯答／未測之後。"""
    by_id = {state.concept_id: state for state in states}
    order = [identity for identity in plan["initial_learning_path"]
             if by_id[identity].status != "completed" and by_id[identity].assessable_claim_ids]
    if level not in ("familiar", "gaps"):
        return order
    correct = set(diagnostic_correct)
    hard = _hard_ids(plan, states) if level == "gaps" else set()
    def priority(identity):
        state = by_id[identity]
        deferred = identity in correct and not state.weak_claim_ids
        if level == "gaps":
            need = 0 if state.weak_claim_ids or identity in hard else 1 if not state.attempts else 2
        else:
            need = 0
        return (deferred, need)
    return sorted(order, key=priority)


def diagnostic_plan(plan, states):
    """每個未完成概念選一個未完成、可檢測重點；難點優先，最多十五題。"""
    by_id = {state.concept_id: state for state in states}
    concepts = {concept["concept_id"]: concept for concept in plan["concepts"]}
    hard = _hard_ids(plan, states)
    candidates = []
    for identity in plan["initial_learning_path"]:
        state = by_id[identity]
        if state.status == "completed":
            continue
        target = next((target for target in concepts[identity]["targets"]
                       if set(target["covered_claim_ids"]).intersection(state.assessable_claim_ids)
                       and not set(target["covered_claim_ids"]) <= set(state.completed_claim_ids)), None)
        if target is not None:
            candidates.append({**target, "concept_id": identity})
    candidates.sort(key=lambda target: target["concept_id"] not in hard)
    targets = candidates[:15]
    return {"policy": "material-diagnostic/v1", "concept_id": None,
            "point_count": len(targets), "targets": targets, "excluded": []}


def material_diagnostic(db, study):
    return db.scalar(select(AssessmentSet).where(AssessmentSet.study_session_id == study.study_session_id,
                                                 AssessmentSet.kind == MATERIAL_DIAGNOSTIC))


def diagnostic_correct_concepts(db, study, group=None):
    group = group if group is not None else material_diagnostic(db, study)
    if group is None or group.status != "completed":
        return set()
    return set(db.scalars(select(AnswerEvent.target_concept_id)
        .join(AssessmentSetItem, AssessmentSetItem.assessment_revision == AnswerEvent.assessment_revision)
        .where(AssessmentSetItem.set_id == group.set_id, AnswerEvent.study_session_id == study.study_session_id,
               AnswerEvent.is_correct.is_(True))))


def start_learning(learner, material_id, revision, idempotency_key, local_config,
                   *, current_concept_id=None, dsn=None):
    from .assessment_sets import create_set_in_transaction, _create_group
    from .assessment_state import ACTIVE, _read_cycles
    from .assessments import _key

    key = _key(idempotency_key).hex()
    fingerprint = canonical_sha256({"material_id": str(material_id), "revision": revision,
                                     "concept_id": current_concept_id})
    with database_session(dsn) as db:
        material = _material(db, learner.learner_id, material_id, lock=True)
        level = material.starting_level
        if level is None:
            raise SourceError("STARTING_LEVEL_REQUIRED")
        study, context = ensure_study_session_in_transaction(
            db, learner, material_id, revision, "study-start:" + key,
            current_concept_id=current_concept_id)
        # session 可能已存在，所有進一步決策與交卷共用 Material → Study 鎖序。
        study = db.scalar(select(StudySession).where(StudySession.study_session_id == study.study_session_id)
                          .with_for_update().execution_options(populate_existing=True))
        receipt = study.start_receipts.get(key)
        if receipt is not None:
            if receipt["fingerprint"] != fingerprint:
                raise SourceError("REQUEST_INVALID")
            selected_id = UUID(receipt["set_id"]) if receipt["set_id"] else None
        else:
            plan = knowledge_reads.learning(db, learner.learner_id, material_id, revision)
            if current_concept_id is not None and current_concept_id not in {c["concept_id"] for c in plan["concepts"]}:
                raise SourceError("REQUEST_INVALID")
            _, states, _ = _material_states(db, study, plan)
            diagnostic = material_diagnostic(db, study)
            order = route_order(plan, states, level, diagnostic_correct_concepts(db, study, diagnostic))
            active = db.scalars(select(AssessmentSet).where(
                AssessmentSet.study_session_id == study.study_session_id, AssessmentSet.status.in_(ACTIVE))
                .order_by(AssessmentSet.created_at, AssessmentSet.set_id)).all()
            has_answers = db.scalar(select(AnswerEvent.answer_event_id)
                                    .where(AnswerEvent.material_id == material_id).limit(1)) is not None
            need_diagnostic = bool(order) and (level == "familiar" or (level == "gaps" and not has_answers))
            desired = current_concept_id or study.current_concept_id
            selected = next((group for group in active if group.kind == MATERIAL_DIAGNOSTIC), None)
            selected = selected or next((group for group in active if group.target_concept_id == desired), None)
            if selected is None and active and (need_diagnostic or current_concept_id is None):
                selected = active[0]
            if selected is None and need_diagnostic and (diagnostic is None or diagnostic.status != "completed"):
                if diagnostic is not None:
                    selected = diagnostic
                else:
                    selected_plan = diagnostic_plan(plan, states)
                    if selected_plan["targets"]:
                        selected = _create_group(db, study, selected_plan,
                            lock=deepcopy(validate_runtime_lock(local_config["runtime_lock"])),
                            key=sha256(("material-diagnostic:" + key).encode()).digest(),
                            fingerprint=bytes.fromhex(canonical_sha256({"study": str(study.study_session_id), "kind": MATERIAL_DIAGNOSTIC})),
                            kind=MATERIAL_DIAGNOSTIC)
                        study.status, study.completed_at = "active", None
            if selected is None:
                target = current_concept_id or (order[0] if order else None)
                if target is not None:
                    state = next(state for state in states if state.concept_id == target)
                    cycle = next((cycle for cycle in _read_cycles(db, study) if cycle["concept_id"] == target), None)
                    if cycle and state.status != "completed" and state.assessable_claim_ids:
                        previous = db.get(AssessmentSet, UUID(cycle["diagnostic_set_id"]))
                        if cycle["outcome"] == "needs_review" or (cycle["outcome"] == "incomplete" and previous.status == "failed"):
                            selected = previous
                    if selected is None:
                        if not state.assessable_claim_ids:
                            study.current_concept_id = target
                        else:
                            selected_id = create_set_in_transaction(db, study, context, target, "start-set:" + key, local_config, state=state)
                            selected = db.get(AssessmentSet, selected_id)
                else:
                    selected = db.scalar(select(AssessmentSet).where(
                        AssessmentSet.study_session_id == study.study_session_id, AssessmentSet.status == "completed")
                        .order_by(AssessmentSet.completed_at.desc(), AssessmentSet.set_id).limit(1))
            selected_id = selected.set_id if selected else None
            if selected is not None and selected.target_concept_id is not None:
                study.current_concept_id = selected.target_concept_id
            study.start_receipts = {**study.start_receipts, key: {
                "fingerprint": fingerprint, "set_id": str(selected_id) if selected_id else None}}
            study.last_applied_guidance_revision = study.last_applied_progress_sha256 = None
        return {"schema": "study-start/v1", "session": _stored(study),
                "selected_set_id": selected_id, "starting_level": level}
