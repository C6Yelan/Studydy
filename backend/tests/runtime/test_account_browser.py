"""以本地 API、真 PostgreSQL 與 production frontend 驗證帳號隔離；不啟動模型。"""

import runtime.api.app as api_app
from browser_e2e_runner import local_api, main as run_browser
from test_closed_loop_v1 import closed_loop


def test_account_browser_with_real_api_and_database(closed_loop, monkeypatch):
    learner, source, settings, structure, dsn, _ = closed_loop
    monkeypatch.setattr(api_app, "runtime_binding", lambda _: {})
    app = api_app.create_app(api_app.ApiSettings(
        profile="local", public_origin="http://127.0.0.1:4173",
        secure_cookie=False, local_config=settings, dsn=dsn,
    ))
    monkeypatch.setenv("STUDYDY_E2E_ACCOUNT_LEARNER", str(learner.learner_id))
    monkeypatch.setenv("STUDYDY_E2E_ACCOUNT_MATERIAL", str(source.material_id))
    monkeypatch.setenv("STUDYDY_E2E_ACCOUNT_RUN", structure["run_id"])
    monkeypatch.setenv("STUDYDY_E2E_ACCOUNT_REVISION", structure["revision"])
    monkeypatch.setenv("STUDYDY_E2E_ACCOUNT_ARTIFACT", str(source.artifact_id))
    with local_api(app):
        assert run_browser("e2e/accounts.spec.ts", production=True) == 0
