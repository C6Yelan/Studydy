from __future__ import annotations

import io
import hashlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import time
from uuid import UUID

import pymupdf
import psycopg
import pytest
from fastapi.testclient import TestClient

from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context, build_knowledge_structure
from learning_adaptation.answer_events import AnswerSubmissionError, read_answer_events, submit_answer
from learning_adaptation.assessments import AssessmentError, generate_assessment, read_assessment
from learning_adaptation.learner_progress import LearnerProgressError, apply_guidance, derive_learner_progress
from learning_adaptation.study_sessions import create_study_session, read_study_session
from runtime.learner_session import TrustedLearner, create_session
import runtime.material_processing as processing
from runtime.material_processing import MaterialProcessingError, _record_progress, claim_next_material_processing_run, create_material_processing_run, read_material_processing_run, runtime_binding
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


def _page(source_sha256: str) -> dict:
    page_ref = "page:sha256:" + canonical_sha256(
        {"source_sha256": source_sha256, "page_number": 1}
    )
    region = [1.0, 2.0, 20.0, 30.0]
    block_id = "block:sha256:" + canonical_sha256(
        {"page_ref": page_ref, "reading_order": 0, "region": region}
    )
    evidence_id = "evidence:sha256:" + canonical_sha256(
        {
            "page_ref": page_ref,
            "block_id": block_id,
            "kind": "paragraph",
            "source": "native_text",
            "text": "A stack follows LIFO order.",
            "reading_order": 0,
            "region": region,
        }
    )
    return {
        "schema": "page-evidence/v4",
        "material_id": "material:sha256:" + source_sha256,
        "page_ref": page_ref,
        "page_number": 1,
        "evidence_blocks": [{
            "evidence_id": evidence_id,
            "block_id": block_id,
            "kind": "paragraph",
            "source": "native_text",
            "text": "A stack follows LIFO order.",
            "reading_order": 0,
            "locator": {"page": 1, "block_id": block_id, "region": region},
        }],
    }


def _structure(run_id: str, source_sha256: str, lock: dict) -> dict:
    context = build_document_context([_page(source_sha256)], page_count=1)
    state = SemanticState()
    response = {
        "concepts": [{
            "k": "stack", "l": "Stack", "a": [],
            "c": [{"m": None, "s": [[0, 0, 0]]}],
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


def _assessment_response(angle: str, prompt: str, evidence_id: str) -> dict:
    candidate = {
        "learning_angle": angle,
        "novelty": "distinct",
        "safety": "safe",
        "prompt": prompt,
        "correct_answer": "LIFO",
        "supporting_evidence_ids": [evidence_id],
        "distractors": [
            "FIFO",
            "RANDOM",
            "PRIORITY",
        ],
    }
    return {"schema": "assessment-semantics-response/v2", "candidates": [candidate, {**candidate, "safety": "reject"}, {**candidate, "safety": "reject"}]}


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


def test_final_schema_contains_only_current_product_tables(clean_database_dsn, migrations_dir):
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (1,)
    with psycopg.connect(clean_database_dsn) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
        }
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public'"
            )
        }
    assert tables == {
        "schema_migrations", "learners", "learner_sessions", "materials", "artifacts",
        "material_processing_runs", "knowledge_structures", "study_sessions", "assessments",
        "answer_events",
    }
    assert not any("formal_concept" in column or "verifier" in column for column in columns)


def test_terminal_material_run_tamper_cannot_report_false_success(closed_loop):
    learner, _source, _settings_value, structure, dsn, _token = closed_loop
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE material_processing_runs SET output_binding="
            "jsonb_set(output_binding,'{page_count}','2'::jsonb) "
            "WHERE run_id=%s",
            (structure["run_id"],),
        )
    with pytest.raises(MaterialProcessingError, match="MATERIAL_RUN_INVALID"):
        read_material_processing_run(
            learner.learner_id, UUID(structure["run_id"]), dsn=dsn
        )


