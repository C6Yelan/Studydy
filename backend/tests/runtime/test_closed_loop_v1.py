from __future__ import annotations

import io
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pymupdf
import psycopg
import pytest
from fastapi.testclient import TestClient

from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context, build_knowledge_structure
from learning_adaptation.answer_events import AnswerSubmissionError, submit_answer
from learning_adaptation.assessments import AssessmentError, generate_assessment
from learning_adaptation.learner_progress import LearnerProgressError, apply_guidance, derive_learner_progress
from learning_adaptation.study_sessions import create_study_session, read_study_session
from runtime.learner_session import TrustedLearner, create_session
from runtime.material_processing import _record_progress, claim_next_material_processing_run, create_material_processing_run
from runtime.storage.artifacts import publish_idempotent_source_pdf
from runtime.storage.knowledge_structures import publish_knowledge_structure, read_knowledge_structure
from runtime.storage.migrations import run_migrations
from pdf_evidence.ocr_page_evidence import canonical_sha256
import runtime.api.app as api_app


class Client:
    pass


def _settings(tmp_path: Path) -> dict:
    root = tmp_path / "installed"
    return {
        "private_runtime_root": str(root / "runtime"),
        "runtime_lock": json.loads((Path(__file__).parents[3] / "local_ai/runtime-lock.json").read_text()),
        "python_executable": str(root / "ocr/runtime/bin/python3.12"),
        "site_packages": str(root / "ocr/runtime/lib/python3.12/site-packages"),
        "ocr_model_root": str(root / "models/unlimited-ocr"),
    }


def _pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Stacks")
    page.insert_text((72, 100), "A stack follows LIFO order.")
    value = document.tobytes()
    document.close()
    return value


def _page() -> dict:
    block_id = "block:sha256:" + "2" * 64
    return {
        "schema": "page-evidence/v3",
        "material_id": "material:sha256:" + "3" * 64,
        "page_ref": "page:sha256:" + "4" * 64,
        "page_number": 1,
        "evidence_blocks": [{
            "evidence_id": "evidence:sha256:" + "5" * 64,
            "block_id": block_id,
            "kind": "paragraph",
            "text": "A stack follows LIFO order.",
            "reading_order": 0,
            "locator": {"page": 1, "block_id": block_id, "region": [1.0, 2.0, 20.0, 30.0]},
        }],
    }


def _structure(run_id: str, source_sha256: str, lock: dict) -> dict:
    context = build_document_context([_page()], page_count=1)
    state = SemanticState()
    response = {
        "schema": "material-semantics-response/v1",
        "concepts": [{
            "key": "stack",
            "label": "Stack",
            "aliases": [],
            "claims": [{
                "meaning": "A stack follows LIFO order.",
                "source_spans": [{"evidence_id": context["evidence"][0]["evidence_id"], "quote": context["evidence"][0]["exact_text"]}],
            }],
        }],
        "relations": [],
    }
    apply_semantic_response(response, context=context, bundle={"sections": context["sections"], "evidence": context["evidence"]}, state=state)
    return build_knowledge_structure(
        context, state, source_sha256=source_sha256, run_id=run_id,
        produced_at="2026-09-05T00:00:00+00:00",
        runtime_lock_sha256=canonical_sha256(lock),
        model_id=lock["semantic_service"]["model_id"],
        model_revision=lock["semantic_service"]["revision"],
        semantic_calls=1, ocr_calls=0,
    )


def _assessment_response(angle: str, prompt: str) -> dict:
    candidate = {
        "learning_angle": angle,
        "novelty": "distinct",
        "safety": "safe",
        "prompt": prompt,
        "correct_answer": "LIFO",
        "supporting_evidence_ids": ["evidence:sha256:" + "5" * 64],
        "distractors": [
            {"text": "FIFO", "changed_from": "LIFO", "changed_to": "FIFO"},
            {"text": "RANDOM", "changed_from": "LIFO", "changed_to": "RANDOM"},
            {"text": "PRIORITY", "changed_from": "LIFO", "changed_to": "PRIORITY"},
        ],
    }
    return {"schema": "assessment-semantics-response/v1", "candidates": [candidate, {**candidate, "safety": "reject"}, {**candidate, "safety": "reject"}]}


