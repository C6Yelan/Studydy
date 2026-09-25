"""Runtime 測試共用的合成教材、學習者、API 與教材庫資料。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from hashlib import sha256
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import psycopg
import pymupdf
import pytest

import runtime.api.app as api_app
from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context, build_structure_draft
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.source_set import rebase_page
from runtime import source_normalization as normalization
from runtime.learner_session import TrustedLearner, register_account
from runtime.material_processing import _record_progress, claim_next_material_processing_run
from runtime.source_resolver import _input, bind_structure_input
from runtime.source_revisions import create_revision
from runtime.storage.knowledge_structures import publish_knowledge_structure
from runtime.storage.migrations import load_migrations, run_migrations
from runtime.storage.tables import Material, MaterialProcessingRun, database_session
from structure_fixtures import build_knowledge_structure



def seed_pdf(owner, stream, key, *, dsn, display_name=None):
    data = stream.read()
    name = display_name or "Synthetic.pdf"
    material = normalization.create_draft(owner, name, key, dsn=dsn)

    def identity(source_bytes, *_):
        with pymupdf.open(stream=source_bytes, filetype="pdf") as pdf:
            page_count = len(pdf)
        digest = sha256(source_bytes).hexdigest()
        mapping = {
            "schema": "source-mapping/v1",
            "format": "pdf",
            "original_sha256": digest,
            "normalized_sha256": digest,
            "page_count": page_count,
            "records": [
                {
                    "normalized_page": page,
                    "origin_locator": {"original_page": page},
                    "accuracy": "exact",
                }
                for page in range(1, page_count + 1)
            ],
        }
        return source_bytes, mapping

    with patch.object(
        normalization, "conversion_policy",
        return_value={"schema": "normalization-policy/v1", "renderer": "fixture-pdf"},
    ):
        source_id = normalization.upload_source(owner, material, data, name, "application/pdf", key, dsn=dsn)
    with patch.object(normalization, "convert", side_effect=identity):
        while True:
            sources = normalization.read_sources(owner, material, dsn=dsn)
            source = next(item for item in sources if item["source_id"] == source_id)
            if source["status"] == "ready":
                break
            assert normalization.normalize_next(dsn=dsn)
    return SimpleNamespace(
        material_id=material,
        artifact_id=source["normalized_artifact_id"],
        sha256=sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def seed_run(owner, material, key, settings, *, dsn):
    with database_session(dsn) as session:
        base_revision = session.get(Material, material).head_revision
    normalization_ids = (
        [source["normalization_id"] for source in normalization.read_sources(owner, material, dsn=dsn)]
        if base_revision is None else []
    )
    config = deepcopy(settings)
    if base_revision is not None:
        lock_path = Path(__file__).parents[3] / "local_ai/runtime-lock.json"
        config["runtime_lock"]["material_review"] = json.loads(lock_path.read_text())["material_review"]
    return create_revision(
        owner, material, normalization_ids, key, config, base_revision=base_revision, dsn=dsn,
    )


def publish_fixture_structure(owner, material, run, document, *, dsn):
    binding = _input(owner, run, dsn=dsn)
    if document.get("input_binding") != binding:
        # fixture 的來源身分須重新綁到本次 SourceSet；草稿沒有正式 schema 或 revision。
        evidence_rows = document["evidence"]
        pages = []
        for page_number in range(1, document["page_count"] + 1):
            blocks = [
                {
                    "kind": evidence["kind"],
                    "source": evidence["source"],
                    "text": evidence["exact_text"],
                    "reading_order": evidence["block_order"],
                    "locator": deepcopy(evidence["source_locator"]),
                }
                for evidence in evidence_rows if evidence["page"] == page_number
            ]
            if blocks:
                pages.append(rebase_page(
                    {"schema": "page-evidence/v1", "evidence_blocks": blocks},
                    binding["source_set_digest"], page_number,
                ))
        context = build_document_context(
            pages,
            page_count=document["page_count"],
            source_pages=binding["bundle"]["pages"],
        )
        evidence_indexes = {
            evidence["evidence_id"]: index for index, evidence in enumerate(evidence_rows)
        }
        concept_keys = {
            concept["concept_id"]: f"concept_{index}"
            for index, concept in enumerate(document["concepts"])
        }
        concepts = []
        for concept in document["concepts"]:
            claims = [
                {
                    "m": claim["text"],
                    "s": [evidence_indexes[ref] for ref in claim["evidence_refs"]],
                }
                for claim in concept["claims"]
            ]
            concepts.append({
                "k": concept_keys[concept["concept_id"]],
                "l": concept["label"],
                "a": concept["aliases"],
                "c": claims,
            })
        relations = [
            {
                "s": concept_keys[relation["source_concept_id"]],
                "t": concept_keys[relation["target_concept_id"]],
                "k": relation["type"],
                "r": relation["learner_reason"],
                "e": [evidence_indexes[ref] for ref in relation["evidence_refs"]],
                "c": relation["confidence"],
            }
            for relation in document["relations"]
        ]
        response = {"concepts": concepts, "relations": relations}
        state = SemanticState()
        state.rejected_claims = document["metrics"]["rejected_claims"]
        state.source_review_required = document.get("source_review_required", False)
        apply_semantic_response(
            response,
            context=context,
            bundle={"sections": context["sections"], "evidence": context["evidence"]},
            state=state,
        )
        with database_session(dsn) as session:
            provenance = deepcopy(session.get(MaterialProcessingRun, run).runtime_binding)
        result = build_structure_draft(
            context,
            state,
            source_sha256=binding["source_set_digest"],
            run_id=str(run),
            produced_at=document["produced_at"],
            runtime_lock_sha256=provenance["runtime_lock_sha256"],
            model_id=provenance["model_id"],
            model_revision=provenance["model_revision"],
            semantic_calls=document["metrics"]["semantic_calls"],
            ocr_calls=document["metrics"]["ocr_calls"],
        )
        result = bind_structure_input(owner, run, result, dsn=dsn)
        document.clear()
        document.update(result)
    return publish_knowledge_structure(owner, material, run, document, dsn=dsn)


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
    run = seed_run(created.learner_id, source.material_id, "process", settings, dsn=clean_database_dsn)
    claim = claim_next_material_processing_run(dsn=clean_database_dsn)
    assert claim is not None and claim.run.run_id == run.run_id
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(run.run_id, stage, 1, 1, dsn=clean_database_dsn)
    structure = _structure(str(run.run_id), source.sha256, settings["runtime_lock"])
    publish_fixture_structure(created.learner_id, source.material_id, run.run_id, structure, dsn=clean_database_dsn)
    return learner, source, settings, structure, clean_database_dsn, created.raw_token


ORIGIN = "https://studydy.test"
HEADERS = {"Origin": ORIGIN}


def _app(dsn, tmp_path, monkeypatch):
    # 帳號測試使用真 API/DB；只隔離與帳號無關的模型 preflight 與 worker。
    monkeypatch.setattr(api_app, "runtime_binding", lambda _: {})
    return api_app.create_app(api_app.ApiSettings(
        profile="test", public_origin=ORIGIN, secure_cookie=True,
        local_config=_settings(tmp_path), dsn=dsn,
    ))


@pytest.fixture
def library_materials(closed_loop):
    learner, first, settings, structure, dsn, token = closed_loop
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE materials SET display_name='堆疊講義.pdf' WHERE material_id=%s",
            (first.material_id,),
        )
    second_run = seed_run(learner.learner_id, first.material_id, "second-version", settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == second_run.run_id
    for stage in ("evidence", "semantics", "publishing"):
        _record_progress(second_run.run_id, stage, 1, 1, dsn=dsn)
    second_structure = _structure(str(second_run.run_id), first.sha256, settings["runtime_lock"], partial=True)
    publish_fixture_structure(learner.learner_id, first.material_id, second_run.run_id, second_structure, dsn=dsn)

    failed = seed_run(learner.learner_id, first.material_id, "new-failed-attempt", settings, dsn=dsn)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE material_processing_runs SET status='failed', "
            "error_code='NO_USABLE_EVIDENCE', completed_at=now(), updated_at=now() "
            "WHERE run_id=%s",
            (failed.run_id,),
        )

    uploaded = seed_pdf(learner.learner_id, io.BytesIO(_pdf()), "uploaded-only", display_name="陣列入門.pdf", dsn=dsn)
    running = seed_pdf(learner.learner_id, io.BytesIO(_pdf()), "running-source", display_name="遞迴課堂筆記.pdf", dsn=dsn)
    running_run = seed_run(learner.learner_id, running.material_id, "running", settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == running_run.run_id
    _record_progress(running_run.run_id, "evidence", 0, 1, dsn=dsn)

    foreign = register_account("library_b@example.com", "Synthetic test password 42", dsn=dsn)
    foreign_source = seed_pdf(foreign.learner_id, io.BytesIO(_pdf()), "foreign-source", display_name="B 的私人教材.pdf", dsn=dsn)
    return {
        "learner": learner, "first": first, "structure": structure,
        "second_structure": second_structure, "failed": failed, "uploaded": uploaded,
        "running": running, "foreign": foreign, "foreign_source": foreign_source,
        "dsn": dsn, "token": token, "settings": settings,
    }


def product_snapshot(dsn):
    """比對完整產品 rows 的摘要，避免把教材內容印進 assertion log。"""
    tables = (
        "materials", "artifacts", "material_processing_runs", "knowledge_structures",
        "study_sessions", "assessments", "answer_events",
    )
    snapshots = {}
    with psycopg.connect(dsn) as connection:
        for table in tables:
            rows = connection.execute(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY 1").fetchall()
            digest = hashlib.sha256("\n".join(row[0] for row in rows).encode()).hexdigest()
            snapshots[table] = (len(rows), digest)
    return snapshots
