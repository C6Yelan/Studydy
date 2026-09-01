from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import ipaddress
import json
import tempfile
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHttpException
from starlette.routing import Match

from .models import (
    AnswerFeedbackView,
    AnswerSubmissionCreate,
    ApiErrorView,
    AssessmentCreate,
    AssessmentView,
    GuidanceApply,
    KnowledgeMapView,
    MaterialProcessingCreate,
    MaterialProcessingRunView,
    MaterialView,
    LearnerProgressView,
    StudySessionCreate,
    StudySessionView,
    project_answer_feedback,
    project_assessment,
    project_learner_progress,
    project_material_run,
    project_study_session,
)
from learning_adaptation.learner_progress import (
    apply_guidance,
    derive_learner_progress,
)
from learning_adaptation.answer_events import submit_answer
from learning_adaptation.assessment_requests import (
    generate_assessment_for_request,
)
from learning_adaptation.assessment_runtime import load_assessment_runtime_lock
from learning_adaptation.assessment_runtime_reuse import AssessmentRuntimeReuse
from learning_adaptation.assessment_items import read_assessment
from learning_adaptation.study_sessions import (
    complete_study_session,
    create_study_session,
    read_study_session,
)
from ..learner_session import (
    SessionError,
    TrustedLearner,
    create_session,
    refresh_session,
    resolve_session,
    revoke_session,
)
from ..material_processing import (
    MaterialProcessingError,
    create_material_processing_run,
    formal_runtime_preflight,
    read_material_processing_run,
)
from ..storage.artifacts import (
    open_verified_source_pdf,
    publish_idempotent_source_pdf,
)
from ..storage.material_review_outputs import read_material_run_outputs
from ..workers import start_runtime_workers


_COOKIE_NAME = "studydy_session"
_ERROR_MESSAGE = "Request could not be completed."
_SOURCE_LIMIT = 104_857_600
_ERROR_STATUS = {
    "REQUEST_INVALID": (400, False),
    "SESSION_REQUIRED": (401, False),
    "ORIGIN_NOT_ALLOWED": (403, False),
    "RESOURCE_NOT_FOUND": (404, False),
    "IDEMPOTENCY_CONFLICT": (409, False),
    "NO_SAFE_ASSESSMENT": (422, False),
    "MATERIAL_TOO_LARGE": (413, False),
    "MATERIAL_PDF_INVALID": (400, False),
    "UNSUPPORTED_MEDIA_TYPE": (415, False),
    "STORAGE_UNAVAILABLE": (503, True),
    "INTERNAL_ERROR": (500, False),
}


