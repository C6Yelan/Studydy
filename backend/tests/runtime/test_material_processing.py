from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pymupdf
import pytest

import runtime.material_processing as processing_module
from pdf_evidence.concept_evidence_output import build_output
from pdf_evidence.text_first_bundle import build_producer_bundle, publish_run
from pdf_evidence.concept_generation import build_semantic_request, validate_concepts
from pdf_evidence.ocr_page_evidence import build_page_evidence, canonical_sha256, extract_page
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
def processing_database_dsn(clean_database_dsn: str, migrations_dir: Path) -> str:
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
    return {
        "private_runtime_root": str(tmp_path / "private-runtime"),
        "runtime_lock": json.loads(
            (Path(__file__).parents[3] / "local_ai" / "runtime-lock.json").read_text(
                encoding="utf-8"
            )
        ),
        "python_executable": "/opt/studydy/ocr/bin/python3.12",
        "site_packages": "/opt/studydy/ocr/lib/python3.12/site-packages",
        "ocr_model_root": "/opt/studydy/models/unlimited-ocr",
        "concept_api_base_url": "http://127.0.0.1:8101",
        "concept_model": "Qwen/Qwen3-4B-Instruct-2507",
        "concept_server_executable": "/opt/studydy/vllm/bin/vllm",
        "concept_model_root": "/opt/studydy/models/qwen3-4b-instruct-2507",
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


def _fake_successful_producer(
    request, settings, *, run_id, produced_at, runtime_binding_sha256
):
    source_path = Path(request["source_path"])
    source_sha256 = request["expected_source_sha256"]
    pages = []
    semantic_pages = []
    with pymupdf.open(source_path) as document:
        page_count = document.page_count
        for page_number in range(1, page_count + 1):
            raw_page = extract_page(document, source_sha256, page_number)
            page = build_page_evidence(
                raw_page,
                [
                    {
                        "type": "text",
                        "text": f"Public evidence {page_number}",
                        "bbox": [100, 100, 900, 300],
                    }
                ],
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
            semantic_request = build_semantic_request(page)
            semantic = validate_concepts(
                json.dumps(
                    {
                        "concepts": [
                            {
                                "label": f"Public concept {page_number}",
                                "definition": "Public definition",
                                "key_points": ["Public point"],
                                "evidence_ids": [
                                    semantic_request["evidence"][0]["evidence_id"]
                                ],
                            }
                        ]
                    },
                    separators=(",", ":"),
                ),
                semantic_request=semantic_request,
                page_ref=page["page_ref"],
                input_binding={"semantic": "fixed"},
                attempt=1,
            )
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


def test_runtime_binding_contains_exact_code_and_no_private_paths(tmp_path: Path):
    settings = _settings(tmp_path)
    binding = formal_runtime_binding(settings)
    assert binding["raw_retention"] == "none"
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
    runtime_file = tmp_path / "verified-runtime-file"
    runtime_file.write_bytes(b"exact runtime")
    expected_sha256 = hashlib.sha256(b"exact runtime").hexdigest()
    monkeypatch.setattr(
        processing_module,
        "_runtime_files",
        lambda _: (
            processing_module._RuntimeFile(
                runtime_file, expected_sha256, len(b"exact runtime")
            ),
        ),
    )
    monkeypatch.setattr(
        processing_module,
        "_distribution_versions",
        lambda _: processing_module._PACKAGE_VERSIONS,
    )

    binding = processing_module.formal_runtime_preflight(settings)
    assert binding["schema"] == "formal-agent1-runtime-binding/v3"
    runtime_root = Path(settings["private_runtime_root"])
    assert runtime_root.stat().st_mode & 0o777 == 0o700

    runtime_file.write_bytes(b"short")
    with pytest.raises(
        MaterialProcessingError, match="MATERIAL_CONFIGURATION_INVALID"
    ):
        processing_module.formal_runtime_preflight(settings)
    runtime_file.write_bytes(b"drift runtime")
    with pytest.raises(
        MaterialProcessingError, match="MATERIAL_CONFIGURATION_INVALID"
    ):
        processing_module.formal_runtime_preflight(settings)


def test_runtime_file_plan_covers_python_ocr_and_qwen(tmp_path: Path):
    settings = _settings(tmp_path)
    python_executable = tmp_path / "python"
    python_executable.write_bytes(b"python")
    python_executable.chmod(0o700)
    for key in ("site_packages", "ocr_model_root", "concept_model_root"):
        path = tmp_path / key
        path.mkdir()
        settings[key] = str(path)
    settings["python_executable"] = str(python_executable)
    concept_server = tmp_path / "vllm"
    concept_server.write_bytes(b"vllm")
    concept_server.chmod(0o700)
    settings["concept_server_executable"] = str(concept_server)

    runtime_files = processing_module._runtime_files(settings)
    relative_names = {runtime_file.path.name for runtime_file in runtime_files}
    assert len(runtime_files) == 25
    assert {
        "python",
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


def test_distribution_versions_require_one_exact_metadata_record_per_package(
    tmp_path: Path,
):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    for name, version in processing_module._PACKAGE_VERSIONS.items():
        metadata_root = site_packages / f"{name.replace('-', '_')}-{version}.dist-info"
        metadata_root.mkdir()
        (metadata_root / "METADATA").write_text(
            f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n",
            encoding="utf-8",
        )
    assert processing_module._distribution_versions(site_packages) == (
        processing_module._PACKAGE_VERSIONS
    )

    duplicate = site_packages / "duplicate.dist-info"
    duplicate.mkdir()
    (duplicate / "METADATA").write_text(
        "Name: torch\nVersion: 2.10.0+cu128\n", encoding="utf-8"
    )
    with pytest.raises(
        MaterialProcessingError, match="MATERIAL_CONFIGURATION_INVALID"
    ):
        processing_module._distribution_versions(site_packages)


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
        connection.execute(
            "UPDATE knowledge_maps SET document=document || '{\"tampered\":true}'::jsonb"
        )
    with pytest.raises(MaterialRunOutputError, match="MATERIAL_OUTPUT_UNAVAILABLE"):
        read_material_run_outputs(
            learner_id,
            source.material_id,
            created.run_id,
            dsn=processing_database_dsn,
        )
