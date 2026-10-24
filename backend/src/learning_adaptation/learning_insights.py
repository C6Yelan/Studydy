"""讀取時由教材結構與真實作答計算難度、弱點及複習建議。"""
from __future__ import annotations

from sqlalchemy import select
from runtime.errors import SourceError
from runtime.storage import knowledge_reads
from runtime.storage.tables import StudySession, AssessmentSet, AssessmentSetItem


def diagnosis_input(concept_views, states, events, plan, *, scope="material", personal_states=None):
    """由原文與真實事件產生短標題、來源及逐重點作答事實。"""
    concepts = {concept["concept_id"]: concept for concept in concept_views}
    plans = {c["concept_id"]: c for c in plan["concepts"]}
    personal = {state.concept_id: state for state in (states if personal_states is None else personal_states)}
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
            for item in claim["evidence"]:
                key = (item["source_id"], item["normalized_page"])
                if key in seen:
                    continue
                seen.add(key)
                references.append({key: item[key] for key in (
                    "evidence_id", "page", "source_id", "source_name", "normalized_page")})
            points.append({"claim_id": identity, "title": title, "evidence": references,
                "answer_count": len(history), "correct_count": sum(event.is_correct for event in history),
                "latest_is_correct": history[-1].is_correct if history else None})
            if is_completed:
                completed.append(identity)
            if is_weak:
                weak.append(identity)
        history_state = personal[state.concept_id]
        result.append({"concept_id": state.concept_id, "label": state.label,
                       "completed_claim_ids": completed, "weak_claim_ids": weak, "points": points,
                       "answer_count": history_state.attempts,
                       "incorrect_count": history_state.attempts - history_state.correct_answers,
                       "personal_difficulty": is_personal_difficulty(history_state)})
    return {"scope": scope, "concepts": result}


def template_diagnosis(data):
    """優勢／弱點列重點；建議只列最多三個概念與原因。"""
    result = {"strengths": [], "weaknesses": [], "recommendations": []}
    for concept in data["concepts"]:
        for category, key in (("strengths", "completed_claim_ids"), ("weaknesses", "weak_claim_ids")):
            points = [point for point in concept["points"] if point["claim_id"] in concept[key]]
            if points:
                result[category].append({"concept_id": concept["concept_id"],
                                        "label": concept["label"], "points": points})
    candidates = [concept for concept in data["concepts"]
                  if concept["weak_claim_ids"] or concept["personal_difficulty"]]
    candidates.sort(key=lambda concept: (-len(concept["weak_claim_ids"]), -int(concept["personal_difficulty"])))
    for concept in candidates[:3]:
        count = len(concept["weak_claim_ids"])
        reason = (f"{count} 個重點尚待加強" + ("，也是你的難點。" if concept["personal_difficulty"] else "。")
                  if count else f"答錯 {concept['incorrect_count']}/{concept['answer_count']} 題，是你的難點。")
        result["recommendations"].append({"concept_id": concept["concept_id"],
                                          "label": concept["label"], "reason": reason})
    return result


def is_personal_difficulty(state):
    return state.attempts >= 3 and (state.attempts - state.correct_answers) * 2 >= state.attempts


def _material_states(db, study, plan, *, through=None):
    from .answer_events import _read_events
    from .learning_states import derive_learning_states
    from .assessment_state import unavailable_claims, completed_concepts
    from .inherited_progress import inherited_progress
    from runtime.learner_session import TrustedLearner

    events = _read_events(db, study)
    inherited, inherited_cycles = inherited_progress(
        db, TrustedLearner(study.learner_id), study.material_id, study.knowledge_structure_revision,
        plan, include_cycles=through is None)
    if through is not None:
        events = tuple(event for event in events if event.created_at <= through)
        inherited = tuple(event for event in inherited if event.created_at <= through)
    combined = tuple(sorted((*inherited, *events), key=lambda e: (e.created_at, str(e.answer_event_id))))
    if through is None:
        states = derive_learning_states(plan, combined,
            unavailable_claim_ids=unavailable_claims(db, study, plan),
            completed_concept_ids=completed_concepts(db, study) | {c["concept_id"] for c in inherited_cycles})
    else:
        # 舊題組不能沿用交卷後完成的 cycle 或無題狀態；完成只依當時已存在的事件。
        states = derive_learning_states(plan, combined)
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



