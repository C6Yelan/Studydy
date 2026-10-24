"""目標的逐重點計數；不以概念完成標記取代作答證據，不建立學習紀錄。"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from runtime.storage import knowledge_reads
from runtime.storage.tables import StudySession
from .answer_events import _read_events
from .assessment_state import completed_concepts, unavailable_claims
from .inherited_progress import inherited_progress
from .learning_states import claim_completion, derive_learning_states
from .study_sessions import _validate_plan


def measure_targets(plan, events, unavailable=(), *, through=None, completed_concept_ids=()):
    """以目前可檢測範圍計數；through 只限制作答時間，不重建歷史可出題狀態。"""
    by_concept = {}
    ordered = sorted((event for event in events if through is None or event.created_at <= through),
                     key=lambda event: (event.created_at, str(event.answer_event_id)))
    for event in ordered:
        by_concept.setdefault(event.target_concept_id, []).append(event)
    unavailable = set(unavailable)
    completed_concepts_set = set(completed_concept_ids)
    concepts = []
    for concept in plan["concepts"]:
        eligible = complete = 0
        for target in concept["targets"]:
            covered = set(target["covered_claim_ids"])
            if covered.intersection(unavailable):
                continue
            eligible += 1
            history = [event for event in by_concept.get(concept["concept_id"], ())
                       if event.target_claim_id in covered]
            complete += claim_completion(history)[0]
        remaining = eligible - complete
        concepts.append({"concept_id": concept["concept_id"], "label": concept["label"],
                         "assessable_points": eligible, "completed_points": complete,
                         "remaining_points": remaining,
                         "manual_review_points": remaining if concept["concept_id"] in completed_concepts_set else 0})
    return {**{key: sum(concept[key] for concept in concepts) for key in (
        "assessable_points", "completed_points", "remaining_points", "manual_review_points")},
        "concepts": concepts}


@dataclass(frozen=True)
class GoalEvidence:
    plan: dict
    study: StudySession | None
    events: tuple
    unavailable: frozenset[str]
    completed_concept_ids: frozenset[str]
    states: tuple


def load_goal_evidence(db, learner, material_id, revision, *, include_cycles=True):
    """目標讀取／設定／日基準共用同一份已驗證證據，不建立 Study。"""
    plan = knowledge_reads.learning(db, learner.learner_id, material_id, revision)
    study = db.scalar(select(StudySession).where(StudySession.learner_id == learner.learner_id,
        StudySession.material_id == material_id, StudySession.knowledge_structure_revision == revision))
    if study is not None:
        _validate_plan(study, plan)
    current = _read_events(db, study) if study is not None else ()
    inherited, cycles = inherited_progress(db, learner, material_id, revision, plan, include_cycles=include_cycles)
    # 承接只映射原事件，不複製事件；相同證據身分在此計一次。
    events = tuple(sorted({event.answer_event_id: event for event in (*inherited, *current)}.values(),
                          key=lambda event: (event.created_at, str(event.answer_event_id))))
    unavailable = unavailable_claims(db, study, plan) if study is not None else set()
    historical_completed = ({cycle["concept_id"] for cycle in cycles}
                            | (completed_concepts(db, study) if study is not None and include_cycles else set()))
    states = derive_learning_states(plan, events, unavailable_claim_ids=unavailable,
                                     completed_concept_ids=historical_completed)
    return GoalEvidence(plan, study, events, frozenset(unavailable), frozenset(historical_completed), states)


def read_goal_metrics(db, learner, material_id, revision, *, through=None):
    """設定／新版基準使用真實當下計量；沒有當版 Study 仍可承接合法舊證據。"""
    scope = load_goal_evidence(db, learner, material_id, revision, include_cycles=through is None)
    metrics = measure_targets(scope.plan, scope.events, scope.unavailable, through=through,
                              completed_concept_ids=scope.completed_concept_ids)
    all_points = measure_targets(scope.plan, scope.events, through=through)
    return {"material_id": str(material_id), "knowledge_structure_revision": revision,
            "unavailable_claim_ids": sorted(scope.unavailable),
            "all_completed_points": all_points["completed_points"], **metrics}
