"""教材與題目真 caller／provider 標籤、封存重用及私人失敗保存。"""

from copy import deepcopy
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from threading import Lock
from uuid import uuid4

import pytest

from assessment_fixtures import concept_fixture, create, model_for, read
from product_fixtures import closed_loop
from backend.tests.runtime.sources.test_source_revisions import revisions
from backend.tests.runtime.sources.test_independent_block_archive import analyze_with, fake_response
from backend.tests.runtime.sources.test_material_review_flow import enable_review, keep_response
from test_material_consolidation import keep, approved
from test_podcast_process import provider_server
from test_luna_call_log import fake_codex
from luna_call_log import call_records
from learning_adaptation import assessment_sets as sets
from runtime import luna_test, material_processing as processing
from runtime.local_ai_config import _runtime_lock
from runtime.semantic_service import request_semantics
from runtime.storage.analysis_archive import AnalysisArchive, AnalysisArchiveError, _material_directory
from runtime.storage.database import database_session
from runtime.storage.tables import AssessmentSet, MaterialProcessingRun


def _real_semantics(monkeypatch, response):
    lock = Lock()
    def call(client, **kwargs):
        # 假 CLI 的可控輸出使用 process env；只序列化替身設定，保留 caller 執行緒 context。
        with lock:
            value = response(client, **kwargs)
            monkeypatch.setenv("SYNTHETIC_RESPONSE_JSON", json.dumps(value))
            return request_semantics(client, **kwargs)
    return call


def _check_labels(records, *, owner, material, work, features):
    assert records and {row["tags"]["feature"] for row in records} == set(features)
    assert all(row["tags"]["learner_id"] == str(owner)
               and row["tags"]["material_id"] == str(material)
               and row["tags"]["work_id"] == str(work)
               and row["tags"]["attempt_id"] for row in records)


@pytest.mark.parametrize("reject", [False, True])
def test_assessment_generation_and_check_use_real_tagged_provider(
    closed_loop, tmp_path, monkeypatch, provider_server, fake_codex, reject, caplog,
):
    fixture = concept_fixture(closed_loop, 1)
    set_id = create(fixture)
    with database_session(fixture["dsn"]) as db:
        original_lock = deepcopy(db.get(AssessmentSet, set_id).runtime_lock_document)
    monkeypatch.setattr(luna_test, "enabled", lambda _: True)
    base = model_for(fixture)
    def response(client, **kwargs):
        if reject and kwargs["task"] == "assessment_check":
            return {"private_candidate": "PRIVATE_ASSESSMENT_CHECK"}
        return base(client, **kwargs)
    work = sets.claim_set_work(dsn=fixture["dsn"])
    sets.execute_set_work(work, dsn=fixture["dsn"], semantic_call=_real_semantics(monkeypatch, response))
    result = read(fixture, set_id)
    assert result["status"] == ("failed" if reject else "ready")
    records = call_records(tmp_path / "luna-calls.jsonl")
    assert len(records) == len(fake_codex.read_text().splitlines()) == 2
    _check_labels(records, owner=fixture["learner"].learner_id, material=fixture["source"].material_id,
                  work=json.dumps([str(work.set_id), str(work.ordinal)], separators=(",", ":")),
                  features={"assessment", "assessment_check"})
    with database_session(fixture["dsn"]) as db:
        assert db.get(AssessmentSet, set_id).runtime_lock_document == original_lock
    failures = list((Path(os.environ["STUDYDY_ARTIFACT_ROOT"]) / "failures").glob("*.json"))
    if reject:
        saved, = failures
        assert "PRIVATE_ASSESSMENT_CHECK" in saved.read_text()
        assert saved.stat().st_mode & 0o777 == 0o600
        assert "PRIVATE_ASSESSMENT_CHECK" not in caplog.text
        assert "PRIVATE_ASSESSMENT_CHECK" not in (tmp_path / "luna-calls.jsonl").read_text()
    else:
        assert failures == []


