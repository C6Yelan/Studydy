from copy import deepcopy

import runtime.local_app as local_app


def test_local_composition_builds_settings_before_app(monkeypatch):
    observed = []
    marker = object()

    class Settings:
        def __init__(self, **values):
            observed.append(deepcopy(values))

    monkeypatch.setattr(local_app, "ApiSettings", Settings)
    monkeypatch.setattr(local_app, "create_app", lambda settings: marker)
    local_config = {"private_runtime_root": "/tmp/studydy-runtime"}
    assert local_app.create_local_app(
        profile="test",
        public_origin="https://studydy.test",
        secure_cookie=True,
        local_config=local_config,
        dsn="test-database-location",
    ) is marker
    local_config["private_runtime_root"] = "changed"
    assert observed[0]["local_config"]["private_runtime_root"] == "/tmp/studydy-runtime"
