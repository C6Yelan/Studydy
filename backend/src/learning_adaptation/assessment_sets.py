"""單一觀念的持久題組：短交易保留工作，交易外出題，逐題檢查後原子發布。"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
from threading import Event, Thread
from types import SimpleNamespace
import unicodedata
from uuid import UUID, uuid4

from sqlalchemy import select, text

from pdf_evidence.material_pipeline import validate_runtime_lock
from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime import command_semantics
from runtime.learner_session import TrustedLearner
from runtime.semantic_service import SemanticServiceError, request_semantics
from runtime.storage.artifacts import _root
from runtime.storage.tables import (Assessment, AssessmentSet, AssessmentSetItem, AnswerEvent,
                                    KnowledgeStructure, Material, StudySession, database_session)
from . import assessments
from .map_context import context_from_structure

ACTIVE = ('preparing', 'partial_ready', 'ready', 'in_progress')
LEASE_SECONDS = 600
MAX_ATTEMPTS = 2


class AssessmentSetError(RuntimeError):
    pass


def _key(value):
    return assessments._key(value)


def _now():
    return datetime.now(UTC)


def _execution(lock):
    command = command_semantics.identity(lock)
    return command if command is not None else {
        'transport': 'http', 'model_id': lock['semantic_service']['model_id'],
        'model_revision': lock['semantic_service']['revision'], 'runtime_lock_sha256': canonical_sha256(lock),
    }


def _scope(session, learner, sid, *, lock=False):
    if not isinstance(learner, TrustedLearner):
        raise AssessmentSetError('ASSESSMENT_SET_NOT_FOUND')
    material_id = session.scalar(select(StudySession.material_id).where(
        StudySession.learner_id == learner.learner_id, StudySession.study_session_id == sid))
    query = select(Material).where(Material.learner_id == learner.learner_id, Material.material_id == material_id)
    material = session.scalar(query.with_for_update() if lock else query)
    if material is None or material.discard_requested_at is not None:
        raise AssessmentSetError('ASSESSMENT_SET_NOT_FOUND')
    query = select(StudySession).where(StudySession.learner_id == learner.learner_id, StudySession.study_session_id == sid)
    study = session.scalar(query.with_for_update() if lock else query)
    if study is None:
        raise AssessmentSetError('ASSESSMENT_SET_NOT_FOUND')
    document = session.scalar(select(KnowledgeStructure.document).where(
        KnowledgeStructure.learner_id == learner.learner_id, KnowledgeStructure.material_id == material_id,
        KnowledgeStructure.structure_revision == study.knowledge_structure_revision))
    if document is None:
        raise AssessmentSetError('ASSESSMENT_SET_NOT_FOUND')
    context = context_from_structure(material_id, document)
    return study, context, document


def _round(session, study, set_id, *, lock=False):
    query = select(AssessmentSet).where(AssessmentSet.set_id == set_id,
        AssessmentSet.study_session_id == study.study_session_id, AssessmentSet.learner_id == study.learner_id,
        AssessmentSet.material_id == study.material_id,
        AssessmentSet.knowledge_structure_revision == study.knowledge_structure_revision)
    value = session.scalar(query.with_for_update() if lock else query)
    if value is None:
        raise AssessmentSetError('ASSESSMENT_SET_NOT_FOUND')
    return value


def _items(session, group):
    return list(session.scalars(select(AssessmentSetItem).where(AssessmentSetItem.set_id == group.set_id)
                               .order_by(AssessmentSetItem.ordinal)))


def has_active_set(session, study_session_id, concept_id=None):
    query = select(AssessmentSet.set_id).where(AssessmentSet.study_session_id == study_session_id,
                                             AssessmentSet.status.in_(ACTIVE))
    if concept_id is not None:
        query = query.where(AssessmentSet.target_concept_id == concept_id)
    return session.scalar(query.limit(1)) is not None


def plan_concept(context, concept_id):
    concept = next((item for item in context.concepts if item.concept_id == concept_id), None)
    if concept is None:
        raise AssessmentSetError('ASSESSMENT_SET_TARGET_INVALID')
    targets, excluded, known = [], [], {}
    for claim in concept.claims:
        # 上游可能把完整定義／假設標成 heading；出題資格依引用內容，不能只靠版面類型。
        if not any(item.quote.strip() for item in claim.evidence):
            excluded.append({'claim_id': claim.claim_id, 'reason': 'no_content_evidence'})
            continue
        # 只合併相同來源與文字的重點；保留大小寫、數值與條件，不猜測語意等價。
        identity = (unicodedata.normalize('NFC', ' '.join(claim.text.split())),
                    tuple(sorted(item.evidence_id for item in claim.evidence)))
        if identity in known:
            targets[known[identity]]['covered_claim_ids'].append(claim.claim_id)
            continue
        known[identity] = len(targets)
        targets.append({'claim_id': claim.claim_id, 'covered_claim_ids': [claim.claim_id],
                        'reason': 'distinct_grounded_point'})
    return {'policy': 'single-concept-grounded-points/v1', 'concept_id': concept_id,
            'point_count': len(concept.claims), 'targets': targets, 'excluded': excluded}


def read_plan(learner, sid, concept_id, *, dsn=None):
    with database_session(dsn) as session:
        session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        study, context, document = _scope(session, learner, sid)
        plan = plan_concept(context, concept_id)
        return {'schema': 'assessment-plan/v1', 'study_session_id': str(sid),
                'knowledge_structure_revision': study.knowledge_structure_revision, **plan,
                'requested_count': len(plan['targets'])}


def create_set(learner, sid, concept_id, idempotency_key, local_config, *, dsn=None):
    key = _key(idempotency_key)
    fingerprint = bytes.fromhex(canonical_sha256({'session': str(sid), 'concept': concept_id}))
    with database_session(dsn) as session:
        study, context, document = _scope(session, learner, sid, lock=True)
        old = session.scalar(select(AssessmentSet).where(AssessmentSet.study_session_id == sid,
                                                        AssessmentSet.idempotency_key_sha256 == key))
        if old is not None:
            if bytes(old.request_fingerprint) != fingerprint:
                raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
            return old.set_id
        if study.status not in ('active', 'no_safe'):
            raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
        if has_active_set(session, sid, concept_id):
            raise AssessmentSetError('ASSESSMENT_SET_ACTIVE')
        plan = plan_concept(context, concept_id)
        lock = deepcopy(validate_runtime_lock(local_config['runtime_lock']))
        study.current_concept_id = concept_id
        study.last_applied_guidance_revision = study.last_applied_progress_sha256 = None
        group = AssessmentSet(set_id=uuid4(), learner_id=study.learner_id, material_id=study.material_id,
            study_session_id=sid, knowledge_structure_revision=study.knowledge_structure_revision,
            target_concept_id=concept_id, kind='diagnostic', target_plan=plan, requested_count=len(plan['targets']),
            runtime_lock_document=lock, execution_identity=_execution(lock),
            status='preparing' if plan['targets'] else 'failed', set_version=1,
            idempotency_key_sha256=key, request_fingerprint=fingerprint, action_receipts={},
            created_at=_now(), updated_at=_now())
        session.add(group); session.flush()
        for ordinal, target in enumerate(plan['targets'], 1):
            session.add(AssessmentSetItem(set_id=group.set_id, ordinal=ordinal, study_session_id=sid,
                knowledge_structure_revision=study.knowledge_structure_revision, target_concept_id=concept_id,
                target_claim_id=target['claim_id'], state='pending', attempts=0))
        return group.set_id


def membership_can_submit(session, study, assessment):
    membership = session.execute(select(AssessmentSetItem, AssessmentSet).join(AssessmentSet,
        AssessmentSet.set_id == AssessmentSetItem.set_id).where(
        AssessmentSetItem.assessment_revision == assessment.assessment_revision)).first()
    if membership is None:
        return study.status in ('active', 'no_safe') and study.current_concept_id == assessment.target_concept_id
    item, group = membership
    return (study.status in ('active', 'no_safe') and group.learner_id == study.learner_id
            and group.study_session_id == study.study_session_id
            and group.knowledge_structure_revision == study.knowledge_structure_revision
            and group.status in ('ready', 'in_progress') and group.sealed_at is not None
            and item.state == 'published')


def _touch_diagnostic(session, group):
    if group.diagnostic_set_id:
        root = session.get(AssessmentSet, group.diagnostic_set_id)
        root.set_version += 1
        root.updated_at = _now()


def record_set_answer(session, assessment):
    group = session.scalar(select(AssessmentSet).join(AssessmentSetItem, AssessmentSetItem.set_id == AssessmentSet.set_id)
                           .where(AssessmentSetItem.assessment_revision == assessment.assessment_revision).with_for_update())
    if group is not None:
        group.status = 'in_progress'
        group.set_version += 1
        group.updated_at = _now()
        _touch_diagnostic(session, group)


def _cycle(session, study, root):
    """同一初篩及其補強的投影；AnswerEvent 是唯一作答事實，不保存閱讀確認或暫緩決定。"""
    family = list(session.scalars(select(AssessmentSet).where(
        (AssessmentSet.set_id == root.set_id) | (AssessmentSet.diagnostic_set_id == root.set_id))
        .order_by(AssessmentSet.created_at, AssessmentSet.set_id)))
    groups = {group.set_id: group for group in family}
    items = list(session.scalars(select(AssessmentSetItem).where(AssessmentSetItem.set_id.in_(groups))))
    by_revision = {item.assessment_revision: item for item in items if item.assessment_revision}
    events = list(session.scalars(select(AnswerEvent).where(AnswerEvent.study_session_id == study.study_session_id,
        AnswerEvent.assessment_revision.in_(by_revision)).order_by(AnswerEvent.event_number))) if by_revision else []
    by_claim = {}
    for event in events:
        by_claim.setdefault(event.target_claim_id, []).append(event)
    initial = {item.target_claim_id: item for item in items if item.set_id == root.set_id}
    active = next((group for group in family if group.status in ACTIVE), None)
    other_active = session.scalar(select(AssessmentSet.set_id).where(
        AssessmentSet.study_session_id == study.study_session_id, AssessmentSet.status.in_(ACTIVE),
        AssessmentSet.set_id.not_in(groups), AssessmentSet.target_concept_id == root.target_concept_id).limit(1)) is not None
    points = []
    for target in root.target_plan['targets']:
        claim_id = target['claim_id']
        item = initial[claim_id]
        history = by_claim.get(claim_id, [])
        initial_answer = next((event for event in history if event.assessment_revision == item.assessment_revision), None)
        latest = history[-1] if history else None
        if initial_answer is None:
            result = 'unanswered' if item.assessment_revision else 'unavailable'
        elif initial_answer.is_correct:
            result = 'diagnostic_pass'
        elif latest.is_correct:
            result = 'remediation_pass'
        else:
            result = 'needs_review'
        points.append({'claim_id': claim_id, 'result': result,
            'latest_answer_event_id': str(latest.answer_event_id) if latest else None,
            'latest_set_id': str(by_revision[latest.assessment_revision].set_id) if latest else None})
    counts = {name: sum(point['result'] == name for point in points) for name in (
        'diagnostic_pass', 'remediation_pass', 'needs_review', 'unanswered', 'unavailable')}
    pending = counts['needs_review']
    unavailable = counts['unavailable'] + len(root.target_plan['excluded'])
    passed = counts['diagnostic_pass'] + counts['remediation_pass']
    if active:
        outcome = 'in_progress'
    elif pending:
        outcome = 'needs_review'
    elif points and passed == len(points) and unavailable == 0:
        outcome = 'passed'
    else:
        outcome = 'incomplete'
    can_act = study.status in ('active', 'no_safe') and root.status == 'completed' and not active and not other_active
    return {'diagnostic_set_id': str(root.set_id), 'concept_id': root.target_concept_id,
        'set_version': root.set_version, 'outcome': outcome,
        'active_set_id': str(active.set_id) if active else None,
        'passed_count': passed, 'remediation_passed_count': counts['remediation_pass'],
        'pending_count': pending, 'unanswered_count': counts['unanswered'], 'unavailable_count': unavailable,
        'can_create_remediation': can_act and pending > 0,
        'points': points}


def _read_cycles(session, study):
    roots = list(session.scalars(select(AssessmentSet).where(AssessmentSet.study_session_id == study.study_session_id,
        AssessmentSet.kind == 'diagnostic').order_by(AssessmentSet.created_at.desc(), AssessmentSet.set_id)))
    latest = {}
    for root in roots:
        if root.target_concept_id not in latest:
            cycle = _cycle(session, study, root)
            latest[root.target_concept_id] = {key: value for key, value in cycle.items()
                if key not in ('points', 'can_create_remediation')}
    return list(latest.values())


def create_remediation(learner, sid, root_id, expected_version, key, local_config, *, dsn=None):
    digest = _key(key)
    fingerprint = bytes.fromhex(canonical_sha256({'diagnostic_set_id': str(root_id), 'version': expected_version}))
    with database_session(dsn) as session:
        study, _, _ = _scope(session, learner, sid, lock=True)
        old = session.scalar(select(AssessmentSet).where(AssessmentSet.study_session_id == sid,
            AssessmentSet.idempotency_key_sha256 == digest))
        if old is not None:
            if bytes(old.request_fingerprint) != fingerprint:
                raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
            return old.set_id
        root = _round(session, study, root_id, lock=True)
        cycle = _cycle(session, study, root)
        if root.kind != 'diagnostic' or root.set_version != expected_version or not cycle['can_create_remediation']:
            raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
        selected = {point['claim_id'] for point in cycle['points'] if point['result'] == 'needs_review'}
        targets = [deepcopy(target) for target in root.target_plan['targets'] if target['claim_id'] in selected]
        plan = {'policy': 'needs-review-points/v1', 'concept_id': root.target_concept_id,
            'point_count': len(targets), 'targets': targets, 'excluded': []}
        lock = deepcopy(validate_runtime_lock(local_config['runtime_lock']))
        group = AssessmentSet(set_id=uuid4(), learner_id=study.learner_id, material_id=study.material_id,
            study_session_id=sid, knowledge_structure_revision=study.knowledge_structure_revision,
            target_concept_id=root.target_concept_id, kind='remediation', diagnostic_set_id=root.set_id,
            target_plan=plan, requested_count=len(targets), runtime_lock_document=lock, execution_identity=_execution(lock),
            status='preparing', set_version=1, idempotency_key_sha256=digest, request_fingerprint=fingerprint,
            action_receipts={}, created_at=_now(), updated_at=_now())
        session.add(group); session.flush()
        for ordinal, target in enumerate(targets, 1):
            session.add(AssessmentSetItem(set_id=group.set_id, ordinal=ordinal, study_session_id=sid,
                knowledge_structure_revision=study.knowledge_structure_revision, target_concept_id=root.target_concept_id,
                target_claim_id=target['claim_id'], state='pending', attempts=0))
        study.current_concept_id = root.target_concept_id
        root.set_version += 1
        root.updated_at = _now()
        return group.set_id


def _summary(session, group, items=None):
    rows = _items(session, group) if items is None else items
    revisions = [item.assessment_revision for item in rows if item.assessment_revision]
    answers = list(session.scalars(select(AnswerEvent).where(AnswerEvent.study_session_id == group.study_session_id,
                                                            AnswerEvent.assessment_revision.in_(revisions)))) if revisions else []
    return {'set_id': str(group.set_id), 'target_concept_id': group.target_concept_id,
            'kind': group.kind, 'diagnostic_set_id': str(group.diagnostic_set_id) if group.diagnostic_set_id else None,
            'status': group.status, 'set_version': group.set_version, 'requested_count': group.requested_count,
            'published_count': len(revisions), 'answered_count': len(answers),
            'passed_count': sum(item.is_correct for item in answers), 'assessment_revisions': revisions,
            'created_at': group.created_at, 'completed_at': group.completed_at}


def list_sets(learner, sid, *, dsn=None):
    with database_session(dsn) as session:
        session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        study, _, _ = _scope(session, learner, sid)
        return _list_sets(session, study)

def _list_sets(session, study):
    groups = list(session.scalars(select(AssessmentSet).where(AssessmentSet.study_session_id == study.study_session_id)
                                  .order_by(AssessmentSet.created_at.desc(), AssessmentSet.set_id)))
    return {'schema': 'assessment-set-list/v1', 'study_session_id': str(study.study_session_id),
            'knowledge_structure_revision': study.knowledge_structure_revision,
            'active_set_ids': [str(item.set_id) for item in groups if item.status in ACTIVE],
            'sets': [_summary(session, group) for group in groups]}

def read_set(learner, sid, set_id, *, dsn=None):
    from .answer_events import _event, _feedback
    from runtime.api.models import project_answer_feedback
    with database_session(dsn) as session:
        session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        study, context, _ = _scope(session, learner, sid)
        group = _round(session, study, set_id)
        rows = _items(session, group)
        projected = []
        for item in rows:
            public = feedback = created = None
            can_submit = False
            if item.assessment_revision is not None:
                assessment = session.get(Assessment, item.assessment_revision)
                stored = assessments._stored(assessment)
                public, created = stored.public_document, assessment.created_at
                event = session.scalar(select(AnswerEvent).where(AnswerEvent.study_session_id == sid,
                    AnswerEvent.assessment_revision == item.assessment_revision))
                if event is not None:
                    feedback = project_answer_feedback(_feedback(_event(event, assessment, study), assessment)).model_dump(by_alias=True)
                else:
                    can_submit = membership_can_submit(session, study, assessment)
            projected.append({'ordinal': item.ordinal, 'target_claim_id': item.target_claim_id,
                'state': item.state, 'attempts': item.attempts, 'failure_reason': item.failure_reason,
                'assessment': public, 'feedback': feedback, 'created_at': created, 'can_submit': can_submit})
        summary = _summary(session, group, rows)
        other_active = session.scalar(select(AssessmentSet.set_id).where(AssessmentSet.study_session_id == sid,
            AssessmentSet.set_id != set_id, AssessmentSet.status.in_(ACTIVE),
            AssessmentSet.target_concept_id == group.target_concept_id).limit(1)) is not None
        return {'schema': 'assessment-set/v1', 'study_session_id': str(sid),
                'material_id': str(study.material_id), 'knowledge_structure_revision': study.knowledge_structure_revision,
                **summary, 'kind': group.kind, 'selection_policy': group.target_plan['policy'],
                'point_count': group.target_plan['point_count'], 'excluded_count': len(group.target_plan['excluded']),
                'verified_count': sum(item.state in ('verified', 'published') for item in rows),
                'can_retry': group.status in ('partial_ready', 'failed') and not other_active
                             and study.status in ('active', 'no_safe') and any(item.state == 'failed' and item.attempts < MAX_ATTEMPTS for item in rows),
                'can_publish_partial': group.status == 'partial_ready' and any(item.state == 'verified' for item in rows),
                'can_complete': group.status in ('ready', 'in_progress'),
                'items': projected,
                'cycle': _cycle(session, study, session.get(AssessmentSet, group.diagnostic_set_id) if group.diagnostic_set_id else group)}


def _assessment_row(study, item, prepared):
    public, private, provenance = (prepared[name] for name in ('public', 'private', 'provenance'))
    return Assessment(assessment_revision=public['assessment_revision'], study_session_id=study.study_session_id,
        knowledge_structure_revision=study.knowledge_structure_revision, question_id=public['question_id'],
        semantic_identity=prepared['semantic_identity'], learning_angle=provenance['learning_angle'],
        target_concept_id=item.target_concept_id, target_claim_id=item.target_claim_id,
        public_document=public, private_answer_document=private, generation_provenance=provenance,
        mastery_qualified=True, request_idempotency_key_sha256=_key(f'set:{item.set_id}:{item.ordinal}'),
        request_fingerprint=assessments._fingerprint(study.study_session_id, study.knowledge_structure_revision, item.target_claim_id),
        created_at=_now())


def _seal(session, study, group, items):
    if group.sealed_at is not None:
        raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
    for item in items:
        if item.state == 'verified':
            row = _assessment_row(study, item, item.prepared_document)
            assessments._stored(row)
            session.add(row); session.flush()
            item.assessment_revision = row.assessment_revision
            item.state, item.prepared_document = 'published', None
        else:
            item.state, item.prepared_document = 'omitted', None
    group.status, group.sealed_at = 'ready', _now()
    group.lease_token = group.lease_expires_at = None


def submit_set_answers(learner, sid, set_id, answers, expected_version, idempotency_key, *, dsn=None):
    """整組驗證、評分與結束在同一交易內；回應遺失可用同一意圖查回／重送。"""
    from . import answer_events
    if (type(expected_version) is not int or expected_version < 1 or not isinstance(answers, list) or not answers
        or any(not isinstance(answer, dict) or set(answer) != {'assessment_revision', 'question_id', 'selected_option_id'}
               or not all(isinstance(value, str) for value in answer.values()) for answer in answers)):
        raise AssessmentSetError('ASSESSMENT_SET_REQUEST_INVALID')
    by_revision = {answer['assessment_revision']: answer for answer in answers}
    if len(by_revision) != len(answers):
        raise AssessmentSetError('ASSESSMENT_SET_REQUEST_INVALID')
    key = _key(idempotency_key).hex()
    fingerprint = canonical_sha256({'action': 'submit-set', 'version': expected_version,
        'answers': sorted(answers, key=lambda answer: answer['assessment_revision'])})
    with database_session(dsn) as session:
        study, _, _ = _scope(session, learner, sid, lock=True)
        group = _round(session, study, set_id, lock=True)
        previous = group.action_receipts.get(key)
        if previous is not None:
            if previous != fingerprint:
                raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
            return
        if (group.set_version != expected_version or group.status not in ('ready', 'in_progress')
            or group.sealed_at is None or study.status not in ('active', 'no_safe')):
            raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
        items = [item for item in _items(session, group) if item.state == 'published']
        if not items or set(by_revision) != {item.assessment_revision for item in items}:
            raise AssessmentSetError('ASSESSMENT_SET_REQUEST_INVALID')
        validated = []
        for item in items:
            row = answer_events._assessment(session, study, item.assessment_revision)
            answer = by_revision[row.assessment_revision]
            if answer['question_id'] != row.question_id or answer['selected_option_id'] not in {o['option_id'] for o in row.public_document['options']}:
                raise AssessmentSetError('ASSESSMENT_SET_REQUEST_INVALID')
            saved = session.scalar(select(AnswerEvent).where(AnswerEvent.study_session_id == sid,
                AnswerEvent.assessment_revision == row.assessment_revision))
            if saved is not None:
                answer_events._event(saved, row, study)
                if saved.selected_option_id != answer['selected_option_id']:
                    raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
            validated.append((item.ordinal, row, answer['selected_option_id'], saved))
        # 先驗完整組再寫；任一寫入／commit 失敗會一起回滾。
        for ordinal, row, selected, saved in validated:
            if saved is None:
                answer_events.record_answer(session, study, row, selected, f'set-submit:{set_id}:{key}:{ordinal}')
        group.status, group.completed_at = 'completed', _now()
        group.set_version += 1
        group.updated_at = _now()
        group.action_receipts = {**group.action_receipts, key: fingerprint}
        _touch_diagnostic(session, group)
        session.flush()


def change_set(learner, sid, set_id, action, expected_version, idempotency_key, *, dsn=None):
    if action not in ('retry', 'publish-partial') or type(expected_version) is not int:
        raise AssessmentSetError('ASSESSMENT_SET_REQUEST_INVALID')
    key = _key(idempotency_key).hex()
    fingerprint = canonical_sha256({'action': action, 'version': expected_version})
    with database_session(dsn) as session:
        study, _, _ = _scope(session, learner, sid, lock=True)
        group = _round(session, study, set_id, lock=True)
        previous = group.action_receipts.get(key)
        if previous is not None:
            if previous != fingerprint:
                raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
            return
        if group.set_version != expected_version:
            raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
        rows = _items(session, group)
        if action == 'retry':
            other = session.scalar(select(AssessmentSet.set_id).where(AssessmentSet.study_session_id == sid,
                AssessmentSet.set_id != set_id, AssessmentSet.status.in_(ACTIVE),
                AssessmentSet.target_concept_id == group.target_concept_id).limit(1))
            if (other is not None or study.status not in ('active', 'no_safe')
                or group.status not in ('partial_ready', 'failed')
                or not any(item.state == 'failed' and item.attempts < MAX_ATTEMPTS for item in rows)):
                raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
            for item in rows:
                if item.state == 'failed' and item.attempts < MAX_ATTEMPTS:
                    item.state, item.failure_reason = 'pending', None
            group.status = 'preparing'
        elif action == 'publish-partial':
            if group.status != 'partial_ready' or not any(item.state == 'verified' for item in rows):
                raise AssessmentSetError('ASSESSMENT_SET_CONFLICT')
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
    if any(item.state in ('pending', 'generating') for item in items):
        return
    if items and all(item.state == 'verified' for item in items):
        _seal(session, study, group, items)
    else:
        group.status = 'partial_ready' if any(item.state == 'verified' for item in items) else 'failed'
        group.lease_token = group.lease_expires_at = None
    group.set_version += 1
    group.updated_at = _now()
    _touch_diagnostic(session, group)


def claim_set_work(*, dsn=None):
    # 先挑 ID，再按 Material → Study → Set 取鎖；模型呼叫不持有任何 DB 鎖。
    with database_session(dsn) as session:
        candidates = session.execute(select(AssessmentSet.learner_id, AssessmentSet.study_session_id, AssessmentSet.set_id)
            .where(AssessmentSet.status == 'preparing').order_by(AssessmentSet.created_at)).all()
    for owner, sid, set_id in candidates:
        try:
            with database_session(dsn) as session:
                study, _, _ = _scope(session, TrustedLearner(owner), sid, lock=True)
                group = _round(session, study, set_id, lock=True)
                if group.status != 'preparing' or (group.lease_expires_at is not None and group.lease_expires_at > _now()):
                    continue
                rows = _items(session, group)
                for item in rows:
                    if item.state == 'generating':
                        item.state, item.failure_reason = 'failed', 'GENERATION_INTERRUPTED'
                if study.status not in ('active', 'no_safe'):
                    group.status = 'cancelled'
                    group.lease_token = group.lease_expires_at = None
                    group.set_version += 1
                    _touch_diagnostic(session, group)
                    continue
                pending = next((item for item in rows if item.state == 'pending'), None)
                if pending is None:
                    _settle(session, study, group, rows)
                    continue
                token = uuid4()
                group.lease_token, group.lease_expires_at = token, _now() + timedelta(seconds=LEASE_SECONDS)
                pending.state, pending.attempts = 'generating', pending.attempts + 1
                group.set_version += 1
                group.updated_at = _now()
                _touch_diagnostic(session, group)
                return SetWork(owner, sid, set_id, pending.ordinal, token)
        except AssessmentSetError:
            continue
    return None


def _leased(session, work):
    study, context, document = _scope(session, TrustedLearner(work.learner_id), work.study_session_id, lock=True)
    group = _round(session, study, work.set_id, lock=True)
    if (group.status != 'preparing' or group.lease_token != work.token
        or group.lease_expires_at is None or group.lease_expires_at <= _now()
        or study.status not in ('active', 'no_safe')):
        raise AssessmentSetError('ASSESSMENT_SET_STALE_WORK')
    return study, context, document, group


def _heartbeat(work, stop, dsn):
    while not stop.wait(30):
        try:
            with database_session(dsn) as session:
                _, _, _, group = _leased(session, work)
                group.lease_expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
        except Exception:
            return


def _bounded_prior(prior, claim):
    evidence = {item.evidence_id for item in claim.evidence}
    ordered = sorted(prior, key=lambda row: (
        row.target_claim_id != claim.claim_id,
        not bool(evidence.intersection(row.public_document['source_evidence_ids'])),
    ))
    selected, size = [], 0
    for item in ordered:
        cost = len(json.dumps({'public': item.public_document, 'answer': item.private_answer_document['correct_answer']}, ensure_ascii=False).encode())
        if size + cost > assessments._PRIOR_CONTEXT_MAX_BYTES:
            continue
        selected.append(item); size += cost
        if len(selected) == assessments._PRIOR_LIMIT:
            break
    return selected


def execute_set_work(work, *, dsn=None, semantic_call=request_semantics):
    stop = Event()
    heartbeat = Thread(target=_heartbeat, args=(work, stop, dsn), daemon=True)
    heartbeat.start()
    prepared = None
    reason = None
    try:
        with database_session(dsn) as session:
            study, context, _, group = _leased(session, work)
            item = session.get(AssessmentSetItem, (work.set_id, work.ordinal))
            if item.state != 'generating':
                raise AssessmentSetError('ASSESSMENT_SET_STALE_WORK')
            concept = next(item for item in context.concepts if item.concept_id == group.target_concept_id)
            claim = next(point for point in concept.claims if point.claim_id == item.target_claim_id)
            lock, execution = deepcopy(group.runtime_lock_document), deepcopy(group.execution_identity)
            study_data = SimpleNamespace(study_session_id=study.study_session_id,
                                         knowledge_structure_revision=study.knowledge_structure_revision)
            prior = [assessments._stored(row) for row in assessments._prior_questions(session, study, claim)]
            verified = [row for row in _items(session, group) if row.state == 'verified']
            staged = [assessments._stored(_assessment_row(study, row, row.prepared_document)) for row in verified]
            used = set(session.scalars(select(Assessment.semantic_identity).where(Assessment.study_session_id == study.study_session_id)))
            used.update(row.semantic_identity for row in staged)
            prior = _bounded_prior([*staged, *prior], claim)
            scope = (study.learner_id, study.material_id, item.attempts)
        if _execution(lock) != execution:
            raise assessments.AssessmentError('ASSESSMENT_CONFIGURATION_INVALID')

        def model(client, **kwargs):
            with database_session(dsn) as session:
                _leased(session, work)
                directory = (_root() / 'analysis' / scope[0].hex / scope[1].hex /
                             f'assessment-set-{work.set_id.hex}' / f'call-{work.ordinal:04d}-{scope[2]}-{kwargs["task"]}')
                for parent in reversed([directory, *list(directory.parents)[:4]]):
                    if parent.is_symlink():
                        raise assessments.AssessmentError('ASSESSMENT_STORE_FAILED')
                    parent.mkdir(mode=0o700, exist_ok=True)
            with command_semantics.retain_call_outputs(directory):
                return semantic_call(client, **kwargs)

        chosen = assessments.prepare_assessment(study_data, concept, claim, prior, used,
                                                runtime_lock=lock, semantic_call=model)
        if _execution(lock) != execution:
            raise assessments.AssessmentError('ASSESSMENT_CONFIGURATION_INVALID')
        if chosen is None:
            reason = 'NO_SAFE_ASSESSMENT'
        else:
            public, private, provenance, _ = assessments._documents(study_data, concept, claim, chosen, runtime_lock=lock)
            prepared = {'public': public, 'private': private, 'provenance': provenance,
                        'semantic_identity': chosen['semantic_identity']}
    except AssessmentSetError:
        return
    except (assessments.AssessmentError, SemanticServiceError, command_semantics.CommandSemanticError) as error:
        allowed = {'NO_SAFE_ASSESSMENT', 'ASSESSMENT_OUTPUT_INVALID', 'ASSESSMENT_CHECK_INVALID',
                   'ASSESSMENT_CONFIGURATION_INVALID', 'SEMANTIC_SERVICE_UNAVAILABLE', 'SEMANTIC_SERVICE_TIMEOUT',
                   'SEMANTIC_RESPONSE_INVALID', 'SEMANTIC_INPUT_TOO_LARGE', 'SEMANTIC_OUTPUT_TRUNCATED',
                   'SEMANTIC_ARTIFACT_WRITE_FAILED', 'ASSESSMENT_STORE_FAILED'}
        reason = str(error) if str(error) in allowed else 'ASSESSMENT_GENERATION_FAILED'
    except Exception:
        reason = 'ASSESSMENT_GENERATION_FAILED'
    finally:
        stop.set(); heartbeat.join()
    # commit 回應遺失時先重讀 lease／狀態；同一份已驗證結果最多補存一次，不再呼叫模型。
    for _ in range(2):
        try:
            _commit_prepared(work, prepared, reason, dsn=dsn)
            return
        except AssessmentSetError:
            return
        except Exception:
            logging.getLogger(__name__).warning('ASSESSMENT_SET_STORE_FAILED', extra={'set_id': str(work.set_id)})


def _commit_prepared(work, prepared, reason, *, dsn):
    with database_session(dsn) as session:
        study, _, _, group = _leased(session, work)
        item = session.get(AssessmentSetItem, (work.set_id, work.ordinal))
        if item.state != 'generating':
            return
        if prepared is not None:
            # 同一 session 的題目若在推論期間已發布，不能再給相同題目第二份 credit。
            duplicate = session.scalar(select(Assessment.assessment_revision).where(
                Assessment.study_session_id == study.study_session_id,
                Assessment.semantic_identity == prepared['semantic_identity']).limit(1))
            if duplicate is not None:
                prepared, reason = None, 'NO_SAFE_ASSESSMENT'
        item.state = 'verified' if prepared is not None else 'failed'
        item.prepared_document, item.failure_reason = prepared, reason
        group.lease_token = group.lease_expires_at = None
        group.set_version += 1
        group.updated_at = _now()
        _touch_diagnostic(session, group)
        session.flush()
        _settle(session, study, group, _items(session, group))


def run_next_set(*, dsn=None):
    work = claim_set_work(dsn=dsn)
    if work is None:
        return False
    execute_set_work(work, dsn=dsn)
    return True