def test_material_parallel_blocks_keep_work_tags(revisions, tmp_path, monkeypatch, provider_server, fake_codex):
    owner, material, settings, dsn, add, start, execute, _, old, _ = revisions
    additions = [add("B.pdf", "A queue removes the first inserted item."),
                 add("C.pdf", "A tree connects parent and child nodes.")]
    settings["runtime_lock"]["material_semantics"] = deepcopy(_runtime_lock()["material_semantics"])
    monkeypatch.setattr(luna_test, "enabled", lambda _: True)
    analyze_with(monkeypatch, _real_semantics(monkeypatch, lambda _client, **kwargs: fake_response(kwargs)))
    run = start(additions, "tagged-parallel", old["revision"])
    result = execute()
    assert result.status in {"succeeded", "partial"}, result.error_code
    records = call_records(tmp_path / "luna-calls.jsonl")
    assert len(records) == len(fake_codex.read_text().splitlines()) == 2
    _check_labels(records, owner=owner.learner_id, material=material, work=run.run_id, features={"material_analysis"})
    assert all(row["tags"]["stage"] == "analysis" for row in records)


def test_material_completed_block_reuse_adds_no_cli_calls_or_lock_changes(
    revisions, tmp_path, monkeypatch, provider_server, fake_codex,
):
    owner, material, settings, dsn, add, start, execute, _, old, _ = revisions
    addition = add("B.pdf", "A queue removes the first inserted item.")
    settings["runtime_lock"]["material_semantics"] = deepcopy(_runtime_lock()["material_semantics"])
    monkeypatch.setattr(luna_test, "enabled", lambda _: True)
    analyze_with(monkeypatch, _real_semantics(monkeypatch, lambda _client, **kwargs: fake_response(kwargs)))
    first = start([addition], "checkpoint-write-failure", old["revision"])
    original = AnalysisArchive.save_checkpoint
    def fail_after_block(self, value):
        if value["cursor"] > 0:
            raise AnalysisArchiveError("ANALYSIS_ARTIFACT_WRITE_FAILED")
        return original(self, value)
    with monkeypatch.context() as patch:
        patch.setattr(AnalysisArchive, "save_checkpoint", fail_after_block)
        assert execute().status == "failed"
    directory = _material_directory(owner.learner_id, material) / first.run_id.hex
    before_checkpoint = (directory / "checkpoint.json").read_bytes()
    complete, = (directory / "blocks").glob("*/complete.json")
    previous_calls = json.loads(complete.read_text())["data"]["call_number"]
    assert len(fake_codex.read_text().splitlines()) == 1
    before_records = call_records(tmp_path / "luna-calls.jsonl")
    original_lock = deepcopy(first.runtime_lock_document)
    resumed = start([addition], "reuse-tagged-block", old["revision"])
    assert (directory / "checkpoint.json").read_bytes() == before_checkpoint
    result = execute()
    assert result.status in {"succeeded", "partial"}, result.error_code
    assert call_records(tmp_path / "luna-calls.jsonl") == before_records
    assert len(fake_codex.read_text().splitlines()) == 1
    completion = json.loads((_material_directory(owner.learner_id, material) / resumed.run_id.hex / "completion.json").read_text())
    assert completion["reused_from_run"] == str(first.run_id)
    with database_session(dsn) as db:
        assert db.get(MaterialProcessingRun, first.run_id).runtime_lock_document == original_lock
    assert result.output_binding["semantic_calls"] == previous_calls


def test_material_review_and_consolidation_use_distinct_stage_labels(
    revisions, tmp_path, monkeypatch, provider_server, fake_codex,
):
    owner, material, settings, dsn, add, start, execute, _, old, _ = revisions
    enable_review(settings, consolidation=True)
    monkeypatch.setattr(luna_test, "enabled", lambda _: True)
    monkeypatch.setattr("runtime.material_review.request_semantics", _real_semantics(monkeypatch, lambda _client, **kwargs: keep_response(kwargs["request"])))
    def consolidated(_client, **kwargs):
        request = kwargs["request"]
        return keep(request) if request["operation"] == "consolidation_rewrite" else approved(request)
    monkeypatch.setattr("runtime.material_consolidation.request_semantics", _real_semantics(monkeypatch, consolidated))
    run = start([], "tagged-review-only", old["revision"])
    result = execute()
    assert result.status == "succeeded", result.error_code
    records = call_records(tmp_path / "luna-calls.jsonl")
    _check_labels(records, owner=owner.learner_id, material=material, work=run.run_id,
                  features={"material_review", "material_consolidation"})
    assert all(row["tags"]["stage"] == row["tags"]["feature"].removeprefix("material_") for row in records)
    assert len(records) == len(fake_codex.read_text().splitlines())


