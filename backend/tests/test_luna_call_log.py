"""真 provider HTTP＋假 Codex 子程序；不使用模型、產品 DB 或私人資料。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from threading import Thread
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from test_podcast_process import provider_server, post
from test_podcast_provider import provider, body as script_body
from luna_call_log import CallLog, FEATURES, LIMIT_EXCEEDED, call_records, read_events
from luna_report import report


def tags(feature="note_summary", *, work="work-1", attempt="attempt-1", limit=10, **extra):
    return {"feature": feature, "material_id": "material-1", "learner_id": "learner-1",
            "work_id": work, "attempt_id": attempt, "call_limit": limit, **extra}


def semantics(metadata=None, **request):
    return {"job_id": str(uuid4()), "job_token": str(uuid4()), "task": "material_semantics",
            "prompt": "合成測試資料", "request": request, "schema": {"type": "object"},
            **({"tags": metadata} if metadata is not None else {})}


@pytest.fixture
def fake_codex(monkeypatch, tmp_path):
    executable = tmp_path / "fake-codex"
    marker = tmp_path / "cli-starts.jsonl"
    executable.write_text("#!" + sys.executable + "\n" + r'''
import json, os, pathlib, sys
prompt = sys.stdin.read()
with open(os.environ['SYNTHETIC_CLI_STARTS'], 'a') as log:
    log.write(json.dumps({'stdin': prompt}) + '\n')
schema = json.loads(pathlib.Path(sys.argv[sys.argv.index('--output-schema')+1]).read_text())
output = pathlib.Path(sys.argv[sys.argv.index('-o')+1])
properties = schema.get('properties', {})
if 'failed-cli' in prompt:
    output.write_text('私密候選：CLI 失敗但有輸出')
    sys.exit(17)
elif 'invalid-json' in prompt:
    output.write_text('私密候選：這不是 JSON')
elif 'checks' in properties:
    request = json.loads(prompt.split('\nINPUT:\n', 1)[1])
    mode = os.environ.get('SYNTHETIC_REJECT')
    reject = mode == '1' or (mode == 'once' and sum('\nINPUT:\n' in json.loads(line)['stdin'] for line in pathlib.Path(os.environ['SYNTHETIC_CLI_STARTS']).read_text().splitlines()) == 1)
    output.write_text(json.dumps({
        'checks': [{'part': p, 'supported': not reject, 'reason': '私密核對理由' if reject else ''} for p in request['parts']],
        'coverage': [{'claim': c, 'explained': not reject, 'script_parts': [] if reject else request['coverage_locations'][c][:1], 'reason': '私密涵蓋理由' if reject else ''} for c in request['coverage_claims']]
    }))
elif 'segments' in properties:
    output.write_text(json.dumps({'segments': [{'title': '條件', 'turns': [{'speaker': 'host', 'text': '空堆疊不可 pop。', 'source_indices': [0]}]}]}))
elif 'turns' in properties:
    output.write_text(json.dumps({'title': '條件', 'turns': [{'speaker': 'host', 'text': '空堆疊不可 pop。', 'source_indices': [0]}]}))
elif 'sections' in properties:
    output.write_text(json.dumps({'sections': [{'pages': [{'layout': 'concept', 'title': '合成觀念', 'anchor': '', 'nodes': [{'label': '觀念', 'text': '合成來源', 'anchor': '', 'key_terms': []}], 'relations': []}]} for _ in range(properties['sections']['minItems'])]}))
elif 'items' in properties:
    output.write_text(json.dumps({'items': [{'index': 0, 'supported': True, 'reason': ''}]}))
else:
    output.write_text(json.dumps({'synthetic': True}))
''', encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv("STUDYDY_CODEX_BIN", str(executable))
    monkeypatch.setenv("SYNTHETIC_CLI_STARTS", str(marker))
    return marker


def test_all_feature_labels_survive_real_http_and_real_fake_process(provider_server, fake_codex, tmp_path):
    for feature in sorted(FEATURES):
        metadata = tags(feature, work=feature)
        assert post(provider_server, "/semantics", semantics(metadata))[0] == 200
    records = call_records(tmp_path / "luna-calls.jsonl")
    assert len(records) == len(FEATURES)
    assert {row["tags"]["feature"] for row in records} == FEATURES
    assert all(row["tags"]["material_id"] == "material-1" and row["tags"]["learner_id"] == "learner-1"
               and row["tags"]["work_id"] == row["tags"]["feature"] and row["outcome"] == "succeeded" for row in records)
    assert len(fake_codex.read_text().splitlines()) == len(FEATURES)
    summary = json.loads(subprocess.check_output([
        sys.executable, str(Path(provider.__file__).with_name("luna_report.py")),
        "--log", str(tmp_path / "luna-calls.jsonl"),
    ], text=True))
    assert len(summary) == len(FEATURES)
    for row in summary:
        record, = [item for item in records if item["tags"]["feature"] == row["feature"]]
        assert row["calls"] == 1 and row["failures"] == row["blocked"] == row["interrupted"] == 0
        assert row["elapsed_seconds"] == record["elapsed_seconds"]
    for line in fake_codex.read_text().splitlines():
        prompt = json.loads(line)["stdin"]
        assert "tags" not in prompt and "learner-1" not in prompt and "attempt-1" not in prompt


def test_parallel_limit_is_atomic_and_blocked_never_starts_cli(provider_server, fake_codex, tmp_path):
    metadata = tags(limit=3)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: post(provider_server, "/semantics", semantics(metadata)), range(8)))
    assert sum(status == 200 for status, _ in results) == 3
    assert sum(value == {"error_code": LIMIT_EXCEEDED} for _, value in results) == 5
    assert len(fake_codex.read_text().splitlines()) == 3
    summary = report(tmp_path / "luna-calls.jsonl")[0]
    assert summary["calls"] == 3 and summary["blocked"] == 5 and summary["failures"] == 0


def test_script_internal_calls_are_individual_and_candidates_wait_for_terminal_failure(provider_server, fake_codex, monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SYNTHETIC_REJECT", "1")
    metadata = tags("podcast_script", limit=37)
    status, response = post(provider_server, "/script", {**script_body(), "tags": metadata})
    assert (status, response) == (502, {"error_code": "PODCAST_SCRIPT_NEEDS_REVIEW"})
    records = call_records(tmp_path / "luna-calls.jsonl")
    assert len(records) == 4 and len(fake_codex.read_text().splitlines()) == 4
    assert not (tmp_path / "failures").exists()
    status, receipt = post(provider_server, "/jobs/luna-result", {"tags": metadata, "outcome": "failed", "reason": "PODCAST_SCRIPT_NEEDS_REVIEW"})
    assert status == 200 and receipt["saved"] and receipt["candidate_available"]
    directory = tmp_path / "failures"
    saved, = directory.glob("*.json")
    assert directory.stat().st_mode & 0o777 == 0o700 and saved.stat().st_mode & 0o777 == 0o600
    private = saved.read_text()
    assert "空堆疊不可 pop" in private and "私密核對理由" in private
    assert "PODCAST_SCRIPT_NEEDS_REVIEW" in private
    for public in ((tmp_path / "luna-calls.jsonl").read_text(), caplog.text):
        assert "空堆疊" not in public and "私密核對理由" not in public


@pytest.mark.parametrize("outcome", ["succeeded", "cancelled"])
def test_success_and_cancel_discard_in_memory_candidates(provider_server, fake_codex, tmp_path, outcome):
    metadata = tags("podcast_script", limit=37)
    assert post(provider_server, "/script", {**script_body(), "tags": metadata})[0] == 200
    assert not (tmp_path / "failures").exists()
    response = post(provider_server, "/jobs/luna-result", {"tags": metadata, "outcome": outcome, "reason": None})
    assert response[0] == 200 and not response[1]["saved"] and response[1]["candidate_available"]
    assert not (tmp_path / "failures").exists()
    # 清除後沒有模型候選，不得宣稱重送通知已保存。
    receipt = post(provider_server, "/jobs/luna-result", {"tags": metadata, "outcome": "failed", "reason": "SYNTHETIC_FAILED"})[1]
    assert not receipt["saved"] and not receipt["candidate_available"]


def test_intermediate_rejection_then_success_leaves_no_private_failure(provider_server, fake_codex, monkeypatch, tmp_path):
    monkeypatch.setenv("SYNTHETIC_REJECT", "once")
    metadata = tags("podcast_script", limit=37)
    assert post(provider_server, "/script", {**script_body(), "tags": metadata})[0] == 200
    assert len(fake_codex.read_text().splitlines()) == 4
    assert not (tmp_path / "failures").exists()
    receipt = post(provider_server, "/jobs/luna-result", {"tags": metadata, "outcome": "succeeded", "reason": None})[1]
    assert receipt["candidate_available"] and not receipt["saved"]
    assert not (tmp_path / "failures").exists()


def test_real_video_flow_uses_one_cli_call_and_preserves_tag_isolation(provider_server, fake_codex, monkeypatch, tmp_path):
    import base64
    from hashlib import sha256
    import video_service
    from test_podcast_video_plan import sample

    episode, _, alignment = sample()
    audio = b"synthetic audio"
    episode["audio"]["sha256"] = sha256(audio).hexdigest()
    for claim in episode["claims"]:
        claim.update(label="合成觀念", text="合成來源")
    monkeypatch.setenv("STUDYDY_VIDEO_PYTHON", sys.executable)
    monkeypatch.setenv("STUDYDY_VIDEO_WORK_DIR", str(tmp_path / "video-work"))
    monkeypatch.setattr(video_service, "ensure_space", lambda _: None)
    monkeypatch.setattr(provider, "align_audio", lambda *args, **kwargs: alignment)
    def media_process(args, env, timeout, cancelled):
        output = Path(args[5])
        if args[-1] == "--validate":
            output.write_text(json.dumps({"plan": {"synthetic": True}}))
        else:
            output.write_text("{}")
            output.with_suffix(".mp4").write_bytes(b"synthetic video")
        return 0
    monkeypatch.setattr(video_service, "_process", media_process)
    metadata = tags("podcast_video", limit=1)
    payload = {"tags": metadata, "job_id": str(uuid4()), "job_token": str(uuid4()),
               "episode": episode, "audio": base64.b64encode(audio).decode(), "source_context": {},
               "podcast_id": "podcast-1", "episode_index": 0, "source_resolver": "/source"}
    assert post(provider_server, "/video", payload)[0] == 200
    records = call_records(tmp_path / "luna-calls.jsonl")
    assert len(records) == len(fake_codex.read_text().splitlines()) == 1
    assert records[0]["tags"] == metadata
    prompt = json.loads(fake_codex.read_text())["stdin"]
    assert "tags" not in prompt and "learner-1" not in prompt and "attempt-1" not in prompt


def test_invalid_json_is_private_only_after_terminal_failure(provider_server, fake_codex, tmp_path):
    metadata = tags()
    assert post(provider_server, "/semantics", semantics(metadata, mode="invalid-json"))[0] == 502
    assert not (tmp_path / "failures").exists()
    assert post(provider_server, "/jobs/luna-result", {"tags": metadata, "outcome": "failed", "reason": "SEMANTIC_RESPONSE_INVALID"})[1]["saved"]
    saved, = (tmp_path / "failures").glob("*.json")
    assert "私密候選" in saved.read_text()
    assert "私密候選" not in (tmp_path / "luna-calls.jsonl").read_text()


@pytest.mark.parametrize("mode", ["invalid-json", "failed-cli"])
def test_material_failure_returns_candidate_for_existing_archive_only(provider_server, fake_codex, tmp_path, mode):
    metadata = tags("material_analysis", budget="material", stage="analysis")
    assert post(provider_server, "/semantics", semantics(metadata, mode=mode))[0] == 502
    receipt = post(provider_server, "/jobs/luna-result", {"tags": metadata, "outcome": "failed", "reason": "SEMANTIC_RESPONSE_INVALID"})[1]
    assert receipt["candidate_available"] and "私密候選" in receipt["candidates"][0]["candidate"]
    assert not receipt["saved"] and not (tmp_path / "failures").exists()
    assert "私密候選" not in (tmp_path / "luna-calls.jsonl").read_text()


@pytest.mark.parametrize("outcome", ["succeeded", "cancelled"])
def test_material_intermediate_error_is_discarded_at_nonfailed_terminal(provider_server, fake_codex, tmp_path, outcome):
    metadata = tags("material_analysis", budget="material", stage="analysis")
    assert post(provider_server, "/semantics", semantics(metadata, mode="invalid-json"))[0] == 502
    receipt = post(provider_server, "/jobs/luna-result", {"tags": metadata, "outcome": outcome, "reason": None})[1]
    assert receipt["candidate_available"] and not receipt["saved"] and "candidates" not in receipt
    assert not (tmp_path / "failures").exists()


def test_spawn_failure_does_not_consume_budget(provider_server, fake_codex, monkeypatch, tmp_path):
    original = os.environ["STUDYDY_CODEX_BIN"]
    monkeypatch.setenv("STUDYDY_CODEX_BIN", str(tmp_path / "missing"))
    metadata = tags(limit=1)
    assert post(provider_server, "/semantics", semantics(metadata))[0] == 502
    monkeypatch.setenv("STUDYDY_CODEX_BIN", original)
    assert post(provider_server, "/semantics", semantics(metadata))[0] == 200
    summary = report(tmp_path / "luna-calls.jsonl")[0]
    assert summary["calls"] == 1 and summary["blocked"] == 1


def test_missing_tags_remains_compatible_without_limit(provider_server, fake_codex, tmp_path):
    assert post(provider_server, "/semantics", semantics())[0] == 200
    assert post(provider_server, "/semantics", semantics())[0] == 200
    assert all(row["tags"] is None for row in call_records(tmp_path / "luna-calls.jsonl"))


@pytest.mark.parametrize("endpoint", ["/audio", "/align", "/transcribe", "/media-transcribe", "/scene-check", "/video"])
def test_tags_are_stripped_before_each_body_forwarding_path(provider_server, fake_codex, monkeypatch, endpoint):
    observed = []
    def generated(body, **kwargs):
        observed.append(body)
        return (b"RIFFsynthetic", None) if endpoint == "/audio" else {"synthetic": True}
    method = {"/audio": "audio", "/align": "align_audio", "/transcribe": "transcribe", "/media-transcribe": "media_transcribe"}.get(endpoint)
    payload = {"tags": tags("podcast_scenes"), "job_id": str(uuid4()), "job_token": str(uuid4())}
    if method:
        monkeypatch.setattr(provider, method, generated)
    elif endpoint == "/video":
        import video_service
        monkeypatch.setattr(video_service, "produce", generated)
    else:
        payload["items"] = [{"synthetic": True}]
    assert post(provider_server, endpoint, payload)[0] == 200
    if observed:
        assert "tags" not in observed[0]
    else:
        assert "tags" not in fake_codex.read_text()


def test_rebuild_counts_after_restart_and_new_attempt_gets_budget(tmp_path):
    path = tmp_path / "luna-calls.jsonl"
    metadata = tags(limit=1)
    store = CallLog(path)
    ticket = store.reserve(metadata)
    ticket.started()
    # 沒有 finished 的已啟動呼叫仍須計入額度，報表不能假成功。
    restored = CallLog(path)
    with pytest.raises(RuntimeError, match=LIMIT_EXCEEDED):
        restored.reserve(metadata)
    assert report(path)[0]["interrupted"] == 1
    retry = restored.reserve({**metadata, "attempt_id": "attempt-2"})
    retry.started()
    retry.finish()
    other = restored.reserve({**metadata, "budget": "research"})
    other.started()
    other.finish()
    assert sum(row["calls"] for row in report(path)) == 3


def test_budget_groups_share_counts_without_material_assignment_reset(tmp_path):
    store = CallLog(tmp_path / "luna-calls.jsonl")
    metadata = tags("assessment", budget="assessment", limit=2)
    for feature in ("assessment", "assessment_check"):
        ticket = store.reserve({**metadata, "feature": feature})
        ticket.started(); ticket.finish()
    with pytest.raises(RuntimeError, match=LIMIT_EXCEEDED):
        store.reserve({**metadata, "material_id": None})


def test_rotation_retains_ninety_days_and_failure_cleanup_thirty_days(tmp_path):
    clock = [datetime(2026, 10, 26, tzinfo=UTC)]
    path = tmp_path / "luna-calls.jsonl"
    store = CallLog(path, now=lambda: clock[0])
    first = store.reserve(tags()); first.started(); first.finish()
    clock[0] += timedelta(days=1)
    second = store.reserve(tags()); second.started(); second.finish()
    assert path.with_name(path.name + ".2026-10-26").exists()
    clock[0] += timedelta(days=89)
    store = CallLog(path, now=lambda: clock[0])
    assert not path.with_name(path.name + ".2026-10-26").exists()
    store.failure_dir.mkdir(mode=0o700)
    old, recent = store.failure_dir / "old.json", store.failure_dir / "recent.json"
    old.write_text("{}"); recent.write_text("{}")
    os.utime(old, (0, (clock[0] - timedelta(days=31)).timestamp()))
    os.utime(recent, (0, (clock[0] - timedelta(days=29)).timestamp()))
    store.cleanup()
    assert not old.exists() and recent.exists()


def test_report_cli_filters_and_counts_same_records_as_actual_cli(provider_server, fake_codex, tmp_path):
    metadata = tags(limit=1)
    assert post(provider_server, "/semantics", semantics(metadata))[0] == 200
    assert post(provider_server, "/semantics", semantics(metadata))[0] == 502
    path = tmp_path / "luna-calls.jsonl"
    command = [sys.executable, str(Path(provider.__file__).with_name("luna_report.py")), "--log", str(path),
               "--material", "material-1", "--feature", "note_summary", "--work", "work-1",
               "--date", datetime.now(UTC).date().isoformat()]
    output = json.loads(subprocess.check_output(command, text=True))
    assert output[0]["calls"] == len(fake_codex.read_text().splitlines()) == 1
    assert output[0]["blocked"] == 1 and output[0]["elapsed_seconds"] > 0
    assert report(path, material="different") == []


def test_failure_write_error_is_reported_and_keeps_candidate_for_retry(tmp_path, monkeypatch):
    store = CallLog(tmp_path / "luna-calls.jsonl")
    metadata = tags("podcast_script")
    with store.request(metadata, "/script"):
        provider.luna_call_log.capture_candidate({"synthetic": "private"})
    original = os.open
    def denied(path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            raise PermissionError("synthetic write failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(RuntimeError, match="LUNA_FAILURE_RECORD_FAILED"):
        store.finalize(metadata, "failed", "SYNTHETIC_FAILED")
    monkeypatch.setattr(os, "open", original)
    assert store.finalize(metadata, "failed", "SYNTHETIC_FAILED")["saved"]


def test_new_provider_server_restores_persisted_budget(provider_server, fake_codex, tmp_path):
    metadata = tags(limit=1)
    assert post(provider_server, "/semantics", semantics(metadata))[0] == 200
    # 新 server 沒有舊實例的記憶體計數，只能由同一呼叫紀錄恢復。
    server = ThreadingHTTPServer(("127.0.0.1", 0), provider.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert post(base, "/semantics", semantics(metadata)) == (502, {"error_code": LIMIT_EXCEEDED})
        assert post(base, "/semantics", semantics({**metadata, "attempt_id": "attempt-2"}))[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert len(fake_codex.read_text().splitlines()) == 2
    assert report(tmp_path / "luna-calls.jsonl")[0]["calls"] == 2
