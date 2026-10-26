"""讀取時由教材結構與真實作答計算難度、弱點及複習建議。"""
from __future__ import annotations

from sqlalchemy import select
from runtime.storage import knowledge_reads
from runtime.storage.tables import StudySession
from .learning_states import claim_completion


def diagnosis_input(concept_views, states, events, plan):
    """由完整重點與真實事件投影來源、累計與補強連對進度。"""
    concepts = {concept["concept_id"]: concept for concept in concept_views}
    plans = {c["concept_id"]: c for c in plan["concepts"]}
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
            title = claim["text"]
            _, consecutive = claim_completion(history)
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
                "latest_is_correct": history[-1].is_correct if history else None,
                "consecutive_correct_count": consecutive,
                "required_consecutive_correct": 1 if not history or history[0].is_correct else 2,
                "completed": is_completed})
            if is_completed:
                completed.append(identity)
            if is_weak:
                weak.append(identity)
        history_state = state
        result.append({"concept_id": state.concept_id, "label": state.label,
                       "completed_claim_ids": completed, "weak_claim_ids": weak, "points": points,
                       "all_points_completed": bool(state.assessable_claim_ids) and set(state.assessable_claim_ids) <= set(state.completed_claim_ids),
                       "answer_count": history_state.attempts,
                       "incorrect_count": history_state.attempts - history_state.correct_answers,
                       "personal_difficulty": is_personal_difficulty(history_state)})
    return {"scope": "material", "concepts": result}


def template_diagnosis(data):
    """優勢／弱點列重點；建議只列最多三個概念與原因。"""
    result = {"strengths": [], "weaknesses": [], "recommendations": []}
    for concept in data["concepts"]:
        for category, key in (("strengths", "completed_claim_ids"), ("weaknesses", "weak_claim_ids")):
            if category == "strengths" and not concept["all_points_completed"]:
                continue
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


def _material_states(db, study, plan):
    from .answer_events import _read_events
    from .learning_states import derive_learning_states
    from .assessment_state import unavailable_claims, completed_concepts
    from .inherited_progress import inherited_progress
    from runtime.learner_session import TrustedLearner

    events = _read_events(db, study)
    inherited, inherited_cycles = inherited_progress(
        db, TrustedLearner(study.learner_id), study.material_id, study.knowledge_structure_revision,
        plan, include_cycles=True)
    combined = tuple(sorted((*inherited, *events), key=lambda e: (e.created_at, str(e.answer_event_id))))
    states = derive_learning_states(plan, combined,
        unavailable_claim_ids=unavailable_claims(db, study, plan),
        completed_concept_ids=completed_concepts(db, study) | {c["concept_id"] for c in inherited_cycles})
    return events, states, combined


def read_diagnosis(db, owner, material_id, revision):
    plan = knowledge_reads.learning(db, owner, material_id, revision)
    study = db.scalar(select(StudySession).where(StudySession.learner_id == owner,
        StudySession.material_id == material_id, StudySession.knowledge_structure_revision == revision))
    result = {"strengths": [], "weaknesses": [], "recommendations": []}
    if study is not None:
        _, states, events = _material_states(db, study, plan)
        if any(state.attempts for state in states):
            rows = knowledge_reads.read_concepts(db, owner, material_id, revision,
                                                [state.concept_id for state in states if state.attempts])
            result = template_diagnosis(diagnosis_input([row.payload["concept"] for row in rows], states, events, plan))
    return {"schema": "learning-diagnosis/v2", "material_id": str(material_id),
            "knowledge_structure_revision": revision, "scope": "material",
            "source_resolver": f"/v1/materials/{material_id}/knowledge-structures/{revision}/evidence", **result}


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
