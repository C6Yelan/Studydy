from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
import pymupdf
import pytest

import runtime.material_processing as processing_module
import runtime.storage.material_review_outputs as output_module
from pdf_evidence.concept_evidence_output import build_output
from pdf_evidence.text_first_bundle import build_producer_bundle, publish_run
from pdf_evidence.concept_generation import build_semantic_request, validate_concepts
from pdf_evidence.document_context import build_document_contexts
from pdf_evidence.ocr_page_evidence import build_page_evidence, canonical_sha256, extract_page
from knowledge_map.artifacts import build_knowledge_map, validate_knowledge_map
from knowledge_map.formal_concepts import (
    build_deduplication_request,
    canonicalize_concepts,
    uncertain_pair_decisions,
)
from learning_resources.map_resources import promote_resources_to_formal_concepts
from runtime.api.models import KnowledgeMapView
from runtime.material_processing import (
    ClaimedMaterialProcessingRun,
    MaterialProcessingError,
    claim_next_material_processing_run,
    create_material_processing_run,
    execute_claimed_material_processing_run,
    formal_runtime_binding,
    read_material_processing_run,
    recover_interrupted_material_runs,
)
from runtime.storage.artifacts import publish_idempotent_source_pdf
from runtime.storage.material_review_outputs import (
    MaterialRunOutputError,
    read_material_run_outputs,
)
from runtime.storage.migrations import run_migrations


_DOMAIN_TABLES = (
    "study_material_outputs",
    "knowledge_maps",
    "resource_catalogs",
    "learning_resource_results",
    "study_sessions",
    "assessments",
)


@pytest.fixture
def processing_database_dsn(
    clean_database_dsn: str,
    migrations_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    monkeypatch.setattr(
        processing_module,
        "formal_runtime_preflight",
        processing_module.formal_runtime_binding,
    )
    monkeypatch.setattr(output_module, "generate_knowledge_map", _fake_knowledge_map)
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
    )
    with psycopg.connect(clean_database_dsn) as connection:
        for table in ("material_processing_runs", *_DOMAIN_TABLES):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
    return clean_database_dsn


