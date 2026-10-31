"""後端標籤、跨步候選及終態私人紀錄；只有合成輸入。"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import os
import stat

import pytest

from runtime import luna_calls, provider_http
from runtime.luna_test import test_lock as luna_lock
from runtime.local_ai_config import _runtime_lock
from runtime.semantic_service import SemanticServiceError, request_semantics
from test_podcast_process import provider_server
from test_luna_call_log import fake_codex
from luna_call_log import call_records


@pytest.fixture(autouse=True)
def private_state(monkeypatch, tmp_path):
    (tmp_path / "artifacts").mkdir()
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    luna_calls._pending.clear()
    yield
    luna_calls._pending.clear()


def start(feature="research", **kwargs):
    return luna_calls.start(feature, "work", "attempt", "material", "learner", **kwargs)


def test_cross_step_candidates_survive_until_failed_terminal(tmp_path, monkeypatch, caplog):
    notifications = []
    monkeypatch.setattr(provider_http, "provider", lambda path, body: notifications.append(body) or {})
    first = start()
    luna_calls.sent(luna_calls.tags())
    luna_calls.capture({"private": "私密候選"})
    luna_calls.rejected("私密退回理由")
    luna_calls.close(first)
    assert not list((tmp_path / "artifacts/failures").glob("*.json"))
    assert not notifications
    second = start()
    assert second.candidates == first.candidates
    luna_calls.completed("failed", "RESEARCH_SEARCH_FAILED")
    luna_calls.close(second)
    path, = (tmp_path / "artifacts/failures").glob("*.json")
    value = json.loads(path.read_text())
    assert value["candidates"][0]["candidate"] == {"private": "私密候選"}
    assert value["rejections"] == ["私密退回理由"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert len(notifications) == 1 and notifications[0]["outcome"] == "failed"
    assert "私密" not in caplog.text
    assert not luna_calls._pending


@pytest.mark.parametrize("outcome", ["succeeded", "cancelled"])
@pytest.mark.parametrize("archive", [None, "analysis/synthetic"])
def test_intermediate_rejection_does_not_become_failure(outcome, archive, tmp_path):
    work = start(archive=archive)
    saved = []
    luna_calls.set_archive_writer(saved.extend)
    luna_calls.capture("私密退稿")
    luna_calls.rejected("未通過", candidate="私密退稿")
    luna_calls.completed(outcome)
    luna_calls.close(work)
    assert not list((tmp_path / "artifacts/failures").glob("*.json"))
    assert not luna_calls._pending
    assert saved == [] and work.candidates == []


def test_cancel_discards_pending_attempt_and_retry_uses_new_identity(tmp_path):
    work = start()
    luna_calls.capture("舊候選")
    luna_calls.close(work)
    luna_calls.discard("research", "work", "learner")
    retried = luna_calls.start("research", "work", "new-attempt", "material", "learner")
    assert retried.tags["attempt_id"] != work.tags["attempt_id"]
    assert retried.candidates == []
    luna_calls.completed("succeeded")
    luna_calls.close(retried)
    assert not list((tmp_path / "artifacts/failures").glob("*.json"))


def test_failure_retention_cleans_without_new_model_call(tmp_path):
    directory = tmp_path / "artifacts/failures"
    directory.mkdir(parents=True)
    old, recent = directory / "old.json", directory / "recent.json"
    old.write_text("{}")
    recent.write_text("{}")
    timestamp = (datetime.now(UTC) - timedelta(days=31)).timestamp()
    os.utime(old, (timestamp, timestamp))
    luna_calls.cleanup()
    assert not old.exists() and recent.exists()


def test_failed_title_waits_for_parent_transaction(tmp_path):
    parent = start("podcast_script")
    title = start("podcast_title", defer_to_parent=True)
    luna_calls.capture("無效集名")
    luna_calls.completed("failed", "PODCAST_TITLE_INVALID")
    luna_calls.close(title)
    assert not list((tmp_path / "artifacts/failures").glob("*.json"))
    luna_calls.completed("succeeded")
    luna_calls.close(parent)
    path, = (tmp_path / "artifacts/failures").glob("*.json")
    assert json.loads(path.read_text())["tags"]["feature"] == "podcast_title"


def test_material_raw_failure_reaches_archive_reference_only(provider_server, fake_codex, tmp_path):
    saved = []
    work = start("material_analysis", archive="analysis/learner/material/run")
    luna_calls.set_archive_writer(saved.extend)
    lock = luna_lock(_runtime_lock())
    with pytest.raises(SemanticServiceError):
        request_semantics(None, runtime_lock=lock, task="material_semantics", tags=luna_calls.tags(feature="material_analysis", stage="analysis"), request={"case": "invalid-json"}, response_schema={"type": "object"})
    luna_calls.completed("failed", "SEMANTIC_SERVICE_UNAVAILABLE")
    luna_calls.close(work)
    candidate, = saved
    assert candidate["candidate"] == "私密候選：這不是 JSON"
    assert candidate["rejection_reason"].startswith("Expecting value")
    path, = (tmp_path / "artifacts/failures").glob("*.json")
    record = json.loads(path.read_text())
    assert record["archive_reference"] == "analysis/learner/material/run"
    assert "candidates" not in record and "私密候選" not in path.read_text()


def test_parallel_semantics_preserves_tags_and_runtime_lock(provider_server, fake_codex, tmp_path):
    lock = luna_lock(_runtime_lock())
    original = deepcopy(lock)
    work = start("material_analysis", archive="analysis/synthetic")
    def invoke():
        return request_semantics(None, runtime_lock=lock, task="material_semantics", tags=luna_calls.tags(feature="material_analysis", stage="analysis"), request={"synthetic": True}, response_schema={"type": "object"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(copy_context().run, invoke) for _ in range(2)]
        assert all(job.result() == {"synthetic": True} for job in jobs)
    luna_calls.completed("succeeded")
    luna_calls.close(work)
    records = call_records(tmp_path / "luna-calls.jsonl")
    assert len(records) == 2
    assert all(record["tags"]["stage"] == "analysis" and record["tags"]["work_id"] == "work" for record in records)
    assert lock == original
    assert all("tags" not in json.loads(line)["stdin"] and "learner" not in json.loads(line)["stdin"] for line in fake_codex.read_text().splitlines())


def test_podcast_failure_masks_secrets_and_keeps_validation_location(tmp_path, monkeypatch):
    from runtime.podcast_script import validate
    monkeypatch.setenv("STUDYDY_PODCAST_PROVIDER_TOKEN", "private-provider-secret")
    work = start(feature="podcast_script")
    luna_calls.capture({"title": "private-provider-secret", "authorization": "unknown-secret",
                        "raw": "Bearer raw-secret"})
    with pytest.raises(ValueError):
        validate({"schema": "podcast-script/v2", "provider": "test", "segments": []}, {"claims": []})
    luna_calls.completed("failed", "PODCAST_SCRIPT_INVALID")
    luna_calls.close(work)
    path, = (tmp_path / "artifacts/failures").glob("*.json")
    saved = path.read_text()
    assert "private-provider-secret" not in saved and "unknown-secret" not in saved and "raw-secret" not in saved
    document = json.loads(saved)
    assert document["rejections"][0]["file"] == "podcast_script.py"
    assert document["rejections"][0]["function"] == "validate"
    assert document["rejections"][0]["line"] > 0


def test_gemma_generate_preserves_all_attempt_diagnostics_on_failure(tmp_path, monkeypatch):
    from runtime import semantic_service, local_ai_config
    from test_semantic_service_v1 import _chat, _lock
    monkeypatch.setattr(local_ai_config, "configured_lock", _lock)
    requests = []
    monkeypatch.setenv("VLLM_API_KEY", "private-provider-secret")
    contents = [('stop', '{"text":"分布分布 private-provider-secret"}'), ('length', '{')]
    monkeypatch.setattr(semantic_service, "semantic_client", lambda: _chat(contents, requests))
    work = start(feature="podcast_script")
    with pytest.raises(semantic_service.SemanticServiceError, match="SEMANTIC_OUTPUT_TRUNCATED"):
        semantic_service.generate("synthetic", {})
    luna_calls.completed("failed", "PODCAST_SCRIPT_INVALID")
    luna_calls.close(work)
    path, = (tmp_path / "artifacts/failures").glob("*.json")
    records = json.loads(path.read_text())["candidates"]
    assert [r["candidate"]["finish_reason"] for r in records] == ["stop", "length"]
    assert all(r["candidate"]["candidate"] for r in records)
    assert requests == [20768, 20768]
    assert "private-provider-secret" not in path.read_text()
