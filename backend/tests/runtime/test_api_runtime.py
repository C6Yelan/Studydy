from __future__ import annotations

import io
from pathlib import Path
import socket
import sys
from threading import Event, Lock, Thread
import time
from uuid import uuid4

import httpx
import pymupdf
import psycopg
import pytest
import uvicorn

from runtime.api import ApiSettings, create_app
from runtime.material_processing import ControlledResourceUpload
import runtime.workers as worker_module
from runtime.workers import start_runtime_workers
from runtime.storage.migrations import run_migrations

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_pipeline_run import _Loopback, _simple_page_body


def _config(tmp_path: Path, endpoint: str = "http://127.0.0.1:1") -> dict:
    return {
        "endpoint_url": endpoint,
        "cache_dir": str(tmp_path / "cache"),
        "deadline_seconds": 10,
        "max_attempts": 2,
        "retry_backoff_seconds": 0,
        "model_id": "local-development-model",
        "model_revision": "revision-1",
        "model_artifact_sha256": "c" * 64,
        "projector_sha256": "d" * 64,
        "runtime_id": "local-runtime-1",
        "processing_policy_version": "development-generation-policy/v1",
    }


def _pdf(text: str = "Native topic") -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=420, height=600)
    page.insert_text((40, 60), "Native topic 1", fontsize=16)
    page.insert_text((40, 110), text, fontsize=11)
    page.draw_rect(pymupdf.Rect(35, 140, 380, 540))
    content = document.tobytes()
    document.close()
    return content


@pytest.fixture
def api_database_dsn(clean_database_dsn: str, migrations_dir: Path) -> str:
    assert run_migrations(clean_database_dsn, migrations_dir=migrations_dir) == (1, 2, 3, 4)
    return clean_database_dsn


@pytest.fixture
def api_artifact_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "api-artifacts"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("STUDYDY_ARTIFACT_ROOT", str(root))
    return root


class _Uvicorn:
    def __init__(self, app_factory) -> None:
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(128)
        self.port = self.socket.getsockname()[1]
        self.server = uvicorn.Server(
            uvicorn.Config(
                app_factory(self.origin), host="127.0.0.1", port=self.port,
                log_config=None, access_log=False, lifespan="on",
            )
        )
        self.thread = Thread(target=self.server.run, kwargs={"sockets": [self.socket]}, name="test-uvicorn")

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("UVICORN_START_FAILED")
            time.sleep(0.01)
        return self

    def __exit__(self, *_args):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.socket.close()
        assert not self.thread.is_alive()


def _settings(origin: str, dsn: str, tmp_path: Path, endpoint: str = "http://127.0.0.1:1") -> ApiSettings:
    return ApiSettings("development", origin, False, _config(tmp_path, endpoint), 10, dsn)


def _resources(_subject: str):
    return (
        ControlledResourceUpload(
            "Native topic reference", ["Native topic"], ["Native topic"],
            "https://example.edu/native-topic.pdf", "cc_by", "attribution_required",
            "2026-08-12T00:00:00+08:00", "supplemental", io.BytesIO(_pdf("Public resource")),
        ),
    )