class _ApiFailure(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__("API_REQUEST_FAILED")
        self.reason_code = reason_code


class ApiSettingsError(ValueError):
    """只保留可安全診斷的 fixed runtime stage。"""

    def __init__(
        self, component: str | None = None, reason: str | None = None
    ) -> None:
        super().__init__("API_SETTINGS_INVALID")
        self.component = component
        self.reason = reason


@dataclass(frozen=True)
class ApiSettings:
    """保存唯一 server-owned API 與 local-only runtime 設定。"""

    profile: str
    public_origin: str
    secure_cookie: bool
    local_config: dict = field(repr=False)
    dsn: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.profile not in {"local", "test"} or type(self.secure_cookie) is not bool:
            raise ValueError("API_SETTINGS_INVALID")
        origin = _normalized_origin(self.public_origin)
        if origin is None or origin != self.public_origin:
            raise ValueError("API_SETTINGS_INVALID")
        parsed = urlsplit(origin)
        is_local_loopback = (
            self.profile == "local"
            and parsed.scheme == "http"
            and _is_numeric_loopback(parsed.hostname)
        )
        if not self.secure_cookie and not is_local_loopback:
            raise ValueError("API_SETTINGS_INVALID")
        if type(self.local_config) is not dict or (
            self.dsn is not None and not isinstance(self.dsn, str)
        ):
            raise ValueError("API_SETTINGS_INVALID")
        try:
            copied = deepcopy(self.local_config)
            formal_runtime_preflight(copied)
        except MaterialProcessingError as error:
            raise ApiSettingsError(error.component, error.reason) from None
        except Exception:
            raise ApiSettingsError() from None
        object.__setattr__(self, "local_config", copied)


def _is_numeric_loopback(hostname: str | None) -> bool:
    if hostname is None:
        return False
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _normalized_origin(value: Any) -> str | None:
    if type(value) is not str or not value or any(ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    normalized = f"{parsed.scheme.lower()}://{host}"
    return normalized + (f":{port}" if port is not None else "")


def _error_response(reason_code: str, *, status_code: int | None = None) -> JSONResponse:
    default_status, retryable = _ERROR_STATUS[reason_code]
    body = ApiErrorView.model_validate(
        {
            "schema": "api-error/v1",
            "request_id": uuid4(),
            "reason_code": reason_code,
            "retryable": retryable,
            "message": _ERROR_MESSAGE,
        }
    )
    return JSONResponse(
        body.model_dump(mode="json", by_alias=True),
        status_code=default_status if status_code is None else status_code,
    )


def _fixed_exception(error: Exception) -> str:
    reason = str(error)
    if "IDEMPOTENCY_CONFLICT" in reason or reason in {
        "MATERIAL_RUN_IDEMPOTENCY_CONFLICT",
        "ANSWER_ALREADY_SUBMITTED",
        "ADAPTIVE_PLAN_STALE",
        "ANSWER_SUBMISSION_STALE",
    }:
        return "IDEMPOTENCY_CONFLICT"
    if reason in {
        "MATERIAL_RUN_NOT_FOUND",
        "MATERIAL_RUN_UNAVAILABLE",
        "MATERIAL_OUTPUT_UNAVAILABLE",
        "ARTIFACT_NOT_AVAILABLE",
        "STUDY_SESSION_UNAVAILABLE",
        "STUDY_SESSION_MAP_UNAVAILABLE",
        "ANSWER_STUDY_SESSION_UNAVAILABLE",
        "ANSWER_ASSESSMENT_UNAVAILABLE",
        "ASSESSMENT_UNAVAILABLE",
        "ASSESSMENT_REQUEST_UNAVAILABLE",
        "ASSESSMENT_GROUNDING_UNAVAILABLE",
        "LEARNING_STATE_UNAVAILABLE",
        "WEAKNESS_UNAVAILABLE",
        "ADAPTIVE_PLAN_UNAVAILABLE",
    }:
        return "RESOURCE_NOT_FOUND"
    if reason in {
        "ASSESSMENT_NO_NEW_SAFE_ITEM",
        "ASSESSMENT_NO_SAFE_CANDIDATE",
    }:
        return "NO_SAFE_ASSESSMENT"
    if reason in {
        "ARTIFACT_REQUEST_INVALID",
        "MATERIAL_RUN_INVALID",
        "STUDY_SESSION_REQUEST_INVALID",
        "STUDY_SESSION_TARGET_INVALID",
        "ANSWER_SUBMISSION_INVALID",
        "ANSWER_OPTION_INVALID",
        "ASSESSMENT_REQUEST_INVALID",
        "ASSESSMENT_GENERATION_REQUEST_INVALID",
        "LEARNING_STATE_REQUEST_INVALID",
        "WEAKNESS_REQUEST_INVALID",
        "ADAPTIVE_PLAN_REQUEST_INVALID",
    }:
        return "REQUEST_INVALID"
    if reason == "ARTIFACT_PDF_INVALID":
        return "MATERIAL_PDF_INVALID"
    if "STORAGE" in reason or reason in {
        "SESSION_CREATE_FAILED",
        "ARTIFACT_PUBLISH_FAILED",
        "ASSESSMENT_MODEL_UNAVAILABLE",
        "ASSESSMENT_VERIFIER_UNAVAILABLE",
        "ASSESSMENT_RUNTIME_BUSY",
        "ASSESSMENT_CONFIGURATION_INVALID",
    }:
        return "STORAGE_UNAVAILABLE"
    return "INTERNAL_ERROR"


def _require_query(request: Request, allowed: set[str]) -> None:
    names = [key for key, _ in request.query_params.multi_items()]
    if set(names) - allowed or len(names) != len(set(names)):
        raise _ApiFailure("REQUEST_INVALID")


def _idempotency_key(request: Request) -> str:
    values = request.headers.getlist("idempotency-key")
    if len(values) != 1:
        raise _ApiFailure("REQUEST_INVALID")
    value = values[0]
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise _ApiFailure("REQUEST_INVALID") from None
    if not 1 <= len(encoded) <= 256 or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise _ApiFailure("REQUEST_INVALID")
    return value


def _trusted_learner(request: Request, settings: ApiSettings) -> TrustedLearner:
    try:
        learner = resolve_session(request.cookies.get(_COOKIE_NAME), dsn=settings.dsn)
    except SessionError:
        raise _ApiFailure("STORAGE_UNAVAILABLE") from None
    if learner is None:
        raise _ApiFailure("SESSION_REQUIRED")
    return learner


async def _require_empty_body(request: Request) -> None:
    if await request.body() != b"":
        raise _ApiFailure("REQUEST_INVALID")


def _set_session_cookie(response: Response, token: str, settings: ApiSettings) -> None:
    response.set_cookie(
        _COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="strict",
        path="/",
    )


def _verified_source_iterator(context: Any, source: Any) -> Iterator[bytes]:
    try:
        while chunk := source.file.read(1024 * 1024):
            yield chunk
    finally:
        context.__exit__(None, None, None)


def _install_openapi(app: FastAPI) -> None:
    """補上 raw PDF、cookie/header 與固定錯誤契約。"""

    idempotent_paths = {
        "/v1/materials",
        "/v1/material-processing-runs",
        "/v1/study-sessions",
        "/v1/study-sessions/{study_session_id}/assessments",
        "/v1/study-sessions/{study_session_id}/assessments/{assessment_revision}/submissions",
    }
    public_paths = {"/v1/session"}

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        components.setdefault("schemas", {})["ApiErrorView"] = ApiErrorView.model_json_schema(by_alias=True)
        components.setdefault("securitySchemes", {})["CookieSession"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": _COOKIE_NAME,
        }
        error_response = {
            "description": "Fixed safe API error",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiErrorView"}}},
        }
        for path, path_item in schema["paths"].items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "delete"}:
                    continue
                operation.get("responses", {}).pop("422", None)
                if method in {"post", "delete"}:
                    operation.setdefault("parameters", []).append(
                        {"name": "Origin", "in": "header", "required": True, "schema": {"type": "string"}}
                    )
                if path in idempotent_paths and method == "post":
                    operation.setdefault("parameters", []).append(
                        {
                            "name": "Idempotency-Key",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1, "maxLength": 256},
                        }
                    )
                if path == "/v1/materials" and method == "post":
                    operation["requestBody"] = {
                        "required": True,
                        "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
                    }
                if path == "/v1/artifacts/{artifact_id}" and method == "get":
                    operation["responses"]["200"] = {
                        "description": "Verified source PDF",
                        "content": {
                            "application/pdf": {
                                "schema": {"type": "string", "format": "binary"}
                            }
                        },
                    }
                if path not in public_paths:
                    operation["security"] = [{"CookieSession": []}]
                response_codes = {400, 500}
                if path not in public_paths:
                    response_codes.add(401)
                if method in {"post", "delete"}:
                    response_codes.add(403)
                if "{" in path:
                    response_codes.add(404)
                if path in idempotent_paths and method == "post":
                    response_codes.add(409)
                if path == "/v1/materials" and method == "post":
                    response_codes.update({413, 415})
                if (
                    path
                    == "/v1/study-sessions/{study_session_id}/assessments"
                    and method == "post"
                ):
                    response_codes.add(422)
                response_codes.add(503)
                for code in sorted(response_codes):
                    operation.setdefault("responses", {})[str(code)] = deepcopy(error_response)
        components["schemas"].pop("HTTPValidationError", None)
        components["schemas"].pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi


