import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

import runtime.api.app as app_module
import runtime.material_processing as processing_module
import runtime.storage.material_review_outputs as output_module
from runtime.api.app import ApiSettings, canonical_openapi_bytes, create_app
from runtime.api.models import MaterialOutputBinding
from runtime.material_processing import (
    MaterialProcessingError,
    claim_next_material_processing_run,
    execute_claimed_material_processing_run,
)
from runtime.storage.migrations import run_migrations
from test_material_processing import _fake_knowledge_map, _fake_successful_producer, _pdf


class _Workers:
    def stop(self):
        return None


@pytest.fixture
def api_database_dsn(clean_database_dsn: str, migrations_dir: Path) -> str:
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
    )
    return clean_database_dsn


@pytest.fixture
def settings(
    api_database_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> ApiSettings:
    artifact_root = tmp_path / "private-artifacts"
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setattr(app_module, "start_runtime_workers", lambda **kwargs: _Workers())
    monkeypatch.setattr(
        app_module,
        "formal_runtime_preflight",
        processing_module.formal_runtime_binding,
    )
    monkeypatch.setattr(
        processing_module,
        "formal_runtime_preflight",
        processing_module.formal_runtime_binding,
    )
    monkeypatch.setattr(output_module, "generate_knowledge_map", _fake_knowledge_map)
    runtime_lock = json.loads(
        (Path(__file__).parents[3] / "local_ai" / "runtime-lock.json").read_text(
            encoding="utf-8"
        )
    )
    root = tmp_path / "local-runtime"
    runtime_settings = {
        "private_runtime_root": str(root / "runtime"),
        "runtime_lock": runtime_lock,
        "python_executable": str(root / "ocr/runtime/bin/python3.12"),
        "site_packages": str(root / "ocr/runtime/lib/python3.12/site-packages"),
        "concept_site_packages": str(root / "vllm/lib/python3.12/site-packages"),
        "ocr_model_root": str(root / "models/unlimited-ocr"),
        "relation_model_root": str(root / "models/mdeberta-v3-base-mnli-xnli"),
        "concept_api_base_url": "http://127.0.0.1:8101",
        "concept_model": runtime_lock["semantic"]["model_id"],
        "concept_server_executable": str(root / "vllm/bin/vllm"),
        "concept_model_root": str(root / "models/qwen3-14b-awq"),
        "concept_kv_cache_bytes": 2_147_483_648,
        "concept_max_concurrency": 1,
        "concept_max_model_len": 8_192,
    }
    return ApiSettings(
        profile="local",
        public_origin="http://127.0.0.1:4173",
        secure_cookie=False,
        local_config=runtime_settings,
        dsn=api_database_dsn,
    )


def _headers(key: str) -> dict[str, str]:
    return {"Origin": "http://127.0.0.1:4173", "Idempotency-Key": key}


def _material_and_run(client: TestClient):
    client.post("/v1/session", headers={"Origin": "http://127.0.0.1:4173"})
    material = client.post(
        "/v1/materials",
        content=_pdf(),
        headers={**_headers(f"upload-{uuid4()}"), "Content-Type": "application/pdf"},
    ).json()
    response = client.post(
        "/v1/material-processing-runs",
        headers=_headers(f"run-{uuid4()}"),
        json={
            "schema": "material-processing-create/v2",
            "material_id": material["material_id"],
            "source_artifact_id": material["source_artifact_id"],
        },
    )
    assert response.status_code == 202
    return material, response.json()


def test_api_settings_fail_closed_when_runtime_preflight_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def failed_preflight(_):
        raise RuntimeError("private diagnostic")

    monkeypatch.setattr(app_module, "formal_runtime_preflight", failed_preflight)
    with pytest.raises(ValueError, match="API_SETTINGS_INVALID"):
        ApiSettings(
            profile="local",
            public_origin="http://127.0.0.1:4173",
            secure_cookie=False,
            local_config={"private_runtime_root": str(tmp_path)},
        )


def test_api_settings_preserves_only_safe_runtime_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def failed_preflight(_):
        raise MaterialProcessingError(
            "MATERIAL_CONFIGURATION_INVALID",
            component="concept_runtime",
            reason="LOCAL_RUNTIME_VERSION_MISMATCH",
        )

    monkeypatch.setattr(app_module, "formal_runtime_preflight", failed_preflight)
    with pytest.raises(ValueError) as failure:
        ApiSettings(
            profile="local",
            public_origin="http://127.0.0.1:4173",
            secure_cookie=False,
            local_config={"private_runtime_root": str(tmp_path)},
        )

    assert str(failure.value) == "API_SETTINGS_INVALID"
    assert failure.value.component == "concept_runtime"
    assert failure.value.reason == "LOCAL_RUNTIME_VERSION_MISMATCH"


@pytest.mark.parametrize("profile", ["development", "production", "unknown"])
def test_api_settings_reject_non_local_profiles(profile, monkeypatch):
    monkeypatch.setattr(app_module, "formal_runtime_preflight", lambda _: {})
    with pytest.raises(ValueError, match="API_SETTINGS_INVALID"):
        ApiSettings(
            profile=profile,
            public_origin="http://127.0.0.1:4173",
            secure_cookie=False,
            local_config={},
        )


@pytest.mark.parametrize(
    ("profile", "public_origin", "secure_cookie"),
    [
        ("local", "http://127.0.0.1:4173", False),
        ("test", "https://studydy.test", True),
    ],
)
def test_api_settings_accept_local_and_test_profiles(
    profile, public_origin, secure_cookie, monkeypatch
):
    monkeypatch.setattr(app_module, "formal_runtime_preflight", lambda _: {})
    settings = ApiSettings(
        profile=profile,
        public_origin=public_origin,
        secure_cookie=secure_cookie,
        local_config={},
    )
    assert settings.profile == profile


def test_output_binding_accepts_multiple_concept_batches_per_page():
    binding = {
        "schema": "material-run-output-binding/v3",
        "producer_bundle_id": "text-first-producer-bundle:sha256:" + "1" * 64,
        "producer_run_id": "text-first-run:00000000-0000-4000-8000-000000000001",
        "concept_evidence_output_id": "concept-evidence-output:sha256:" + "2" * 64,
        "study_material_output_revision": "study-material-output:sha256:" + "3" * 64,
        "knowledge_map_revision": "knowledge-map:sha256:" + "4" * 64,
        "runtime_binding_sha256": "5" * 64,
        "page_count": 1,
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["CONTENT_REVIEW_REQUIRED"],
        "ocr_calls": 1,
        "concept_calls": 3,
    }

    assert MaterialOutputBinding.model_validate(binding).concept_calls == 3
    binding["concept_calls"] = -1
    with pytest.raises(ValueError):
        MaterialOutputBinding.model_validate(binding)


def test_create_and_poll_v2_rejects_caller_page_subset(settings: ApiSettings):
    with TestClient(create_app(settings)) as client:
        material, run = _material_and_run(client)
        assert run["schema"] == "material-processing-run/v2"
        assert run["status"] == "pending"
        assert run["output_binding"] is None
        assert client.get(f"/v1/material-processing-runs/{run['run_id']}").json() == run
        rejected = client.post(
            "/v1/material-processing-runs",
            headers=_headers("subset-rejected"),
            json={
                "schema": "material-processing-create/v2",
                "material_id": material["material_id"],
                "source_artifact_id": material["source_artifact_id"],
                "page_numbers": [1],
            },
        )
        assert rejected.status_code == 400
        assert rejected.json()["reason_code"] == "REQUEST_INVALID"


def test_success_exposes_only_review_map_with_pdf_locator(
    settings: ApiSettings, monkeypatch: pytest.MonkeyPatch
):
    with TestClient(create_app(settings)) as client:
        material, run = _material_and_run(client)
        claim = claim_next_material_processing_run(dsn=settings.dsn)
        assert claim is not None
        monkeypatch.setattr(
            processing_module, "run_full_text_first_pdf", _fake_successful_producer
        )
        completed = execute_claimed_material_processing_run(
            claim, settings.local_config, dsn=settings.dsn
        )
        response = client.get(f"/v1/material-processing-runs/{run['run_id']}")
        assert response.status_code == 200
        terminal = response.json()
        assert terminal["status"] == "succeeded"
        binding = terminal["output_binding"]
        map_response = client.get(
            f"/v1/materials/{material['material_id']}/knowledge-maps/{binding['knowledge_map_revision']}",
            params={"run_id": run["run_id"]},
        )
        assert map_response.status_code == 200
        view = map_response.json()
        assert view["schema"] == "knowledge-map-view/v6"
        assert view["status"]["decision"] == "review"
        assert view["concepts"][0]["claims"][0]["evidence"][0]["page_number"] == 1
        encoded = json.dumps(view)
        assert "Public evidence" not in encoded
        assert "runtime_binding" not in encoded
        assert "model_text" not in encoded


def test_openapi_has_no_deferred_downstream_routes(settings: ApiSettings):
    app = create_app(settings)
    encoded = canonical_openapi_bytes(app)
    fixture = Path(__file__).parent / "fixtures" / "openapi-v2.json"
    assert encoded == fixture.read_bytes()
    document = json.loads(encoded)
    paths = set(document["paths"])
    assert paths == {
        "/v1/session",
        "/v1/session/refresh",
        "/v1/materials",
        "/v1/material-processing-runs",
        "/v1/material-processing-runs/{run_id}",
        "/v1/materials/{material_id}/knowledge-maps/{map_revision}",
        "/v1/artifacts/{artifact_id}",
    }
    assert "HTTPValidationError" not in document["components"]["schemas"]
    assert "ValidationError" not in document["components"]["schemas"]
    assert all(
        schema.get("additionalProperties") is False
        for schema in document["components"]["schemas"].values()
        if schema.get("type") == "object"
    )
    schemas = document["components"]["schemas"]
    for schema_name, field_name in (
        ("MaterialOutputBinding", "page_count"),
        ("MaterialOutputBinding", "ocr_calls"),
        ("MaterialOutputBinding", "concept_calls"),
        ("EvidenceView", "page_number"),
        ("ExcludedPageView", "page_number"),
    ):
        assert "maximum" not in schemas[schema_name]["properties"][field_name]
    for field_name in ("concepts", "relations", "initial_learning_path", "excluded_pages"):
        assert "maxItems" not in schemas["KnowledgeMapView"]["properties"][field_name]
    assert "maxItems" not in schemas["FormalConceptView"]["properties"]["claims"]
    assert "maxLength" not in schemas["FormalConceptView"]["properties"]["label"]
    assert "maxItems" not in schemas["FormalClaimView"]["properties"]["evidence"]
    assert "maxLength" not in schemas["FormalClaimView"]["properties"]["text"]
    source_response = document["paths"]["/v1/artifacts/{artifact_id}"]["get"]["responses"]["200"]
    assert source_response["content"]["application/pdf"]["schema"] == {
        "type": "string",
        "format": "binary",
    }
    encoded_document = json.dumps(document)
    for deferred in (
        "learning-path",
        "assessment",
        "learning-state",
        "learning-resource-result",
    ):
        assert deferred not in encoded_document


def test_owner_scope_and_safe_fixed_errors(settings: ApiSettings):
    app = create_app(settings)
    with TestClient(app) as owner:
        _, run = _material_and_run(owner)
    with TestClient(app) as stranger:
        stranger.post("/v1/session", headers={"Origin": "http://127.0.0.1:4173"})
        response = stranger.get(f"/v1/material-processing-runs/{run['run_id']}")
        assert response.status_code == 404
        assert response.json()["reason_code"] == "RESOURCE_NOT_FOUND"
        assert response.json()["message"] == "Request could not be completed."
