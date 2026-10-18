"""同一學習 snapshot 的讀取用例；HTTP 型別與序列化留在 API 邊界。"""

from uuid import UUID

from learning_adaptation import assessment_sets
from learning_adaptation.learner_progress import progress_snapshot
from .errors import SourceError
from .material_processing import read_material_processing_run
from .storage import knowledge_reads
from .storage.knowledge_structures import _read_verified_document, _view


def _check_scope(study, plan, material_id, revision, run_id):
    if (
        study.material_id != material_id
        or study.knowledge_structure_revision != revision
        or plan["run_id"] != str(run_id)
    ):
        raise SourceError("RESOURCE_NOT_FOUND")


def _selection(rounds, concept_id, set_id):
    selected_id = (
        str(set_id)
        if set_id is not None
        else next(
            (
                group["set_id"]
                for group in rounds
                if group["target_concept_id"] == concept_id
            ),
            None,
        )
    )
    selected = next((group for group in rounds if group["set_id"] == selected_id), None)
    if selected_id is not None and selected is None:
        raise SourceError("RESOURCE_NOT_FOUND")
    return selected_id, selected


def read_workspace(
    learner, material_id, revision, study_session_id, run_id, set_id=None, *, dsn=None
):
    with progress_snapshot(learner, study_session_id, dsn=dsn) as (
        db,
        study,
        plan,
        progress,
    ):
        _check_scope(study, plan, material_id, revision, run_id)
        meta = knowledge_reads.metadata(db, learner.learner_id, material_id, revision)
        run = read_material_processing_run(learner.learner_id, run_id, dsn=dsn)
        rounds = assessment_sets._list_sets(db, study)["sets"]
        selected_id, selected = _selection(rounds, study.current_concept_id, set_id)
        identities = list(
            dict.fromkeys(
                identity
                for identity in (
                    selected["target_concept_id"] if selected else None,
                    study.current_concept_id,
                    progress.next_action.target_concept_id,
                )
                if identity is not None
            )
        )
        navigation_ids = set(identities) | {
            group["target_concept_id"] for group in rounds
        }
        navigation_ids.update(
            cycle["concept_id"] for cycle in progress.assessment_cycles
        )
        navigation_ids.update(progress.next_action.prerequisite_concept_ids)
        navigation = {
            "schema": "study-navigation/v1",
            "material_id": str(material_id),
            "knowledge_structure_revision": revision,
            "run_id": str(run_id),
            "source_resolver": meta["source_resolver"],
            "status": meta["status"],
            "concept_count": len(plan["concepts"]),
            "concepts": [
                {"concept_id": c["concept_id"], "label": c["label"]}
                for c in plan["concepts"]
                if c["concept_id"] in navigation_ids
            ],
            "initial_learning_path": [
                step
                for step in plan["path_steps"]
                if step["concept_id"] in navigation_ids
            ],
        }
        return {
            "schema": "study-workspace/v1",
            "session": study,
            "run_id": run_id,
            "source_artifact_id": run.source_artifact_id,
            "outline": navigation,
            "context": knowledge_reads.concept_context(
                db, learner.learner_id, material_id, revision, identities
            )
            if identities
            else None,
            "progress": progress,
            "focus_concept_ids": identities,
            "assessment_sets": rounds,
            "selected_set_id": selected_id,
            "selected_set": assessment_sets._read_set(db, study, UUID(selected_id))
            if selected_id
            else None,
        }


def read_resume(
    learner, material_id, revision, study_session_id, run_id, set_id=None, *, dsn=None
):
    with progress_snapshot(learner, study_session_id, dsn=dsn) as (
        db,
        study,
        plan,
        progress,
    ):
        _check_scope(study, plan, material_id, revision, run_id)
        document = _read_verified_document(
            db, learner.learner_id, material_id, revision=revision
        )
        run = read_material_processing_run(learner.learner_id, run_id, dsn=dsn)
        rounds = assessment_sets._list_sets(db, study)["sets"]
        selected_id, _ = _selection(rounds, study.current_concept_id, set_id)
        return {
            "session": study,
            "run_id": run_id,
            "source_artifact_id": run.source_artifact_id,
            "knowledge_structure": _view(document, material_id),
            "progress": progress,
            "assessment_sets": rounds,
            "selected_set_id": selected_id,
            "selected_set": assessment_sets._read_set(db, study, UUID(selected_id))
            if selected_id is not None
            else None,
        }