def create_app(settings: ApiSettings) -> FastAPI:
    """建立 material review 與 StudySession closed-loop 的固定 `/v1` surface。"""

    if not isinstance(settings, ApiSettings):
        raise ValueError("API_SETTINGS_INVALID")
    assessment_settings = {
        **deepcopy(settings.local_config),
        "assessment_runtime_lock": load_assessment_runtime_lock(),
    }
    assessment_runtime_reuse = AssessmentRuntimeReuse(assessment_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        workers = start_runtime_workers(dsn=settings.dsn, local_config=settings.local_config)
        try:
            yield
        finally:
            workers.stop()
            assessment_runtime_reuse.close()

    app = FastAPI(
        title="Studydy Material Review API",
        version="2.0.0",
        openapi_version="3.1.0",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.assessment_runtime_reuse = assessment_runtime_reuse

    @app.get("/v1/openapi.json", include_in_schema=False)
    async def openapi_document() -> Response:
        return Response(canonical_openapi_bytes(app), media_type="application/json")

    @app.middleware("http")
    async def enforce_transport_boundary(request: Request, call_next: Callable):
        partial_match = False
        for route in app.routes:
            match, _ = route.matches(request.scope)
            if match is Match.FULL:
                break
            partial_match = partial_match or match is Match.PARTIAL
        else:
            return _error_response("REQUEST_INVALID", status_code=405) if partial_match else _error_response("RESOURCE_NOT_FOUND")
        if request.headers.getlist("x-learner-id"):
            return _error_response("REQUEST_INVALID")
        if request.method in {"POST", "DELETE"}:
            origins = request.headers.getlist("origin")
            if len(origins) != 1 or origins[0] != settings.public_origin:
                return _error_response("ORIGIN_NOT_ALLOWED")
        try:
            return await call_next(request)
        except _ApiFailure as error:
            return _error_response(error.reason_code)
        except Exception as error:
            return _error_response(_fixed_exception(error))

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_: Request, __: RequestValidationError):
        return _error_response("REQUEST_INVALID")

    @app.exception_handler(StarletteHttpException)
    async def http_error(_: Request, error: StarletteHttpException):
        if error.status_code == 404:
            return _error_response("RESOURCE_NOT_FOUND")
        if error.status_code == 405:
            return _error_response("REQUEST_INVALID", status_code=405)
        return _error_response("INTERNAL_ERROR")

    @app.post("/v1/session", status_code=204, operation_id="createSession", tags=["session"])
    async def create_session_route(request: Request, response: Response) -> None:
        _require_query(request, set())
        await _require_empty_body(request)
        created = create_session(dsn=settings.dsn)
        _set_session_cookie(response, created.raw_token, settings)

    @app.post("/v1/session/refresh", status_code=204, operation_id="refreshSession", tags=["session"])
    async def refresh_session_route(request: Request, response: Response) -> None:
        _require_query(request, set())
        await _require_empty_body(request)
        raw_token = request.cookies.get(_COOKIE_NAME)
        learner = refresh_session(raw_token, dsn=settings.dsn)
        if learner is None:
            raise _ApiFailure("SESSION_REQUIRED")
        _set_session_cookie(response, raw_token or "", settings)

    @app.delete("/v1/session", status_code=204, operation_id="deleteSession", tags=["session"])
    async def delete_session_route(request: Request, response: Response) -> None:
        _require_query(request, set())
        await _require_empty_body(request)
        revoke_session(request.cookies.get(_COOKIE_NAME), dsn=settings.dsn)
        response.delete_cookie(_COOKIE_NAME, path="/", secure=settings.secure_cookie, httponly=True, samesite="strict")

    @app.post(
        "/v1/materials",
        response_model=MaterialView,
        response_model_by_alias=True,
        status_code=201,
        operation_id="createMaterial",
        tags=["materials"],
    )
    async def create_material_route(request: Request) -> MaterialView:
        _require_query(request, set())
        key = _idempotency_key(request)
        learner = _trusted_learner(request, settings)
        if request.headers.get("content-type") != "application/pdf":
            raise _ApiFailure("UNSUPPORTED_MEDIA_TYPE")
        with tempfile.TemporaryFile(mode="w+b") as source:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > _SOURCE_LIMIT:
                    raise _ApiFailure("MATERIAL_TOO_LARGE")
                source.write(chunk)
            source.seek(0)
            published = publish_idempotent_source_pdf(learner.learner_id, source, key, dsn=settings.dsn)
        return MaterialView.model_validate(
            {
                "schema": "material/v1",
                "material_id": published.material_id,
                "source_artifact_id": published.artifact_id,
                "source_sha256": published.sha256,
                "size_bytes": published.size_bytes,
            }
        )

    @app.post(
        "/v1/material-processing-runs",
        response_model=MaterialProcessingRunView,
        response_model_by_alias=True,
        status_code=202,
        operation_id="createMaterialProcessingRun",
        tags=["material-processing"],
    )
    async def create_material_run_route(request: Request, body: MaterialProcessingCreate) -> MaterialProcessingRunView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        return project_material_run(
            create_material_processing_run(
                learner.learner_id,
                body.material_id,
                body.source_artifact_id,
                _idempotency_key(request),
                deepcopy(settings.local_config),
                dsn=settings.dsn,
            )
        )

    @app.get(
        "/v1/material-processing-runs/{run_id}",
        response_model=MaterialProcessingRunView,
        response_model_by_alias=True,
        operation_id="getMaterialProcessingRun",
        tags=["material-processing"],
    )
    async def read_material_run_route(request: Request, run_id: UUID) -> MaterialProcessingRunView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        return project_material_run(read_material_processing_run(learner.learner_id, run_id, dsn=settings.dsn))

    @app.get(
        "/v1/materials/{material_id}/knowledge-maps/{map_revision}",
        response_model=KnowledgeMapView,
        response_model_by_alias=True,
        operation_id="getKnowledgeMapReview",
        tags=["review"],
    )
    async def read_map_route(request: Request, material_id: UUID, map_revision: str, run_id: UUID) -> KnowledgeMapView:
        _require_query(request, {"run_id"})
        learner = _trusted_learner(request, settings)
        outputs = read_material_run_outputs(learner.learner_id, material_id, run_id, dsn=settings.dsn)
        if outputs.knowledge_map_revision != map_revision:
            raise _ApiFailure("RESOURCE_NOT_FOUND")
        return KnowledgeMapView.model_validate(deepcopy(outputs.knowledge_map_view))

    @app.post(
        "/v1/study-sessions",
        response_model=StudySessionView,
        response_model_by_alias=True,
        status_code=201,
        operation_id="createStudySession",
        tags=["learning"],
    )
    async def create_study_session_route(
        request: Request, body: StudySessionCreate
    ) -> StudySessionView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        stored = create_study_session(
            learner,
            body.material_id,
            body.knowledge_map_revision,
            _idempotency_key(request),
            current_formal_concept_id=body.current_formal_concept_id,
            dsn=settings.dsn,
        )
        return project_study_session(stored)

    @app.get(
        "/v1/study-sessions/{study_session_id}",
        response_model=StudySessionView,
        response_model_by_alias=True,
        operation_id="getStudySession",
        tags=["learning"],
    )
    async def read_study_session_route(
        request: Request, study_session_id: UUID
    ) -> StudySessionView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        return project_study_session(
            read_study_session(learner, study_session_id, dsn=settings.dsn)
        )

    @app.post(
        "/v1/study-sessions/{study_session_id}/complete",
        response_model=StudySessionView,
        response_model_by_alias=True,
        operation_id="completeStudySession",
        tags=["learning"],
    )
    async def complete_study_session_route(
        request: Request, study_session_id: UUID
    ) -> StudySessionView:
        _require_query(request, set())
        await _require_empty_body(request)
        learner = _trusted_learner(request, settings)
        return project_study_session(
            complete_study_session(
                learner, study_session_id, dsn=settings.dsn
            )
        )

    @app.post(
        "/v1/study-sessions/{study_session_id}/assessments",
        response_model=AssessmentView,
        response_model_by_alias=True,
        status_code=201,
        operation_id="createAssessment",
        tags=["learning"],
    )
    async def create_assessment_route(
        request: Request,
        study_session_id: UUID,
        body: AssessmentCreate,
    ) -> AssessmentView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        key = _idempotency_key(request)
        stored = await run_in_threadpool(
            generate_assessment_for_request,
            learner,
            study_session_id,
            body.target_claim_id,
            deepcopy(settings.local_config),
            key,
            runtime_reuse=assessment_runtime_reuse,
            dsn=settings.dsn,
        )
        return project_assessment(stored)

    @app.get(
        "/v1/study-sessions/{study_session_id}/assessments/{assessment_revision}",
        response_model=AssessmentView,
        response_model_by_alias=True,
        operation_id="getAssessment",
        tags=["learning"],
    )
    async def read_assessment_route(
        request: Request,
        study_session_id: UUID,
        assessment_revision: str,
    ) -> AssessmentView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        stored = read_assessment(
            learner, assessment_revision, dsn=settings.dsn
        )
        if stored.study_session_id != study_session_id:
            raise _ApiFailure("RESOURCE_NOT_FOUND")
        return project_assessment(stored)

    @app.post(
        "/v1/study-sessions/{study_session_id}/assessments/{assessment_revision}/submissions",
        response_model=AnswerFeedbackView,
        response_model_by_alias=True,
        status_code=201,
        operation_id="submitAssessmentAnswer",
        tags=["learning"],
    )
    async def submit_answer_route(
        request: Request,
        study_session_id: UUID,
        assessment_revision: str,
        body: AnswerSubmissionCreate,
    ) -> AnswerFeedbackView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        submitted = submit_answer(
            learner,
            study_session_id,
            assessment_revision,
            body.question_id,
            body.selected_option_id,
            _idempotency_key(request),
            dsn=settings.dsn,
        )
        return project_answer_feedback(submitted.feedback)

    @app.get(
        "/v1/study-sessions/{study_session_id}/progress",
        response_model=LearnerProgressView,
        response_model_by_alias=True,
        operation_id="getLearnerProgress",
        tags=["learning"],
    )
    async def read_learner_progress_route(
        request: Request, study_session_id: UUID
    ) -> LearnerProgressView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        return project_learner_progress(
            derive_learner_progress(
                learner, study_session_id, dsn=settings.dsn
            )
        )

    @app.post(
        "/v1/study-sessions/{study_session_id}/guidance/apply",
        response_model=LearnerProgressView,
        response_model_by_alias=True,
        operation_id="applyGuidance",
        tags=["learning"],
    )
    async def apply_guidance_route(
        request: Request,
        study_session_id: UUID,
        body: GuidanceApply,
    ) -> LearnerProgressView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        return project_learner_progress(
            apply_guidance(
                learner,
                study_session_id,
                body.guidance_revision,
                dsn=settings.dsn,
            )
        )

    @app.get("/v1/artifacts/{artifact_id}", operation_id="getSourceArtifact", tags=["artifacts"], response_class=StreamingResponse)
    async def read_artifact_route(request: Request, artifact_id: UUID) -> StreamingResponse:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        context = open_verified_source_pdf(learner.learner_id, artifact_id, dsn=settings.dsn)
        try:
            source = context.__enter__()
        except Exception:
            raise _ApiFailure("RESOURCE_NOT_FOUND") from None
        return StreamingResponse(
            _verified_source_iterator(context, source),
            media_type="application/pdf",
            headers={"Content-Length": str(source.size_bytes), "ETag": f'"sha256:{source.sha256}"'},
        )

    _install_openapi(app)
    return app


def canonical_openapi_bytes(app: FastAPI) -> bytes:
    return (
        json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