def test_persisted_closed_loop_private_answer_mastery_and_guidance(closed_loop):
    learner, source, settings, structure, dsn, _token = closed_loop
    stored = read_knowledge_structure(learner.learner_id, source.material_id, revision=structure["revision"], dsn=dsn)
    concept = structure["concepts"][0]
    claim_id = concept["claims"][0]["claim_id"]
    study = create_study_session(learner, source.material_id, structure["revision"], "study", dsn=dsn)
    assert study.current_concept_id == concept["concept_id"]

    first = generate_assessment(
        learner, study.study_session_id, claim_id, "assessment-1", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response("definition", "根據教材，Stack 使用哪種順序？", concept["evidence_refs"][0]),
    )
    assert "correct_option_id" not in first.public_document
    correct = first.private_answer_document["correct_option_id"]
    submitted = submit_answer(learner, study.study_session_id, first.assessment_revision, first.question_id, correct, "answer-1", dsn=dsn)
    assert submitted.feedback.is_correct
    replay = submit_answer(learner, study.study_session_id, first.assessment_revision, first.question_id, correct, "answer-1", dsn=dsn)
    assert replay.event.answer_event_id == submitted.event.answer_event_id

    second = generate_assessment(
        learner, study.study_session_id, claim_id, "assessment-2", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response("recognition", "依教材，哪個縮寫描述 Stack 順序？", concept["evidence_refs"][0]),
    )
    submit_answer(learner, study.study_session_id, second.assessment_revision, second.question_id, second.private_answer_document["correct_option_id"], "answer-2", dsn=dsn)
    progress = derive_learner_progress(learner, study.study_session_id, dsn=dsn)
    assert progress.concept_states[0].status == "mastered"
    assert progress.next_action.action == "complete"
    applied = apply_guidance(
        learner, study.study_session_id, progress.guidance_revision, dsn=dsn
    )
    replay = apply_guidance(
        learner, study.study_session_id, progress.guidance_revision, dsn=dsn
    )
    assert replay == applied
    assert read_study_session(learner, study.study_session_id, dsn=dsn).status == "completed"
    assert stored.view["concepts"][0]["claims"][0]["evidence"][0]["page"] == 1


