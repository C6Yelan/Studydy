import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pymupdf
import psycopg
import pytest

import runtime.api.app as app_module
import runtime.material_processing as processing_module
import runtime.storage.material_review_outputs as output_module
import learning_adaptation.assessment_requests as assessment_request_module
from learning_adaptation.assessment_items import (
    build_single_choice_assessment,
    store_assessment,
    used_question_ids,
)
from learning_adaptation.map_context import read_map_context
from learning_adaptation.study_sessions import read_study_session
from runtime.api.app import ApiSettings, canonical_openapi_bytes, create_app
from runtime.api.models import MaterialOutputBinding
from runtime.learner_session import resolve_session
from runtime.material_processing import (
    MaterialProcessingError,
    claim_next_material_processing_run,
    execute_claimed_material_processing_run,
)
from runtime.storage.migrations import run_migrations
from runtime.storage.artifacts import ArtifactError
from test_material_processing import _fake_knowledge_map, _fake_successful_producer, _pdf
from test_study_sessions import _insert_material_map, _knowledge_map


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
        10,
        11,
        12,
        13,
        14,
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


def _encrypted_pdf() -> bytes:
    document = pymupdf.open()
    document.new_page()
    content = document.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="user-password",
    )
    document.close()
    return content


def _assert_no_material_residue(settings: ApiSettings, artifact_root: Path) -> None:
    with psycopg.connect(settings.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM materials").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM artifacts").fetchone() == (0,)
    if artifact_root.exists():
        assert list((artifact_root / "objects").iterdir()) == []
        assert list((artifact_root / ".staging").iterdir()) == []


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
        assert run["schema"] == "material-processing-run/v3"
        assert run["status"] == "pending"
        assert (run["progress_stage"], run["completed_pages"], run["total_pages"]) == (
            "queued",
            0,
            None,
        )
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


@pytest.mark.parametrize("content", [b"not a pdf", _encrypted_pdf()])
def test_corrupt_and_encrypted_pdf_have_specific_safe_error(
    settings: ApiSettings,
    content: bytes,
):
    with TestClient(create_app(settings)) as client:
        client.post("/v1/session", headers={"Origin": settings.public_origin})
        response = client.post(
            "/v1/materials",
            content=content,
            headers={**_headers(f"invalid-{uuid4()}"), "Content-Type": "application/pdf"},
        )
        assert response.status_code == 400
        assert response.json()["reason_code"] == "MATERIAL_PDF_INVALID"


def test_zero_byte_pdf_is_invalid_without_material_or_storage_residue(
    settings: ApiSettings,
    tmp_path: Path,
):
    with TestClient(create_app(settings)) as client:
        client.post("/v1/session", headers={"Origin": settings.public_origin})
        response = client.post(
            "/v1/materials",
            content=b"",
            headers={**_headers("zero-byte"), "Content-Type": "application/pdf"},
        )

    assert response.status_code == 400
    assert response.json()["reason_code"] == "MATERIAL_PDF_INVALID"
    _assert_no_material_residue(settings, tmp_path / "private-artifacts")


