from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
import sys
from uuid import UUID, uuid4

import psycopg
import pymupdf
import pytest

import runtime.material_processing as processing_module
import runtime.storage.material_outputs as outputs_module
from runtime.storage.artifacts import publish_idempotent_source_pdf
from runtime.material_processing import (
    ClaimedMaterialProcessingRun,
    ControlledResourceUpload,
    MaterialProcessingError,
    claim_next_material_processing_run,
    create_material_processing_run,
    execute_claimed_material_processing_run,
    read_material_processing_run,
    recover_interrupted_material_runs,
)
from runtime.storage.material_outputs import (
    MaterialRunOutputError,
    publish_terminal_outputs,
    read_material_run_outputs,
)
from runtime.storage.migrations import run_migrations
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_pipeline_run import _Loopback, _config, _make_pdf, _simple_page_body


@pytest.fixture
def processing_database_dsn(clean_database_dsn: str, migrations_dir: Path) -> str:
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (1, 2, 3, 4)
    return clean_database_dsn


@pytest.fixture
def artifact_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "private-artifacts"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(root))
    return root


def _learner(dsn: str) -> UUID:
    learner = uuid4()
    with psycopg.connect(dsn) as connection:
        connection.execute("INSERT INTO learners VALUES (%s,clock_timestamp())", (learner,))
    return learner


def _resource_pdf(text: str = "Native topic reference") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def _upload(content: bytes | None = None, *, locator: str = "https://example.edu/native-topic.pdf") -> ControlledResourceUpload:
    return ControlledResourceUpload(
        title="Native topic reference",
        topics=["Native topic"],
        keywords=["Native topic"],
        source_locator=locator,
        license_status="cc_by",
        use_boundary="attribution_required",
        checked_at="2026-08-12T00:00:00+08:00",
        learning_use="supplemental",
        source=io.BytesIO(content or _resource_pdf()),
    )


def _source(dsn: str, learner: UUID, tmp_path: Path, *, page_count: int = 1):
    path = tmp_path / f"{uuid4().hex}.pdf"
    _make_pdf(path, page_count=page_count)
    return publish_idempotent_source_pdf(
        learner, io.BytesIO(path.read_bytes()), f"material-test-{uuid4()}", dsn=dsn
    )


def _create_claimed_run(
    dsn: str,
    tmp_path: Path,
    config: dict,
    key: str,
):
    learner = _learner(dsn)
    source = _source(dsn, learner, tmp_path)
    created = create_material_processing_run(
        learner,
        source.material_id,
        source.artifact_id,
        "mathematics",
        key,
        [_upload()],
        config,
        page_limit=10,
        dsn=dsn,
    )
    claim = claim_next_material_processing_run(dsn=dsn)
    assert claim is not None and claim.run.run_id == created.run_id
    return learner, source, created, claim


def _rebind_output_id(output: dict) -> None:
    content = {key: value for key, value in output.items() if key != "output_id"}
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    output["output_id"] = (
        "study-material-output:sha256:" + hashlib.sha256(encoded).hexdigest()
    )


def _assert_no_terminal_outputs(dsn: str, run_id: UUID) -> None:
    terminal_tables = (
        "study_material_outputs",
        "knowledge_maps",
        "learning_paths",
        "learning_resource_results",
        "assessments",
        "learning_states",
    )
    with psycopg.connect(dsn) as connection:
        for table in terminal_tables:
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert connection.execute(
            "SELECT output_binding FROM material_processing_runs WHERE run_id=%s",
            (run_id,),
        ).fetchone() == (None,)


