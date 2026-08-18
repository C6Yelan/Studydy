"""Student MVP 的 formal material runtime HTTP surface。"""

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
from starlette.exceptions import HTTPException as StarletteHttpException
from starlette.routing import Match

from .models import (
    ApiErrorView,
    KnowledgeMapView,
    MaterialProcessingCreate,
    MaterialProcessingRunView,
    MaterialView,
    project_material_run,
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
    "MATERIAL_TOO_LARGE": (413, False),
    "UNSUPPORTED_MEDIA_TYPE": (415, False),
    "STORAGE_UNAVAILABLE": (503, True),
    "INTERNAL_ERROR": (500, False),
}


class _ApiFailure(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__("API_REQUEST_FAILED")
        self.reason_code = reason_code


@dataclass(frozen=True)
class ApiSettings:
    """保存唯一 server-owned API 與 local-only runtime 設定。"""

    profile: str
    public_origin: str
    secure_cookie: bool
    local_config: dict = field(repr=False)
    dsn: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.profile not in {"development", "test"} or type(self.secure_cookie) is not bool:
            raise ValueError("API_SETTINGS_INVALID")
        origin = _normalized_origin(self.public_origin)
        if origin is None or origin != self.public_origin:
            raise ValueError("API_SETTINGS_INVALID")
        parsed = urlsplit(origin)
        is_development_loopback = (
            self.profile == "development"
            and parsed.scheme == "http"
            and _is_numeric_loopback(parsed.hostname)
        )
        if not self.secure_cookie and not is_development_loopback:
            raise ValueError("API_SETTINGS_INVALID")
        if type(self.local_config) is not dict or (
            self.dsn is not None and not isinstance(self.dsn, str)
        ):
            raise ValueError("API_SETTINGS_INVALID")
        try:
            copied = deepcopy(self.local_config)
            formal_runtime_preflight(copied)
        except Exception:
            raise ValueError("API_SETTINGS_INVALID") from None
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
    if reason in {"ARTIFACT_IDEMPOTENCY_CONFLICT", "MATERIAL_RUN_IDEMPOTENCY_CONFLICT"}:
        return "IDEMPOTENCY_CONFLICT"
    if reason in {
        "MATERIAL_RUN_NOT_FOUND",
        "MATERIAL_RUN_UNAVAILABLE",
        "MATERIAL_OUTPUT_UNAVAILABLE",
        "ARTIFACT_NOT_AVAILABLE",
    }:
        return "RESOURCE_NOT_FOUND"
    if reason in {
        "ARTIFACT_REQUEST_INVALID",
        "ARTIFACT_PDF_INVALID",
        "MATERIAL_RUN_INVALID",
    }:
        return "REQUEST_INVALID"
    if "STORAGE" in reason or reason in {"SESSION_CREATE_FAILED", "ARTIFACT_PUBLISH_FAILED"}:
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

    idempotent_paths = {"/v1/materials", "/v1/material-processing-runs"}
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
                if method == "get" and "{" in path:
                    response_codes.add(404)
                if path in idempotent_paths and method == "post":
                    response_codes.add(409)
                if path == "/v1/materials" and method == "post":
                    response_codes.update({413, 415})
                response_codes.add(503)
                for code in sorted(response_codes):
                    operation.setdefault("responses", {})[str(code)] = deepcopy(error_response)
        components["schemas"].pop("HTTPValidationError", None)
        components["schemas"].pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi


def create_app(settings: ApiSettings) -> FastAPI:
    """建立只含 material run、review Map 與 source PDF 的固定 `/v1` surface。"""

    if not isinstance(settings, ApiSettings):
        raise ValueError("API_SETTINGS_INVALID")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        workers = start_runtime_workers(dsn=settings.dsn, local_config=settings.local_config)
        try:
            yield
        finally:
            workers.stop()

    app = FastAPI(
        title="Studydy Material Review API",
        version="2.0.0",
        openapi_version="3.1.0",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

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