@pytest.fixture
def closed_loop(clean_database_dsn, migrations_dir, tmp_path, monkeypatch):
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (1,)
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == ()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(artifact_root))
    created = create_session(dsn=clean_database_dsn)
    learner = TrustedLearner(created.learner_id)
    source = publish_idempotent_source_pdf(created.learner_id, io.BytesIO(_pdf()), "upload", dsn=clean_database_dsn)
    settings = _settings(tmp_path)
    run = create_material_processing_run(created.learner_id, source.material_id, source.artifact_id, "process", settings, dsn=clean_database_dsn)
    claim = claim_next_material_processing_run(dsn=clean_database_dsn)
    assert claim is not None and claim.run.run_id == run.run_id
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(run.run_id, stage, 1, 1, dsn=clean_database_dsn)
    structure = _structure(str(run.run_id), source.sha256, settings["runtime_lock"])
    publish_knowledge_structure(created.learner_id, source.material_id, run.run_id, structure, dsn=clean_database_dsn)
    return learner, source, settings, structure, clean_database_dsn, created.raw_token


def test_persisted_closed_loop_private_answer_mastery_and_guidance(closed_loop):
    learner, source, settings, structure, dsn, _token = closed_loop
    stored = read_knowledge_structure(learner.learner_id, source.material_id, revision=structure["revision"], dsn=dsn)
    concept = structure["concepts"][0]
    claim_id = concept["claims"][0]["claim_id"]
    study = create_study_session(learner, source.material_id, structure["revision"], "study", dsn=dsn)
    assert study.current_concept_id == concept["concept_id"]

    first = generate_assessment(
        learner, study.study_session_id, claim_id, "assessment-1", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response("definition", "根據教材，Stack 使用哪種順序？"),
    )
    assert "correct_option_id" not in first.public_document
    correct = first.private_answer_document["correct_option_id"]
    submitted = submit_answer(learner, study.study_session_id, first.assessment_revision, first.question_id, correct, "answer-1", dsn=dsn)
    assert submitted.feedback.is_correct
    replay = submit_answer(learner, study.study_session_id, first.assessment_revision, first.question_id, correct, "answer-1", dsn=dsn)
    assert replay.event.answer_event_id == submitted.event.answer_event_id

    second = generate_assessment(
        learner, study.study_session_id, claim_id, "assessment-2", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response("recognition", "依教材，哪個縮寫描述 Stack 順序？"),
    )
    submit_answer(learner, study.study_session_id, second.assessment_revision, second.question_id, second.private_answer_document["correct_option_id"], "answer-2", dsn=dsn)
    progress = derive_learner_progress(learner, study.study_session_id, dsn=dsn)
    assert progress.concept_states[0].status == "mastered"
    assert progress.next_action.action == "complete"
    apply_guidance(learner, study.study_session_id, progress.guidance_revision, dsn=dsn)
    assert read_study_session(learner, study.study_session_id, dsn=dsn).status == "completed"
    assert stored.view["concepts"][0]["claims"][0]["evidence"][0]["page"] == 1


def test_answer_idempotency_conflict_is_not_false_success(closed_loop):
    learner, source, settings, structure, dsn, _token = closed_loop
    concept = structure["concepts"][0]
    study = create_study_session(learner, source.material_id, structure["revision"], "study", dsn=dsn)
    assessment = generate_assessment(
        learner, study.study_session_id, concept["claims"][0]["claim_id"], "assessment", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response("definition", "根據教材，Stack 使用哪種順序？"),
    )
    first, second = assessment.public_document["options"][:2]
    submit_answer(learner, study.study_session_id, assessment.assessment_revision, assessment.question_id, first["option_id"], "same", dsn=dsn)
    with pytest.raises(AnswerSubmissionError, match="ANSWER_IDEMPOTENCY_CONFLICT"):
        submit_answer(learner, study.study_session_id, assessment.assessment_revision, assessment.question_id, second["option_id"], "same", dsn=dsn)


