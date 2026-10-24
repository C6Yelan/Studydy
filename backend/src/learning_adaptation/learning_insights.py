"""題組後診斷與每版本難度的持久工作；讀取不觸發生成。"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, or_
from sqlalchemy.dialects.postgresql import insert
from content_identity import canonical_sha256
from runtime.errors import SourceError
from runtime.local_ai_config import configured_lock
from runtime.material_runtime import validate_runtime_lock
from runtime.semantic_service import request_semantics, SemanticServiceError
from .concept_difficulty import difficulty_batches, evaluate_batch
from runtime.generation_jobs import WORKER_LEASE_TTL, JobCancelled, check_job, lease_is_current
from runtime.storage.database import database_session
from runtime.storage import knowledge_reads
from runtime.storage.tables import LearningInsight, Material, StudySession, AssessmentSet, AssessmentSetItem


def diagnosis_input(document, states, events, plan, *, scope="material"):
    """封存短標題、原來源與逐重點作答事實；不從敘述反推數字。"""
    concepts = {c["concept_id"]: c for c in document["concepts"]}
    plans = {c["concept_id"]: c for c in plan["concepts"]}
    evidence = {item["evidence_id"]: item for item in document["evidence"]}
    pages = document["input_binding"]["bundle"]["pages"]
    sources = {item["source_id"]: item for item in document["input_binding"]["manifest"]["items"]}
    result = []
    for state in states:
        if not state.attempts:
            continue
        claims = {claim["claim_id"]: claim for claim in concepts[state.concept_id]["claims"]}
        points, completed, weak = [], [], []
        for target in plans[state.concept_id]["targets"]:
            identity = target["claim_id"]
            covered = set(target["covered_claim_ids"])
            is_completed = bool(covered.intersection(state.completed_claim_ids))
            is_weak = bool(covered.intersection(state.weak_claim_ids))
            if not (is_completed or is_weak):
                continue
            claim = claims[identity]
            history = [event for event in events if event.target_concept_id == state.concept_id
                       and event.target_claim_id in covered]
            title = next(line.strip() for line in claim["text"].splitlines() if line.strip())
            if len(title) > 80:
                title = title[:80] + "…"
            references, seen = [], set()
            for reference in claim["evidence_refs"]:
                item = evidence[reference]
                location = pages[item["page"] - 1]
                key = (location["source_id"], location["normalized_page"])
                if key in seen:
                    continue
                seen.add(key)
                references.append({"evidence_id": reference, "page": item["page"],
                    "source_id": location["source_id"],
                    "source_name": sources[location["source_id"]]["original_name"],
                    "normalized_page": location["normalized_page"]})
            points.append({"claim_id": identity, "title": title, "evidence": references,
                "answer_count": len(history), "correct_count": sum(event.is_correct for event in history),
                "latest_is_correct": history[-1].is_correct if history else None})
            if is_completed:
                completed.append(identity)
            if is_weak:
                weak.append(identity)
        result.append({"concept_id": state.concept_id, "label": state.label,
                       "completed_claim_ids": completed, "weak_claim_ids": weak, "points": points})
    return {"scope": scope, "concepts": result}


def template_diagnosis(data):
    """直接列出短標題與作答事實，不拼接教材敘述，也不填空區塊。"""
    result = {"strengths": [], "weaknesses": [], "recommendations": []}
    for concept in data["concepts"]:
        for category, key in (("strengths", "completed_claim_ids"),
                              ("weaknesses", "weak_claim_ids"),
                              ("recommendations", "weak_claim_ids")):
            points = [point for point in concept["points"] if point["claim_id"] in concept[key]]
            if points:
                result[category].append({"concept_id": concept["concept_id"],
                                        "label": concept["label"], "points": points})
    result["recommendations"].sort(key=lambda item: -len(item["points"]))
    return result


def _now():
    return datetime.now(UTC)


def _enqueue(db, *, owner, material_id, revision, task, scope_key, data, study=None, group=None):
    now = _now()
    values = dict(insight_id=uuid4(), learner_id=owner, material_id=material_id,
                  structure_revision=revision, task=task, scope_key=scope_key,
                  study_session_id=study.study_session_id if study else None,
                  set_id=group.set_id if group else None,
                  event_watermark=study.last_event_number if study else 0,
                  status="pending", input_document=data,
                  runtime_lock_document=validate_runtime_lock(configured_lock()) if task == "difficulty" else None,
                  completed_batches=[], model_calls=0,
                  created_at=now, updated_at=now)
    query = insert(LearningInsight).values(**values)
    keys = ["learner_id", "material_id", "structure_revision", "scope_key"]
    if study is not None and group is None:
        query = query.on_conflict_do_update(index_elements=keys,
            set_={k: values[k] for k in ("event_watermark", "status", "input_document", "updated_at")}
                 | {"result_document": None, "failure_reason": None, "lease_token": None, "lease_expires_at": None},
            where=LearningInsight.event_watermark < study.last_event_number)
    else:
        query = query.on_conflict_do_nothing(index_elements=keys)
    db.execute(query)


def enqueue_difficulty(db, owner, material_id, document):
    _enqueue(db, owner=owner, material_id=material_id, revision=document["revision"],
             task="difficulty", scope_key="difficulty", data={"schema": "concept-difficulty-job/v3"})


def _material_states(db, study, plan):
    from .answer_events import _read_events
    from .learning_states import derive_learning_states
    from .assessment_state import unavailable_claims, completed_concepts
    from .inherited_progress import inherited_progress
    from .study_sessions import _stored
    from runtime.learner_session import TrustedLearner

    events = _read_events(db, study)
    inherited, inherited_cycles = inherited_progress(db, TrustedLearner(study.learner_id), _stored(study), plan)
    combined = tuple(sorted((*inherited, *events), key=lambda e: (e.created_at, str(e.answer_event_id))))
    states = derive_learning_states(plan, combined,
        unavailable_claim_ids=unavailable_claims(db, study, plan),
        completed_concept_ids=completed_concepts(db, study) | {c["concept_id"] for c in inherited_cycles})
    return events, states, combined


def _set_states(plan, material_states, set_events):
    """完成沿用截至交卷的完整歷史，題數與呈現範圍只取本組。"""
    result = []
    plans = {c["concept_id"]: c for c in plan["concepts"]}
    for state in material_states:
        events = [event for event in set_events if event.target_concept_id == state.concept_id]
        if not events:
            continue
        targets = {event.target_claim_id for event in events}
        claims = set(targets)
        for target in plans[state.concept_id]["targets"]:
            if targets.intersection(target["covered_claim_ids"]):
                claims.update(target["covered_claim_ids"])
        result.append(state.model_copy(update={
            "attempts": len(events), "correct_answers": sum(event.is_correct for event in events),
            "completed_claim_ids": [identity for identity in state.completed_claim_ids if identity in claims],
            "weak_claim_ids": [identity for identity in state.weak_claim_ids if identity in claims],
        }))
    return result


def enqueue_diagnoses(db, study, group, document):
    """交卷交易內封存兩種範圍；以完成當下資料為準，後續作答不改舊題組。"""
    plan = knowledge_reads.learning(db, study.learner_id, study.material_id, study.knowledge_structure_revision)
    events, material_states, combined = _material_states(db, study, plan)
    revisions = set(db.scalars(select(AssessmentSetItem.assessment_revision).where(AssessmentSetItem.set_id == group.set_id)))
    set_events = tuple(e for e in events if e.assessment_revision in revisions)
    set_states = _set_states(plan, material_states, set_events)
    for scope, states, selected in (("material", material_states, None), ("set", set_states, group)):
        _enqueue(db, owner=study.learner_id, material_id=study.material_id,
            revision=study.knowledge_structure_revision, task="diagnosis",
            scope_key=f"set:{group.set_id}" if selected else f"material:{study.study_session_id}",
            data=diagnosis_input(document, states, set_events if selected else combined, plan, scope=scope),
            study=study, group=selected)



def _difficulty_work(identity, token, owner, material_id, lock, completed, check, *, dsn):
    from runtime.storage.knowledge_structures import _read_verified_document

    with database_session(dsn) as db:
        row = db.get(LearningInsight, identity)
        document = _read_verified_document(db, owner, material_id, revision=row.structure_revision)
    batches = difficulty_batches(document, lock, max_calls=5)
    if len(completed) > len(batches):
        raise ValueError("DIFFICULTY_CHECKPOINT_INVALID")
    for index, saved in enumerate(completed):
        batch = batches[index]
        binding = canonical_sha256({"request": batch["request"], "mapping": batch["mapping"]})
        if (saved["input_sha256"] != binding
                or saved["result_sha256"] != canonical_sha256(saved["concepts"])):
            raise ValueError("DIFFICULTY_CHECKPOINT_INVALID")
    for batch in batches[len(completed):]:
        check()
        with database_session(dsn) as db:
            row = db.scalar(select(LearningInsight).where(LearningInsight.insight_id == identity).with_for_update())
            if row is None or row.status != "running" or not lease_is_current(row, token):
                raise JobCancelled()
            if row.model_calls >= 5:
                raise ValueError("DIFFICULTY_CALL_BUDGET_EXCEEDED")
            row.model_calls += 1
        result = evaluate_batch(batch, lock, semantic_call=request_semantics, cancellation_check=check)
        check()
        completed.append({"input_sha256": canonical_sha256({"request": batch["request"], "mapping": batch["mapping"]}),
                          "result_sha256": canonical_sha256(result), "concepts": result})
        with database_session(dsn) as db:
            material = db.scalar(select(Material).where(Material.material_id == material_id,
                Material.learner_id == owner).with_for_update())
            row = db.scalar(select(LearningInsight).where(LearningInsight.insight_id == identity).with_for_update())
            if (material is None or material.discard_requested_at is not None or row is None
                    or row.status != "running" or not lease_is_current(row, token)):
                raise JobCancelled()
            row.completed_batches = deepcopy(completed)
            row.updated_at = _now()
    return {"concepts": [concept for batch in completed for concept in batch["concepts"]]}


def run_next_insight(task, *, dsn=None, shutdown=None):
    now = _now()
    with database_session(dsn) as db:
        row = db.scalar(select(LearningInsight).join(Material, Material.material_id == LearningInsight.material_id)
            .where(LearningInsight.task == task, Material.discard_requested_at.is_(None),
                   or_(LearningInsight.status == "pending",
                       (LearningInsight.status == "running") & (LearningInsight.lease_expires_at <= now)))
            .order_by(LearningInsight.created_at).with_for_update(of=LearningInsight, skip_locked=True).limit(1))
        if row is None:
            return False
        row.status, row.lease_token = "running", uuid4()
        row.lease_expires_at, row.updated_at = now + WORKER_LEASE_TTL, now
        identity, token = row.insight_id, row.lease_token
        material_id, owner = row.material_id, row.learner_id
        data = deepcopy(row.input_document)
        lock, completed = deepcopy(row.runtime_lock_document), deepcopy(row.completed_batches)

    def check():
        if shutdown is not None and shutdown.is_set():
            raise JobCancelled()
        check_job(LearningInsight, identity, token, dsn=dsn)

    result, failure, status = None, None, "ready"
    try:
        check()
        result = (_difficulty_work(identity, token, owner, material_id, lock, completed, check, dsn=dsn)
                  if task == "difficulty" else template_diagnosis(data))
        check()
    except JobCancelled:
        status = "pending"
    except SemanticServiceError as error:
        status, failure = "failed", error.reason_code
    except ValueError as error:
        code = str(error)
        status, failure = "failed", code if code in {
            "DIFFICULTY_INPUT_TOO_LARGE", "DIFFICULTY_CALL_BUDGET_EXCEEDED",
            "DIFFICULTY_OUTPUT_INVALID", "DIFFICULTY_CHECKPOINT_INVALID",
        } else "LEARNING_INSIGHT_GENERATION_FAILED"
    except Exception:
        status, failure = "failed", "LEARNING_INSIGHT_GENERATION_FAILED"
    with database_session(dsn) as db:
        # 與教材刪除／交卷共用 Material → 工作鎖序，取消後不能發布。
        material = db.scalar(select(Material).where(Material.material_id == material_id,
            Material.learner_id == owner).with_for_update())
        row = db.scalar(select(LearningInsight).where(LearningInsight.insight_id == identity).with_for_update())
        if (material is not None and material.discard_requested_at is None
                and row is not None and row.status == "running" and lease_is_current(row, token)):
            row.status, row.result_document, row.failure_reason = status, result, failure
            row.lease_token, row.lease_expires_at = None, None
            row.updated_at = _now()
    return True


def read_diagnosis(db, owner, material_id, revision, set_id=None):
    plan = knowledge_reads.learning(db, owner, material_id, revision)
    study = db.scalar(select(StudySession).where(StudySession.learner_id == owner,
        StudySession.material_id == material_id, StudySession.knowledge_structure_revision == revision))
    if set_id is not None:
        group = db.get(AssessmentSet, set_id)
        if (group is None or study is None or group.study_session_id != study.study_session_id
                or group.learner_id != owner or group.knowledge_structure_revision != revision):
            raise SourceError("RESOURCE_NOT_FOUND")
    scope_key = f"set:{set_id}" if set_id else f"material:{study.study_session_id}" if study else ""
    row = db.scalar(select(LearningInsight).where(LearningInsight.learner_id == owner,
        LearningInsight.material_id == material_id, LearningInsight.structure_revision == revision,
        LearningInsight.scope_key == scope_key))
    has_answers = bool(study and study.last_event_number)
    if row is None and study is not None and not has_answers and set_id is None:
        has_answers = any(state.attempts or state.completed_claim_ids
                          for state in _material_states(db, study, plan)[1])
    return {"schema": "learning-diagnosis/v1", "material_id": str(material_id),
            "knowledge_structure_revision": revision, "scope": "set" if set_id else "material",
            "source_resolver": f"/v1/materials/{material_id}/knowledge-structures/{revision}/evidence",
            "set_id": str(set_id) if set_id else None,
            "status": row.status if row else "not_generated" if has_answers else "no_answers",
            "mode": "template", "event_watermark": row.event_watermark if row else 0,
            "strengths": [], "weaknesses": [], "recommendations": [],
            **(row.result_document if row and row.status == "ready" else {}),
            "failure_reason": row.failure_reason if row else None}


def read_difficulty(db, owner, material_id, revision):
    plan = knowledge_reads.learning(db, owner, material_id, revision)
    row = db.scalar(select(LearningInsight).where(LearningInsight.learner_id == owner,
        LearningInsight.material_id == material_id, LearningInsight.structure_revision == revision,
        LearningInsight.scope_key == "difficulty"))
    study = db.scalar(select(StudySession).where(StudySession.learner_id == owner,
        StudySession.material_id == material_id, StudySession.knowledge_structure_revision == revision))
    states = {state.concept_id: state for state in _material_states(db, study, plan)[1]} if study else {}
    labels = {c["concept_id"]: c["label"] for c in plan["concepts"]}
    model_concepts = (row.result_document["concepts"] if row and row.status == "ready" else
                      [{"concept_id": identity, "level": None, "reason": None}
                       for identity, state in states.items() if state.attempts])
    concepts = []
    for item in model_concepts:
        state = states.get(item["concept_id"])
        answers = state.attempts if state else 0
        incorrect = answers - state.correct_answers if state else 0
        concepts.append({**item, "label": labels[item["concept_id"]], "answer_count": answers,
                         "incorrect_count": incorrect,
                         "personal_difficulty": answers >= 3 and incorrect * 2 >= answers})
    return {"schema": "concept-difficulty/v1", "material_id": str(material_id),
            "knowledge_structure_revision": revision, "status": row.status if row else "not_generated",
            "mode": "model", "concepts": concepts, "failure_reason": row.failure_reason if row else None}


def backfill_difficulty(owner, *, dsn=None):
    """部署後明確呼叫；每份目前教材版本只入列一次，不在讀取時補跑。"""
    from runtime.storage.knowledge_structures import _read_verified_document
    with database_session(dsn) as db:
        materials = db.scalars(select(Material).where(Material.learner_id == owner, Material.discard_requested_at.is_(None), Material.head_revision.is_not(None))).all()
        count = 0
        for material in materials:
            existing = db.scalar(select(LearningInsight.insight_id).where(LearningInsight.learner_id == material.learner_id,
                LearningInsight.material_id == material.material_id, LearningInsight.structure_revision == material.head_revision,
                LearningInsight.scope_key == "difficulty"))
            if existing is None:
                document = _read_verified_document(db, material.learner_id, material.material_id, revision=material.head_revision)
                enqueue_difficulty(db, material.learner_id, material.material_id, document)
                count += 1
    return count
