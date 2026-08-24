from copy import deepcopy
import runpy
import sys

import pytest
import runtime.api.app as app_module
import runtime.local_app as local_app
import uvicorn


def _environment(profile="local", runtime_root="/temporary/studydy"):
    return {
        "STUDYDY_PROFILE": profile,
        "STUDYDY_PUBLIC_ORIGIN": "http://127.0.0.1:4173",
        "STUDYDY_SECURE_COOKIE": "false",
        "STUDYDY_LOCAL_RUNTIME_ROOT": runtime_root,
    }


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


def test_formal_launch_uses_the_local_composition_root(monkeypatch):
    observed = []
    app = object()
    monkeypatch.setattr(
        local_app,
        "create_local_app",
        lambda **arguments: observed.append(deepcopy(arguments)) or app,
    )
    monkeypatch.setattr(
        local_app.uvicorn,
        "run",
        lambda created_app, **arguments: observed.append((created_app, arguments)),
    )

    local_app.run_local_app(environment=_environment(), port=8183)

    assert observed[0]["profile"] == "local"
    assert observed[0]["dsn"] is None
    assert set(observed[0]["local_config"]) == {
        "private_runtime_root",
        "runtime_lock",
        "python_executable",
        "site_packages",
        "concept_site_packages",
        "ocr_model_root",
        "relation_model_root",
        "concept_api_base_url",
        "concept_model",
        "concept_server_executable",
        "concept_model_root",
        "concept_kv_cache_bytes",
        "concept_max_concurrency",
        "concept_max_model_len",
    }
    assert observed[0]["local_config"]["concept_kv_cache_bytes"] == 2_147_483_648
    assert observed[0]["local_config"]["concept_max_concurrency"] == 1
    assert observed[0]["local_config"]["concept_max_model_len"] == 8_192
    assert observed[0]["local_config"]["python_executable"] == (
        "/temporary/studydy/ocr/runtime/bin/python3.12"
    )
    assert observed[0]["local_config"]["relation_model_root"] == (
        "/temporary/studydy/models/mdeberta-v3-base-mnli-xnli"
    )
    assert observed[1] == (
        app,
        {
            "host": "127.0.0.1",
            "port": 8183,
            "log_config": None,
            "access_log": False,
        },
    )


def test_cli_reader_returns_only_existing_local_ai_arguments():
    environment = _environment()
    environment["STUDYDY_PUBLIC_ORIGIN"] = "private-app-value"

    local_config = local_app.read_local_ai_config_from_environment(environment)

    assert set(local_config) == {
        "private_runtime_root",
        "runtime_lock",
        "python_executable",
        "site_packages",
        "concept_site_packages",
        "ocr_model_root",
        "relation_model_root",
        "concept_api_base_url",
        "concept_model",
        "concept_server_executable",
        "concept_model_root",
        "concept_kv_cache_bytes",
        "concept_max_concurrency",
        "concept_max_model_len",
    }
    assert "public_origin" not in local_config
    assert local_config["concept_max_concurrency"] == 1


def test_default_root_and_only_three_tunings_are_environment_configurable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_app.Path, "home", lambda: tmp_path)
    environment = {
        "STUDYDY_CONCEPT_KV_CACHE_BYTES": "1024",
        "STUDYDY_CONCEPT_MAX_CONCURRENCY": "1",
        "STUDYDY_CONCEPT_MAX_MODEL_LEN": "5632",
    }

    local_config = local_app.read_local_ai_config_from_environment(environment)

    root = tmp_path / ".local" / "share" / "studydy"
    assert local_config["python_executable"] == str(
        root / "ocr" / "runtime" / "bin" / "python3.12"
    )
    assert local_config["site_packages"] == str(
        root / "ocr" / "runtime" / "lib" / "python3.12" / "site-packages"
    )
    assert local_config["concept_model"] == local_config["runtime_lock"][
        "semantic"
    ]["model_id"]
    assert local_config["concept_kv_cache_bytes"] == 1024
    assert local_config["concept_max_concurrency"] == 1


@pytest.mark.parametrize("profile", ["development", "production", "unknown"])
def test_formal_launch_rejects_non_local_profile(profile):
    with pytest.raises(ValueError, match="LOCAL_APP_SETTINGS_INVALID"):
        local_app.run_local_app(environment=_environment(profile))


def test_module_entry_invokes_uvicorn(monkeypatch):
    observed = []
    monkeypatch.setattr(app_module, "formal_runtime_preflight", lambda _: {})
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda app, **arguments: observed.append((app, arguments)),
    )
    for name, value in _environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delitem(sys.modules, "runtime.local_app")

    runpy.run_module("runtime.local_app", run_name="__main__")

    assert len(observed) == 1
    assert observed[0][1]["host"] == "127.0.0.1"
    assert observed[0][1]["port"] == 8000
