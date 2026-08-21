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
from pdf_evidence.ocr_page_evidence import build_page_evidence, canonical_sha256, extract_page
from knowledge_map.artifacts import validate_knowledge_map
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
    "learning_paths",
    "resource_catalogs",
    "learning_resource_results",
    "assessments",
    "answer_events",
    "learning_states",
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
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (
        1,
        2,
        3,
        4,
        5,
    )
    with psycopg.connect(clean_database_dsn) as connection:
        for table in ("material_processing_runs", *_DOMAIN_TABLES):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
    return clean_database_dsn


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
        "concept_api_base_url": "http://127.0.0.1:8101",
        "concept_model": "Qwen/Qwen3-4B-Instruct-2507",
        "concept_server_executable": str(root / "vllm/bin/vllm"),
        "concept_model_root": str(root / "models/qwen3-4b-instruct-2507"),
        "concept_kv_cache_bytes": 2_147_483_648,
        "concept_max_concurrency": 2,
        "concept_max_model_len": 5_632,
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


def _fake_producer(
    request, settings, *, has_partial_page, run_id, produced_at, runtime_binding_sha256
):
    source_path = Path(request["source_path"])
    source_sha256 = request["expected_source_sha256"]
    pages = []
    semantic_pages = []
    with pymupdf.open(source_path) as document:
        page_count = document.page_count
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
                    "page": settings["runtime_lock"]["page"],
                    "ocr": settings["runtime_lock"]["ocr"],
                },
                produced_at=produced_at,
            )
            raw_page.pop("png_bytes", None)
            raw_page.pop("native_evidence", None)
            semantic_request, evidence_aliases = build_semantic_request(page)
            semantic = validate_concepts(
                json.dumps(
                    {
                        "concepts": [
                            {
                                "label": f"Public concept {page_number}",
                                "definition": "Public definition",
                                "key_points": ["Public point"],
                                "evidence_ids": [
                                    semantic_request["evidence"][0]["id"]
                                ],
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
    output = build_output(
        run_id=run_id,
        produced_at=produced_at,
        source_binding={
            "source_sha256": source_sha256,
            "page_numbers": list(range(1, page_count + 1)),
        },
        pages=pages,
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
    request, settings, *, run_id, produced_at, runtime_binding_sha256
):
    return _fake_producer(
        request,
        settings,
        has_partial_page=False,
        run_id=run_id,
        produced_at=produced_at,
        runtime_binding_sha256=runtime_binding_sha256,
    )


def _fake_partial_producer(
    request, settings, *, run_id, produced_at, runtime_binding_sha256
):
    return _fake_producer(
        request,
        settings,
        has_partial_page=True,
        run_id=run_id,
        produced_at=produced_at,
        runtime_binding_sha256=runtime_binding_sha256,
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
    assert completed.status == "succeeded"
    assert completed.output_binding["schema"] == "material-run-output-binding/v2"
    assert not (
        Path(settings["private_runtime_root"])
        / "runs"
        / f"text-first-run:{created.run_id}"
    ).exists()
    outputs = read_material_run_outputs(
        learner_id, source.material_id, created.run_id, dsn=processing_database_dsn
    )
    assert outputs.study_material_output["schema"] == "study-material-output/v3"
    assert outputs.knowledge_map["schema"] == "knowledge-map/v2"
    assert outputs.knowledge_map_view["schema"] == "knowledge-map-view/v2"
    assert "text" not in json.dumps(outputs.knowledge_map_view)
    with psycopg.connect(processing_database_dsn) as connection:
        assert connection.execute("SELECT count(*) FROM study_material_outputs").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM knowledge_maps").fetchone() == (1,)
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


def test_long_document_publishes_every_page_and_resolves_page_above_old_limit(
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
        for evidence in concept["evidence"]
    }
    assert locator_pages == set(range(1, 41))
    assert any(
        evidence["page_number"] == 40
        for concept in outputs.knowledge_map_view["concepts"]
        for evidence in concept["evidence"]
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
    assert outputs.study_material_output["concepts"][0]["processing"] == "partial"
    assert outputs.knowledge_map["processing"] == "partial"
    view = deepcopy(outputs.knowledge_map_view)
    evidence = view["concepts"][0]["evidence"][0]
    view["images"] = [
        {
            "image_id": "image:sha256:" + "a" * 64,
            "page_ref": evidence["page_ref"],
            "page_number": evidence["page_number"],
            "region": deepcopy(evidence["region"]),
            "evidence": [
                {**deepcopy(evidence), "evidence_id": f"evidence:sha256:{index:064x}"}
                for index in range(9)
            ],
        }
    ]
    api_view = KnowledgeMapView.model_validate(view).model_dump(by_alias=True)
    assert api_view["status"]["processing"] == "partial"
    assert api_view["excluded_pages"] == []
    assert api_view["images"][0]["evidence"] == view["images"][0]["evidence"]
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
    assert binding["timeouts_seconds"] == {
        "resident_lock": 5,
        "ocr_page": 120,
        "concept_attempt": 300,
        "concept_server_ready": 300,
    }
    assert binding["retry_policy"]["concept_attempts"] == 2
    encoded = json.dumps(binding)
    assert settings["private_runtime_root"] not in encoded
    assert settings["ocr_model_root"] not in encoded
    assert settings["concept_server_executable"] not in encoded
    assert settings["concept_model_root"] not in encoded
    assert binding["concept_api"] == {
        "base_url": "http://127.0.0.1:8101",
        "model": "Qwen/Qwen3-4B-Instruct-2507",
        "model_revision": "cdbee75f17c01a7cc42f958dc650907174af0554",
        "model_binding_manifest_sha256": "61cbb8e0973dcbefc6009f66ddfc2da2fe3d9aba4094ade8a82043f6624651c4",
        "protocol": "openai-chat-completions/v1",
        "kv_cache_bytes": 2_147_483_648,
        "max_concurrency": 2,
        "max_model_len": 5_632,
        "server": settings["runtime_lock"]["semantic"]["server"],
        "structured_output": settings["runtime_lock"]["semantic"]["structured_output"],
        "input_token_budget": settings["runtime_lock"]["semantic"]["input_token_budget"],
    }
    assert len(binding["code_hashes"]) == 11
    assert "backend/src/pdf_evidence/artifact_reason_codes.py" in binding["code_hashes"]

    for changed in (
        {**settings, "concept_api_base_url": "http://example.test:8101"},
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
    assert binding["schema"] == "formal-agent1-runtime-binding/v4"
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
        "site_packages", "concept_site_packages", "ocr_model_root", "concept_model_root"
    ):
        Path(settings[key]).mkdir(parents=True)
    concept_server = Path(settings["concept_server_executable"])
    concept_server.parent.mkdir(parents=True, exist_ok=True)
    concept_server.write_bytes(b"vllm")
    concept_server.chmod(0o700)

    runtime_files = processing_module._runtime_files(settings)
    relative_names = {runtime_file.path.name for runtime_file in runtime_files}
    assert len(runtime_files) == 26
    assert {
        "python3.12",
        "vllm",
        "__init__.py",
        "protocol.py",
        "ocr_process.py",
        "model-00001-of-000001.safetensors",
        "model.safetensors.index.json",
        "special_tokens_map.json",
        "configuration_deepseek_v2.py",
        "model-00001-of-00003.safetensors",
        "tokenizer.json",
    } <= relative_names
    assert tuple(settings["runtime_lock"]["ocr"]["package_sources"]) == (
        "__init__.py",
        "protocol.py",
        "ocr_process.py",
    )


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
    ):
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
        forged = deepcopy(stored)
        forged["concepts"][0]["label"] = "Forged but self-rehashed label"
        forged["revision"] = "knowledge-map:sha256:" + canonical_sha256(
            {key: value for key, value in forged.items() if key != "revision"}
        )
        assert validate_knowledge_map(forged) is None
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
