"""筆記／章節真 worker、隔離 DB、真 provider 與假 CLI 的標籤與私人失敗回歸。"""

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from assessment_fixtures import concept_fixture
from product_fixtures import closed_loop
from test_note_queue import setup, drain, answer, HEADERS
from test_knowledge_chapter_queue import _answer, _key, _row, _drain_others
from test_podcast_process import provider_server
from test_luna_call_log import fake_codex
from luna_call_log import call_records
from runtime import knowledge_chapters as kc, material_notes as mn, luna_calls, luna_test
from runtime.provider_http import provider
from runtime.storage.database import database_session
from runtime.storage.tables import MaterialNotes


def _model(monkeypatch, feature, expected_key, replies, seen):
    def call(request, *args):
        metadata = luna_calls.tags()
        seen.append(metadata)
        assert metadata["feature"] == feature
        assert metadata["work_id"] == luna_calls.identity(expected_key)
        assert metadata["learner_id"] == str(expected_key[0])
        assert metadata["material_id"] == str(expected_key[1])
        assert metadata["attempt_id"]
        response = replies(request)
        monkeypatch.setenv("SYNTHETIC_RESPONSE_JSON", json.dumps(response))
        return provider("/semantics", {
            "task": "material_review", "prompt": "合成離線驗收", "request": request,
            "schema": {"type": "object"}, "tags": metadata,
        })
    return call


@pytest.mark.parametrize("kind", ["summary", "full"])
def test_note_invalid_candidate_records_tags_and_private_failure(
    closed_loop, tmp_path, monkeypatch, provider_server, fake_codex, kind, caplog,
):
    fixture, client, path = setup(closed_loop, tmp_path, monkeypatch)
    drain(fixture)
    if kind == "full":
        assert client.post(path + "/full", headers=HEADERS, json={"expected_generation": 0}).status_code == 202
        drain(fixture)
    before = client.get(path + "/" + kind).json()
    key = (*_key(fixture), kind)
    with database_session(fixture["dsn"]) as db:
        lock_before = deepcopy(db.get(MaterialNotes, key).runtime_lock)
    assert client.post(path + "/" + kind, headers=HEADERS,
                       json={"expected_generation": before["generation"]}).status_code == 202
    monkeypatch.setattr(luna_test, "enabled", lambda _: True)
    seen = []
    model = _model(monkeypatch, "note_" + kind, key, lambda _: {"paragraphs": [], "private_candidate": "PRIVATE_NOTE_CANDIDATE"}, seen)
    assert mn.step(dsn=fixture["dsn"], call=model)
    assert not mn.step(dsn=fixture["dsn"], call=model)
    after = client.get(path + "/" + kind).json()
    assert after["status"] == "failed" and after["error_code"]
    assert after["paragraphs"] == before["paragraphs"] and after["generation"] == before["generation"]
    assert seen and len({row["attempt_id"] for row in seen}) == 1
    with database_session(fixture["dsn"]) as db:
        assert db.get(MaterialNotes, key).runtime_lock == lock_before
    records = call_records(tmp_path / "luna-calls.jsonl")
    assert len(fake_codex.read_text().splitlines()) == len(records) == len(seen)
    assert all(row["tags"]["feature"] == "note_" + kind for row in records)
    _assert_private_failure("PRIVATE_NOTE_CANDIDATE", after["error_code"], tmp_path, caplog)


def test_chapter_invalid_candidate_records_tags_and_private_failure(
    closed_loop, tmp_path, monkeypatch, provider_server, fake_codex, caplog,
):
    fixture = concept_fixture(closed_loop, 3)
    _drain_others(fixture)
    assert kc.step(dsn=fixture["dsn"], call=_answer, model_id="synthetic")
    before = _row(fixture)
    assert before["status"] == "ready"
    assert kc.queue_existing(dsn=fixture["dsn"], material_ids=[fixture["source"].material_id], requeue=True)
    monkeypatch.setattr(luna_test, "enabled", lambda _: True)
    seen = []
    model = _model(monkeypatch, "chapters", _key(fixture), lambda _: {"chapters": [], "private_candidate": "PRIVATE_CHAPTER_CANDIDATE"}, seen)
    assert kc.step(dsn=fixture["dsn"], call=model, model_id="synthetic")
    assert not kc.step(dsn=fixture["dsn"], call=model)
    after = _row(fixture)
    assert after["status"] == "failed" and after["error_code"] == "CHAPTER_ALL_WINDOWS_FAILED"
    assert len(seen) == 2 and seen[0]["attempt_id"] == seen[1]["attempt_id"]
    assert len(fake_codex.read_text().splitlines()) == 2
    records = call_records(tmp_path / "luna-calls.jsonl")
    assert len(records) == 2
    assert all(row["tags"]["feature"] == "chapters" for row in records)
    _assert_private_failure("PRIVATE_CHAPTER_CANDIDATE", after["error_code"], tmp_path, caplog)


def _assert_private_failure(marker, reason, tmp_path, caplog):
    directory = Path(os.environ["STUDYDY_ARTIFACT_ROOT"]) / "failures"
    files = list(directory.glob("*.json"))
    assert files and directory.stat().st_mode & 0o777 == 0o700
    saved, = [path for path in files if marker in path.read_text()]
    assert saved.stat().st_mode & 0o777 == 0o600
    record = json.loads(saved.read_text())
    assert record["reason"] == reason and record["candidates"]
    assert marker not in caplog.text
    assert marker not in (tmp_path / "luna-calls.jsonl").read_text()