def test_concurrent_same_assessment_intent_publishes_once(closed_loop):
    learner, source, settings, structure, dsn, _token = closed_loop
    study = create_study_session(learner, source.material_id, structure["revision"], "study", dsn=dsn)
    claim_id = structure["concepts"][0]["claims"][0]["claim_id"]

    def request():
        return generate_assessment(
            learner, study.study_session_id, claim_id, "same-assessment-intent", settings,
            dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response("definition", "根據教材，Stack 使用哪種順序？"),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = [future.result(timeout=10) for future in (executor.submit(request), executor.submit(request))]
    assert first.assessment_revision == second.assessment_revision
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM assessments").fetchone() == (1,)


def test_guidance_revision_becomes_stale_after_answer_event(closed_loop):
    learner, source, settings, structure, dsn, _token = closed_loop
    study = create_study_session(learner, source.material_id, structure["revision"], "study", dsn=dsn)
    claim_id = structure["concepts"][0]["claims"][0]["claim_id"]
    before = derive_learner_progress(learner, study.study_session_id, dsn=dsn)
    assessment = generate_assessment(
        learner, study.study_session_id, claim_id, "assessment", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response("definition", "根據教材，Stack 使用哪種順序？"),
    )
    submit_answer(
        learner, study.study_session_id, assessment.assessment_revision,
        assessment.question_id, assessment.private_answer_document["correct_option_id"],
        "answer", dsn=dsn,
    )
    with pytest.raises(LearnerProgressError, match="LEARNER_GUIDANCE_STALE"):
        apply_guidance(learner, study.study_session_id, before.guidance_revision, dsn=dsn)


def test_no_safe_assessment_is_truthful_and_creates_no_private_answer(closed_loop):
    learner, source, settings, structure, dsn, _token = closed_loop
    study = create_study_session(learner, source.material_id, structure["revision"], "study", dsn=dsn)
    claim_id = structure["concepts"][0]["claims"][0]["claim_id"]
    rejected = _assessment_response("unsafe", "Ambiguous question")
    rejected["candidates"][0]["safety"] = "reject"
    with pytest.raises(AssessmentError, match="NO_SAFE_ASSESSMENT"):
        generate_assessment(
            learner, study.study_session_id, claim_id, "no-safe", settings,
            dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: rejected,
        )
    stored = read_study_session(learner, study.study_session_id, dsn=dsn)
    assert stored.status == "no_safe" and stored.no_safe_claim_ids == (claim_id,)
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM assessments").fetchone() == (0,)


def test_http_api_projects_the_same_closed_loop_without_private_answer(closed_loop, monkeypatch):
    learner, source, settings, structure, dsn, token = closed_loop

    class Workers:
        def stop(self):
            pass

    monkeypatch.setattr(api_app, "runtime_preflight", lambda _config: {})
    monkeypatch.setattr(api_app, "start_runtime_workers", lambda **_arguments: Workers())

    def generate(*arguments, **keywords):
        return generate_assessment(
            *arguments,
            **keywords,
            client=Client(),
            semantic_call=lambda *_args, **_kwargs: _assessment_response("definition", "根據教材，Stack 使用哪種順序？"),
        )

    monkeypatch.setattr(api_app, "generate_assessment", generate)
    app = api_app.create_app(api_app.ApiSettings(
        profile="test",
        public_origin="https://studydy.test",
        secure_cookie=True,
        local_config=settings,
        dsn=dsn,
    ))
    headers = {"Origin": "https://studydy.test"}
    with TestClient(app, base_url="https://studydy.test") as client:
        client.cookies.set("studydy_session", token)
        map_response = client.get(
            f"/v1/materials/{source.material_id}/knowledge-structures/{structure['revision']}"
        )
        assert map_response.status_code == 200
        assert map_response.json()["relations"] == []
        study_response = client.post(
            "/v1/study-sessions",
            headers={**headers, "Idempotency-Key": "http-study"},
            json={
                "schema": "study-session-create/v2",
                "material_id": str(source.material_id),
                "knowledge_structure_revision": structure["revision"],
                "current_concept_id": structure["concepts"][0]["concept_id"],
            },
        )
        assert study_response.status_code == 201
        study_id = study_response.json()["study_session_id"]
        assessment_response = client.post(
            f"/v1/study-sessions/{study_id}/assessments",
            headers={**headers, "Idempotency-Key": "http-assessment"},
            json={
                "schema": "assessment-create/v2",
                "target_claim_id": structure["concepts"][0]["claims"][0]["claim_id"],
            },
        )
        assert assessment_response.status_code == 201
        public = assessment_response.json()
        assert "correct_option_id" not in public
        correct = next(option for option in public["options"] if option["text"] == "LIFO")
        feedback = client.post(
            f"/v1/study-sessions/{study_id}/assessments/{public['assessment_revision']}/submissions",
            headers={**headers, "Idempotency-Key": "http-answer"},
            json={
                "schema": "answer-submission-create/v2",
                "question_id": public["question_id"],
                "selected_option_id": correct["option_id"],
            },
        )
        assert feedback.status_code == 201
        assert feedback.json()["is_correct"] is True
        progress = client.get(f"/v1/study-sessions/{study_id}/progress")
        assert progress.status_code == 200
        assert progress.json()["concept_states"][0]["attempts"] == 1
        openapi = client.get("/v1/openapi.json").text
        assert "knowledge-structures" in openapi
        assert "formal_concept" not in openapi
