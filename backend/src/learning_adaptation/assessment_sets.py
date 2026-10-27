"""單一觀念的持久題組：短交易保留工作，交易外出題，逐題檢查後原子發布。"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
from threading import Event, Thread
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import select

from runtime import luna_calls, luna_test
from runtime.material_runtime import validate_runtime_lock
from content_identity import canonical_sha256
from runtime.learner_session import TrustedLearner
from runtime.semantic_service import SemanticServiceError, request_semantics
from runtime.storage.tables import (
    Assessment,
    AssessmentSet,
    AssessmentSetItem,
    AnswerEvent,
)
from runtime.storage.database import database_session
from . import assessments
from .assessment_state import (
    ACTIVE,
    MAX_ATTEMPTS,
    AssessmentSetError,
    _scope,
    _round,
    _items,
    has_active_set,
    _cycle,
)
from .learning_states import plan_concept_content

LEASE_SECONDS = 600
_LEASE_HEARTBEAT_SECONDS = 30


def _now():
    return datetime.now(UTC)


def plan_concept(context, concept_id):
    concept = next(
        (item for item in context.concepts if item.concept_id == concept_id), None
    )
    if concept is None:
        raise AssessmentSetError("ASSESSMENT_SET_TARGET_INVALID")
    return plan_concept_content(concept)


def _create_group(
    session, study, plan, *, lock, key, fingerprint, diagnostic_set_id=None
):
    """初篩與補強共用題組／成員保存；呼叫者負責各自的資格與狀態轉移。"""
    from .learning_goals import freeze_goal_day
    freeze_goal_day(session, study.learner_id, study.material_id)
    group = AssessmentSet(
        set_id=uuid4(),
        learner_id=study.learner_id,
        material_id=study.material_id,
        study_session_id=study.study_session_id,
        knowledge_structure_revision=study.knowledge_structure_revision,
        target_concept_id=plan["concept_id"],
        kind=("remediation" if diagnostic_set_id is not None else "diagnostic"),
        diagnostic_set_id=diagnostic_set_id,
        target_plan=plan,
        requested_count=len(plan["targets"]),
        runtime_lock_document=lock,
        status="preparing" if plan["targets"] else "failed",
        set_version=1,
        idempotency_key_sha256=key,
        request_fingerprint=fingerprint,
        action_receipts={},
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(group)
    session.flush()
    for ordinal, target in enumerate(plan["targets"], 1):
        session.add(
            AssessmentSetItem(
                set_id=group.set_id,
                ordinal=ordinal,
                study_session_id=study.study_session_id,
                knowledge_structure_revision=study.knowledge_structure_revision,
                target_concept_id=plan["concept_id"],
                target_claim_id=target["claim_id"],
                state="pending",
                attempts=0,
            )
        )
    return group


def create_set_in_transaction(session, study, context, concept_id, idempotency_key, local_config, *, state=None):
    key = assessments._key(idempotency_key)
    fingerprint = bytes.fromhex(canonical_sha256({"session": str(study.study_session_id), "concept": concept_id}))
    old = session.scalar(select(AssessmentSet).where(
        AssessmentSet.study_session_id == study.study_session_id,
        AssessmentSet.idempotency_key_sha256 == key))
    if old is not None:
        if bytes(old.request_fingerprint) != fingerprint:
            raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
        return old.set_id
    if study.status not in ("active", "no_safe", "completed"):
        raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
    if has_active_set(session, study.study_session_id, concept_id):
        raise AssessmentSetError("ASSESSMENT_SET_ACTIVE")
    plan = plan_concept(context, concept_id)
    if state is None:
        from runtime.storage import knowledge_reads
        from .learning_insights import _material_states
        indexed = knowledge_reads.learning(session, study.learner_id, study.material_id, study.knowledge_structure_revision)
        state = next(item for item in _material_states(session, study, indexed)[1] if item.concept_id == concept_id)
    # 未完成概念只補剩餘重點；已完成概念的「再次練習」仍建立整組。
    if state.status != "completed":
        completed = set(state.completed_claim_ids)
        plan["targets"] = [target for target in plan["targets"]
                           if not set(target["covered_claim_ids"]) <= completed]
    study.status, study.completed_at = "active", None
    lock = deepcopy(validate_runtime_lock(local_config["runtime_lock"]))
    study.current_concept_id = concept_id
    study.last_applied_guidance_revision = study.last_applied_progress_sha256 = None
    group = _create_group(session, study, plan, lock=lock, key=key, fingerprint=fingerprint)
    return group.set_id


def create_set(learner, sid, concept_id, idempotency_key, local_config, *, dsn=None):
    with database_session(dsn) as session:
        study, context, _ = _scope(session, learner, sid, lock=True)
        return create_set_in_transaction(session, study, context, concept_id, idempotency_key, local_config)


def _touch_diagnostic(session, group):
    if group.diagnostic_set_id:
        root = session.get(AssessmentSet, group.diagnostic_set_id)
        root.set_version += 1
        root.updated_at = _now()


def record_set_answer(session, assessment):
    group = session.scalar(
        select(AssessmentSet)
        .join(AssessmentSetItem, AssessmentSetItem.set_id == AssessmentSet.set_id)
        .where(AssessmentSetItem.assessment_revision == assessment.assessment_revision)
        .with_for_update()
    )
    if group is not None:
        group.status = "in_progress"
        group.set_version += 1
        group.updated_at = _now()
        _touch_diagnostic(session, group)


def create_remediation(
    learner, sid, root_id, expected_version, key, local_config, *, dsn=None
):
    digest = assessments._key(key)
    fingerprint = bytes.fromhex(
        canonical_sha256(
            {
                "diagnostic_set_id": str(root_id),
                "version": expected_version,
            }
        )
    )
    with database_session(dsn) as session:
        study, _, _ = _scope(session, learner, sid, lock=True)
        old = session.scalar(
            select(AssessmentSet).where(
                AssessmentSet.study_session_id == sid,
                AssessmentSet.idempotency_key_sha256 == digest,
            )
        )
        if old is not None:
            if bytes(old.request_fingerprint) != fingerprint:
                raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
            return old.set_id
        root = _round(session, study, root_id, lock=True)
        cycle = _cycle(session, study, root)
        if (
            root.kind != "diagnostic"
            or root.set_version != expected_version
            or not cycle["can_create_remediation"]
        ):
            raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
        selected = {
            point["claim_id"]
            for point in cycle["points"]
            if point["result"] == "needs_review"
        }
        targets = [
            deepcopy(target)
            for target in root.target_plan["targets"]
            if target["claim_id"] in selected
        ]
        plan = {
            "policy": "needs-review-points/v1",
            "concept_id": root.target_concept_id,
            "point_count": len(targets),
            "targets": targets,
            "excluded": [],
        }
        lock = deepcopy(validate_runtime_lock(local_config["runtime_lock"]))
        group = _create_group(
            session, study, plan, lock=lock, key=digest, fingerprint=fingerprint,
            diagnostic_set_id=root.set_id,
        )
        study.current_concept_id = root.target_concept_id
        root.set_version += 1
        root.updated_at = _now()
        return group.set_id


def _assessment_row(study, item, prepared):
    public, private, provenance = (
        prepared[name] for name in ("public", "private", "provenance")
    )
    return Assessment(
        assessment_revision=public["assessment_revision"],
        study_session_id=study.study_session_id,
        knowledge_structure_revision=study.knowledge_structure_revision,
        question_id=public["question_id"],
        semantic_identity=prepared["semantic_identity"],
        learning_angle=provenance["learning_angle"],
        target_concept_id=item.target_concept_id,
        target_claim_id=item.target_claim_id,
        public_document=public,
        private_answer_document=private,
        generation_provenance=provenance,
        mastery_qualified=True,
        request_idempotency_key_sha256=assessments._key(
            f"set:{item.set_id}:{item.ordinal}"
        ),
        request_fingerprint=assessments._fingerprint(
            study.study_session_id,
            study.knowledge_structure_revision,
            item.target_claim_id,
        ),
        created_at=_now(),
    )


def _seal(session, study, group, items):
    if group.sealed_at is not None:
        raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
    from .learning_goals import freeze_goal_day
    freeze_goal_day(session, study.learner_id, study.material_id)
    expected_provenance = assessments._provenance(group)
    for item in items:
        if item.state == "verified":
            row = _assessment_row(study, item, item.prepared_document)
            assessments._stored(row, expected_provenance)
            session.add(row)
            session.flush()
            item.assessment_revision = row.assessment_revision
            item.state, item.prepared_document = "published", None
        else:
            item.state, item.prepared_document = "omitted", None
    group.status, group.sealed_at = "ready", _now()
    group.lease_token = group.lease_expires_at = None


def submit_set_answers(
    learner, sid, set_id, answers, expected_version, idempotency_key, *, dsn=None
):
    """整組驗證、評分與結束在同一交易內；回應遺失可用同一意圖查回／重送。"""
    from . import answer_events

    if (
        type(expected_version) is not int
        or expected_version < 1
        or not isinstance(answers, list)
        or not answers
        or any(
            not isinstance(answer, dict)
            or set(answer)
            != {
                "assessment_revision",
                "question_id",
                "selected_option_id",
            }
            or not all(isinstance(value, str) for value in answer.values())
            for answer in answers
        )
    ):
        raise AssessmentSetError("ASSESSMENT_SET_REQUEST_INVALID")
    by_revision = {answer["assessment_revision"]: answer for answer in answers}
    if len(by_revision) != len(answers):
        raise AssessmentSetError("ASSESSMENT_SET_REQUEST_INVALID")
    key = assessments._key(idempotency_key).hex()
    fingerprint = canonical_sha256(
        {
            "action": "submit-set",
            "version": expected_version,
            "answers": sorted(
                answers, key=lambda answer: answer["assessment_revision"]
            ),
        }
    )
    with database_session(dsn) as session:
        study, _, _ = _scope(session, learner, sid, lock=True)
        group = _round(session, study, set_id, lock=True)
        previous = group.action_receipts.get(key)
        if previous is not None:
            if previous != fingerprint:
                raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
            return
        if (
            group.set_version != expected_version
            or group.status not in ("ready", "in_progress")
            or group.sealed_at is None
            or study.status not in ("active", "no_safe")
        ):
            raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
        items = [item for item in _items(session, group) if item.state == "published"]
        if not items or set(by_revision) != {
            item.assessment_revision for item in items
        }:
            raise AssessmentSetError("ASSESSMENT_SET_REQUEST_INVALID")
        validated = []
        provenance_by_set = {group.set_id: assessments._provenance(group)}
        for item in items:
            row = answer_events._assessment(
                session, study, item.assessment_revision, provenance_by_set
            )
            answer = by_revision[row.assessment_revision]
            if answer["question_id"] != row.question_id or answer[
                "selected_option_id"
            ] not in {option["option_id"] for option in row.public_document["options"]}:
                raise AssessmentSetError("ASSESSMENT_SET_REQUEST_INVALID")
            saved = session.scalar(
                select(AnswerEvent).where(
                    AnswerEvent.study_session_id == sid,
                    AnswerEvent.assessment_revision == row.assessment_revision,
                )
            )
            if saved is not None:
                answer_events._event(saved, row, study)
                if saved.selected_option_id != answer["selected_option_id"]:
                    raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
            validated.append((item.ordinal, row, answer["selected_option_id"], saved))
        # 先驗完整組再寫；任一寫入／commit 失敗會一起回滾。
        for ordinal, row, selected, saved in validated:
            if saved is None:
                answer_events.record_answer(
                    session,
                    study,
                    row,
                    selected,
                    f"set-submit:{set_id}:{key}:{ordinal}",
                )
        group.status, group.completed_at = "completed", _now()
        group.set_version += 1
        group.updated_at = _now()
        group.action_receipts = {**group.action_receipts, key: fingerprint}
        _touch_diagnostic(session, group)
        session.flush()
        from .learning_goals import record_goal_milestones
        # 成就用的關卡／達標紀錄失敗不影響交卷：只回滾這個 savepoint 並記錄錯誤類型。
        try:
            with session.begin_nested():
                record_goal_milestones(session, study.learner_id, study.material_id)
        except Exception as error:
            logging.getLogger(__name__).warning(
                "GOAL_MILESTONE_RECORD_FAILED set_id=%s error=%s", set_id, type(error).__name__)


def change_set(
    learner, sid, set_id, action, expected_version, idempotency_key, *, dsn=None
):
    if action not in ("retry", "publish-partial") or type(expected_version) is not int:
        raise AssessmentSetError("ASSESSMENT_SET_REQUEST_INVALID")
    key = assessments._key(idempotency_key).hex()
    fingerprint = canonical_sha256({"action": action, "version": expected_version})
    with database_session(dsn) as session:
        study, _, _ = _scope(session, learner, sid, lock=True)
        group = _round(session, study, set_id, lock=True)
        previous = group.action_receipts.get(key)
        if previous is not None:
            if previous != fingerprint:
                raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
            return
        if group.set_version != expected_version:
            raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
        rows = _items(session, group)
        if action == "retry":
            other = session.scalar(
                select(AssessmentSet.set_id)
                .where(
                    AssessmentSet.study_session_id == sid,
                    AssessmentSet.set_id != set_id,
                    AssessmentSet.status.in_(ACTIVE),
                    AssessmentSet.target_concept_id == group.target_concept_id,
                )
                .limit(1)
            )
            if (
                other is not None
                or study.status not in ("active", "no_safe")
                or group.status not in ("partial_ready", "failed")
                or not any(
                    item.state == "failed" and item.attempts < MAX_ATTEMPTS
                    for item in rows
                )
            ):
                raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
            from .learning_goals import freeze_goal_day
            freeze_goal_day(session, study.learner_id, study.material_id)
            for item in rows:
                if item.state == "failed" and item.attempts < MAX_ATTEMPTS:
                    item.state, item.failure_reason = "pending", None
            group.status = "preparing"
        elif action == "publish-partial":
            if group.status != "partial_ready" or not any(
                item.state == "verified" for item in rows
            ):
                raise AssessmentSetError("ASSESSMENT_SET_CONFLICT")
            _seal(session, study, group, rows)
        group.set_version += 1
        group.updated_at = _now()
        group.action_receipts = {**group.action_receipts, key: fingerprint}
        session.flush()
        _touch_diagnostic(session, group)


@dataclass(frozen=True)
class SetWork:
    learner_id: UUID
    study_session_id: UUID
    set_id: UUID
    ordinal: int
    token: UUID


def _settle(session, study, group, items):
    if any(item.state in ("pending", "generating") for item in items):
        return
    if items and all(item.state == "verified" for item in items):
        _seal(session, study, group, items)
    else:
        group.status = (
            "partial_ready"
            if any(item.state == "verified" for item in items)
            else "failed"
        )
        group.lease_token = group.lease_expires_at = None
    group.set_version += 1
    group.updated_at = _now()
    _touch_diagnostic(session, group)


def claim_set_work(*, dsn=None):
    # 先挑 ID，再按 Material → Study → Set 取鎖；模型呼叫不持有任何 DB 鎖。
    with database_session(dsn) as session:
        candidates = session.execute(
            select(
                AssessmentSet.learner_id,
                AssessmentSet.study_session_id,
                AssessmentSet.set_id,
            )
            .where(AssessmentSet.status == "preparing")
            .order_by(AssessmentSet.created_at)
        ).all()
    for owner, sid, set_id in candidates:
        try:
            with database_session(dsn) as session:
                study, _, _ = _scope(session, TrustedLearner(owner), sid, lock=True)
                group = _round(session, study, set_id, lock=True)
                if group.status != "preparing" or (
                    group.lease_expires_at is not None
                    and group.lease_expires_at > _now()
                ):
                    continue
                rows = _items(session, group)
                for item in rows:
                    if item.state == "generating":
                        item.state, item.failure_reason = (
                            "failed",
                            "GENERATION_INTERRUPTED",
                        )
                if study.status not in ("active", "no_safe"):
                    group.status = "cancelled"
                    group.lease_token = group.lease_expires_at = None
                    group.set_version += 1
                    _touch_diagnostic(session, group)
                    continue
                pending = next((item for item in rows if item.state == "pending"), None)
                if pending is None:
                    _settle(session, study, group, rows)
                    continue
                token = uuid4()
                group.lease_token, group.lease_expires_at = (
                    token,
                    _now() + timedelta(seconds=LEASE_SECONDS),
                )
                pending.state, pending.attempts = "generating", pending.attempts + 1
                group.set_version += 1
                group.updated_at = _now()
                _touch_diagnostic(session, group)
                return SetWork(owner, sid, set_id, pending.ordinal, token)
        except AssessmentSetError:
            continue
    return None


def _leased(session, work):
    study, context, document = _scope(
        session,
        TrustedLearner(work.learner_id),
        work.study_session_id,
        lock=True,
    )
    group = _round(session, study, work.set_id, lock=True)
    if (
        group.status != "preparing"
        or group.lease_token != work.token
        or group.lease_expires_at is None
        or group.lease_expires_at <= _now()
        or study.status not in ("active", "no_safe")
    ):
        raise AssessmentSetError("ASSESSMENT_SET_STALE_WORK")
    return study, context, document, group


def _heartbeat(work, stop, dsn, stale=None):
    while not stop.wait(_LEASE_HEARTBEAT_SECONDS):
        try:
            with database_session(dsn) as session:
                _, _, _, group = _leased(session, work)
                group.lease_expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
        except AssessmentSetError:
            if stale is not None:
                stale.set()
            return
        except Exception:
            # DB 短暫忙碌時續試；_leased 仍會拒絕取消、過期或已換 token 的工作。
            continue


def _bounded_prior(prior, claim):
    evidence = {item.evidence_id for item in claim.evidence}
    ordered = sorted(
        prior,
        key=lambda row: (
            row.target_claim_id != claim.claim_id,
            not bool(evidence.intersection(row.public_document["source_evidence_ids"])),
        ),
    )
    selected, size = [], 0
    for item in ordered:
        cost = len(
            json.dumps(
                {
                    "public": item.public_document,
                    "answer": item.private_answer_document["correct_answer"],
                },
                ensure_ascii=False,
            ).encode()
        )
        if size + cost > assessments._PRIOR_CONTEXT_MAX_BYTES:
            continue
        selected.append(item)
        size += cost
        if len(selected) == assessments._PRIOR_LIMIT:
            break
    return selected


def execute_set_work(work, *, dsn=None, semantic_call=request_semantics, shutdown=None):
    trace = None
    try:
        stop = Event()
        stale = Event()

        def check_wait():
            if stale.is_set() or (shutdown is not None and shutdown.is_set()):
                raise AssessmentSetError("ASSESSMENT_SET_STALE_WORK")

        heartbeat = Thread(target=_heartbeat, args=(work, stop, dsn, stale), daemon=True)
        heartbeat.start()
        prepared = None
        reason = None
        try:
            check_wait()
            with database_session(dsn) as session:
                study, context, _, group = _leased(session, work)
                item = session.get(AssessmentSetItem, (work.set_id, work.ordinal))
                if item.state != "generating":
                    raise AssessmentSetError("ASSESSMENT_SET_STALE_WORK")
                concept = next(
                    concept for concept in context.concepts
                    if concept.concept_id == item.target_concept_id
                )
                claim = next(
                    point
                    for point in concept.claims
                    if point.claim_id == item.target_claim_id
                )
                lock = deepcopy(group.runtime_lock_document)
                trace = luna_calls.start(
                    "assessment", (work.set_id, work.ordinal), work.token, study.material_id, work.learner_id,
                    enabled=luna_test.enabled(lock),
                )
                study_data = SimpleNamespace(
                    study_session_id=study.study_session_id,
                    knowledge_structure_revision=study.knowledge_structure_revision,
                )
                prior = assessments._prior_questions(session, study, claim)
                expected_provenance = assessments._provenance(group)
                verified = [
                    row for row in _items(session, group) if row.state == "verified"
                ]
                staged = [
                    assessments._stored(
                        _assessment_row(study, row, row.prepared_document),
                        expected_provenance,
                    )
                    for row in verified
                ]
                used = set(
                    session.scalars(
                        select(Assessment.semantic_identity).where(
                            Assessment.study_session_id == study.study_session_id
                        )
                    )
                )
                used.update(row.semantic_identity for row in staged)
                prior = _bounded_prior([*staged, *prior], claim)

            def model(client, **kwargs):
                check_wait()
                with database_session(dsn) as session:
                    _leased(session, work)
                result = semantic_call(client, **kwargs, cancellation_check=check_wait)
                check_wait()
                with database_session(dsn) as session:
                    _leased(session, work)
                return luna_calls.capture(result) if semantic_call is not request_semantics else result

            chosen = assessments.prepare_assessment(
                study_data,
                concept,
                claim,
                prior,
                used,
                runtime_lock=lock,
                semantic_call=model,
            )
            if chosen is None:
                reason = "NO_SAFE_ASSESSMENT"
            else:
                public, private, provenance = assessments._documents(
                    study_data,
                    concept,
                    claim,
                    chosen,
                    runtime_lock=lock,
                )
                prepared = {
                    "public": public,
                    "private": private,
                    "provenance": provenance,
                    "semantic_identity": chosen["semantic_identity"],
                }
        except AssessmentSetError:
            return
        except (
            assessments.AssessmentError,
            SemanticServiceError,
        ) as error:
            allowed = {
                "NO_SAFE_ASSESSMENT",
                "ASSESSMENT_OUTPUT_INVALID",
                "ASSESSMENT_CHECK_INVALID",
                "ASSESSMENT_CONFIGURATION_INVALID",
                "SEMANTIC_SERVICE_UNAVAILABLE",
                "SEMANTIC_SERVICE_TIMEOUT",
                "SEMANTIC_RESPONSE_INVALID",
                "SEMANTIC_INPUT_TOO_LARGE",
                "SEMANTIC_OUTPUT_TRUNCATED",
                "ASSESSMENT_STORE_FAILED",
            }
            luna_calls.rejected(str(error))
            reason = str(error) if str(error) in allowed else "ASSESSMENT_GENERATION_FAILED"
        except Exception:
            reason = "ASSESSMENT_GENERATION_FAILED"
        finally:
            stop.set()
            heartbeat.join()
        # commit 回應遺失時先重讀 lease／狀態；同一份已驗證結果最多補存一次，不再呼叫模型。
        for _ in range(2):
            try:
                check_wait()
                _commit_prepared(work, prepared, reason, dsn=dsn)
                return
            except AssessmentSetError:
                return
            except Exception:
                logging.getLogger(__name__).warning(
                    "ASSESSMENT_SET_STORE_FAILED",
                    extra={"set_id": str(work.set_id)},
                )

    finally:
        if trace is not None:
            luna_calls.close(trace)


def _commit_prepared(work, prepared, reason, *, dsn):
    with database_session(dsn) as session:
        study, _, _, group = _leased(session, work)
        item = session.get(AssessmentSetItem, (work.set_id, work.ordinal))
        if item.state != "generating":
            return
        if prepared is not None:
            # 同一 session 的題目若在推論期間已發布，不能再給相同題目第二份 credit。
            duplicate = session.scalar(
                select(Assessment.assessment_revision)
                .where(
                    Assessment.study_session_id == study.study_session_id,
                    Assessment.semantic_identity == prepared["semantic_identity"],
                )
                .limit(1)
            )
            if duplicate is not None:
                prepared, reason = None, "NO_SAFE_ASSESSMENT"
        if reason == "NO_SAFE_ASSESSMENT":
            from .learning_goals import freeze_goal_day
            freeze_goal_day(session, study.learner_id, study.material_id)
        item.state = "verified" if prepared is not None else "failed"
        item.prepared_document, item.failure_reason = prepared, reason
        group.lease_token = group.lease_expires_at = None
        group.set_version += 1
        group.updated_at = _now()
        _touch_diagnostic(session, group)
        session.flush()
        _settle(session, study, group, _items(session, group))
    luna_calls.completed("succeeded" if prepared is not None else "failed", reason)


def run_next_set(*, dsn=None, shutdown=None):
    if shutdown is not None and shutdown.is_set():
        return False
    work = claim_set_work(dsn=dsn)
    if work is None:
        return False
    execute_set_work(work, dsn=dsn, shutdown=shutdown)
    return True