def test_answer_idempotency_conflict_is_not_false_success(closed_loop):
    learner, source, settings, structure, dsn, _token = closed_loop
    concept = structure["concepts"][0]
    study = create_study_session(learner, source.material_id, structure["revision"], "study", dsn=dsn)
    assessment = generate_assessment(
        learner, study.study_session_id, concept["claims"][0]["claim_id"], "assessment", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response("definition", "根據教材，Stack 使用哪種順序？", concept["evidence_refs"][0]),
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
            dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response("definition", "根據教材，Stack 使用哪種順序？", structure["concepts"][0]["evidence_refs"][0]),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = [future.result(timeout=10) for future in (executor.submit(request), executor.submit(request))]
    assert first.assessment_revision == second.assessment_revision
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM assessments").fetchone() == (1,)


@pytest.mark.parametrize("second_novelty,second_correct,expected_status,qualified_count", [
    ("uncertain", True, "learning", 1),
    ("distinct", False, "needs_review", 1),
    ("distinct", True, "mastered", 2),
])
def test_publication_and_mastery_remain_separate_after_real_persistence(
    closed_loop, second_novelty, second_correct, expected_status, qualified_count
):
    """安全題可發布；不確定的新意或答錯不能累積成虛假掌握。"""
    learner, source, settings, structure, dsn, _token = closed_loop
    concept = structure["concepts"][0]
    claim_id = concept["claims"][0]["claim_id"]
    study = create_study_session(learner, source.material_id, structure["revision"], "study", dsn=dsn)
    for number in (1, 2):
        response = _assessment_response(
            f"angle-{number}", f"教材中的 Stack 順序，第 {number} 題？", concept["evidence_refs"][0]
        )
        response["candidates"][0]["novelty"] = "distinct" if number == 1 else second_novelty
        assessment = generate_assessment(
            learner, study.study_session_id, claim_id, f"assessment-{number}", settings,
            dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: response,
        )
        assert assessment.mastery_qualified is (number == 1 or second_novelty == "distinct")
        correct = assessment.private_answer_document["correct_option_id"]
        selected = correct if number == 1 or second_correct else next(
            option["option_id"] for option in assessment.public_document["options"] if option["option_id"] != correct
        )
        submitted = submit_answer(
            learner, study.study_session_id, assessment.assessment_revision,
            assessment.question_id, selected, f"answer-{number}", dsn=dsn,
        )
        assert submitted.feedback.is_correct is (number == 1 or second_correct)
    progress = derive_learner_progress(learner, study.study_session_id, dsn=dsn)
    assert progress.concept_states[0].status == expected_status
    assert progress.concept_states[0].qualified_correct_items == qualified_count
    assert read_study_session(learner, study.study_session_id, dsn=dsn).status == "active"
    assert read_knowledge_structure(
        learner.learner_id, source.material_id, revision=structure["revision"], dsn=dsn
    ).document == structure


def test_guidance_revision_becomes_stale_after_answer_event(closed_loop):
    learner, source, settings, structure, dsn, _token = closed_loop
    study = create_study_session(learner, source.material_id, structure["revision"], "study", dsn=dsn)
    claim_id = structure["concepts"][0]["claims"][0]["claim_id"]
    before = derive_learner_progress(learner, study.study_session_id, dsn=dsn)
    assessment = generate_assessment(
        learner, study.study_session_id, claim_id, "assessment", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response("definition", "根據教材，Stack 使用哪種順序？", structure["concepts"][0]["evidence_refs"][0]),
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
    rejected = _assessment_response("unsafe", "Ambiguous question", structure["concepts"][0]["evidence_refs"][0])
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


@pytest.mark.parametrize("mutation", ["private_answer", "public_prompt", "mastery"])
def test_assessment_tamper_is_rejected_before_read_or_scoring(closed_loop, mutation):
    learner, source, settings, structure, dsn, _token = closed_loop
    concept = structure["concepts"][0]
    study = create_study_session(learner, source.material_id, structure["revision"], "study", dsn=dsn)
    assessment = generate_assessment(
        learner, study.study_session_id, concept["claims"][0]["claim_id"], "assessment", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response(
            "definition", "根據教材，Stack 使用哪種順序？", concept["evidence_refs"][0]
        ),
    )
    with psycopg.connect(dsn) as connection:
        if mutation == "private_answer":
            replacement = next(
                option["option_id"]
                for option in assessment.public_document["options"]
                if option["option_id"] != assessment.private_answer_document["correct_option_id"]
            )
            connection.execute(
                "UPDATE assessments SET private_answer_document="
                "jsonb_set(private_answer_document,'{correct_option_id}',to_jsonb(%s::text)) "
                "WHERE assessment_revision=%s",
                (replacement, assessment.assessment_revision),
            )
        elif mutation == "public_prompt":
            connection.execute(
                "UPDATE assessments SET public_document="
                "jsonb_set(public_document,'{prompt}',to_jsonb('changed'::text)) "
                "WHERE assessment_revision=%s",
                (assessment.assessment_revision,),
            )
        else:
            connection.execute(
                "UPDATE assessments SET mastery_qualified=NOT mastery_qualified "
                "WHERE assessment_revision=%s",
                (assessment.assessment_revision,),
            )
    with pytest.raises(AssessmentError, match="ASSESSMENT_UNAVAILABLE"):
        read_assessment(
            learner, study.study_session_id, assessment.assessment_revision, dsn=dsn
        )
    with pytest.raises(AnswerSubmissionError, match="ANSWER_ASSESSMENT_UNAVAILABLE"):
        submit_answer(
            learner,
            study.study_session_id,
            assessment.assessment_revision,
            assessment.question_id,
            assessment.public_document["options"][0]["option_id"],
            f"tampered-{mutation}",
            dsn=dsn,
        )


def test_answer_event_correctness_tamper_cannot_change_mastery(closed_loop):
    learner, source, settings, structure, dsn, _token = closed_loop
    concept = structure["concepts"][0]
    study = create_study_session(learner, source.material_id, structure["revision"], "study", dsn=dsn)
    assessment = generate_assessment(
        learner, study.study_session_id, concept["claims"][0]["claim_id"], "assessment", settings,
        dsn=dsn, client=Client(), semantic_call=lambda *_args, **_kwargs: _assessment_response(
            "definition", "根據教材，Stack 使用哪種順序？", concept["evidence_refs"][0]
        ),
    )
    submitted = submit_answer(
        learner,
        study.study_session_id,
        assessment.assessment_revision,
        assessment.question_id,
        assessment.private_answer_document["correct_option_id"],
        "answer",
        dsn=dsn,
    )
    assert submitted.event.is_correct is True
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE answer_events SET is_correct=false WHERE answer_event_id=%s",
            (submitted.event.answer_event_id,),
        )
    with pytest.raises(AnswerSubmissionError, match="ANSWER_EVENT_UNAVAILABLE"):
        read_answer_events(learner, study.study_session_id, dsn=dsn)
    with pytest.raises(LearnerProgressError, match="LEARNER_PROGRESS_UNAVAILABLE"):
        derive_learner_progress(learner, study.study_session_id, dsn=dsn)


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
            semantic_call=lambda *_args, **_kwargs: _assessment_response("definition", "根據教材，Stack 使用哪種順序？", structure["concepts"][0]["evidence_refs"][0]),
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


def test_http_upload_worker_assessment_and_guidance_are_one_closed_loop(
    clean_database_dsn, migrations_dir, tmp_path, monkeypatch
):
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (1,)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(artifact_root))
    settings = _settings(tmp_path)
    produced: dict[str, dict] = {}

    def deterministic_analysis(request, local_config, *, run_id, progress_callback, **_arguments):
        source = Path(request["source_path"]).read_bytes()
        assert hashlib.sha256(source).hexdigest() == request["expected_source_sha256"]
        progress_callback("evidence", 1, 1)
        progress_callback("semantics", 1, 1)
        structure = _structure(run_id, request["expected_source_sha256"], local_config["runtime_lock"])
        produced["structure"] = structure
        return structure

    monkeypatch.setattr(api_app, "runtime_preflight", runtime_binding)
    monkeypatch.setattr(processing, "runtime_preflight", runtime_binding)
    monkeypatch.setattr(processing, "analyze_material", deterministic_analysis)
    assessment_round = 0

    def generate(*arguments, **keywords):
        nonlocal assessment_round
        assessment_round += 1
        evidence_id = produced["structure"]["concepts"][0]["evidence_refs"][0]
        return generate_assessment(
            *arguments,
            **keywords,
            client=Client(),
            semantic_call=lambda *_args, **_kwargs: _assessment_response(
                f"angle-{assessment_round}",
                f"根據教材，第 {assessment_round} 題：Stack 使用哪種順序？",
                evidence_id,
            ),
        )

    monkeypatch.setattr(api_app, "generate_assessment", generate)
    app = api_app.create_app(api_app.ApiSettings(
        profile="test",
        public_origin="https://studydy.test",
        secure_cookie=True,
        local_config=settings,
        dsn=clean_database_dsn,
    ))
    mutation_headers = {"Origin": "https://studydy.test"}
    with TestClient(app, base_url="https://studydy.test") as client:
        assert client.post("/v1/session", headers=mutation_headers).status_code == 204
        uploaded = client.post(
            "/v1/materials",
            headers={
                **mutation_headers,
                "Idempotency-Key": "full-upload",
                "Content-Type": "application/pdf",
            },
            content=_pdf(),
        )
        assert uploaded.status_code == 201
        material = uploaded.json()
        created = client.post(
            "/v1/material-processing-runs",
            headers={**mutation_headers, "Idempotency-Key": "full-process"},
            json={
                "schema": "material-processing-create/v1",
                "material_id": material["material_id"],
                "source_artifact_id": material["source_artifact_id"],
            },
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        deadline = time.monotonic() + 5
        while True:
            run = client.get(f"/v1/material-processing-runs/{run_id}").json()
            if run["status"] not in {"pending", "running"}:
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert run["status"] == "succeeded"
        revision = run["output_binding"]["knowledge_structure_revision"]
        map_url = f"/v1/materials/{material['material_id']}/knowledge-structures/{revision}"
        first_map = client.get(map_url)
        second_map = client.get(map_url)
        assert first_map.status_code == second_map.status_code == 200
        assert first_map.content == second_map.content
        view = first_map.json()
        assert view["concepts"][0]["claims"][0]["evidence"][0]["source_locator"]["page"] == 1
        study = client.post(
            "/v1/study-sessions",
            headers={**mutation_headers, "Idempotency-Key": "full-study"},
            json={
                "schema": "study-session-create/v2",
                "material_id": material["material_id"],
                "knowledge_structure_revision": revision,
                "current_concept_id": view["concepts"][0]["concept_id"],
            },
        ).json()
        claim_id = view["concepts"][0]["claims"][0]["claim_id"]
        for number in (1, 2):
            assessment = client.post(
                f"/v1/study-sessions/{study['study_session_id']}/assessments",
                headers={**mutation_headers, "Idempotency-Key": f"full-assessment-{number}"},
                json={"schema": "assessment-create/v2", "target_claim_id": claim_id},
            )
            assert assessment.status_code == 201
            public = assessment.json()
            assert "correct_option_id" not in public
            correct = next(option for option in public["options"] if option["text"] == "LIFO")
            feedback = client.post(
                f"/v1/study-sessions/{study['study_session_id']}/assessments/{public['assessment_revision']}/submissions",
                headers={**mutation_headers, "Idempotency-Key": f"full-answer-{number}"},
                json={
                    "schema": "answer-submission-create/v2",
                    "question_id": public["question_id"],
                    "selected_option_id": correct["option_id"],
                },
            )
            assert feedback.status_code == 201 and feedback.json()["is_correct"] is True
        progress = client.get(f"/v1/study-sessions/{study['study_session_id']}/progress").json()
        assert progress["concept_states"][0]["status"] == "mastered"
        completed = client.post(
            f"/v1/study-sessions/{study['study_session_id']}/guidance/apply",
            headers=mutation_headers,
            json={"schema": "guidance-apply/v2", "guidance_revision": progress["guidance_revision"]},
        )
        assert completed.status_code == 200
        assert client.get(f"/v1/study-sessions/{study['study_session_id']}").json()["status"] == "completed"
