from browser_e2e_runner import PORT
"""新瀏覽器從 server library 找回教材；所有 reopen 必須維持原資料與零模型呼叫。"""

import httpx

import runtime.api.app as api_app
from browser_e2e_runner import local_api, main as run_browser
from test_material_library import library_materials, product_snapshot
from test_closed_loop_v1 import closed_loop


def test_library_browser_reads_existing_records_without_generation(library_materials, monkeypatch):
    fixture = library_materials
    monkeypatch.setattr(api_app, "runtime_binding", lambda _: {})
    app = api_app.create_app(api_app.ApiSettings(
        profile="local", public_origin=f"http://127.0.0.1:{PORT}", secure_cookie=False,
        local_config=fixture["settings"], dsn=fixture["dsn"],
    ))
    writes = []
    read_paths = []
    model_calls = []

    @app.middleware("http")
    async def observe_requests(request, call_next):
        path = request.url.path
        if request.method == "GET":
            read_paths.append(path)
        elif not path.startswith("/v1/session"):
            writes.append(path)
        return await call_next(request)

    def reject_outgoing_http(*_args, **_kwargs):
        model_calls.append("unexpected-http")
        raise AssertionError("REOPEN_MUST_NOT_CALL_MODEL")

    # backend 的模型 HTTP 邊界禁止外呼；Browser→API 使用真 TCP，不經此 transport。
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_outgoing_http)
    monkeypatch.setenv("STUDYDY_E2E_LIBRARY", "true")
    before = product_snapshot(fixture["dsn"])
    with local_api(app):
        assert run_browser("e2e/material-library.spec.ts", production=True) == 0
    assert product_snapshot(fixture["dsn"]) == before
    assert writes == [] and model_calls == []
    material_id = fixture["first"].material_id
    for structure in (fixture["structure"], fixture["second_structure"]):
        assert f"/v1/materials/{material_id}/knowledge-structures/{structure['revision']}" in read_paths
        assert f"/v1/material-processing-runs/{structure['run_id']}" in read_paths
    assert f"/v1/artifacts/{fixture['first'].artifact_id}" in read_paths