def read_diagnosis(db, owner, material_id, revision, set_id=None):
    plan = knowledge_reads.learning(db, owner, material_id, revision)
    study = db.scalar(select(StudySession).where(StudySession.learner_id == owner,
        StudySession.material_id == material_id, StudySession.knowledge_structure_revision == revision))
    group = None
    if set_id is not None:
        group = db.get(AssessmentSet, set_id)
        if (group is None or study is None or group.study_session_id != study.study_session_id
                or group.learner_id != owner or group.knowledge_structure_revision != revision):
            raise SourceError("RESOURCE_NOT_FOUND")
    result = {"strengths": [], "weaknesses": [], "recommendations": []}
    if study is not None and (group is None or group.completed_at is not None):
        events, material_states, combined = _material_states(
            db, study, plan, through=group.completed_at if group else None)
        if group is not None:
            revisions = set(db.scalars(select(AssessmentSetItem.assessment_revision)
                                      .where(AssessmentSetItem.set_id == group.set_id)))
            selected_events = tuple(event for event in events if event.assessment_revision in revisions)
            states = _set_states(plan, material_states, selected_events)
        else:
            selected_events, states = combined, material_states
        if any(state.attempts for state in states):
            rows = knowledge_reads.read_concepts(db, owner, material_id, revision,
                                                [state.concept_id for state in states if state.attempts])
            data = diagnosis_input([row.payload["concept"] for row in rows], states, selected_events, plan,
                                   scope="set" if group else "material", personal_states=material_states)
            result = template_diagnosis(data)
    return {"schema": "learning-diagnosis/v1", "material_id": str(material_id),
            "knowledge_structure_revision": revision, "scope": "set" if set_id else "material",
            "source_resolver": f"/v1/materials/{material_id}/knowledge-structures/{revision}/evidence",
            "set_id": str(set_id) if set_id else None, **result}


def structural_difficulty(plan):
    """只計 prerequisite 先備鏈的最長深度；根節點為 0。"""
    children = {concept["concept_id"]: [] for concept in plan["concepts"]}
    remaining = {concept["concept_id"]: len(concept["prerequisite_ids"]) for concept in plan["concepts"]}
    depths = {identity: 0 for identity in children}
    for concept in plan["concepts"]:
        for parent in concept["prerequisite_ids"]:
            children[parent].append(concept["concept_id"])
    ready = [identity for identity, count in remaining.items() if count == 0]
    for identity in ready:
        for child in children[identity]:
            depths[child] = max(depths[child], depths[identity] + 1)
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
    result = []
    for concept in plan["concepts"]:
        depth, count = depths[concept["concept_id"]], len(concept["claim_ids"])
        reasons = ([f"需先學 {depth} 層先備概念"] if depth else []) + [f"包含 {count} 個重點"]
        result.append({"concept_id": concept["concept_id"], "label": concept["label"],
                       "level": "hard" if depth >= 3 or count >= 5 else "standard",
                       "reason": "；".join(reasons)})
    return result


def read_difficulty(db, owner, material_id, revision):
    plan = knowledge_reads.learning(db, owner, material_id, revision)
    study = db.scalar(select(StudySession).where(StudySession.learner_id == owner,
        StudySession.material_id == material_id, StudySession.knowledge_structure_revision == revision))
    states = {state.concept_id: state for state in _material_states(db, study, plan)[1]} if study else {}
    concepts = []
    for item in structural_difficulty(plan):
        state = states.get(item["concept_id"])
        answers = state.attempts if state else 0
        incorrect = answers - state.correct_answers if state else 0
        concepts.append({**item, "answer_count": answers, "incorrect_count": incorrect,
                         "personal_difficulty": is_personal_difficulty(state) if state else False})
    return {"schema": "concept-difficulty/v1", "material_id": str(material_id),
            "knowledge_structure_revision": revision, "mode": "structure", "concepts": concepts}