def test_streaming_oversize_pdf_stops_before_publish_without_residue(
    settings: ApiSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    publish_was_called = False

    def unexpected_publish(*_args, **_kwargs):
        nonlocal publish_was_called
        publish_was_called = True
        raise AssertionError("oversize request reached artifact publishing")

    def oversize_pdf_chunks():
        first_chunk = b"%PDF-1.7\n" + b"x" * (1024 * 1024 - len(b"%PDF-1.7\n"))
        full_chunk = b"x" * (1024 * 1024)
        yield first_chunk
        for _ in range(99):
            yield full_chunk
        yield b"x"

    monkeypatch.setattr(
        app_module, "publish_idempotent_source_pdf", unexpected_publish
    )
    with TestClient(create_app(settings)) as client:
        client.post("/v1/session", headers={"Origin": settings.public_origin})
        response = client.post(
            "/v1/materials",
            content=oversize_pdf_chunks(),
            headers={**_headers("oversize"), "Content-Type": "application/pdf"},
        )

    assert response.status_code == 413
    assert response.json()["reason_code"] == "MATERIAL_TOO_LARGE"
    assert publish_was_called is False
    _assert_no_material_residue(settings, tmp_path / "private-artifacts")


def test_material_storage_failure_remains_distinct_from_invalid_pdf(
    settings: ApiSettings,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_storage(*_args, **_kwargs):
        raise ArtifactError("ARTIFACT_PUBLISH_FAILED")

    monkeypatch.setattr(app_module, "publish_idempotent_source_pdf", fail_storage)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/session", headers={"Origin": settings.public_origin})
        response = client.post(
            "/v1/materials",
            content=_pdf(),
            headers={**_headers("storage-failure"), "Content-Type": "application/pdf"},
        )
        assert response.status_code == 503
        assert response.json()["reason_code"] == "STORAGE_UNAVAILABLE"


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
        assert (
            terminal["progress_stage"],
            terminal["completed_pages"],
            terminal["total_pages"],
        ) == ("completed", binding["page_count"], binding["page_count"])
        map_response = client.get(
            f"/v1/materials/{material['material_id']}/knowledge-maps/{binding['knowledge_map_revision']}",
            params={"run_id": run["run_id"]},
        )
        assert map_response.status_code == 200
        view = map_response.json()
        assert view["schema"] == "knowledge-map-view/v9"
        assert view["status"]["decision"] == "review"
        assert view["concepts"][0]["claims"][0]["evidence"][0]["page_number"] == 1
        encoded = json.dumps(view)
        assert "Public evidence" not in encoded
        assert "runtime_binding" not in encoded
        assert "model_text" not in encoded


def test_openapi_has_phase_06_learning_routes_without_private_fields(
    settings: ApiSettings,
):
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
        "/v1/study-sessions",
        "/v1/study-sessions/{study_session_id}",
        "/v1/study-sessions/{study_session_id}/complete",
        "/v1/study-sessions/{study_session_id}/context",
        "/v1/study-sessions/{study_session_id}/assessments",
        "/v1/study-sessions/{study_session_id}/assessments/{assessment_revision}",
        "/v1/study-sessions/{study_session_id}/assessments/{assessment_revision}/submissions",
        "/v1/study-sessions/{study_session_id}/learning-state",
        "/v1/study-sessions/{study_session_id}/weakness",
        "/v1/study-sessions/{study_session_id}/adaptive-plan",
        "/v1/study-sessions/{study_session_id}/adaptive-plan/apply",
    }
    assert "HTTPValidationError" not in document["components"]["schemas"]
    assert "ValidationError" not in document["components"]["schemas"]
    assert all(
        schema.get("additionalProperties") is False
        for schema in document["components"]["schemas"].values()
        if schema.get("type") == "object"
    )
    schemas = document["components"]["schemas"]
    run_schema = schemas["MaterialProcessingRunView"]
    assert run_schema["properties"]["schema"]["const"] == "material-processing-run/v3"
    assert {"progress_stage", "completed_pages", "total_pages"} <= set(
        run_schema["properties"]
    )
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
    for private_field in (
        "correct_option_id",
        "private_answer",
        "private_answer_sha256",
        "generation_provenance",
        "supporting_answer_event_ids",
        "source_answer_event_ids",
        "entailment",
    ):
        assert private_field not in encoded_document


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


def _learning_material(client: TestClient, settings: ApiSettings):
    assert client.post(
        "/v1/session", headers={"Origin": settings.public_origin}
    ).status_code == 204
    learner = resolve_session(
        client.cookies.get("studydy_session"), dsn=settings.dsn
    )
    assert learner is not None
    knowledge_map = _knowledge_map()
    material_id = _insert_material_map(
        settings.dsn, learner.learner_id, knowledge_map
    )
    return learner, knowledge_map, material_id


def _fake_assessment_generation(
    learner,
    study_session_id,
    target_claim_id,
    _local_config,
    *,
    dsn,
):
    study_session = read_study_session(learner, study_session_id, dsn=dsn)
    context = read_map_context(
        learner.learner_id,
        study_session.material_id,
        study_session.knowledge_map_revision,
        dsn=dsn,
    )
    concept = next(
        concept
        for concept in context.formal_concepts
        if concept.formal_concept_id
        == study_session.current_formal_concept_id
    )
    claim = next(
        claim for claim in concept.claims if claim.claim_id == target_claim_id
    )
    documents = build_single_choice_assessment(
        study_session_id=study_session_id,
        knowledge_map_revision=study_session.knowledge_map_revision,
        target_formal_concept_id=concept.formal_concept_id,
        target_claim_id=claim.claim_id,
        source_evidence_ids=[claim.evidence[0].evidence_id],
        prompt="Which option matches the selected Evidence?",
        option_texts=[
            "Grounded answer",
            "First distractor",
            "Second distractor",
            "Third distractor",
        ],
        correct_option_index=0,
        rationale="The selected Evidence supports the grounded answer.",
    )
    return store_assessment(
        learner,
        documents.public_document,
        documents.private_answer_document,
        dsn=dsn,
    )


def test_learning_api_closed_public_wiring_and_safe_feedback(
    settings: ApiSettings, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        assessment_request_module,
        "generate_and_store_assessment",
        _fake_assessment_generation,
    )
    with TestClient(create_app(settings)) as client:
        _, knowledge_map, material_id = _learning_material(client, settings)
        target = knowledge_map["formal_concepts"][1]
        created = client.post(
            "/v1/study-sessions",
            headers=_headers("study-session"),
            json={
                "schema": "study-session-create/v1",
                "material_id": str(material_id),
                "knowledge_map_revision": knowledge_map["revision"],
                "current_formal_concept_id": target["formal_concept_id"],
            },
        )
        assert created.status_code == 201
        study_session = created.json()
        session_id = study_session["study_session_id"]
        assert client.get(f"/v1/study-sessions/{session_id}").json() == study_session

        context = client.get(
            f"/v1/study-sessions/{session_id}/context"
        ).json()
        assert context["base_knowledge_map_revision"] == knowledge_map["revision"]
        assert [
            item["formal_concept_id"] for item in context["initial_learning_path"]
        ] == [
            step["formal_concept_id"]
            for step in knowledge_map["initial_learning_path"]
        ]

        assessment_request = {
            "schema": "assessment-create/v1",
            "target_claim_id": target["claims"][0]["claim_id"],
        }
        assessment_url = f"/v1/study-sessions/{session_id}/assessments"
        first = client.post(
            assessment_url,
            headers=_headers("assessment-request"),
            json=assessment_request,
        )
        replay = client.post(
            assessment_url,
            headers=_headers("assessment-request"),
            json=assessment_request,
        )
        assert first.status_code == replay.status_code == 201
        assert first.json() == replay.json()
        assessment = first.json()
        encoded_assessment = json.dumps(assessment)
        assert len(assessment["options"]) == 4
        assert "correct_option" not in encoded_assessment
        assert "rationale" not in encoded_assessment
        assert "generation_provenance" not in encoded_assessment
        assert client.get(
            f"{assessment_url}/{assessment['assessment_revision']}"
        ).json() == assessment

        submission_url = (
            f"{assessment_url}/{assessment['assessment_revision']}/submissions"
        )
        feedback = client.post(
            submission_url,
            headers=_headers("answer-request"),
            json={
                "schema": "answer-submission-create/v1",
                "question_id": assessment["question_id"],
                "selected_option_id": assessment["options"][1]["option_id"],
            },
        )
        assert feedback.status_code == 201
        feedback_document = feedback.json()
        assert feedback_document["is_correct"] is False
        assert "correct_option_id" not in json.dumps(feedback_document)
        assert client.post(
            submission_url,
            headers=_headers("invalid-extra"),
            json={
                "schema": "answer-submission-create/v1",
                "question_id": assessment["question_id"],
                "selected_option_id": assessment["options"][1]["option_id"],
                "correctness": True,
            },
        ).status_code == 400

        state = client.get(
            f"/v1/study-sessions/{session_id}/learning-state"
        ).json()
        weakness = client.get(
            f"/v1/study-sessions/{session_id}/weakness"
        ).json()
        adaptive = client.get(
            f"/v1/study-sessions/{session_id}/adaptive-plan"
        ).json()
        assert state["concept_states"][1]["status"] == "needs_review"
        assert "source_answer_event_ids" not in json.dumps(state)
        assert "supporting_answer_event_ids" not in json.dumps(weakness)
        assert adaptive["plan"]["primary_step"]["action"] == "relearn_prerequisite"
        assert adaptive["suggestion"]["action"] == "relearn_prerequisite"
        applied = client.post(
            f"/v1/study-sessions/{session_id}/adaptive-plan/apply",
            headers={"Origin": settings.public_origin},
            json={
                "schema": "adaptive-plan-apply/v1",
                "adaptive_plan_revision": adaptive["plan"]["adaptive_plan_revision"],
            },
        )
        assert applied.status_code == 200
        assert applied.json()["deferred_formal_concept_id"] == target[
            "formal_concept_id"
        ]
        assert client.post(
            f"/v1/study-sessions/{session_id}/complete",
            headers={"Origin": settings.public_origin},
        ).json()["status"] == "completed"


def test_learning_api_owner_and_assessment_idempotency_conflict(
    settings: ApiSettings, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        assessment_request_module,
        "generate_and_store_assessment",
        _fake_assessment_generation,
    )
    app = create_app(settings)
    with TestClient(app) as owner:
        _, knowledge_map, material_id = _learning_material(owner, settings)
        target = knowledge_map["formal_concepts"][0]
        session = owner.post(
            "/v1/study-sessions",
            headers=_headers("owner-study"),
            json={
                "schema": "study-session-create/v1",
                "material_id": str(material_id),
                "knowledge_map_revision": knowledge_map["revision"],
                "current_formal_concept_id": target["formal_concept_id"],
            },
        ).json()
        session_id = session["study_session_id"]
        first = owner.post(
            f"/v1/study-sessions/{session_id}/assessments",
            headers=_headers("same-key"),
            json={
                "schema": "assessment-create/v1",
                "target_claim_id": target["claims"][0]["claim_id"],
            },
        )
        assert first.status_code == 201
        conflict = owner.post(
            f"/v1/study-sessions/{session_id}/assessments",
            headers=_headers("same-key"),
            json={
                "schema": "assessment-create/v1",
                "target_claim_id": knowledge_map["formal_concepts"][1][
                    "claims"
                ][0]["claim_id"],
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["reason_code"] == "IDEMPOTENCY_CONFLICT"
    with TestClient(app) as stranger:
        stranger.post("/v1/session", headers={"Origin": settings.public_origin})
        unavailable = stranger.get(f"/v1/study-sessions/{session_id}")
        assert unavailable.status_code == 404
        assert unavailable.json()["reason_code"] == "RESOURCE_NOT_FOUND"


def _sequenced_assessment_generation(
    learner,
    study_session_id,
    target_claim_id,
    _local_config,
    *,
    dsn,
):
    study_session = read_study_session(learner, study_session_id, dsn=dsn)
    context = read_map_context(
        learner.learner_id,
        study_session.material_id,
        study_session.knowledge_map_revision,
        dsn=dsn,
    )
    concept = next(
        concept
        for concept in context.formal_concepts
        if concept.formal_concept_id
        == study_session.current_formal_concept_id
    )
    claim = next(
        claim for claim in concept.claims if claim.claim_id == target_claim_id
    )
    sequence = len(
        used_question_ids(
            learner, study_session_id, target_claim_id, dsn=dsn
        )
    ) + 1
    documents = build_single_choice_assessment(
        study_session_id=study_session_id,
        knowledge_map_revision=study_session.knowledge_map_revision,
        target_formal_concept_id=concept.formal_concept_id,
        target_claim_id=claim.claim_id,
        source_evidence_ids=[claim.evidence[0].evidence_id],
        prompt=f"Safe reassessment item {sequence} for {concept.label}?",
        option_texts=[
            f"Grounded answer {sequence}",
            f"First distractor {sequence}",
            f"Second distractor {sequence}",
            f"Third distractor {sequence}",
        ],
        correct_option_index=0,
        rationale="The selected canonical Evidence supports this answer.",
    )
    return store_assessment(
        learner,
        documents.public_document,
        documents.private_answer_document,
        require_new=True,
        dsn=dsn,
    )


def _api_assessment(
    client: TestClient,
    session_id: str,
    claim_id: str,
    key: str,
) -> dict:
    response = client.post(
        f"/v1/study-sessions/{session_id}/assessments",
        headers=_headers(key),
        json={
            "schema": "assessment-create/v1",
            "target_claim_id": claim_id,
        },
    )
    assert response.status_code == 201
    return response.json()


def _api_answer(
    client: TestClient,
    session_id: str,
    assessment: dict,
    *,
    correct: bool,
    key: str,
) -> dict:
    option_index = 0 if correct else 1
    response = client.post(
        f"/v1/study-sessions/{session_id}/assessments/"
        f"{assessment['assessment_revision']}/submissions",
        headers=_headers(key),
        json={
            "schema": "answer-submission-create/v1",
            "question_id": assessment["question_id"],
            "selected_option_id": assessment["options"][option_index][
                "option_id"
            ],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_phase_06_public_api_closed_loop_matches_golden(
    settings: ApiSettings, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        assessment_request_module,
        "generate_and_store_assessment",
        _sequenced_assessment_generation,
    )
    golden = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "learning-loop-golden-v1.json"
        ).read_text(encoding="utf-8")
    )
    expected = {item["name"]: item for item in golden["checkpoints"]}
    with TestClient(create_app(settings)) as client:
        _, knowledge_map, material_id = _learning_material(client, settings)
        prerequisite, target, next_concept = knowledge_map["formal_concepts"]
        created = client.post(
            "/v1/study-sessions",
            headers=_headers("closed-loop-session"),
            json={
                "schema": "study-session-create/v1",
                "material_id": str(material_id),
                "knowledge_map_revision": knowledge_map["revision"],
                "current_formal_concept_id": target["formal_concept_id"],
            },
        ).json()
        session_id = created["study_session_id"]
        target_claim_id = target["claims"][0]["claim_id"]

        target_items = []
        for sequence in (1, 2):
            assessment = _api_assessment(
                client,
                session_id,
                target_claim_id,
                f"target-wrong-item-{sequence}",
            )
            target_items.append(assessment["question_id"])
            feedback = _api_answer(
                client,
                session_id,
                assessment,
                correct=False,
                key=f"target-wrong-answer-{sequence}",
            )
            if sequence == 1:
                assert _api_answer(
                    client,
                    session_id,
                    assessment,
                    correct=False,
                    key=f"target-wrong-answer-{sequence}",
                ) == feedback
        assert len(set(target_items)) == 2
        state_after_errors = client.get(
            f"/v1/study-sessions/{session_id}/learning-state"
        ).json()
        weakness_after_errors = client.get(
            f"/v1/study-sessions/{session_id}/weakness"
        ).json()
        adaptive_after_errors = client.get(
            f"/v1/study-sessions/{session_id}/adaptive-plan"
        ).json()
        target_state = next(
            item
            for item in state_after_errors["concept_states"]
            if item["formal_concept_id"] == target["formal_concept_id"]
        )
        target_weakness = next(
            item
            for item in weakness_after_errors["findings"]
            if item["target_formal_concept_id"]
            == target["formal_concept_id"]
        )
        checkpoint = expected["repeated_target_errors"]
        assert target_state["status"] == checkpoint["target_status"]
        assert target_weakness["category"] == checkpoint["weakness_category"]
        assert adaptive_after_errors["plan"]["primary_step"]["action"] == checkpoint["adaptive_action"]
        assert client.post(
            f"/v1/study-sessions/{session_id}/adaptive-plan/apply",
            headers={"Origin": settings.public_origin},
            json={
                "schema": "adaptive-plan-apply/v1",
                "adaptive_plan_revision": adaptive_after_errors["plan"][
                    "adaptive_plan_revision"
                ],
            },
        ).status_code == 200

        prerequisite_items = []
        for sequence in (1, 2):
            assessment = _api_assessment(
                client,
                session_id,
                prerequisite["claims"][0]["claim_id"],
                f"prerequisite-item-{sequence}",
            )
            prerequisite_items.append(assessment["question_id"])
            _api_answer(
                client,
                session_id,
                assessment,
                correct=True,
                key=f"prerequisite-answer-{sequence}",
            )
        assert len(set(prerequisite_items)) == 2
        state_after_prerequisite = client.get(
            f"/v1/study-sessions/{session_id}/learning-state"
        ).json()
        return_adaptive = client.get(
            f"/v1/study-sessions/{session_id}/adaptive-plan"
        ).json()
        prerequisite_state = next(
            item
            for item in state_after_prerequisite["concept_states"]
            if item["formal_concept_id"] == prerequisite["formal_concept_id"]
        )
        checkpoint = expected["prerequisite_mastered"]
        assert prerequisite_state["status"] == checkpoint["prerequisite_status"]
        assert return_adaptive["plan"]["primary_step"]["action"] == checkpoint["adaptive_action"]
        returned = client.post(
            f"/v1/study-sessions/{session_id}/adaptive-plan/apply",
            headers={"Origin": settings.public_origin},
            json={
                "schema": "adaptive-plan-apply/v1",
                "adaptive_plan_revision": return_adaptive["plan"][
                    "adaptive_plan_revision"
                ],
            },
        ).json()
        assert returned["current_formal_concept_id"] == target[
            "formal_concept_id"
        ]
        assert returned["deferred_formal_concept_id"] is None

        reassessment_ids = []
        reassessments = []
        for sequence in (3, 4):
            assessment = _api_assessment(
                client,
                session_id,
                target_claim_id,
                f"target-correct-item-{sequence}",
            )
            reassessments.append(assessment)
            reassessment_ids.append(assessment["question_id"])
            _api_answer(
                client,
                session_id,
                assessment,
                correct=True,
                key=f"target-correct-answer-{sequence}",
            )
        assert not set(reassessment_ids) & set(target_items)
        final_state = client.get(
            f"/v1/study-sessions/{session_id}/learning-state"
        ).json()
        final_adaptive = client.get(
            f"/v1/study-sessions/{session_id}/adaptive-plan"
        ).json()
        target_state = next(
            item
            for item in final_state["concept_states"]
            if item["formal_concept_id"] == target["formal_concept_id"]
        )
        checkpoint = expected["target_reassessment_mastered"]
        assert target_state["status"] == checkpoint["target_status"]
        assert final_adaptive["plan"]["primary_step"]["action"] == checkpoint["adaptive_action"]
        assert final_adaptive["plan"]["primary_step"][
            "target_formal_concept_id"
        ] == next_concept["formal_concept_id"]
        assert final_state["event_watermark"] == 6
        assert final_state["state_revision"] != state_after_errors[
            "state_revision"
        ]
        stale = client.post(
            f"/v1/study-sessions/{session_id}/adaptive-plan/apply",
            headers={"Origin": settings.public_origin},
            json={
                "schema": "adaptive-plan-apply/v1",
                "adaptive_plan_revision": adaptive_after_errors["plan"][
                    "adaptive_plan_revision"
                ],
            },
        )
        assert stale.status_code == 409

        second_session = client.post(
            "/v1/study-sessions",
            headers=_headers("isolated-session"),
            json={
                "schema": "study-session-create/v1",
                "material_id": str(material_id),
                "knowledge_map_revision": knowledge_map["revision"],
                "current_formal_concept_id": target["formal_concept_id"],
            },
        ).json()
        isolated_state = client.get(
            f"/v1/study-sessions/{second_session['study_session_id']}/learning-state"
        ).json()
        isolated_weakness = client.get(
            f"/v1/study-sessions/{second_session['study_session_id']}/weakness"
        ).json()
        checkpoint = expected["new_session_isolated"]
        isolated_target = next(
            item
            for item in isolated_state["concept_states"]
            if item["formal_concept_id"] == target["formal_concept_id"]
        )
        assert isolated_state["event_watermark"] == checkpoint[
            "event_watermark"
        ]
        assert isolated_target["status"] == checkpoint["target_status"]
        assert len(isolated_weakness["findings"]) == checkpoint[
            "weakness_findings"
        ]

        cross_session = client.post(
            f"/v1/study-sessions/{second_session['study_session_id']}/assessments/"
            f"{reassessments[0]['assessment_revision']}/submissions",
            headers=_headers("cross-session-answer"),
            json={
                "schema": "answer-submission-create/v1",
                "question_id": reassessments[0]["question_id"],
                "selected_option_id": "option:sha256:" + "9" * 64,
            },
        )
        assert cross_session.status_code == 404
