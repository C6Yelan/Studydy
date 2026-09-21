"""舊 AnswerEvent 保持原樣；僅將未變知識點的作答證據投影到目前學習狀態。"""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from knowledge_map.source_identity import unchanged_claims
from runtime.storage.knowledge_structures import read_knowledge_structure
from runtime.storage.tables import StudySession, KnowledgeStructure, MaterialProcessingRun, database_session
from .answer_events import read_answer_events


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
    current=session.scalar(select(KnowledgeStructure).where(KnowledgeStructure.learner_id==owner,
        KnowledgeStructure.material_id==material_id,KnowledgeStructure.structure_revision==revision))
    run=session.get(MaterialProcessingRun,current.run_id)
    if not run.base_revision:return None
    prior=session.scalar(select(StudySession).where(StudySession.learner_id==owner,StudySession.material_id==material_id,
        StudySession.knowledge_structure_revision==run.base_revision,StudySession.status.in_(('active','no_safe')))
        .order_by(StudySession.started_at.desc()).limit(1))
    if prior is None or prior.current_concept_id is None:return None
    old=session.scalar(select(KnowledgeStructure).where(KnowledgeStructure.learner_id==owner,
        KnowledgeStructure.material_id==material_id,KnowledgeStructure.structure_revision==run.base_revision))
    if old is None:return None
    mapped={target[0] for origin,target in unchanged_claims(old.document,current.document).items() if origin[0]==prior.current_concept_id}
    return next(iter(mapped)) if len(mapped)==1 else None


def inherited_answers(learner, study, *, dsn=None):
    with database_session(dsn) as session:
        current = session.scalar(select(KnowledgeStructure).where(KnowledgeStructure.learner_id==learner.learner_id,
            KnowledgeStructure.material_id==study.material_id, KnowledgeStructure.structure_revision==study.knowledge_structure_revision))
        if current is None:
            raise ValueError('KNOWLEDGE_STRUCTURE_UNAVAILABLE')
        run=session.get(MaterialProcessingRun,current.run_id)
        if run.base_revision is None:
            return ()
        identities=session.execute(select(StudySession.study_session_id,StudySession.knowledge_structure_revision)
            .join(KnowledgeStructure,(KnowledgeStructure.learner_id==StudySession.learner_id)
                  &(KnowledgeStructure.material_id==StudySession.material_id)
                  &(KnowledgeStructure.structure_revision==StudySession.knowledge_structure_revision))
            .where(StudySession.learner_id==learner.learner_id,StudySession.material_id==study.material_id,
                   KnowledgeStructure.created_at<current.created_at)).all()
    result=[]
    for session_id,revision in identities:
        previous=read_knowledge_structure(learner.learner_id,study.material_id,revision=revision,dsn=dsn).document
        matches=unchanged_claims(previous,current.document)
        if not matches:
            continue
        for event in read_answer_events(learner,session_id,dsn=dsn):
            target=matches.get((event.target_concept_id,event.target_claim_id))
            if target is not None:
                result.append(InheritedAnswerEvidence(*target,event.semantic_identity,event.is_correct,
                    event.mastery_qualified,event.created_at,event.answer_event_id,event.assisted))
    return tuple(result)
