"""知識閱讀、學習進度與評量的 HTTP 契約。"""

from __future__ import annotations

from copy import deepcopy
from typing import Literal
from uuid import UUID
from fastapi import Request, Query
from sqlalchemy import text
from learning_adaptation import assessment_sets, assessment_state
from learning_adaptation.learner_progress import (
    LearnerProgressError,
    apply_guidance,
    derive_learner_progress,
    progress_snapshot,
)
from learning_adaptation.study_sessions import (
    create_study_session,
    set_current_study_concept,
)
from ..learner_session import TrustedLearner
from ..storage.knowledge_structures import read_knowledge_structure
from ..storage import knowledge_reads
from ..learning_activity import RHYTHM_MAX_OFFSET, learning_activity, learning_rhythm
from ..storage.database import database_session
from ..study_workspace import read_workspace, read_resume
from . import request_context
from .models import (
    LearningGoalView, LearningGoalUpdate, LearningGoalAck, LearningGoalListView,
    StartingLevelView, StartingLevelUpdate, StudyStartCreate, StudyStartView,
    KnowledgeOutlineView,
    KnowledgeConceptsView,
    KnowledgeRegionsView,
    KnowledgeSearchView,
    LearningActivityView, CelebrationClaim, CelebrationClaimView,
    LearningRhythmView,
    LibrarySearchView,
    MapProgressView,
    ProgressSummaryView,
    StudyWorkspaceView,
    AssessmentPlanView,
    AssessmentSetAction,
    AssessmentSetCreate,
    AssessmentSetListView,
    AssessmentSetSubmission,
    AssessmentSetView,
    GuidanceApply,
    KnowledgeStructureView,
    LearnerProgressView,
    StudyResumeView,
    StudySessionCreate,
    StudySessionFocus,
    StudySessionView,
    project_learner_progress,
    project_knowledge_structure,
    project_study_session,
)


