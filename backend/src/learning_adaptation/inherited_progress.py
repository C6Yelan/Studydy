"""將歷次地圖中未變 Claim 的作答證據投影到目前學習狀態。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from knowledge_map.source_identity import unchanged_claims
from runtime.storage.knowledge_structures import _read_verified_document
from runtime.storage.tables import AssessmentSet, KnowledgeStructure, MaterialProcessingRun, StudySession

from .answer_events import _read_events
from .map_context import _context_from_validated_document
from .study_sessions import _row, _validate_context


@dataclass(frozen=True)
class InheritedAnswerEvidence:
    target_concept_id: str
    target_claim_id: str
    semantic_identity: str
    is_correct: bool
    mastery_qualified: bool
    created_at: datetime
    answer_event_id: UUID
    assisted: bool = False


def preferred_focus(session, owner, material_id, revision):
    current = session.scalar(select(KnowledgeStructure).where(
        KnowledgeStructure.learner_id == owner,
        KnowledgeStructure.material_id == material_id,
        KnowledgeStructure.structure_revision == revision,
    ))
    run = session.get(MaterialProcessingRun, current.run_id)
    if not run.base_revision:
        return None

    previous_study = session.scalar(
        select(StudySession).where(
            StudySession.learner_id == owner,
            StudySession.material_id == material_id,
            StudySession.knowledge_structure_revision == run.base_revision,
            StudySession.status.in_(("active", "no_safe")),
        ).order_by(StudySession.started_at.desc()).limit(1)
    )
    if previous_study is None or previous_study.current_concept_id is None:
        return None

    previous_structure = session.scalar(select(KnowledgeStructure).where(
        KnowledgeStructure.learner_id == owner,
        KnowledgeStructure.material_id == material_id,
        KnowledgeStructure.structure_revision == run.base_revision,
    ))
    if previous_structure is None:
        return None

    matched_concepts = {
        target[0]
        for origin, target in unchanged_claims(
            previous_structure.document, current.document
        ).items()
        if origin[0] == previous_study.current_concept_id
    }
    return next(iter(matched_concepts)) if len(matched_concepts) == 1 else None


def inherited_progress(session, learner, study, document):
    from .assessment_sets import _read_cycles
    current = session.scalar(select(KnowledgeStructure).where(
        KnowledgeStructure.learner_id == learner.learner_id,
        KnowledgeStructure.material_id == study.material_id,
        KnowledgeStructure.structure_revision == study.knowledge_structure_revision,
    ))
    if current is None:
        raise ValueError("KNOWLEDGE_STRUCTURE_UNAVAILABLE")
    run = session.get(MaterialProcessingRun, current.run_id)
    if run.base_revision is None:
        return (), []

    previous_sessions = session.execute(
        select(StudySession.study_session_id, StudySession.knowledge_structure_revision)
        .join(KnowledgeStructure, (
            (KnowledgeStructure.learner_id == StudySession.learner_id)
            & (KnowledgeStructure.material_id == StudySession.material_id)
            & (KnowledgeStructure.structure_revision == StudySession.knowledge_structure_revision)
        ))
        .where(
            StudySession.learner_id == learner.learner_id,
            StudySession.material_id == study.material_id,
            KnowledgeStructure.created_at < current.created_at,
        )
        .order_by(StudySession.started_at, StudySession.study_session_id)
    ).all()

    inherited = []
    latest_cycles = {}
    current_claims = {
        concept["concept_id"]: {claim["claim_id"] for claim in concept["claims"]}
        for concept in document["concepts"]
    }
    for session_id, revision in previous_sessions:
        previous = _read_verified_document(
            session, learner.learner_id, study.material_id, revision=revision
        )
        matches = unchanged_claims(previous, document)
        if not matches:
            continue
        previous_study = _row(session, learner.learner_id, session_id)
        previous_context = _context_from_validated_document(study.material_id, previous)
        _validate_context(previous_study, previous_context)
        previous_claims = {
            concept["concept_id"]: {claim["claim_id"] for claim in concept["claims"]}
            for concept in previous["concepts"]
        }
        for cycle in _read_cycles(session, previous_study):
            origin = cycle["concept_id"]
            targets = [matches.get((origin, claim)) for claim in previous_claims[origin]]
            if not targets or any(target is None for target in targets):
                continue
            target_ids = {target[0] for target in targets}
            if len(target_ids) != 1:
                continue
            target_id = next(iter(target_ids))
            # 只有整個觀念的重點完整且唯一匹配，才能承接通過結果。
            if {target[1] for target in targets} != current_claims[target_id]:
                continue
            root = session.get(AssessmentSet, UUID(cycle["diagnostic_set_id"]))
            order = (root.created_at, str(root.set_id))
            if target_id in latest_cycles and latest_cycles[target_id][0] >= order:
                continue
            # 較新的失敗或未完成檢測會取代舊通過狀態，不回退挑選舊成績。
            inherited_cycle = None
            if cycle["outcome"] == "passed" and cycle["active_set_id"] is None:
                inherited_cycle = {
                    **cycle, "concept_id": target_id,
                    "inherited_from": {
                        "study_session_id": str(session_id),
                        "knowledge_structure_revision": revision,
                        "run_id": previous["run_id"],
                    },
                }
            latest_cycles[target_id] = (order, inherited_cycle)
        for event in _read_events(session, previous_study):
            target = matches.get((event.target_concept_id, event.target_claim_id))
            if target is not None:
                inherited.append(InheritedAnswerEvidence(
                    *target,
                    event.semantic_identity,
                    event.is_correct,
                    event.mastery_qualified,
                    event.created_at,
                    event.answer_event_id,
                    event.assisted,
                ))
    return tuple(inherited), [
        latest_cycles[key][1] for key in sorted(latest_cycles)
        if latest_cycles[key][1] is not None
    ]