def test_material_invalid_raw_response_is_archived_only_after_failed_commit(
    revisions, tmp_path, monkeypatch, provider_server, fake_codex, caplog,
):
    owner, material, settings, dsn, add, start, execute, _, old, _ = revisions
    enable_review(settings)
    monkeypatch.setattr(luna_test, "enabled", lambda _: True)
    monkeypatch.setenv("SYNTHETIC_RESPONSE_JSON", "PRIVATE_MATERIAL_INVALID_JSON")
    monkeypatch.setattr("runtime.material_review.request_semantics", request_semantics)
    run = start([], "tagged-private-invalid", old["revision"])
    claim = processing.claim_next_material_processing_run(dsn=dsn)
    archive = AnalysisArchive(claim, dsn=dsn)
    result = processing.execute_claimed_material_processing_run(claim, settings, dsn=dsn)
    assert result.status == "failed"
    path = archive.directory / "provider-failure.json"
    raw = path.read_bytes()
    assert "PRIVATE_MATERIAL_INVALID_JSON" in raw.decode()
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    private, = (Path(os.environ["STUDYDY_ARTIFACT_ROOT"]) / "failures").glob("*.json")
    assert json.loads(private.read_text())["archive_reference"].endswith(run.run_id.hex)
    assert "PRIVATE_MATERIAL_INVALID_JSON" not in private.read_text()
    assert "PRIVATE_MATERIAL_INVALID_JSON" not in caplog.text
    assert "PRIVATE_MATERIAL_INVALID_JSON" not in (tmp_path / "luna-calls.jsonl").read_text()
    assert not (tmp_path / "failures").exists()
    with database_session(dsn) as db:
        db.get(MaterialProcessingRun, run.run_id).worker_token = uuid4()
    with pytest.raises(AnalysisArchiveError, match="MATERIAL_RUN_UNAVAILABLE"):
        archive.save_provider_failure([{"candidate": "late wrong token"}])
    with database_session(dsn) as db:
        row = db.get(MaterialProcessingRun, run.run_id)
        row.worker_token, row.cancel_requested_at = claim.worker_token, datetime.now(UTC)
        row.status, row.error_code = "cancelled", None
    with pytest.raises(AnalysisArchiveError, match="MATERIAL_RUN_UNAVAILABLE"):
        archive.save_provider_failure([{"candidate": "late cancelled"}])
    assert path.read_bytes() == raw


def test_material_oversized_json_is_archived_after_failed_commit(
    revisions, tmp_path, monkeypatch, provider_server, fake_codex, caplog,
):
    owner, material, settings, dsn, add, start, execute, _, old, _ = revisions
    enable_review(settings)
    monkeypatch.setattr(luna_test, "enabled", lambda _: True)
    candidate = {"private_candidate": "PRIVATE_MATERIAL_OVERSIZED_JSON " + "x " * 40000}
    monkeypatch.setenv("SYNTHETIC_RESPONSE_JSON", json.dumps(candidate))
    monkeypatch.setattr("runtime.material_review.request_semantics", request_semantics)
    run = start([], "tagged-private-oversized", old["revision"])
    claim = processing.claim_next_material_processing_run(dsn=dsn)
    archive = AnalysisArchive(claim, dsn=dsn)
    result = processing.execute_claimed_material_processing_run(claim, settings, dsn=dsn)
    assert result.status == "failed" and result.error_code == "SEMANTIC_OUTPUT_TOO_LARGE"
    path = archive.directory / "provider-failure.json"
    saved, = json.loads(path.read_text())["data"]["candidates"]
    assert saved["candidate"] == candidate
    assert saved["rejection_reason"] == "SEMANTIC_OUTPUT_TOO_LARGE"
    assert saved["tags"]["feature"] == "material_review"
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    private, = (Path(os.environ["STUDYDY_ARTIFACT_ROOT"]) / "failures").glob("*.json")
    index = json.loads(private.read_text())
    assert index["archive_reference"].endswith(run.run_id.hex)
    assert "candidates" not in index
    assert "PRIVATE_MATERIAL_OVERSIZED_JSON" not in private.read_text()
    assert "PRIVATE_MATERIAL_OVERSIZED_JSON" not in caplog.text
    assert "PRIVATE_MATERIAL_OVERSIZED_JSON" not in (tmp_path / "luna-calls.jsonl").read_text()
    record, = call_records(tmp_path / "luna-calls.jsonl")
    assert record["outcome"] == "succeeded"  # provider 已成功解析，拒收發生在後端容量邊界。
    assert len(fake_codex.read_text().splitlines()) == 1
    assert not (tmp_path / "failures").exists()