def test_real_uvicorn_full_lifecycle_restart_and_owner_isolation(
    api_database_dsn: str, api_artifact_root: Path, tmp_path: Path
) -> None:
    source_bytes = _pdf("Fresh HTTP-only source")
    cookie = None
    with _Loopback({1: _simple_page_body(1)}) as provider:
      with _Uvicorn(lambda origin: create_app(_settings(origin, api_database_dsn, tmp_path, provider.endpoint), _resources)) as server:
        with httpx.Client(base_url=server.origin, timeout=30) as client:
            openapi = client.get("/v1/openapi.json")
            assert openapi.status_code == 200
            assert openapi.content == (Path(__file__).parent / "fixtures" / "openapi-v1.json").read_bytes()
            assert client.post("/v1/session", headers={"Origin": server.origin}).status_code == 204
            cookie = client.cookies.get("studydy_session")
            headers = {"Origin": server.origin, "Content-Type": "application/pdf", "Idempotency-Key": "upload"}
            invalid_headers = {**headers, "Idempotency-Key": "invalid-upload"}
            invalid_upload = client.post(
                "/v1/materials", headers=invalid_headers, content=b"%PDF-1.7\n%%EOF"
            )
            assert invalid_upload.status_code == 400
            assert invalid_upload.json()["reason_code"] == "REQUEST_INVALID"
            with psycopg.connect(api_database_dsn) as connection:
                assert connection.execute("SELECT count(*) FROM materials").fetchone() == (0,)
            uploaded = client.post("/v1/materials", headers=headers, content=source_bytes)
            replay = client.post("/v1/materials", headers=headers, content=source_bytes)
            assert uploaded.status_code == replay.status_code == 201
            assert uploaded.json() == replay.json()
            material = uploaded.json()
            assert client.get(f"/v1/artifacts/{material['source_artifact_id']}").content == source_bytes
            created = client.post(
                "/v1/material-processing-runs",
                headers={"Origin": server.origin, "Idempotency-Key": "material-run"},
                json={
                    "schema": "material-processing-create/v1",
                    "material_id": material["material_id"],
                    "source_artifact_id": material["source_artifact_id"],
                    "subject": "mathematics",
                },
            )
            assert created.status_code == 202
            run = created.json()
            deadline = time.monotonic() + 30
            while run["status"] in {"pending", "running"}:
                assert time.monotonic() < deadline
                time.sleep(0.05)
                response = client.get(f"/v1/material-processing-runs/{run['run_id']}")
                assert response.status_code == 200
                run = response.json()
            assert run["status"] in {"succeeded", "partial"}
            binding = run["output_binding"]
            query = {"run_id": run["run_id"]}
            paths = (
                f"/v1/materials/{material['material_id']}/knowledge-maps/{binding['knowledge_map_revision']}",
                f"/v1/materials/{material['material_id']}/learning-paths/{binding['learning_path_revision']}",
                f"/v1/materials/{material['material_id']}/knowledge-map-views/{binding['knowledge_map_revision']}/{binding['learning_path_revision']}",
                f"/v1/materials/{material['material_id']}/learning-resource-results/{binding['learning_resource_result_revision']}",
            )
            revision_documents = {}
            for path in paths:
                revision_response = client.get(path, params=query)
                assert revision_response.status_code == 200
                revision_documents[path] = revision_response.json()
            wrong_material = client.get(
                (
                    f"/v1/materials/{uuid4()}/knowledge-maps/"
                    f"{binding['knowledge_map_revision']}"
                ),
                params=query,
            )
            assert wrong_material.status_code == 404
            assert wrong_material.json()["reason_code"] == "RESOURCE_NOT_FOUND"
            assessment_path = (
                f"/v1/materials/{material['material_id']}/assessments/"
                f"{binding['assessment_revision']}"
            )
            assessment_query = {
                "output_revision": binding["study_material_output_revision"],
                "map_revision": binding["knowledge_map_revision"],
                "path_revision": binding["learning_path_revision"],
            }
            assessment_response = client.get(
                assessment_path,
                params=assessment_query,
            )
            assert assessment_response.status_code == 200 and "answer_key" not in assessment_response.text
            assessment = assessment_response.json()
            learning_body = {
                "schema": "learning-update-create/v1",
                "material_id": material["material_id"],
                "map_revision": binding["knowledge_map_revision"],
                "path_revision": binding["learning_path_revision"],
                "assessment_revision": binding["assessment_revision"],
                "responses": [
                    {"question_id": question["question_id"], "selected_option_id": question["options"][0]["option_id"]}
                    for question in assessment["questions"]
                ],
            }
            learning_headers = {"Origin": server.origin, "Idempotency-Key": "learning"}
            state_response = client.post(
                f"/v1/materials/{material['material_id']}/learning-states",
                headers=learning_headers, json=learning_body,
            )
            assert state_response.status_code == 201
            state = state_response.json()
            replay_state = client.post(
                f"/v1/materials/{material['material_id']}/learning-states",
                headers=learning_headers, json=learning_body,
            )
            assert replay_state.status_code == 200 and replay_state.json() == state
            assert client.get(
                f"/v1/materials/{material['material_id']}/learning-states/{state['state_revision']}"
            ).json() == state

    with _Uvicorn(lambda origin: create_app(_settings(origin, api_database_dsn, tmp_path), lambda _: ())) as server:
        with httpx.Client(base_url=server.origin, timeout=10) as owner:
            owner.cookies.set("studydy_session", cookie)
            assert owner.get(f"/v1/artifacts/{material['source_artifact_id']}").content == source_bytes
            assert owner.get(f"/v1/material-processing-runs/{run['run_id']}").json() == run
            for path, document in revision_documents.items():
                response = owner.get(path, params=query)
                assert response.status_code == 200 and response.json() == document
            restarted_assessment = owner.get(assessment_path, params=assessment_query)
            assert restarted_assessment.status_code == 200
            assert restarted_assessment.json() == assessment
            assert owner.get(f"/v1/materials/{material['material_id']}/learning-states/{state['state_revision']}").json() == state
        with httpx.Client(base_url=server.origin, timeout=10) as other:
            assert other.post("/v1/session", headers={"Origin": server.origin}).status_code == 204
            owner_only_reads = [
                (f"/v1/artifacts/{material['source_artifact_id']}", None),
                (f"/v1/material-processing-runs/{run['run_id']}", None),
                *((path, query) for path in paths),
                (assessment_path, assessment_query),
            ]
            assert len(owner_only_reads) == 7
            for path, params in owner_only_reads:
                response = other.get(path, params=params)
                assert response.status_code == 404 and response.json()["reason_code"] == "RESOURCE_NOT_FOUND"
            state_read = other.get(
                f"/v1/materials/{material['material_id']}/learning-states/{state['state_revision']}"
            )
            assert state_read.status_code == 404
            assert state_read.json()["reason_code"] == "RESOURCE_NOT_FOUND"
            assert other.post("/v1/session/refresh", headers={"Origin": server.origin}).status_code == 204
            assert other.delete("/v1/session", headers={"Origin": server.origin}).status_code == 204


def test_single_material_worker_recovers_then_runs_serially(monkeypatch: pytest.MonkeyPatch) -> None:
    release = Event()
    started = Event()
    lock = Lock()
    calls = {"recover": 0, "active": 0, "maximum": 0, "claims": 0}
    claim = object()

    def recover(**_):
        calls["recover"] += 1

    def claim_next(**_):
        calls["claims"] += 1
        return claim if calls["claims"] == 1 else None

    def execute(*_args, **_kwargs):
        with lock:
            calls["active"] += 1
            calls["maximum"] = max(calls["maximum"], calls["active"])
        started.set()
        release.wait(5)
        with lock:
            calls["active"] -= 1

    monkeypatch.setattr(worker_module, "recover_interrupted_material_runs", recover)
    monkeypatch.setattr(worker_module, "claim_next_material_processing_run", claim_next)
    monkeypatch.setattr(worker_module, "execute_claimed_material_processing_run", execute)
    workers = start_runtime_workers(dsn=None, local_config={})
    assert started.wait(2)
    stopper = Thread(target=workers.stop)
    stopper.start()
    time.sleep(0.05)
    assert stopper.is_alive()
    release.set()
    stopper.join(2)
    assert calls["recover"] == 1 and calls["maximum"] == 1 and not stopper.is_alive()
