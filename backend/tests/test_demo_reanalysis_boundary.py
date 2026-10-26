"""外送前拒絕預算耗盡，不得把固定原因碼轉成可重試的連線錯誤。"""

import pytest

from runtime.local_ai_config import read_local_ai_config_from_environment
from runtime.semantic_service import SemanticServiceError, request_semantics


def test_exhausted_budget_never_reaches_provider(monkeypatch):
    import runtime.provider_http as provider_http
    lock = read_local_ai_config_from_environment(
        {"STUDYDY_TEXT_TEST_PROVIDER": "codex-exec-luna"})["runtime_lock"]
    sent = []
    monkeypatch.setattr(provider_http, "provider", lambda *args: sent.append(args))

    def exhausted():
        raise SemanticServiceError("MATERIAL_MODEL_BUDGET_EXHAUSTED")

    with pytest.raises(SemanticServiceError, match="MATERIAL_MODEL_BUDGET_EXHAUSTED"):
        request_semantics(None, runtime_lock=lock, task="material_review",
            request={}, response_schema={}, before_request=exhausted)
    assert sent == []


def test_cli_bootstraps_container_database_before_status(monkeypatch, capsys):
    from psycopg.conninfo import conninfo_to_dict
    import os
    import runtime.maintenance_reanalysis as maintenance
    monkeypatch.setenv("POSTGRES_DB", "studydy_test")
    monkeypatch.setenv("POSTGRES_USER", "synthetic_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "synthetic_test_only")
    monkeypatch.delenv("STUDYDY_DATABASE_DSN", raising=False)
    monkeypatch.setattr("sys.argv", ["reanalyze-demo-material"])

    def status():
        fields = conninfo_to_dict(os.environ["STUDYDY_DATABASE_DSN"])
        assert fields["host"] == "postgres"
        assert fields["dbname"] == "studydy_test"
        return {"state": "not_started"}

    monkeypatch.setattr(maintenance, "status", status)
    monkeypatch.setattr(maintenance, "enqueue", lambda *_: pytest.fail("status enqueued work"))
    maintenance.main()
    assert capsys.readouterr().out.strip() == '{"state": "not_started"}'


def test_cli_resume_flag_does_not_enqueue(monkeypatch, capsys):
    import runtime.maintenance_reanalysis as maintenance
    monkeypatch.setattr('sys.argv', ['reanalyze-demo-material', '--resume-once'])
    monkeypatch.setattr('runtime.storage.database.configure_database', lambda _: None)
    config = {'runtime_lock': {}}
    monkeypatch.setattr('runtime.local_ai_config.read_local_ai_config_from_environment', lambda _: config)
    calls = []
    monkeypatch.setattr(maintenance, 'resume_once', lambda value: calls.append(value))
    monkeypatch.setattr(maintenance, 'enqueue', lambda *_: pytest.fail('resume enqueued new work'))
    monkeypatch.setattr(maintenance, 'status', lambda: {'state': 'pending'})
    maintenance.main()
    assert calls == [config]
    assert capsys.readouterr().out.strip() == '{"state": "pending"}'