def install(app, settings):
    from learning_adaptation import learning_goals

    @app.get("/v1/materials/{material_id}/goal", response_model=LearningGoalView, tags=["learning"])
    def material_goal(request: Request, material_id: UUID,
                      preview_offset: int = Query(0, ge=0), preview_days: int = Query(7, ge=1, le=31)):
        request_context.require_query(request, {"preview_offset", "preview_days"})
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            db.info["read_snapshot"] = {"documents": {}, "inherited": {}, "cycles": {}}
            return learning_goals.read_goal(db, learner.learner_id, material_id,
                                            preview_offset=preview_offset, preview_days=preview_days)

    @app.put("/v1/materials/{material_id}/goal", response_model=LearningGoalView, tags=["learning"])
    def update_material_goal(request: Request, material_id: UUID, body: LearningGoalUpdate):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return learning_goals.set_goal(learner, material_id, body.target_date, body.target_percent, dsn=settings.dsn)

    @app.post("/v1/materials/{material_id}/goal/acknowledge-update", response_model=LearningGoalView, tags=["learning"])
    def acknowledge_goal_update(request: Request, material_id: UUID, body: LearningGoalAck):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return learning_goals.acknowledge_update(learner, material_id, body.knowledge_structure_revision, dsn=settings.dsn)

    @app.get("/v1/learning-goals", response_model=LearningGoalListView, tags=["learning"])
    def learning_goal_list(request: Request):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            db.info["read_snapshot"] = {"documents": {}, "inherited": {}, "cycles": {}}
            return learning_goals.list_goals(db, learner.learner_id)

    from learning_adaptation import starting_levels

    @app.get("/v1/materials/{material_id}/starting-level", response_model=StartingLevelView, tags=["learning"])
    def starting_level(request: Request, material_id: UUID):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            return starting_levels.read_level(db, learner.learner_id, material_id)

    @app.put("/v1/materials/{material_id}/starting-level", response_model=StartingLevelView, tags=["learning"])
    def update_starting_level(request: Request, material_id: UUID, body: StartingLevelUpdate):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return starting_levels.set_level(learner, material_id, body.starting_level, dsn=settings.dsn)

    @app.post("/v1/materials/{material_id}/study-start", response_model=StudyStartView, tags=["learning"])
    def start_material_study(request: Request, material_id: UUID, body: StudyStartCreate):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        result = starting_levels.start_learning(learner, material_id, body.knowledge_structure_revision,
            request_context.idempotency_key(request), settings.local_config,
            current_concept_id=body.current_concept_id, dsn=settings.dsn)
        result["session"] = project_study_session(result["session"])
        return result

    from learning_adaptation.learning_insights import read_diagnosis, read_difficulty
    from .models import LearningDiagnosisView, ConceptDifficultyView

    @app.get("/v1/materials/{material_id}/knowledge-structures/{structure_revision}/learning-diagnosis",
             response_model=LearningDiagnosisView, tags=["learning"])
    def learning_diagnosis(request: Request, material_id: UUID, structure_revision: str,
                           set_id: UUID | None = None):
        request_context.require_query(request, {"set_id"})
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            return read_diagnosis(db, learner.learner_id, material_id, structure_revision, set_id)

    @app.get("/v1/materials/{material_id}/knowledge-structures/{structure_revision}/concept-difficulty",
             response_model=ConceptDifficultyView, tags=["learning"])
    def concept_difficulty(request: Request, material_id: UUID, structure_revision: str):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            return read_difficulty(db, learner.learner_id, material_id, structure_revision)

    @app.get(
        "/v1/materials/{material_id}/knowledge-structures/{structure_revision}/outline",
        response_model=KnowledgeOutlineView,
        tags=["review"],
    )
    def read_outline(request: Request, material_id: UUID, structure_revision: str):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            return knowledge_reads.outline(
                db, learner.learner_id, material_id, structure_revision
            )

    @app.get(
        "/v1/materials/{material_id}/knowledge-structures/{structure_revision}/regions",
        response_model=KnowledgeRegionsView,
        tags=["review"],
    )
    def read_regions(request: Request, material_id: UUID, structure_revision: str):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            return knowledge_reads.regions(
                db, learner.learner_id, material_id, structure_revision
            )

    @app.get(
        "/v1/search",
        response_model=LibrarySearchView,
        tags=["review"],
    )
    def search_library(
        request: Request, q: str = Query(min_length=1, max_length=200)
    ):
        request_context.require_query(request, {"q"})
        if not q.strip():
            raise request_context.ApiFailure("REQUEST_INVALID")
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            return knowledge_reads.search_library(db, learner.learner_id, q)

    @app.get(
        "/v1/learning-activity",
        response_model=LearningActivityView,
        tags=["learning"],
    )
    def read_learning_activity(request: Request):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            return learning_activity(db, learner.learner_id)

    @app.post("/v1/learning-activity/celebrations/claim", response_model=CelebrationClaimView, tags=["learning"])
    def claim_learning_celebrations(request: Request, body: CelebrationClaim):
        from ..learning_retention import claim_celebrations
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            return claim_celebrations(db, learner.learner_id)

    @app.get(
        "/v1/learning-activity/rhythm",
        response_model=LearningRhythmView,
        tags=["learning"],
    )
    def read_learning_rhythm(
        request: Request,
        period: Literal["week", "month", "quarter", "year"] = Query(),
        # 往前翻的頁數；各區間的上限見 RHYTHM_MAX_OFFSET（都約十年），避免任意大的區間。
        offset: int = Query(0, ge=0),
    ):
        request_context.require_query(request, {"period", "offset"})
        if offset > RHYTHM_MAX_OFFSET[period]:
            raise request_context.ApiFailure("REQUEST_INVALID")
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            return learning_rhythm(db, learner.learner_id, period=period, offset=offset)

    @app.get(
        "/v1/materials/{material_id}/knowledge-structures/{structure_revision}/concepts",
        response_model=KnowledgeConceptsView,
        tags=["review"],
    )
    def read_concepts(
        request: Request,
        material_id: UUID,
        structure_revision: str,
        ids: str,
        neighbors: bool = False,
    ):
        request_context.require_query(request, {"ids", "neighbors"})
        import re

        identities = ids.split(",")
        if (
            not 1 <= len(identities) <= 32
            or len(set(identities)) != len(identities)
            or any(
                not re.fullmatch(r"concept:sha256:[0-9a-f]{64}", identity)
                for identity in identities
            )
        ):
            raise request_context.ApiFailure("REQUEST_INVALID")
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            return knowledge_reads.concept_context(
                db,
                learner.learner_id,
                material_id,
                structure_revision,
                identities,
                neighbors=neighbors,
            )

    @app.get(
        "/v1/materials/{material_id}/knowledge-structures/{structure_revision}/search",
        response_model=KnowledgeSearchView,
        tags=["review"],
    )
    def search_concepts(
        request: Request,
        material_id: UUID,
        structure_revision: str,
        q: str = Query(min_length=1, max_length=200),
    ):
        request_context.require_query(request, {"q"})
        learner = request_context.trusted_learner(request, settings)
        with database_session(settings.dsn) as db:
            return knowledge_reads.search(
                db, learner.learner_id, material_id, structure_revision, q
            )

    @app.get(
        "/v1/study-sessions/{study_session_id}/map-progress",
        response_model=MapProgressView,
        tags=["learning"],
    )
    def map_progress(request: Request, study_session_id: UUID):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        progress = derive_learner_progress(learner, study_session_id, dsn=settings.dsn)
        return {
            "schema": "map-progress/v1",
            "starting_level": progress.starting_level,
            "learning_path": progress.learning_path,
            "study_session_id": study_session_id,
            "knowledge_structure_revision": progress.knowledge_structure_revision,
            "current_concept_id": progress.current_concept_id,
            "assessment_cycles": progress.assessment_cycles,
            "next_action": progress.next_action.model_dump(),
            "concept_states": [
                {
                    "concept_id": s.concept_id,
                    "status": s.status,
                    "attempts": s.attempts,
                    "assessable": bool(s.assessable_claim_ids),
                }
                for s in progress.concept_states
            ],
        }

    @app.get(
        "/v1/materials/{material_id}/knowledge-structures/{structure_revision}/progress-summary",
        response_model=ProgressSummaryView,
        tags=["learning"],
    )
    def progress_summary(
        request: Request,
        material_id: UUID,
        structure_revision: str,
        study_session_id: UUID | None = None,
    ):
        request_context.require_query(request, {"study_session_id"})
        learner = request_context.trusted_learner(request, settings)
        summary = {
            "schema": "progress-summary/v1",
            "material_id": material_id,
            "knowledge_structure_revision": structure_revision,
            "study_session_id": study_session_id,
        }
        if study_session_id is None:
            with database_session(settings.dsn) as db:
                plan = knowledge_reads.learning(
                    db, learner.learner_id, material_id, structure_revision
                )
            return summary | {
                "concept_count": len(plan["concepts"]),
                "completed_count": 0,
                "current_concept_id": None,
                "current_concept_label": None,
            }
        with progress_snapshot(learner, study_session_id, dsn=settings.dsn) as (
            _, study, plan, progress,
        ):
            # 學習工作階段須屬於路徑上的教材與版本，避免以他份教材的進度顯示。
            if (
                study.material_id != material_id
                or study.knowledge_structure_revision != structure_revision
            ):
                raise LearnerProgressError("LEARNER_PROGRESS_UNAVAILABLE")
            current = progress.current_concept_id
            return summary | {
                "concept_count": len(plan["concepts"]),
                "completed_count": sum(
                    s.status == "completed" for s in progress.concept_states
                ),
                "current_concept_id": current,
                "current_concept_label": next(
                    (c["label"] for c in plan["concepts"] if c["concept_id"] == current),
                    None,
                ),
            }

    @app.get(
        "/v1/materials/{material_id}/knowledge-structures/{structure_revision}"
        "/study-sessions/{study_session_id}/workspace",
        response_model=StudyWorkspaceView,
        tags=["learning"],
    )
    def study_workspace(
        request: Request,
        material_id: UUID,
        structure_revision: str,
        study_session_id: UUID,
        run_id: UUID,
        set_id: UUID | None = None,
    ):
        request_context.require_query(request, {"run_id", "set_id"})
        learner = request_context.trusted_learner(request, settings)
        result = read_workspace(
            learner,
            material_id,
            structure_revision,
            study_session_id,
            run_id,
            set_id,
            dsn=settings.dsn,
        )
        identities = result.pop("focus_concept_ids")
        progress = result["progress"]
        focused = project_learner_progress(progress).model_dump(
            mode="json", by_alias=True
        )
        focused.update(
            schema="study-progress/v1",
            concept_count=len(progress.concept_states),
            concept_states=[
                state
                for state in focused["concept_states"]
                if state["concept_id"] in identities
            ],
            weaknesses=[
                state
                for state in focused["weaknesses"]
                if state["concept_id"] in identities
            ],
        )
        result["session"] = project_study_session(result["session"])
        result["progress"] = focused
        return result

    @app.get(
        "/v1/materials/{material_id}/knowledge-structures/{structure_revision}",
        response_model=KnowledgeStructureView,
        response_model_by_alias=True,
        operation_id="getKnowledgeStructure",
        tags=["review"],
    )
    def read_map_route(
        request: Request, material_id: UUID, structure_revision: str
    ) -> KnowledgeStructureView:
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        stored = read_knowledge_structure(
            learner.learner_id,
            material_id,
            revision=structure_revision,
            dsn=settings.dsn,
        )
        return project_knowledge_structure(stored.view)

    @app.get(
        "/v1/materials/{material_id}/knowledge-structures/{structure_revision}"
        "/study-sessions/{study_session_id}/resume",
        response_model=StudyResumeView,
        operation_id="resumeStudySession",
        tags=["learning"],
    )
    def resume_study_route(
        request: Request,
        material_id: UUID,
        structure_revision: str,
        study_session_id: UUID,
        run_id: UUID,
        set_id: UUID | None = None,
    ) -> StudyResumeView:
        request_context.require_query(request, {"run_id", "set_id"})
        learner = request_context.trusted_learner(request, settings)
        result = read_resume(
            learner,
            material_id,
            structure_revision,
            study_session_id,
            run_id,
            set_id,
            dsn=settings.dsn,
        )
        return StudyResumeView(
            **{
                **result,
                "session": project_study_session(result["session"]),
                "knowledge_structure": project_knowledge_structure(
                    result["knowledge_structure"]
                ),
                "progress": project_learner_progress(result["progress"]),
            }
        )

    @app.post(
        "/v1/study-sessions",
        response_model=StudySessionView,
        response_model_by_alias=True,
        status_code=201,
        operation_id="createStudySession",
        tags=["learning"],
    )
    def create_study_session_route(
        request: Request, body: StudySessionCreate
    ) -> StudySessionView:
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        stored = create_study_session(
            learner,
            body.material_id,
            body.knowledge_structure_revision,
            request_context.idempotency_key(request),
            current_concept_id=body.current_concept_id,
            dsn=settings.dsn,
        )
        return project_study_session(stored)

    @app.post(
        "/v1/study-sessions/{study_session_id}/focus",
        response_model=StudySessionView,
        response_model_by_alias=True,
        operation_id="focusStudySession",
        tags=["learning"],
    )
    def focus_study_session_route(
        request: Request, study_session_id: UUID, body: StudySessionFocus
    ) -> StudySessionView:
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return project_study_session(
            set_current_study_concept(
                learner,
                study_session_id,
                body.current_concept_id,
                dsn=settings.dsn,
            )
        )

    def read_set_view(learner: TrustedLearner, study_session_id: UUID, set_id: UUID):
        return AssessmentSetView.model_validate(
            assessment_state.read_set(
                learner,
                study_session_id,
                set_id,
                dsn=settings.dsn,
            )
        )

    @app.get(
        "/v1/study-sessions/{study_session_id}/assessment-plan",
        response_model=AssessmentPlanView,
        operation_id="getAssessmentPlan",
        tags=["learning"],
    )
    def get_assessment_plan(request: Request, study_session_id: UUID, concept_id: str):
        request_context.require_query(request, {"concept_id"})
        return AssessmentPlanView.model_validate(
            assessment_state.read_plan(
                request_context.trusted_learner(request, settings),
                study_session_id,
                concept_id,
                dsn=settings.dsn,
            )
        )

    @app.get(
        "/v1/study-sessions/{study_session_id}/assessment-sets",
        response_model=AssessmentSetListView,
        operation_id="listAssessmentSets",
        tags=["learning"],
    )
    def list_assessment_sets(request: Request, study_session_id: UUID):
        request_context.require_query(request, set())
        return AssessmentSetListView.model_validate(
            assessment_state.list_sets(
                request_context.trusted_learner(request, settings),
                study_session_id,
                dsn=settings.dsn,
            )
        )

    @app.post(
        "/v1/study-sessions/{study_session_id}/assessment-sets",
        response_model=AssessmentSetView,
        status_code=202,
        operation_id="createAssessmentSet",
        tags=["learning"],
    )
    def create_assessment_set(
        request: Request, study_session_id: UUID, body: AssessmentSetCreate
    ):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        set_id = assessment_sets.create_set(
            learner,
            study_session_id,
            body.target_concept_id,
            request_context.idempotency_key(request),
            deepcopy(settings.local_config),
            dsn=settings.dsn,
        )
        return read_set_view(learner, study_session_id, set_id)

    @app.get(
        "/v1/study-sessions/{study_session_id}/assessment-sets/{set_id}",
        response_model=AssessmentSetView,
        operation_id="getAssessmentSet",
        tags=["learning"],
    )
    def get_assessment_set(request: Request, study_session_id: UUID, set_id: UUID):
        request_context.require_query(request, set())
        return read_set_view(
            request_context.trusted_learner(request, settings), study_session_id, set_id
        )

    @app.post(
        "/v1/study-sessions/{study_session_id}/assessment-sets/{set_id}/submissions",
        response_model=AssessmentSetView,
        operation_id="submitAssessmentSet",
        tags=["learning"],
    )
    def submit_assessment_set(
        request: Request,
        study_session_id: UUID,
        set_id: UUID,
        body: AssessmentSetSubmission,
    ):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        assessment_sets.submit_set_answers(
            learner,
            study_session_id,
            set_id,
            [answer.model_dump() for answer in body.answers],
            body.expected_set_version,
            request_context.idempotency_key(request),
            dsn=settings.dsn,
        )
        return read_set_view(learner, study_session_id, set_id)

    @app.post(
        "/v1/study-sessions/{study_session_id}/assessment-sets/{set_id}/remediation",
        response_model=AssessmentSetView,
        status_code=202,
        operation_id="createRemediationSet",
        tags=["learning"],
    )
    def create_remediation_set(
        request: Request,
        study_session_id: UUID,
        set_id: UUID,
        body: AssessmentSetAction,
    ):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        created = assessment_sets.create_remediation(
            learner,
            study_session_id,
            set_id,
            body.expected_set_version,
            request_context.idempotency_key(request),
            deepcopy(settings.local_config),
            dsn=settings.dsn,
        )
        return read_set_view(learner, study_session_id, created)

    def apply_set_action(request, study_session_id, set_id, body, action):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        assessment_sets.change_set(
            learner,
            study_session_id,
            set_id,
            action,
            body.expected_set_version,
            request_context.idempotency_key(request),
            dsn=settings.dsn,
        )
        return read_set_view(learner, study_session_id, set_id)

    @app.post(
        "/v1/study-sessions/{study_session_id}/assessment-sets/{set_id}/retry",
        response_model=AssessmentSetView,
        operation_id="retryAssessmentSet",
        tags=["learning"],
    )
    def retry_assessment_set(
        request: Request,
        study_session_id: UUID,
        set_id: UUID,
        body: AssessmentSetAction,
    ):
        return apply_set_action(request, study_session_id, set_id, body, "retry")

    @app.post(
        "/v1/study-sessions/{study_session_id}/assessment-sets/{set_id}/publish-partial",
        response_model=AssessmentSetView,
        operation_id="publishPartialAssessmentSet",
        tags=["learning"],
    )
    def publish_partial_assessment_set(
        request: Request,
        study_session_id: UUID,
        set_id: UUID,
        body: AssessmentSetAction,
    ):
        return apply_set_action(
            request, study_session_id, set_id, body, "publish-partial"
        )

    @app.post(
        "/v1/study-sessions/{study_session_id}/guidance/apply",
        response_model=LearnerProgressView,
        operation_id="applyGuidance",
        tags=["learning"],
    )
    def apply_guidance_route(
        request: Request, study_session_id: UUID, body: GuidanceApply
    ):
        request_context.require_query(request, set())
        return project_learner_progress(
            apply_guidance(
                request_context.trusted_learner(request, settings),
                study_session_id,
                body.guidance_revision,
                dsn=settings.dsn,
            )
        )

    @app.get(
        "/v1/study-sessions/{study_session_id}/progress",
        response_model=LearnerProgressView,
        response_model_by_alias=True,
        operation_id="getLearnerProgress",
        tags=["learning"],
    )
    def read_learner_progress_route(
        request: Request, study_session_id: UUID
    ) -> LearnerProgressView:
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return project_learner_progress(
            derive_learner_progress(learner, study_session_id, dsn=settings.dsn)
        )