def test_populated_migration_five_deletes_v2_terminal_runs_on_forward_upgrade(
    clean_database_dsn: str,
    migrations_dir: Path,
    tmp_path: Path,
):
    migration_five_dir = tmp_path / "migration-five"
    migration_five_dir.mkdir()
    for source in sorted(migrations_dir.glob("000[1-5]_*.sql")):
        (migration_five_dir / source.name).write_bytes(source.read_bytes())

    assert run_migrations(
        clean_database_dsn, migrations_dir=migration_five_dir
    ) == (1, 2, 3, 4, 5)

    learner_id = uuid4()
    material_id = uuid4()
    source_artifact_id = uuid4()
    deleted_run_ids = [uuid4(), uuid4()]
    surviving_run_ids = [uuid4(), uuid4(), uuid4()]
    runs = (
        (
            deleted_run_ids[0],
            "succeeded",
            None,
            {"schema": "material-run-output-binding/v2"},
            True,
        ),
        (
            deleted_run_ids[1],
            "partial",
            None,
            {"schema": "material-run-output-binding/v2"},
            True,
        ),
        (surviving_run_ids[0], "pending", None, None, False),
        (surviving_run_ids[1], "running", None, None, False),
        (surviving_run_ids[2], "failed", "MATERIAL_ANALYSIS_FAILED", None, True),
    )
    with psycopg.connect(clean_database_dsn) as connection:
        connection.execute("SET CONSTRAINTS ALL DEFERRED")
        connection.execute(
            "INSERT INTO learners VALUES (%s, clock_timestamp())",
            (learner_id,),
        )
        connection.execute(
            """
            INSERT INTO materials (
                material_id, learner_id, source_artifact_id,
                upload_idempotency_key_sha256, upload_request_fingerprint, created_at
            ) VALUES (%s, %s, %s, %s, %s, clock_timestamp())
            """,
            (material_id, learner_id, source_artifact_id, b"m" * 32, b"r" * 32),
        )
        connection.execute(
            """
            INSERT INTO artifacts (
                artifact_id, learner_id, material_id, kind, media_type,
                sha256, size_bytes, created_at
            ) VALUES (
                %s, %s, %s, 'source_pdf', 'application/pdf',
                %s, 1, clock_timestamp()
            )
            """,
            (source_artifact_id, learner_id, material_id, b"s" * 32),
        )
        for index, (
            run_id,
            status,
            error_code,
            output_binding,
            is_completed,
        ) in enumerate(runs):
            connection.execute(
                """
                INSERT INTO material_processing_runs (
                    run_id, learner_id, material_id, source_artifact_id,
                    idempotency_key_sha256, request_fingerprint, runtime_binding,
                    status, error_code, output_binding, created_at, updated_at,
                    completed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, clock_timestamp(), clock_timestamp(),
                    CASE WHEN %s THEN clock_timestamp() ELSE NULL END
                )
                """,
                (
                    run_id,
                    learner_id,
                    material_id,
                    source_artifact_id,
                    bytes([index + 1]) * 32,
                    bytes([index + 11]) * 32,
                    Jsonb({"schema": "test-runtime-binding"}),
                    status,
                    error_code,
                    Jsonb(output_binding) if output_binding is not None else None,
                    is_completed,
                ),
            )

    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
    )

    with psycopg.connect(clean_database_dsn) as connection:
        remaining_run_ids = {
            row[0]
            for row in connection.execute(
                "SELECT run_id FROM material_processing_runs"
            ).fetchall()
        }
        assert not set(deleted_run_ids) & remaining_run_ids
        assert set(surviving_run_ids) == remaining_run_ids

        v3_run_id = uuid4()
        connection.execute(
            """
            INSERT INTO material_processing_runs (
                run_id, learner_id, material_id, source_artifact_id,
                idempotency_key_sha256, request_fingerprint, runtime_binding,
                status, progress_stage, completed_pages, total_pages,
                error_code, output_binding, created_at, updated_at, completed_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                'succeeded', 'completed', 3, 3, NULL, %s,
                clock_timestamp(), clock_timestamp(), clock_timestamp()
            )
            """,
            (
                v3_run_id,
                learner_id,
                material_id,
                source_artifact_id,
                b"v" * 32,
                b"f" * 32,
                Jsonb({"schema": "test-runtime-binding"}),
                Jsonb({
                    "schema": "material-run-output-binding/v3",
                    "page_count": 3,
                }),
            ),
        )
        assert connection.execute(
            "SELECT count(*) FROM material_processing_runs WHERE run_id = %s",
            (v3_run_id,),
        ).fetchone() == (1,)
        constraint = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'material_processing_runs_terminal_v3_check'
            """
        ).fetchone()
    assert constraint is not None
    assert "material-run-output-binding/v3" in constraint[0]
    assert "material-run-output-binding/v2" not in constraint[0]


@pytest.fixture(autouse=True)
def artifact_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "private-artifacts"
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(root))
    return root


def _settings(tmp_path: Path) -> dict:
    root = tmp_path / "local-runtime"
    return {
        "private_runtime_root": str(root / "runtime"),
        "runtime_lock": json.loads(
            (Path(__file__).parents[3] / "local_ai" / "runtime-lock.json").read_text(
                encoding="utf-8"
            )
        ),
        "python_executable": str(root / "ocr/runtime/bin/python3.12"),
        "site_packages": str(root / "ocr/runtime/lib/python3.12/site-packages"),
        "concept_site_packages": str(root / "vllm/lib/python3.12/site-packages"),
        "ocr_model_root": str(root / "models/unlimited-ocr"),
        "verifier_model_root": str(root / "models/mdeberta-v3-base-mnli-xnli"),
        "concept_api_base_url": "http://127.0.0.1:8101",
        "concept_model": "Qwen/Qwen3-14B-AWQ",
        "concept_server_executable": str(root / "vllm/bin/vllm"),
        "concept_model_root": str(root / "models/qwen3-14b-awq"),
        "concept_kv_cache_bytes": 2_147_483_648,
        "concept_max_concurrency": 1,
        "concept_max_model_len": 8_192,
    }


def _learner(dsn: str) -> UUID:
    learner_id = uuid4()
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO learners VALUES (%s,clock_timestamp())", (learner_id,)
        )
    return learner_id


def _pdf(page_count: int = 1) -> bytes:
    document = pymupdf.open()
    for page_number in range(1, page_count + 1):
        page = document.new_page()
        page.insert_text((72, 72), f"Public evidence {page_number}")
    content = document.tobytes()
    document.close()
    return content


def _source(dsn: str, learner_id: UUID, *, page_count: int = 1):
    return publish_idempotent_source_pdf(
        learner_id,
        io.BytesIO(_pdf(page_count)),
        f"material-{uuid4()}",
        dsn=dsn,
    )


def _fake_knowledge_map(
    study_output,
    _settings,
    material_runtime_binding_sha256,
    *,
    resource_context,
    resource_library,
):
    request, concept_aliases = build_deduplication_request(study_output)
    decisions = uncertain_pair_decisions(request)
    resolutions = [canonicalize_concepts(
        study_output,
        request,
        concept_aliases,
        decisions,
        verification_diagnostics={
            "qwen_same_pairs": 0,
            "qwen_distinct_pairs": 0,
            "qwen_uncertain_pairs": len(decisions),
            "verifier_requested_pairs": 0,
            "verifier_scored_pairs": 0,
            "verifier_allowed_pairs": 0,
            "verifier_vetoed_pairs": 0,
            "verifier_unsupported_pairs": 0,
            "verifier_failed_pairs": 0,
        },
    )]
    formal_concepts = [
        concept for resolution in resolutions for concept in resolution["formal_concepts"]
    ]
    return build_knowledge_map(
        study_output,
        resolutions,
        resource_promotion=promote_resources_to_formal_concepts(
            formal_concepts, resource_context, study_output, resource_library
        ),
        material_runtime_binding_sha256=material_runtime_binding_sha256,
    )


def _fake_producer(
    request,
    settings,
    *,
    has_partial_page,
    run_id,
    produced_at,
    runtime_binding_sha256,
    progress_callback,
):
    source_path = Path(request["source_path"])
    source_sha256 = request["expected_source_sha256"]
    pages = []
    semantic_pages = []
    with pymupdf.open(source_path) as document:
        page_count = document.page_count
        progress_callback("page_evidence", 0, page_count)
        for page_number in range(1, page_count + 1):
            raw_page = extract_page(document, source_sha256, page_number)
            ocr_blocks = [
                {
                    "type": "text",
                    "text": f"Public evidence {page_number}",
                    "bbox": [100, 100, 900, 300],
                }
            ]
            if has_partial_page:
                ocr_blocks.append(
                    {"type": "text", "text": "", "bbox": [100, 400, 900, 500]}
                )
            page = build_page_evidence(
                raw_page,
                ocr_blocks,
                input_binding={
                    "source_sha256": source_sha256,
                    "page_number": page_number,
                    "render_sha256": raw_page["render"]["sha256"],
                    "route": "OCR_needed",
                    "page": settings["runtime_lock"]["page"],
                    "ocr": settings["runtime_lock"]["ocr"],
                },
                produced_at=produced_at,
            )
            raw_page.pop("png_bytes", None)
            raw_page.pop("native_evidence", None)
            document_context = build_document_contexts([page])[0]
            semantic_request, evidence_aliases = build_semantic_request(
                page, document_context
            )
            semantic = validate_concepts(
                json.dumps(
                    {
                        "concepts": [
                            {
                                "label": f"Public concept {page_number}",
                                "claims": [{
                                    "text": f"Public evidence {page_number}",
                                    "evidence_ids": [semantic_request["evidence"][0]["id"]],
                                }],
                            }
                        ]
                    },
                    separators=(",", ":"),
                ),
                semantic_request=semantic_request,
                evidence_aliases=evidence_aliases,
                page_ref=page["page_ref"],
                input_binding={"semantic": "fixed"},
                attempt=1,
            )
            if has_partial_page:
                semantic["rejected_candidates"] = [
                    {
                        "candidate_index": 1,
                        "processing": "failed",
                        "quality": "needs_review",
                        "decision": "reject",
                        "reason_codes": ["INVALID_TEXT_FIELD"],
                    }
                ]
                semantic["processing"] = "partial"
            pages.append(page)
            semantic_pages.append(semantic)
            progress_callback("page_evidence", page_number, page_count)
    progress_callback("concept_generation", 0, page_count)
    for completed_pages in range(1, page_count + 1):
        progress_callback("concept_generation", completed_pages, page_count)
    document_contexts = build_document_contexts(pages)
    contexts_by_page = {
        context["page_ref"]: context for context in document_contexts
    }
    for page, semantic in zip(pages, semantic_pages, strict=True):
        semantic_request, _ = build_semantic_request(
            page, contexts_by_page[page["page_ref"]]
        )
        semantic["input_binding"] = {
            "batch_bindings": [{
                "batch_index": 0,
                "semantic_request_sha256": canonical_sha256(semantic_request),
                "semantic_request": deepcopy(semantic_request),
            }]
        }
    output = build_output(
        run_id=run_id,
        produced_at=produced_at,
        source_binding={
            "source_sha256": source_sha256,
            "page_numbers": list(range(1, page_count + 1)),
        },
        pages=pages,
        context_pages=pages,
        document_contexts=document_contexts,
        semantic_pages=semantic_pages,
        runtime_binding=settings["runtime_lock"],
        run_reasons=[],
    )
    bundle = build_producer_bundle(
        run_id=run_id,
        produced_at=produced_at,
        output=output,
        runtime_binding_sha256=runtime_binding_sha256,
        reasons=output["reason_codes"],
        duration_ms=1,
        ocr_calls=page_count,
        concept_calls=page_count,
        ocr_loads=1,
        concept_loads=1,
        page_count=page_count,
    )
    publish_run(Path(settings["private_runtime_root"]), bundle, output)
    return bundle


def _fake_successful_producer(
    request, settings, *, run_id, produced_at, runtime_binding_sha256,
    progress_callback,
):
    return _fake_producer(
        request,
        settings,
        has_partial_page=False,
        run_id=run_id,
        produced_at=produced_at,
        runtime_binding_sha256=runtime_binding_sha256,
        progress_callback=progress_callback,
    )


def _fake_partial_producer(
    request, settings, *, run_id, produced_at, runtime_binding_sha256,
    progress_callback,
):
    return _fake_producer(
        request,
        settings,
        has_partial_page=True,
        run_id=run_id,
        produced_at=produced_at,
        runtime_binding_sha256=runtime_binding_sha256,
        progress_callback=progress_callback,
    )


def _created_run(dsn: str, tmp_path: Path, key: str = "run-key"):
    learner_id = _learner(dsn)
    source = _source(dsn, learner_id)
    settings = _settings(tmp_path)
    created = create_material_processing_run(
        learner_id,
        source.material_id,
        source.artifact_id,
        key,
        settings,
        dsn=dsn,
    )
    return learner_id, source, settings, created


def _assert_downstream_zero(dsn: str) -> None:
    with psycopg.connect(dsn) as connection:
        for table in _DOMAIN_TABLES[2:]:
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


def test_create_replay_claim_execute_and_publish_only_output_and_map(
    processing_database_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    learner_id, source, settings, created = _created_run(
        processing_database_dsn, tmp_path
    )
    assert created.status == "pending"
    replay = create_material_processing_run(
        learner_id,
        source.material_id,
        source.artifact_id,
        "run-key",
        settings,
        dsn=processing_database_dsn,
    )
    assert replay.run_id == created.run_id
    other_source = _source(processing_database_dsn, learner_id)
    with pytest.raises(
        MaterialProcessingError, match="MATERIAL_RUN_IDEMPOTENCY_CONFLICT"
    ):
        create_material_processing_run(
            learner_id,
            other_source.material_id,
            other_source.artifact_id,
            "run-key",
            settings,
            dsn=processing_database_dsn,
        )

    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert isinstance(claim, ClaimedMaterialProcessingRun)
    monkeypatch.setattr(
        processing_module, "run_full_text_first_pdf", _fake_successful_producer
    )
    completed = execute_claimed_material_processing_run(
        claim, settings, dsn=processing_database_dsn
    )
    assert completed.status == "succeeded", completed.error_code
    assert (
        completed.progress_stage,
        completed.completed_pages,
        completed.total_pages,
    ) == ("completed", 1, 1)
    assert completed.output_binding["schema"] == "material-run-output-binding/v3"
    assert not (
        Path(settings["private_runtime_root"])
        / "runs"
        / f"text-first-run:{created.run_id}"
    ).exists()
    outputs = read_material_run_outputs(
        learner_id, source.material_id, created.run_id, dsn=processing_database_dsn
    )
    assert outputs.study_material_output["schema"] == "study-material-output/v8"
    assert outputs.study_material_output["evidence_text_index"]
    assert all(
        set(evidence) == {"evidence_id", "text"}
        for evidence in outputs.study_material_output["evidence_text_index"]
    )
    assert outputs.knowledge_map["schema"] == "knowledge-map/v11"
    assert outputs.knowledge_map_view["schema"] == "knowledge-map-view/v11"
    with psycopg.connect(processing_database_dsn) as connection:
        assert connection.execute("SELECT count(*) FROM study_material_outputs").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM knowledge_maps").fetchone() == (1,)
    _assert_downstream_zero(processing_database_dsn)


def test_progress_updates_are_monotonic_and_reject_illegal_transitions(
    processing_database_dsn: str,
    tmp_path: Path,
):
    learner_id, _, _, created = _created_run(
        processing_database_dsn, tmp_path, key="progress-transitions"
    )
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None
    record = processing_module._record_material_progress

    with pytest.raises(MaterialProcessingError, match="MATERIAL_RUN_INVALID"):
        record(created.run_id, "concept_generation", 0, 4, dsn=processing_database_dsn)
    record(created.run_id, "page_evidence", 0, 4, dsn=processing_database_dsn)
    record(created.run_id, "page_evidence", 2, 4, dsn=processing_database_dsn)
    with pytest.raises(MaterialProcessingError, match="MATERIAL_RUN_INVALID"):
        record(created.run_id, "concept_generation", 0, 4, dsn=processing_database_dsn)
    with pytest.raises(MaterialProcessingError, match="MATERIAL_RUN_INVALID"):
        record(created.run_id, "page_evidence", 1, 4, dsn=processing_database_dsn)
    with pytest.raises(MaterialProcessingError, match="MATERIAL_RUN_INVALID"):
        record(created.run_id, "page_evidence", 3, 5, dsn=processing_database_dsn)
    with pytest.raises(MaterialProcessingError, match="MATERIAL_RUN_INVALID"):
        record(created.run_id, "knowledge_map_generation", 4, 4, dsn=processing_database_dsn)
    record(created.run_id, "page_evidence", 4, 4, dsn=processing_database_dsn)
    record(created.run_id, "concept_generation", 0, 4, dsn=processing_database_dsn)
    record(created.run_id, "concept_generation", 2, 4, dsn=processing_database_dsn)
    with pytest.raises(MaterialProcessingError, match="MATERIAL_RUN_INVALID"):
        record(
            created.run_id,
            "knowledge_map_generation",
            4,
            4,
            dsn=processing_database_dsn,
        )
    record(created.run_id, "concept_generation", 4, 4, dsn=processing_database_dsn)
    record(created.run_id, "knowledge_map_generation", 4, 4, dsn=processing_database_dsn)
    record(created.run_id, "publishing", 4, 4, dsn=processing_database_dsn)

    running = read_material_processing_run(
        learner_id, created.run_id, dsn=processing_database_dsn
    )
    assert (
        running.progress_stage,
        running.completed_pages,
        running.total_pages,
    ) == ("publishing", 4, 4)
    processing_module._record_run_failure(
        created.run_id, "MATERIAL_ANALYSIS_FAILED", dsn=processing_database_dsn
    )
    with pytest.raises(MaterialProcessingError, match="MATERIAL_RUN_INVALID"):
        record(created.run_id, "publishing", 4, 4, dsn=processing_database_dsn)


def test_progress_storage_failure_marks_run_failed_without_outputs(
    processing_database_dsn: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, settings, created = _created_run(
        processing_database_dsn, tmp_path, key="progress-storage-failure"
    )
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None

    def fail_progress(*_args, **_kwargs):
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED")

    monkeypatch.setattr(processing_module, "_record_material_progress", fail_progress)
    monkeypatch.setattr(
        processing_module, "run_full_text_first_pdf", _fake_successful_producer
    )
    failed = execute_claimed_material_processing_run(
        claim, settings, dsn=processing_database_dsn
    )

    assert failed.status == "failed"
    assert failed.output_binding is None
    _assert_downstream_zero(processing_database_dsn)


def test_handoff_cleanup_failure_rolls_back_outputs_and_marks_run_failed(
    processing_database_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    learner_id, source, settings, created = _created_run(
        processing_database_dsn, tmp_path, key="cleanup-failure"
    )
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None
    monkeypatch.setattr(
        processing_module, "run_full_text_first_pdf", _fake_successful_producer
    )

    def fail_cleanup(*_):
        raise OSError("PRODUCER_BUNDLE_CLEANUP_FAILED")

    monkeypatch.setattr(output_module, "remove_producer_bundle", fail_cleanup)
    completed = execute_claimed_material_processing_run(
        claim, settings, dsn=processing_database_dsn
    )

    assert completed.status == "failed"
    assert completed.error_code == "PRODUCER_BUNDLE_CLEANUP_FAILED"
    assert completed.output_binding is None
    with pytest.raises(MaterialRunOutputError, match="MATERIAL_OUTPUT_UNAVAILABLE"):
        read_material_run_outputs(
            learner_id, source.material_id, created.run_id, dsn=processing_database_dsn
        )
    with psycopg.connect(processing_database_dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM study_material_outputs"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM knowledge_maps"
        ).fetchone() == (0,)
    assert (
        Path(settings["private_runtime_root"])
        / "runs"
        / f"text-first-run:{created.run_id}"
    ).is_dir()


def test_forty_page_document_publishes_every_page_and_locator(
    processing_database_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    learner_id = _learner(processing_database_dsn)
    source = _source(processing_database_dsn, learner_id, page_count=40)
    settings = _settings(tmp_path)
    created = create_material_processing_run(
        learner_id,
        source.material_id,
        source.artifact_id,
        "long-document",
        settings,
        dsn=processing_database_dsn,
    )
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None and claim.run.run_id == created.run_id
    monkeypatch.setattr(
        processing_module, "run_full_text_first_pdf", _fake_successful_producer
    )

    completed = execute_claimed_material_processing_run(
        claim, settings, dsn=processing_database_dsn
    )
    assert completed.status == "succeeded"
    assert completed.output_binding["page_count"] == 40
    outputs = read_material_run_outputs(
        learner_id, source.material_id, created.run_id, dsn=processing_database_dsn
    )
    assert [page["page_number"] for page in outputs.study_material_output["pages"]] == list(
        range(1, 41)
    )
    locator_pages = {
        evidence["page_number"]
        for concept in outputs.knowledge_map_view["concepts"]
        for claim in concept["claims"]
        for evidence in claim["evidence"]
    }
    assert locator_pages == set(range(1, 41))
    assert any(
        evidence["page_number"] == 40
        for concept in outputs.knowledge_map_view["concepts"]
        for claim in concept["claims"]
        for evidence in claim["evidence"]
    )
    _assert_downstream_zero(processing_database_dsn)


def test_partial_page_and_semantic_status_reaches_persisted_run(
    processing_database_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    learner_id, source, settings, created = _created_run(
        processing_database_dsn, tmp_path, key="partial-run"
    )
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None
    monkeypatch.setattr(
        processing_module, "run_full_text_first_pdf", _fake_partial_producer
    )

    completed = execute_claimed_material_processing_run(
        claim, settings, dsn=processing_database_dsn
    )
    outputs = read_material_run_outputs(
        learner_id, source.material_id, created.run_id, dsn=processing_database_dsn
    )

    assert completed.status == "partial"
    assert completed.output_binding["processing"] == "partial"
    assert outputs.study_material_output["processing"] == "partial"
    assert outputs.study_material_output["pages"][0]["processing"] == "partial"
    assert outputs.study_material_output["concepts"][0]["processing"] == "succeeded"
    assert outputs.knowledge_map["processing"] == "partial"
    view = deepcopy(outputs.knowledge_map_view)
    evidence = view["concepts"][0]["claims"][0]["evidence"][0]
    api_view = KnowledgeMapView.model_validate(view).model_dump(by_alias=True)
    assert api_view["status"]["processing"] == "partial"
    assert api_view["excluded_pages"] == []
    assert api_view["concepts"][0]["claims"][0]["evidence"][0] == evidence
    invalid_tree = deepcopy(api_view)
    invalid_tree["document_tree"]["root"]["section_ids"] = []
    with pytest.raises(ValueError, match="KNOWLEDGE_MAP_VIEW_INVALID"):
        KnowledgeMapView.model_validate(invalid_tree)
    invalid_path = deepcopy(api_view)
    invalid_path["initial_learning_path"][0]["order_basis"][
            "page_number"
    ] += 1
    with pytest.raises(ValueError, match="KNOWLEDGE_MAP_VIEW_INVALID"):
        KnowledgeMapView.model_validate(invalid_path)
    view["status"]["processing"] = "succeeded"
    view["excluded_pages"] = [
        {
            "page_ref": "page:sha256:" + "b" * 64,
            "page_number": 2,
            "page_evidence_id": None,
            "last_stage": "page_evidence",
            "processing": "failed",
            "quality": "needs_review",
            "decision": "reject",
            "reason_codes": ["NO_USABLE_EVIDENCE"],
        }
    ]
    with pytest.raises(ValueError, match="KNOWLEDGE_MAP_VIEW_INVALID"):
        KnowledgeMapView.model_validate(view)


def test_runtime_binding_contains_exact_code_and_no_private_paths(tmp_path: Path):
    settings = _settings(tmp_path)
    binding = formal_runtime_binding(settings)
    assert binding["retention_policy"] == {
        "provider_raw": "not_persisted",
        "validated_cache": "local_private_cache",
        "run_handoff": "deleted_before_terminal_publish",
    }
    assert binding["page_range"] == {
        "minimum": 1,
        "caller_subset": False,
    }
    assert "concept_calls_per_page" not in binding["call_ceilings"]
    assert binding["call_ceilings"] == {
        "ocr_calls_per_page": 1,
        "ocr_initial_loads": 1,
            "concept_initial_loads": 2,
        "concept_equivalence_initial_loads": 1,
        "concept_equivalence_pairs_per_material": 16,
        "concept_equivalence_directions_per_material": 32,
    }
    assert binding["timeouts_seconds"] == {
        "resident_lock": 5,
        "ocr_page": 120,
        "concept_attempt": 300,
        "concept_server_ready": 300,
        "concept_equivalence": 120,
    }
    assert binding["retry_policy"]["concept_attempts"] == 2
    encoded = json.dumps(binding)
    assert settings["private_runtime_root"] not in encoded
    assert settings["ocr_model_root"] not in encoded
    assert settings["verifier_model_root"] not in encoded
    assert settings["concept_server_executable"] not in encoded
    assert settings["concept_model_root"] not in encoded
    assert binding["concept_api"] == {
        "base_url": "http://127.0.0.1:8101",
        "model": "Qwen/Qwen3-14B-AWQ",
        "model_revision": "content-sha256:5a690dbf98db87941c991fdc50afcf637e01c35c6ae11b04da1f6ac5d9d17619",
        "model_binding_manifest_sha256": "5a690dbf98db87941c991fdc50afcf637e01c35c6ae11b04da1f6ac5d9d17619",
        "protocol": "openai-chat-completions/v1",
        "kv_cache_bytes": 2_147_483_648,
        "max_concurrency": 1,
        "max_model_len": 8_192,
        "server": settings["runtime_lock"]["semantic"]["server"],
        "structured_output": settings["runtime_lock"]["semantic"]["structured_output"],
        "input_token_budget": settings["runtime_lock"]["semantic"]["input_token_budget"],
    }
    assert binding["verifier_model"] == settings["runtime_lock"][
        "verifier_model"
    ]
    assert binding["concept_equivalence"] == settings["runtime_lock"][
        "concept_equivalence"
    ]
    assert len(binding["code_hashes"]) == 17
    assert "backend/src/pdf_evidence/artifact_reason_codes.py" in binding["code_hashes"]
    assert "backend/src/pdf_evidence/process_guard.py" in binding["code_hashes"]
    repository_root = Path(__file__).parents[3]
    for locked_sha256, relative_path in (
        (
            settings["runtime_lock"]["page"]["code_hashes"][
                "backend_ocr_page_evidence"
            ],
            "backend/src/pdf_evidence/ocr_page_evidence.py",
        ),
        (
            settings["runtime_lock"]["semantic"]["code_hashes"][
                "backend_concept_api"
            ],
            "backend/src/pdf_evidence/concept_api.py",
        ),
        (
            settings["runtime_lock"]["semantic"]["code_hashes"][
                "backend_process_guard"
            ],
            "backend/src/pdf_evidence/process_guard.py",
        ),
        (
            settings["runtime_lock"]["semantic"]["code_hashes"][
                "backend_document_context"
            ],
            "backend/src/pdf_evidence/document_context.py",
        ),
        (
            settings["runtime_lock"]["semantic"]["code_hashes"][
                "backend_study_material_output"
            ],
            "backend/src/pdf_evidence/study_material_output.py",
        ),
    ):
        source_sha256 = hashlib.sha256(
            (repository_root / relative_path).read_bytes()
        ).hexdigest()
        assert locked_sha256 == source_sha256


def test_assessment_policy_does_not_change_material_runtime_identity(tmp_path: Path):
    settings = _settings(tmp_path)
    baseline = formal_runtime_binding(settings)
    assessment_policy = json.loads(
        (
            Path(__file__).parents[3]
            / "local_ai"
            / "assessment-runtime-lock.json"
        ).read_text(encoding="utf-8")
    )
    assessment_policy["proposal"]["prompt"] = "changed Assessment-only policy"

    assert formal_runtime_binding(settings) == baseline
    assert "assessment_runtime_lock" not in settings
    assert not any(
        "assessment" in relative_path
        for relative_path in baseline["code_hashes"]
    )

    for changed in (
        {**settings, "concept_api_base_url": "http://example.test:8101"},
        {**settings, "verifier_model_root": str(tmp_path / "different-model")},
        {**settings, "concept_model": "different-model"},
        {**settings, "concept_kv_cache_bytes": 0},
        {**settings, "concept_max_concurrency": 3},
        {**settings, "concept_max_model_len": 0},
    ):
        with pytest.raises(
            MaterialProcessingError, match="MATERIAL_CONFIGURATION_INVALID"
        ):
            formal_runtime_binding(changed)


def test_formal_runtime_preflight_hashes_actual_files_and_detects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = _settings(tmp_path)
    runtime_target = tmp_path / "verified-runtime-target"
    runtime_target.write_bytes(b"exact runtime")
    runtime_file = tmp_path / "verified-runtime-file"
    runtime_file.symlink_to(runtime_target)
    expected_sha256 = hashlib.sha256(b"exact runtime").hexdigest()
    monkeypatch.setattr(
        processing_module,
        "_runtime_files",
        lambda _: (
            processing_module._RuntimeFile(
                runtime_file,
                expected_sha256,
                "ocr_package",
                len(b"exact runtime"),
            ),
        ),
    )
    monkeypatch.setattr(
        processing_module,
        "_distribution_versions",
        lambda _path, expected, **_: expected,
    )

    binding = processing_module.formal_runtime_preflight(settings)
    assert binding["schema"] == "formal-material-runtime-binding/v7"
    runtime_root = Path(settings["private_runtime_root"])
    assert runtime_root.stat().st_mode & 0o777 == 0o700

    runtime_target.write_bytes(b"short")
    with pytest.raises(MaterialProcessingError) as size_failure:
        processing_module.formal_runtime_preflight(settings)
    assert size_failure.value.component == "ocr_package"
    assert size_failure.value.reason == "LOCAL_RUNTIME_SIZE_MISMATCH"
    runtime_target.write_bytes(b"drift runtime")
    with pytest.raises(MaterialProcessingError) as hash_failure:
        processing_module.formal_runtime_preflight(settings)
    assert hash_failure.value.component == "ocr_package"
    assert hash_failure.value.reason == "LOCAL_RUNTIME_HASH_MISMATCH"


def test_preflight_prepares_private_root_only_after_shared_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = _settings(tmp_path)
    observed = []

    def failed_validation(_):
        observed.append("validate")
        raise MaterialProcessingError(
            "MATERIAL_CONFIGURATION_INVALID",
            component="ocr_package",
            reason="LOCAL_RUNTIME_HASH_MISMATCH",
        )

    monkeypatch.setattr(
        processing_module, "validate_installed_local_runtime", failed_validation
    )
    monkeypatch.setattr(
        processing_module,
        "_prepare_private_runtime_root",
        lambda _: observed.append("prepare"),
    )

    with pytest.raises(MaterialProcessingError) as failure:
        processing_module.formal_runtime_preflight(settings)

    assert observed == ["validate"]
    assert failure.value.component == "ocr_package"
    assert failure.value.reason == "LOCAL_RUNTIME_HASH_MISMATCH"


def test_runtime_file_plan_covers_python_ocr_and_qwen(tmp_path: Path):
    settings = _settings(tmp_path)
    python_executable = Path(settings["python_executable"])
    python_executable.parent.mkdir(parents=True)
    python_executable.write_bytes(b"python")
    python_executable.chmod(0o700)
    for key in (
        "site_packages", "concept_site_packages", "ocr_model_root",
        "verifier_model_root", "concept_model_root"
    ):
        Path(settings[key]).mkdir(parents=True)
    concept_server = Path(settings["concept_server_executable"])
    concept_server.parent.mkdir(parents=True, exist_ok=True)
    concept_server.write_bytes(b"vllm")
    concept_server.chmod(0o700)

    runtime_files = processing_module._runtime_files(settings)
    relative_names = {runtime_file.path.name for runtime_file in runtime_files}
    assert len(runtime_files) == 28
    assert {
        "python3.12",
        "vllm",
        "__init__.py",
        "protocol.py",
        "ocr_process.py",
        "equivalence_process.py",
        "model-00001-of-000001.safetensors",
        "model.safetensors.index.json",
        "special_tokens_map.json",
        "configuration_deepseek_v2.py",
            "model-00001-of-00002.safetensors",
        "tokenizer.json",
    } <= relative_names
    assert tuple(settings["runtime_lock"]["ocr"]["package_sources"]) == (
        "__init__.py",
        "protocol.py",
        "ocr_process.py",
    )
    assert settings["runtime_lock"]["concept_equivalence"]["package_source"] == {
        "name": "equivalence_process.py",
        "sha256": "2d56949bf2499514e64edc38fa257d9b54221c12e387ae677a5443cd510512a1",
    }


def test_distribution_versions_require_one_exact_metadata_record_per_package(
    tmp_path: Path,
):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    for name, version in processing_module._OCR_PACKAGE_VERSIONS.items():
        metadata_root = site_packages / f"{name.replace('-', '_')}-{version}.dist-info"
        metadata_root.mkdir()
        (metadata_root / "METADATA").write_text(
            f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n",
            encoding="utf-8",
        )
    assert processing_module._distribution_versions(
        site_packages,
        processing_module._OCR_PACKAGE_VERSIONS,
        component="ocr_package",
    ) == (
        processing_module._OCR_PACKAGE_VERSIONS
    )

    duplicate = site_packages / "duplicate.dist-info"
    duplicate.mkdir()
    (duplicate / "METADATA").write_text(
        "Name: torch\nVersion: 2.10.0+cu128\n", encoding="utf-8"
    )
    with pytest.raises(
        MaterialProcessingError, match="MATERIAL_CONFIGURATION_INVALID"
    ):
        processing_module._distribution_versions(
            site_packages,
            processing_module._OCR_PACKAGE_VERSIONS,
            component="ocr_package",
        )


def test_changed_runtime_binding_fails_before_producer_and_writes_no_revisions(
    processing_database_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    learner_id, source, settings, created = _created_run(
        processing_database_dsn, tmp_path
    )
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None
    changed = deepcopy(settings)
    changed["runtime_lock"]["semantic"]["generation"]["max_tokens"] = 1
    monkeypatch.setattr(
        processing_module,
        "run_full_text_first_pdf",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("producer must not start")
        ),
    )
    failed = execute_claimed_material_processing_run(
        claim, changed, dsn=processing_database_dsn
    )
    assert failed.status == "failed"
    assert failed.error_code == "MATERIAL_CONFIGURATION_INVALID"
    with psycopg.connect(processing_database_dsn) as connection:
        assert connection.execute("SELECT count(*) FROM study_material_outputs").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM knowledge_maps").fetchone() == (0,)


def test_runtime_stage_failure_stops_before_producer_and_keeps_persisted_code(
    processing_database_dsn: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, settings, _ = _created_run(processing_database_dsn, tmp_path)
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None

    def failed_preflight(_):
        raise MaterialProcessingError(
            "MATERIAL_CONFIGURATION_INVALID",
            component="python_runtime",
            reason="LOCAL_RUNTIME_NOT_EXECUTABLE",
        )

    monkeypatch.setattr(processing_module, "formal_runtime_preflight", failed_preflight)
    monkeypatch.setattr(
        processing_module,
        "run_full_text_first_pdf",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("producer must not start")
        ),
    )

    failed = execute_claimed_material_processing_run(
        claim, settings, dsn=processing_database_dsn
    )

    assert failed.status == "failed"
    assert failed.error_code == "MATERIAL_CONFIGURATION_INVALID"


def test_failed_producer_publishes_zero_domain_revisions(
    processing_database_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    learner_id, source, settings, created = _created_run(
        processing_database_dsn, tmp_path
    )
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None

    def failed_producer(
        request,
        local_settings,
        *,
        run_id,
        produced_at,
        runtime_binding_sha256,
        progress_callback,
    ):
        progress_callback("page_evidence", 0, 40)
        bundle = build_producer_bundle(
            run_id=run_id,
            produced_at=produced_at,
            output=None,
            runtime_binding_sha256=runtime_binding_sha256,
            reasons=["INTERNAL_FAILURE"],
            duration_ms=1,
            ocr_calls=0,
            concept_calls=0,
            page_count=40,
        )
        publish_run(Path(local_settings["private_runtime_root"]), bundle, None)
        return bundle

    monkeypatch.setattr(processing_module, "run_full_text_first_pdf", failed_producer)
    failed = execute_claimed_material_processing_run(
        claim, settings, dsn=processing_database_dsn
    )
    assert failed.status == "failed"
    assert failed.error_code == "INTERNAL_FAILURE"
    assert failed.output_binding is None
    with psycopg.connect(processing_database_dsn) as connection:
        assert connection.execute("SELECT count(*) FROM study_material_outputs").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM knowledge_maps").fetchone() == (0,)
    _assert_downstream_zero(processing_database_dsn)


def test_knowledge_generation_failure_keeps_only_safe_local_diagnostic(
    processing_database_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _, _, settings, created = _created_run(processing_database_dsn, tmp_path)
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None
    monkeypatch.setattr(
        processing_module, "run_full_text_first_pdf", _fake_successful_producer
    )
    monkeypatch.setattr(
        output_module,
        "generate_knowledge_map",
        lambda *_: (_ for _ in ()).throw(ValueError("raw model output")),
    )

    failed = execute_claimed_material_processing_run(
        claim, settings, dsn=processing_database_dsn
    )

    assert failed.status == "failed"
    assert failed.error_code == "KNOWLEDGE_GENERATION_FAILED"
    diagnostic = json.loads(
        (
            Path(settings["private_runtime_root"])
            / "stage-failures"
            / f"{created.run_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert diagnostic["stage"] == "formal_knowledge"
    assert diagnostic["reason_code"] == "KNOWLEDGE_GENERATION_FAILED"
    assert "raw model output" not in json.dumps(diagnostic)


def test_startup_recovery_precedes_claim(
    processing_database_dsn: str, tmp_path: Path
):
    learner_id, _, settings, created = _created_run(
        processing_database_dsn, tmp_path, "recovery-key"
    )
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None and claim.run.run_id == created.run_id
    assert recover_interrupted_material_runs(dsn=processing_database_dsn) == 1
    recovered = read_material_processing_run(
        learner_id, created.run_id, dsn=processing_database_dsn
    )
    assert recovered.status == "failed"
    assert recovered.error_code == "RESTART_INTERRUPTED"


def test_owner_scope_and_tampered_map_read_fail_closed(
    processing_database_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    learner_id, source, settings, created = _created_run(
        processing_database_dsn, tmp_path
    )
    other_learner = _learner(processing_database_dsn)
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    monkeypatch.setattr(
        processing_module, "run_full_text_first_pdf", _fake_successful_producer
    )
    assert claim is not None
    completed = execute_claimed_material_processing_run(
        claim, settings, dsn=processing_database_dsn
    )
    assert completed.status == "succeeded"
    with pytest.raises(MaterialRunOutputError, match="MATERIAL_OUTPUT_UNAVAILABLE"):
        read_material_run_outputs(
            other_learner,
            source.material_id,
            created.run_id,
            dsn=processing_database_dsn,
        )
    with psycopg.connect(processing_database_dsn) as connection:
        stored = connection.execute("SELECT document FROM knowledge_maps").fetchone()[0]
        stored_source = connection.execute(
            "SELECT document FROM study_material_outputs"
        ).fetchone()[0]
        forged = deepcopy(stored)
        forged["document_tree"]["sections"][0]["source_order"][
            "reading_order"
        ] += 17
        forged["revision"] = "knowledge-map:sha256:" + canonical_sha256(
            {key: value for key, value in forged.items() if key != "revision"}
        )
        assert validate_knowledge_map(
            forged, stored_source
        ) == "KNOWLEDGE_MAP_INVALID"
        connection.execute(
            "UPDATE knowledge_maps SET map_revision=%s, document=%s",
            (forged["revision"], Jsonb(forged)),
        )
    with pytest.raises(MaterialRunOutputError, match="MATERIAL_OUTPUT_UNAVAILABLE"):
        read_material_run_outputs(
            learner_id,
            source.material_id,
            created.run_id,
            dsn=processing_database_dsn,
        )
