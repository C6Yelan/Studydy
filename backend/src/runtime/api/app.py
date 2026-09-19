from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import ipaddress
import json
import tempfile
from typing import Any, Callable, Iterator
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHttpException
from starlette.routing import Match

from .models import (
    MaterialDraftCreate, MaterialDraftView, SourceListView, RevisionCreate, RevisionCancel, SourceCapabilities, EvidenceSourceView,
    AccountCredentials,
    LearnerIdentityView,
    AnswerFeedbackView,
    AnswerSubmissionCreate,
    ApiErrorView,
    AssessmentCreate,
    AssessmentView,
    GuidanceApply,
    KnowledgeStructureView,
    MaterialProcessingCreate,
    MaterialProcessingRunView,
    MaterialDiscardView,
    MaterialView,
    MaterialLibraryItem,
    MaterialRename,
    MaterialLibraryView,
    LearnerProgressView,
    StudySessionCreate,
    StudySessionFocus,
    StudySessionView,
    StudyResumeView,
    AssessmentRecordView,
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
from learning_adaptation.answer_events import read_assessment_records, submit_answer
from learning_adaptation.assessments import generate_assessment, read_assessment
from learning_adaptation.study_sessions import (
    complete_study_session,
    create_study_session,
    read_study_session,
    set_current_study_concept,
)
from ..learner_session import (
    IDLE_LIFETIME,
    SessionError,
    TrustedLearner,
    register_account,
    login_account,
    refresh_session,
    resolve_session,
    revoke_session,
)
from ..material_processing import (
    MaterialProcessingError,
    create_material_processing_run,
    runtime_binding,
    read_material_processing_run,
)
from ..material_discard import MaterialDiscardError, request_material_discard
from ..storage.artifacts import (
    open_verified_source_pdf,
    publish_idempotent_source_pdf,
)
from ..storage.knowledge_structures import read_knowledge_structure
from ..storage.materials import MaterialLibraryError, read_material_library, rename_material
from ..workers import start_runtime_workers
from ..source_normalization import SourceError,create_draft,upload_source,read_sources,retry_normalization,remove_staged_source
from ..source_revisions import create_revision
from ..source_resolver import resolve_evidence_source
from ..storage.source_artifacts import open_verified_artifact
from ..storage.tables import Artifact,Material,MaterialSource,database_session
from sqlalchemy import select
from document_normalization.converter import MAX_FILE_BYTES,MIME,configured_python,NormalizationError


_COOKIE_NAME = "studydy_session"
_ERROR_MESSAGE = "Request could not be completed."
_SOURCE_LIMIT = 104_857_600
_ERROR_STATUS = {
    "DUPLICATE_SOURCE": (409, False),
    "REVISION_CONFLICT": (409, False),
    "REVISION_IN_PROGRESS": (409, True),
    "SOURCE_IN_USE": (409, False),
    "SOURCE_BUSY": (409, True),
    "SOURCE_NOT_READY": (409, True),
    "NORMALIZER_UNAVAILABLE": (503, True),
    "REQUEST_INVALID": (400, False),
    "INVALID_EMAIL": (400, False),
    "INVALID_CREDENTIALS": (401, False),
    "ACCOUNT_UNAVAILABLE": (409, False),
    "SESSION_REQUIRED": (401, False),
    "ORIGIN_NOT_ALLOWED": (403, False),
    "RESOURCE_NOT_FOUND": (404, False),
    "IDEMPOTENCY_CONFLICT": (409, False),
    "MATERIAL_NOT_DISCARDABLE": (409, False),
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
            # API availability does not depend on installed/online AI services.
            # Processing and assessment retain their operation-time checks.
            runtime_binding(copied)
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
    if isinstance(error,(SourceError,NormalizationError,MaterialProcessingError)) and reason in _ERROR_STATUS:return reason
    if reason == "MATERIAL_NOT_DISCARDABLE" or (isinstance(error, MaterialDiscardError) and reason == "RESOURCE_NOT_FOUND"):
        return reason
    if isinstance(error, MaterialLibraryError) and reason in {"REQUEST_INVALID", "RESOURCE_NOT_FOUND", "MATERIAL_NOT_DISCARDABLE"}:
        return reason
    if isinstance(error, SessionError) and reason in _ERROR_STATUS:
        return reason
    if "IDEMPOTENCY_CONFLICT" in reason or reason in {
        "MATERIAL_RUN_IDEMPOTENCY_CONFLICT",
        "ANSWER_ALREADY_SUBMITTED",
        "LEARNER_GUIDANCE_STALE",
        "LEARNER_PROGRESS_STALE",
        "ANSWER_SUBMISSION_STALE",
    }:
        return "IDEMPOTENCY_CONFLICT"
    if reason in {
        "MATERIAL_RUN_NOT_FOUND",
        "MATERIAL_RUN_UNAVAILABLE",
        "KNOWLEDGE_STRUCTURE_UNAVAILABLE",
        "ARTIFACT_NOT_AVAILABLE",
        "STUDY_SESSION_UNAVAILABLE",
        "STUDY_SESSION_MAP_UNAVAILABLE",
        "ANSWER_STUDY_SESSION_UNAVAILABLE",
        "ANSWER_ASSESSMENT_UNAVAILABLE",
        "ANSWER_EVENT_UNAVAILABLE",
        "ASSESSMENT_UNAVAILABLE",
        "ASSESSMENT_SESSION_UNAVAILABLE",
        "LEARNER_PROGRESS_UNAVAILABLE",
    }:
        return "RESOURCE_NOT_FOUND"
    if reason in {
        "NO_SAFE_ASSESSMENT",
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
        "ASSESSMENT_TARGET_INVALID",
    }:
        return "REQUEST_INVALID"
    if reason == "ARTIFACT_PDF_INVALID":
        return "MATERIAL_PDF_INVALID"
    if "STORAGE" in reason or reason in {
        "SESSION_CREATE_FAILED",
        "ARTIFACT_PUBLISH_FAILED",
        "SEMANTIC_SERVICE_UNAVAILABLE",
        "SEMANTIC_SERVICE_TIMEOUT",
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
        max_age=int(IDLE_LIFETIME.total_seconds()),
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
        "/v2/materials", "/v2/materials/{material_id}/sources", "/v2/materials/{material_id}/revisions",
        "/v2/material-processing-runs/{run_id}/retry",
        "/v1/materials",
        "/v1/material-processing-runs",
        "/v1/study-sessions",
        "/v1/study-sessions/{study_session_id}/assessments",
        "/v1/study-sessions/{study_session_id}/assessments/{assessment_revision}/submissions",
    }
    public_paths = {"/v1/accounts", "/v1/session/login"}

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
                if path in {"/v1/materials", "/v2/materials/{material_id}/sources"} and method == "post":
                    operation.setdefault("parameters", []).append({
                        "name": "X-Material-Name", "in": "header", "required": path.startswith("/v2/"),
                        "description": "URI-encoded UTF-8 filename, 1–200 decoded characters; first upload owns the name.",
                        "schema": {"type": "string", "maxLength": 2400},
                    })
                    operation["requestBody"] = {
                        "required": True,
                        "content": {media:{"schema":{"type":"string","format":"binary"}} for media in (MIME.values() if path.startswith("/v2/") else ["application/pdf"])},
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
                if path == "/v1/accounts":
                    response_codes.add(409)
                if path == "/v1/session/login":
                    response_codes.add(401)
                if path.endswith("/resume"):
                    response_codes.add(409)
                if path == "/v1/materials/{material_id}" and method == "delete":
                    response_codes.add(409)
                if path not in public_paths:
                    response_codes.add(401)
                if method in {"post", "delete"}:
                    response_codes.add(403)
                if "{" in path:
                    response_codes.add(404)
                if path in idempotent_paths and method == "post":
                    response_codes.add(409)
                if path in {"/v1/materials","/v2/materials/{material_id}/sources"} and method == "post":
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
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        workers = start_runtime_workers(
            dsn=settings.dsn, local_config=settings.local_config
        )
        try:
            yield
        finally:
            workers.stop()

    app = FastAPI(
        title="Studydy Material Review API",
        version="3.0.0",
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

    @app.middleware("http")
    async def private_response_cache(request: Request, call_next: Callable):
        response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request: Request, error: RequestValidationError):
        if request.url.path in {"/v1/accounts", "/v1/session/login"} and any(
            item["loc"] == ("body", "email") for item in error.errors()
        ):
            return _error_response("INVALID_EMAIL")
        return _error_response("REQUEST_INVALID")

    @app.exception_handler(StarletteHttpException)
    async def http_error(_: Request, error: StarletteHttpException):
        if error.status_code == 404:
            return _error_response("RESOURCE_NOT_FOUND")
        if error.status_code == 405:
            return _error_response("REQUEST_INVALID", status_code=405)
        return _error_response("INTERNAL_ERROR")

    @app.post("/v1/accounts", status_code=201, response_model=LearnerIdentityView,
              operation_id="registerAccount", tags=["session"])
    def register_account_route(request: Request, response: Response, body: AccountCredentials) -> LearnerIdentityView:
        _require_query(request, set())
        created = register_account(body.email, body.password, dsn=settings.dsn)
        _set_session_cookie(response, created.raw_token, settings)
        return LearnerIdentityView(learner_id=created.learner_id)

    @app.post("/v1/session/login", response_model=LearnerIdentityView,
              operation_id="loginAccount", tags=["session"])
    def login_account_route(request: Request, response: Response, body: AccountCredentials) -> LearnerIdentityView:
        _require_query(request, set())
        created = login_account(body.email, body.password, dsn=settings.dsn)
        _set_session_cookie(response, created.raw_token, settings)
        return LearnerIdentityView(learner_id=created.learner_id)

    @app.get("/v1/session", response_model=LearnerIdentityView,
             operation_id="readIdentity", tags=["session"])
    def read_identity_route(request: Request) -> LearnerIdentityView:
        _require_query(request, set())
        return LearnerIdentityView(learner_id=_trusted_learner(request, settings).learner_id)

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

    @app.get("/v1/materials", response_model=MaterialLibraryView,
             operation_id="listMaterials", tags=["materials"])
    def list_materials_route(request: Request) -> MaterialLibraryView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        return MaterialLibraryView(materials=read_material_library(learner.learner_id, dsn=settings.dsn))

    @app.get("/v1/materials/{material_id}", response_model=MaterialLibraryItem,
             operation_id="getMaterial", tags=["materials"])
    def read_material_route(request: Request, material_id: UUID) -> MaterialLibraryItem:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        materials = read_material_library(learner.learner_id, material_id=material_id, dsn=settings.dsn)
        if not materials:
            raise _ApiFailure("RESOURCE_NOT_FOUND")
        return MaterialLibraryItem.model_validate(materials[0])

    @app.post("/v1/materials/{material_id}/rename", response_model=MaterialLibraryItem,
              operation_id="renameMaterial", tags=["materials"])
    def rename_material_route(request: Request, material_id: UUID, body: MaterialRename) -> MaterialLibraryItem:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        return MaterialLibraryItem.model_validate(rename_material(
            learner.learner_id, material_id, body.display_name, dsn=settings.dsn,
        ))

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
        names = request.headers.getlist("x-material-name")
        if len(names) > 1 or (names and len(names[0]) > 2400):
            raise _ApiFailure("REQUEST_INVALID")
        try:
            display_name = unquote(names[0], encoding="utf-8", errors="strict") if names else None
        except UnicodeError:
            raise _ApiFailure("REQUEST_INVALID") from None
        with tempfile.TemporaryFile(mode="w+b") as source:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > _SOURCE_LIMIT:
                    raise _ApiFailure("MATERIAL_TOO_LARGE")
                source.write(chunk)
            source.seek(0)
            published = publish_idempotent_source_pdf(learner.learner_id, source, key, dsn=settings.dsn, display_name=display_name)
        return MaterialView.model_validate(
            {
                "schema": "material/v1",
                "material_id": published.material_id,
                "source_artifact_id": published.artifact_id,
                "source_sha256": published.sha256,
                "size_bytes": published.size_bytes,
            }
        )

    def source_listing(owner,material_id):
        sources=read_sources(owner,material_id,dsn=settings.dsn)
        with database_session(settings.dsn) as session:
            material=session.scalar(select(Material).where(Material.learner_id==owner,Material.material_id==material_id))
            if material is None:raise SourceError("RESOURCE_NOT_FOUND")
            discarding=material.discard_requested_at is not None
        return SourceListView(material_id=material_id,sources=sources,discard_requested=discarding)

    @app.get("/v2/source-capabilities",response_model=SourceCapabilities)
    def source_capabilities(request:Request):
        _require_query(request,set());_trusted_learner(request,settings)
        enabled=configured_python() is not None
        return SourceCapabilities(formats=[{"extension":ext,"media_type":media,"max_bytes":MAX_FILE_BYTES} for ext,media in MIME.items() if ext==".pdf" or enabled],
            quality_notice="建議優先上傳 PDF。其他支援格式會自動轉為 PDF，轉換品質不保證，請檢查轉換後內容。")

    @app.post("/v2/materials",status_code=201,response_model=MaterialDraftView)
    def create_material_draft(request:Request,body:MaterialDraftCreate):
        _require_query(request,set());owner=_trusted_learner(request,settings).learner_id
        return MaterialDraftView(material_id=create_draft(owner,body.display_name,_idempotency_key(request),dsn=settings.dsn))

    @app.post("/v2/materials/{material_id}/sources",status_code=202,response_model=SourceListView)
    async def upload_material_source(request:Request,material_id:UUID):
        _require_query(request,set());owner=_trusted_learner(request,settings).learner_id;key=_idempotency_key(request)
        names=request.headers.getlist("x-material-name")
        if len(names)!=1 or len(names[0])>2400:raise _ApiFailure("REQUEST_INVALID")
        try:name=unquote(names[0],encoding="utf-8",errors="strict")
        except UnicodeError:raise _ApiFailure("REQUEST_INVALID") from None
        data=bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk)>MAX_FILE_BYTES:raise _ApiFailure("MATERIAL_TOO_LARGE")
            data.extend(chunk)
        await run_in_threadpool(upload_source,owner,material_id,bytes(data),name,request.headers.get("content-type"),key,dsn=settings.dsn)
        return source_listing(owner,material_id)

    @app.get("/v2/materials/{material_id}/sources",response_model=SourceListView)
    def get_material_sources(request:Request,material_id:UUID):
        _require_query(request,set());owner=_trusted_learner(request,settings).learner_id
        return source_listing(owner,material_id)

    @app.post("/v2/materials/{material_id}/sources/{normalization_id}/retry",response_model=SourceListView)
    async def retry_material_source(request:Request,material_id:UUID,normalization_id:UUID):
        _require_query(request,set());await _require_empty_body(request);owner=_trusted_learner(request,settings).learner_id
        retry_normalization(owner,material_id,normalization_id,dsn=settings.dsn)
        return source_listing(owner,material_id)

    @app.post("/v2/materials/{material_id}/revisions",status_code=202,response_model=MaterialProcessingRunView)
    def create_material_revision(request:Request,material_id:UUID,body:RevisionCreate):
        _require_query(request,set());owner=_trusted_learner(request,settings).learner_id
        return project_material_run(create_revision(owner,material_id,body.normalization_ids,_idempotency_key(request),deepcopy(settings.local_config),base_revision=body.base_revision,dsn=settings.dsn))

    @app.post("/v2/material-processing-runs/{run_id}/cancel",response_model=MaterialProcessingRunView)
    def cancel_material_revision(request:Request,run_id:UUID,body:RevisionCancel):
        from ..material_processing import request_material_processing_cancellation,read_material_processing_run
        _require_query(request,set());owner=_trusted_learner(request,settings).learner_id
        run=read_material_processing_run(owner,run_id,dsn=settings.dsn)
        if run.base_revision!=body.base_revision:raise _ApiFailure('REQUEST_INVALID')
        return project_material_run(request_material_processing_cancellation(owner,run_id,update_only=True,dsn=settings.dsn))

    @app.post("/v2/material-processing-runs/{run_id}/retry",status_code=202,response_model=MaterialProcessingRunView)
    async def retry_material_revision(request:Request,run_id:UUID):
        from ..source_revisions import retry_revision
        _require_query(request,set());await _require_empty_body(request)
        owner=_trusted_learner(request,settings).learner_id
        return project_material_run(await run_in_threadpool(retry_revision,owner,run_id,_idempotency_key(request),deepcopy(settings.local_config),dsn=settings.dsn))

    @app.delete("/v2/materials/{material_id}/sources/{source_id}",response_model=SourceListView)
    async def remove_material_source(request:Request,material_id:UUID,source_id:UUID):
        _require_query(request,set());await _require_empty_body(request);owner=_trusted_learner(request,settings).learner_id
        remove_staged_source(owner,material_id,source_id,dsn=settings.dsn)
        return source_listing(owner,material_id)

    @app.get("/v2/materials/{material_id}/knowledge-structures/{revision}/evidence/{evidence_id}/source",response_model=EvidenceSourceView)
    def evidence_source(request:Request,material_id:UUID,revision:str,evidence_id:str):
        _require_query(request,set());owner=_trusted_learner(request,settings).learner_id
        return resolve_evidence_source(owner,material_id,revision,evidence_id,dsn=settings.dsn)

    @app.get("/v2/artifacts/{artifact_id}",response_class=StreamingResponse)
    def original_artifact(request:Request,artifact_id:UUID):
        _require_query(request,set());owner=_trusted_learner(request,settings).learner_id
        with database_session(settings.dsn) as session:
            artifact=session.get(Artifact,artifact_id)
            if artifact is None or artifact.learner_id!=owner or artifact.kind not in {"original","source_pdf"}:raise _ApiFailure("RESOURCE_NOT_FOUND")
            media=artifact.media_type
            original_name=session.scalar(select(MaterialSource.original_name).where(MaterialSource.learner_id==owner,MaterialSource.original_artifact_id==artifact_id))
        context=open_verified_artifact(owner,artifact_id,dsn=settings.dsn)
        try:source=context.__enter__()
        except Exception:raise _ApiFailure("RESOURCE_NOT_FOUND") from None
        extension=next((ext for ext,mime in MIME.items() if mime==media),".bin")
        return StreamingResponse(_verified_source_iterator(context,source),media_type=media,
            headers={"Content-Disposition":f'attachment; filename="material{extension}"; filename*=UTF-8\'\'{quote(original_name or ("material"+extension),safe="")}',"X-Content-Type-Options":"nosniff","Content-Length":str(source.size_bytes)})

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

    @app.delete(
        "/v1/materials/{material_id}", status_code=202,
        response_model=MaterialDiscardView, response_model_by_alias=True,
        operation_id="discardMaterial", tags=["materials"],
    )
    async def discard_material_route(request: Request, material_id: UUID) -> MaterialDiscardView:
        _require_query(request, set())
        await _require_empty_body(request)
        learner = _trusted_learner(request, settings)
        state = await run_in_threadpool(request_material_discard, learner.learner_id, material_id, dsn=settings.dsn)
        return MaterialDiscardView(material_id=material_id, state=state)

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
        "/v1/materials/{material_id}/knowledge-structures/{structure_revision}",
        response_model=KnowledgeStructureView,
        response_model_by_alias=True,
        operation_id="getKnowledgeStructure",
        tags=["review"],
    )
    async def read_map_route(request: Request, material_id: UUID, structure_revision: str) -> KnowledgeStructureView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        stored = read_knowledge_structure(
            learner.learner_id, material_id, revision=structure_revision, dsn=settings.dsn
        )
        return KnowledgeStructureView.model_validate(deepcopy(stored.view))

    @app.get(
        "/v1/materials/{material_id}/knowledge-structures/{structure_revision}/study-sessions/{study_session_id}/resume",
        response_model=StudyResumeView, operation_id="resumeStudySession", tags=["learning"],
    )
    def resume_study_route(
        request: Request, material_id: UUID, structure_revision: str, study_session_id: UUID,
        run_id: UUID, assessment_revision: str | None = None,
    ) -> StudyResumeView:
        _require_query(request, {"run_id", "assessment_revision"})
        learner = _trusted_learner(request, settings)
        study = read_study_session(learner, study_session_id, dsn=settings.dsn)
        if study.material_id != material_id or study.knowledge_structure_revision != structure_revision:
            raise _ApiFailure("RESOURCE_NOT_FOUND")
        structure = read_knowledge_structure(learner.learner_id, material_id, revision=structure_revision, dsn=settings.dsn)
        if structure.document["run_id"] != str(run_id):
            raise _ApiFailure("RESOURCE_NOT_FOUND")
        run = read_material_processing_run(learner.learner_id, run_id, dsn=settings.dsn)
        records = read_assessment_records(learner, study_session_id, dsn=settings.dsn)
        progress = derive_learner_progress(learner, study_session_id, dsn=settings.dsn)
        # 讀取期間若有提交或 guidance 變動，拒絕混合兩個時點的狀態，交由使用者重讀。
        if (read_study_session(learner, study_session_id, dsn=settings.dsn) != study
            or progress.event_watermark != study.last_event_number
            or derive_learner_progress(learner, study_session_id, dsn=settings.dsn).guidance_revision != progress.guidance_revision
            or sum(record.feedback is not None for record in records) != study.last_event_number):
            raise _ApiFailure("IDEMPOTENCY_CONFLICT")
        selected = assessment_revision
        if selected is not None and selected not in {record.assessment.assessment_revision for record in records}:
            raise _ApiFailure("RESOURCE_NOT_FOUND")
        if selected is None:
            selected = next((record.assessment.assessment_revision for record in records
                if study.status == "completed" or record.assessment.target_concept_id == study.current_concept_id), None)
        return StudyResumeView(
            session=project_study_session(study), run_id=run_id, source_artifact_id=run.source_artifact_id,
            knowledge_structure=KnowledgeStructureView.model_validate(structure.view),
            progress=project_learner_progress(progress), selected_assessment_revision=selected,
            assessments=[AssessmentRecordView(
                assessment=project_assessment(record.assessment),
                feedback=project_answer_feedback(record.feedback) if record.feedback is not None else None,
                created_at=record.created_at,
                can_submit=(record.feedback is None and study.status in {"active", "no_safe"}
                            and record.assessment.target_concept_id == study.current_concept_id),
            ) for record in records],
        )

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
            body.knowledge_structure_revision,
            _idempotency_key(request),
            current_concept_id=body.current_concept_id,
            dsn=settings.dsn,
        )
        return project_study_session(stored)

    @app.post(
        "/v1/study-sessions/{study_session_id}/focus",
        response_model=StudySessionView, response_model_by_alias=True,
        operation_id="focusStudySession", tags=["learning"],
    )
    async def focus_study_session_route(request: Request, study_session_id: UUID, body: StudySessionFocus) -> StudySessionView:
        _require_query(request, set())
        learner = _trusted_learner(request, settings)
        return project_study_session(set_current_study_concept(
            learner, study_session_id, body.current_concept_id, dsn=settings.dsn,
        ))

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
            generate_assessment,
            learner,
            study_session_id,
            body.target_claim_id,
            key,
            deepcopy(settings.local_config),
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
            learner, study_session_id, assessment_revision, dsn=settings.dsn
        )
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