def test_create_replay_claim_execute_and_completed_restart_read(
    processing_database_dsn: str,
    artifact_root: Path,
    tmp_path: Path,
) -> None:
    learner = _learner(processing_database_dsn)
    source = _source(processing_database_dsn, learner, tmp_path)
    resource = _resource_pdf()
    with _Loopback({1: _simple_page_body(1)}) as provider:
        config = _config(tmp_path / "cache", provider.endpoint)
        created = create_material_processing_run(
            learner, source.material_id, source.artifact_id, "mathematics", "create-key",
            [_upload(resource)], config, page_limit=10, dsn=processing_database_dsn,
        )
        assert created.status == "pending" and created.catalog_revision is not None
        replay = create_material_processing_run(
            learner, source.material_id, source.artifact_id, "mathematics", "create-key",
            [_upload(resource)], config, page_limit=10, dsn=processing_database_dsn,
        )
        assert replay.run_id == created.run_id
        with pytest.raises(MaterialProcessingError, match="MATERIAL_RUN_IDEMPOTENCY_CONFLICT"):
            create_material_processing_run(
                learner, source.material_id, source.artifact_id, "mathematics", "create-key",
                [_upload(resource)], config, page_limit=9, dsn=processing_database_dsn,
        )
        claim = claim_next_material_processing_run(dsn=processing_database_dsn)
        assert isinstance(claim, ClaimedMaterialProcessingRun)
        completed = execute_claimed_material_processing_run(claim, config, dsn=processing_database_dsn)
    assert completed.status in {"succeeded", "partial"}
    outputs = read_material_run_outputs(
        learner, source.material_id, created.run_id, dsn=processing_database_dsn
    )
    assert outputs.assessment_revision.startswith("assessment:sha256:")
    assert len(outputs.assessment_view["questions"]) == len(outputs.knowledge_map["concepts"])
    assert "answer_key" not in repr(outputs)
    assert read_material_processing_run(learner, created.run_id, dsn=processing_database_dsn) == completed


def test_rejected_page_publishes_partial_outputs_without_fake_success(
    processing_database_dsn: str,
    artifact_root: Path,
    tmp_path: Path,
) -> None:
    """單頁 visual reject 不阻斷其餘頁，但 run/Map 必須保留 partial。"""
    learner = _learner(processing_database_dsn)
    source = _source(
        processing_database_dsn, learner, tmp_path, page_count=2
    )
    with _Loopback(
        {1: _simple_page_body(1), 2: _simple_page_body(2)},
        visual_decision={1: "retain", 2: "reject"},
    ) as provider:
        config = _config(tmp_path / "partial-cache", provider.endpoint)
        created = create_material_processing_run(
            learner,
            source.material_id,
            source.artifact_id,
            "mathematics",
            "partial-run",
            [_upload()],
            config,
            page_limit=10,
            dsn=processing_database_dsn,
        )
        claim = claim_next_material_processing_run(dsn=processing_database_dsn)
        assert claim is not None and claim.run.run_id == created.run_id
        completed = execute_claimed_material_processing_run(
            claim, config, dsn=processing_database_dsn
        )

    assert completed.status == "partial"
    outputs = read_material_run_outputs(
        learner, source.material_id, created.run_id, dsn=processing_database_dsn
    )
    exclusion = next(
        item
        for item in outputs.study_material_output["known_limitations"]
        if item["reason_code"] == "PAGE_CONTENT_EXCLUDED"
    )
    assert [page["page_number"] for page in exclusion["affected_pages"]] == [2]
    assert outputs.knowledge_map["processing"] == "partial"
    assert outputs.knowledge_map_view["limitations"][-1] == {
        "reason_code": "PAGE_CONTENT_EXCLUDED",
        "page_numbers": [2],
        "affected_page_count": 1,
    }


