from __future__ import annotations

from structure_fixtures import build_knowledge_structure

from product_fixtures import seed_pdf, seed_run, publish_fixture_structure

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

from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context
from learning_adaptation.answer_events import AnswerSubmissionError, read_answer_events
from learning_adaptation.assessments import AssessmentError
from learning_adaptation.learner_progress import LearnerProgressError, derive_learner_progress
from learning_adaptation.study_sessions import create_study_session, read_study_session
from runtime.learner_session import TrustedLearner, register_account
import runtime.material_processing as processing
from runtime.material_runtime import runtime_binding
from runtime.material_processing import MaterialProcessingError, _record_progress, claim_next_material_processing_run, read_material_processing_run
from runtime.storage.knowledge_structures import read_knowledge_structure
from runtime.storage.migrations import run_migrations, load_migrations
from pdf_evidence.ocr_page_evidence import canonical_sha256
import runtime.api.app as api_app


class Client:
    pass


def _settings(tmp_path: Path) -> dict:
    root = tmp_path / "installed"
    lock = json.loads((Path(__file__).parents[3] / "local_ai/runtime-lock.json").read_text())
    # 此 fixture 驗證已保存分析／作答；獨立檢核由 test_material_review_flow 啟用測試。
    lock.pop('material_review', None)
    return {
        "private_runtime_root": str(root / "runtime"),
        "runtime_lock": lock,
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
        "schema": "page-evidence/v1",
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


def _structure(run_id: str, source_sha256: str, lock: dict, *, partial: bool = False) -> dict:
    context = build_document_context([_page(source_sha256)], page_count=1)
    state = SemanticState()
    if partial:
        state.rejected_claims = 1
    response = {
        "concepts": [{
            "k": "stack", "l": "Stack", "a": [],
            "c": [{"m": None, "s": [0]}],
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
    return {"schema": "assessment-semantics-response/v1", "candidates": [candidate, {**candidate, "safety": "reject"}, {**candidate, "safety": "reject"}]}


@pytest.fixture
def closed_loop(clean_database_dsn, migrations_dir, tmp_path, monkeypatch):
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == tuple(m.version for m in load_migrations(migrations_dir))
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == ()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(artifact_root))
    created = register_account("learner_test@example.com", "Synthetic test password 42", dsn=clean_database_dsn)
    learner = TrustedLearner(created.learner_id)
    source = seed_pdf(created.learner_id, io.BytesIO(_pdf()), "upload", dsn=clean_database_dsn)
    settings = _settings(tmp_path)
    run = seed_run(created.learner_id, source.material_id, source.artifact_id, "process", settings, dsn=clean_database_dsn)
    claim = claim_next_material_processing_run(dsn=clean_database_dsn)
    assert claim is not None and claim.run.run_id == run.run_id
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(run.run_id, stage, 1, 1, dsn=clean_database_dsn)
    structure = _structure(str(run.run_id), source.sha256, settings["runtime_lock"])
    publish_fixture_structure(created.learner_id, source.material_id, run.run_id, structure, dsn=clean_database_dsn)
    return learner, source, settings, structure, clean_database_dsn, created.raw_token


@pytest.mark.parametrize('version', ['knowledge-structure/v2', None])
def test_database_accepts_only_explicit_v1_structure(closed_loop, version):
    _, _, _, _, dsn, _ = closed_loop
    with psycopg.connect(dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation, match='knowledge_structure_version'):
            with connection.transaction():
                if version is None:
                    connection.execute("UPDATE knowledge_structures SET document=document-'schema'")
                else:
                    connection.execute("UPDATE knowledge_structures SET document=jsonb_set(document,'{schema}',to_jsonb(%s::text))", (version,))


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


def test_real_api_lifespan_login_and_saved_reads_work_without_ai(closed_loop, monkeypatch):
    """真 worker 啟停與登入、已保存 Map 讀取皆不需要模型在線。"""
    import httpx
    from runtime.local_app import create_local_app
    learner, source, settings, structure, dsn, _token = closed_loop
    calls = []
    def offline(*_args, **_kwargs):
        calls.append("model")
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", offline)
    origin = "http://127.0.0.1:4173"
    app = create_local_app(profile="local", public_origin=origin, secure_cookie=False, local_config=settings, dsn=dsn)
    with TestClient(app, base_url=origin) as client:
        login = client.post("/v1/session/login", headers={"Origin": origin}, json={
            "email": "learner_test@example.com", "password": "Synthetic test password 42",
        })
        assert login.status_code == 200
        assert client.get("/v1/session").json()["learner_id"] == str(learner.learner_id)
        assert client.get("/v1/materials").status_code == 200
        map_response = client.get(
            f"/v1/materials/{source.material_id}/knowledge-structures/{structure['revision']}"
        )
        assert map_response.status_code == 200
        assert map_response.json()["concepts"][0]["claims"][0]["evidence"][0]["quote"]
    assert calls == []