def test_terminal_publish_rejects_immutable_study_material_output_conflict(
    processing_database_dsn: str,
    artifact_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = "study_material_outputs"
    document_column = "document"
    fixed_now = datetime(2026, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(outputs_module, "_now", lambda: fixed_now)
    learner = _learner(processing_database_dsn)
    source = _source(processing_database_dsn, learner, tmp_path)
    with _Loopback({1: _simple_page_body(1)}) as provider:
        config = _config(tmp_path / "cache", provider.endpoint)
        created = create_material_processing_run(
            learner,
            source.material_id,
            source.artifact_id,
            "mathematics",
            f"conflict-{table}",
            [_upload()],
            config,
            page_limit=10,
            dsn=processing_database_dsn,
        )
        claim = claim_next_material_processing_run(dsn=processing_database_dsn)
        assert claim is not None
        completed = execute_claimed_material_processing_run(
            claim, config, dsn=processing_database_dsn
        )
    assert completed.output_binding is not None
    outputs = read_material_run_outputs(
        learner, source.material_id, created.run_id, dsn=processing_database_dsn
    )
    with psycopg.connect(processing_database_dsn) as connection:
        connection.execute(
            f"UPDATE {table} SET {document_column}={document_column} || '{{\"tampered\":true}}'::jsonb"
        )
        connection.execute(
            """UPDATE material_processing_runs
               SET status='running',output_binding=NULL,completed_at=NULL
               WHERE run_id=%s""",
            (created.run_id,),
        )
    safe_status = {
        key: completed.output_binding[key]
        for key in (
            "processing",
            "quality",
            "decision",
            "reason_code",
            "provider_call_counts",
        )
    }
    with pytest.raises(MaterialRunOutputError, match="MATERIAL_OUTPUT_FAILED"):
        publish_terminal_outputs(
            learner,
            source.material_id,
            created.run_id,
            "mathematics",
            outputs.resource_catalog,
            outputs.study_material_output,
            safe_status,
            dsn=processing_database_dsn,
        )
    with psycopg.connect(processing_database_dsn) as connection:
        assert connection.execute(
            "SELECT status FROM material_processing_runs WHERE run_id=%s",
            (created.run_id,),
        ).fetchone() == ("running",)


def test_preparation_failure_cleans_resources_and_marks_run_failed(
    processing_database_dsn: str, artifact_root: Path, tmp_path: Path
) -> None:
    learner = _learner(processing_database_dsn)
    source = _source(processing_database_dsn, learner, tmp_path)
    config = _config(tmp_path / "cache", "http://127.0.0.1:1")
    with pytest.raises(MaterialProcessingError, match="CONTROLLED_RESOURCE_INVALID"):
        create_material_processing_run(
            learner, source.material_id, source.artifact_id, "mathematics", "bad-resource",
            [_upload(locator="https://user:secret@example.edu/private.pdf")],
            config, page_limit=10, dsn=processing_database_dsn,
        )
    with psycopg.connect(processing_database_dsn) as connection:
        assert connection.execute("SELECT count(*) FROM artifacts WHERE kind='resource_pdf'").fetchone() == (0,)


def test_invalid_development_run_output_is_terminal_failure_with_no_domain_outputs(
    processing_database_dsn: str,
    artifact_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    learner = _learner(processing_database_dsn)
    source = _source(processing_database_dsn, learner, tmp_path)
    config = _config(tmp_path / "cache", "http://127.0.0.1:1")
    created = create_material_processing_run(
        learner,
        source.material_id,
        source.artifact_id,
        "mathematics",
        "invalid-development-run",
        [_upload()], config, page_limit=10, dsn=processing_database_dsn,
    )
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None
    monkeypatch.setattr(processing_module, "run_development_pdf", lambda *args, **kwargs: {"processing": "succeeded"})
    failed = execute_claimed_material_processing_run(claim, config, dsn=processing_database_dsn)
    assert failed.status == "failed" and failed.error_code == "MATERIAL_ANALYSIS_FAILED"
    with pytest.raises(MaterialRunOutputError, match="MATERIAL_OUTPUT_UNAVAILABLE"):
        read_material_run_outputs(
            learner, source.material_id, created.run_id, dsn=processing_database_dsn
        )
    _assert_no_terminal_outputs(processing_database_dsn, created.run_id)


def test_handoff_binding_reject_is_material_failure_without_terminal_outputs(
    processing_database_dsn: str,
    artifact_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _Loopback({1: _simple_page_body(1)}) as provider:
        config = _config(tmp_path / "cache", provider.endpoint)
        learner, source, created, claim = _create_claimed_run(
            processing_database_dsn,
            tmp_path,
            config,
            "handoff-binding-reject",
        )
        run_pipeline = processing_module.run_development_pdf

        def wrong_handoff(*args, **kwargs):
            development_run = deepcopy(run_pipeline(*args, **kwargs))
            development_run["study_material_output"]["handoff_id"] = str(uuid4())
            _rebind_output_id(development_run["study_material_output"])
            return development_run

        monkeypatch.setattr(processing_module, "run_development_pdf", wrong_handoff)
        failed = execute_claimed_material_processing_run(
            claim, config, dsn=processing_database_dsn
        )

    assert failed.status == "failed"
    assert failed.error_code == "MATERIAL_ANALYSIS_FAILED"
    assert failed.output_binding is None
    _assert_no_terminal_outputs(processing_database_dsn, created.run_id)


def test_resource_result_builder_failure_is_terminal_output_failure(
    processing_database_dsn: str,
    artifact_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    learner = _learner(processing_database_dsn)
    source = _source(processing_database_dsn, learner, tmp_path)
    with _Loopback({1: _simple_page_body(1)}) as provider:
        config = _config(tmp_path / "resource-result-cache", provider.endpoint)
        created = create_material_processing_run(
            learner,
            source.material_id,
            source.artifact_id,
            "mathematics",
            "resource-result-failure",
            [_upload()],
            config,
            page_limit=10,
            dsn=processing_database_dsn,
        )
        claim = claim_next_material_processing_run(dsn=processing_database_dsn)
        assert claim is not None
        monkeypatch.setattr(
            outputs_module,
            "build_learning_resource_result",
            lambda *args, **kwargs: {
                "schema": "learning-resource-result/v1",
                "processing": "failed",
                "reason_code": "LEARNING_RESOURCE_GATE_INVALID",
            },
        )
        failed = execute_claimed_material_processing_run(
            claim,
            config,
            dsn=processing_database_dsn,
        )

    assert failed.run_id == created.run_id
    assert failed.status == "failed"
    assert failed.error_code == "MATERIAL_OUTPUT_FAILED"
    _assert_no_terminal_outputs(processing_database_dsn, created.run_id)


def test_startup_recovery_marks_only_running_as_interrupted(
    processing_database_dsn: str, artifact_root: Path, tmp_path: Path
) -> None:
    learner = _learner(processing_database_dsn)
    source = _source(processing_database_dsn, learner, tmp_path)
    config = _config(tmp_path / "cache", "http://127.0.0.1:1")
    pending = create_material_processing_run(
        learner, source.material_id, source.artifact_id, "mathematics", "recover",
        [_upload()], config, page_limit=10, dsn=processing_database_dsn,
    )
    claim = claim_next_material_processing_run(dsn=processing_database_dsn)
    assert claim is not None and claim.run.run_id == pending.run_id
    assert recover_interrupted_material_runs(dsn=processing_database_dsn) == 1
    recovered = read_material_processing_run(learner, pending.run_id, dsn=processing_database_dsn)
    assert recovered.status == "failed" and recovered.error_code == "RESTART_INTERRUPTED"


def test_owner_and_corrupt_output_reads_fail_closed(
    processing_database_dsn: str, artifact_root: Path, tmp_path: Path
) -> None:
    learner = _learner(processing_database_dsn)
    other = _learner(processing_database_dsn)
    source = _source(processing_database_dsn, learner, tmp_path)
    with pytest.raises(MaterialProcessingError, match="MATERIAL_RUN_INVALID"):
        create_material_processing_run(
            other, source.material_id, source.artifact_id, "mathematics", "cross-owner",
            [_upload()], _config(tmp_path / "cache", "http://127.0.0.1:1"),
            page_limit=10, dsn=processing_database_dsn,
        )
    with _Loopback({1: _simple_page_body(1)}) as provider:
        config = _config(tmp_path / "owner-cache", provider.endpoint)
        created = create_material_processing_run(
            learner,
            source.material_id,
            source.artifact_id,
            "mathematics",
            "owner-read",
            [_upload()],
            config,
            page_limit=10,
            dsn=processing_database_dsn,
        )
        claim = claim_next_material_processing_run(dsn=processing_database_dsn)
        assert claim is not None and claim.run.run_id == created.run_id
        completed = execute_claimed_material_processing_run(
            claim, config, dsn=processing_database_dsn
        )
    assert completed.status in {"succeeded", "partial"}
    with pytest.raises(MaterialRunOutputError, match="MATERIAL_OUTPUT_UNAVAILABLE"):
        read_material_run_outputs(
            other, source.material_id, created.run_id, dsn=processing_database_dsn
        )
    with psycopg.connect(processing_database_dsn) as connection:
        connection.execute(
            """UPDATE resource_catalogs
               SET document=document || '{"tampered":true}'::jsonb
               WHERE learner_id=%s AND material_id=%s""",
            (learner, source.material_id),
        )
    with pytest.raises(MaterialRunOutputError, match="MATERIAL_OUTPUT_UNAVAILABLE"):
        read_material_run_outputs(
            learner, source.material_id, created.run_id, dsn=processing_database_dsn
        )
