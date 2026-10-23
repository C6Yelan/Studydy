from __future__ import annotations


from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import ipaddress
import json
from typing import Any, Callable, Iterator
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHttpException
from starlette.routing import Match

from document_normalization.converter import (
    MAX_FILE_BYTES,
    MIME,
    NormalizationError,
    normalizer_available,
)
from learning_adaptation import assessment_state
from .. import card_sets, learner_profile
from .research_routes import install as install_research
from .topic_routes import install as install_topics
from .scene_routes import install as install_scenes
from .video_routes import install as install_videos
from .voice_routes import install as install_voice
from ..learner_session import (
    IDLE_LIFETIME,
    PasswordAttemptLimiter,
    SessionError,
    change_password,
    device_label,
    list_active_sessions,
    login_account,
    revoke_other_sessions,
    refresh_session,
    register_account,
    revoke_own_session,
    revoke_session,
)
from ..material_discard import MaterialDiscardError, request_material_discard
from ..material_processing import (
    MaterialProcessingError,
    read_material_processing_run,
)
from ..material_runtime import MaterialRuntimeError, runtime_binding
from ..source_normalization import (
    create_draft,
    read_sources,
    remove_staged_source,
    retry_normalization,
    upload_source,
)
from ..errors import PodcastError, SourceError
from . import request_context
from .study_routes import install as install_study_routes
from .card_routes import install as install_card_routes
from .podcast_routes import install as install_podcast_routes
from ..source_revisions import create_revision
from ..storage.artifacts import (
    open_verified_artifact,
)
from ..storage.knowledge_structures import resolve_evidence_source
from ..storage.materials import (
    MaterialLibraryError,
    read_material_library,
    rename_material,
)
from ..storage.tables import Artifact, MaterialSource
from ..storage.database import database_ready, database_session
from ..workers import run_workers
from .models import (
    AccountCredentials,
    ApiErrorView,
    EvidenceSourceView,
    LearnerIdentityView,
    LearnerProfileUpdate,
    LearnerProfileView,
    PasswordChange,
    AccountSessionView,
    AccountSessionsView,
    MaterialDiscardView,
    MaterialDraftCreate,
    MaterialDraftView,
    MaterialLibraryItem,
    MaterialLibraryView,
    MaterialProcessingRunView,
    MaterialRename,
    RevisionCancel,
    RevisionCreate,
    SourceCapabilities,
    SourceListView,
    VersionMoveCreate,
    VersionMovePreviewView,
    VersionMoveView,
    project_material_run,
)


_ERROR_MESSAGE = "Request could not be completed."
_ERROR_STATUS = {
    "PODCAST_CONFLICT": (409, False),
    "PODCAST_SOURCE_INSUFFICIENT": (422, False),
    "PODCAST_SOURCE_TOO_LARGE": (422, False),
    "CARD_SET_CONFLICT": (409, False),
    "CARD_REVIEW_CONFLICT": (409, False),
    "VERSION_MOVE_STALE": (409, False),
    "LEARNER_GUIDANCE_STALE": (409, True),
    "ASSESSMENT_SET_CONFLICT": (409, False),
    "ASSESSMENT_SET_ACTIVE": (409, False),
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
    "CURRENT_PASSWORD_INVALID": (400, False),
    "TOO_MANY_ATTEMPTS": (429, True),
    "ACCOUNT_UNAVAILABLE": (409, False),
    "SESSION_REQUIRED": (401, False),
    "ORIGIN_NOT_ALLOWED": (403, False),
    "RESOURCE_NOT_FOUND": (404, False),
    "IDEMPOTENCY_CONFLICT": (409, False),
    "MATERIAL_NOT_DISCARDABLE": (409, False),
    "MATERIAL_TOO_LARGE": (413, False),
    "UNSUPPORTED_MEDIA_TYPE": (415, False),
    "STORAGE_UNAVAILABLE": (503, True),
    "INTERNAL_ERROR": (500, False),
}

_ASSESSMENT_SET_ERRORS = {
    "ASSESSMENT_SET_NOT_FOUND": "RESOURCE_NOT_FOUND",
    "ASSESSMENT_SET_REQUEST_INVALID": "REQUEST_INVALID",
    "ASSESSMENT_SET_TARGET_INVALID": "REQUEST_INVALID",
    "ASSESSMENT_SET_CONFLICT": "ASSESSMENT_SET_CONFLICT",
    "ASSESSMENT_SET_ACTIVE": "ASSESSMENT_SET_ACTIVE",
}

_INTERNAL_ERRORS = {
    "ASSESSMENT_SET_ACTIVE": "ASSESSMENT_SET_ACTIVE",
    "LEARNER_GUIDANCE_STALE": "LEARNER_GUIDANCE_STALE",
    "STUDY_SESSION_IDEMPOTENCY_CONFLICT": "IDEMPOTENCY_CONFLICT",
    "LEARNER_PROGRESS_STALE": "IDEMPOTENCY_CONFLICT",
    "MATERIAL_RUN_NOT_FOUND": "RESOURCE_NOT_FOUND",
    "MATERIAL_RUN_UNAVAILABLE": "RESOURCE_NOT_FOUND",
    "KNOWLEDGE_STRUCTURE_UNAVAILABLE": "RESOURCE_NOT_FOUND",
    "MATERIAL_FORMAT_UNSUPPORTED": "MATERIAL_FORMAT_UNSUPPORTED",
    "ARTIFACT_NOT_AVAILABLE": "RESOURCE_NOT_FOUND",
    "STUDY_SESSION_UNAVAILABLE": "RESOURCE_NOT_FOUND",
    "STUDY_SESSION_MAP_UNAVAILABLE": "RESOURCE_NOT_FOUND",
    "ANSWER_ASSESSMENT_UNAVAILABLE": "RESOURCE_NOT_FOUND",
    "ANSWER_EVENT_UNAVAILABLE": "RESOURCE_NOT_FOUND",
    "ASSESSMENT_UNAVAILABLE": "RESOURCE_NOT_FOUND",
    "LEARNER_PROGRESS_UNAVAILABLE": "RESOURCE_NOT_FOUND",
    "ARTIFACT_REQUEST_INVALID": "REQUEST_INVALID",
    "MATERIAL_RUN_INVALID": "REQUEST_INVALID",
    "STUDY_SESSION_REQUEST_INVALID": "REQUEST_INVALID",
    "STUDY_SESSION_TARGET_INVALID": "REQUEST_INVALID",
    "ASSESSMENT_REQUEST_INVALID": "REQUEST_INVALID",
    "SESSION_CREATE_FAILED": "STORAGE_UNAVAILABLE",
    "SESSION_STORAGE_FAILED": "STORAGE_UNAVAILABLE",
    "ARTIFACT_STORAGE_FAILED": "STORAGE_UNAVAILABLE",
    "MATERIAL_DISCARD_STORAGE_FAILED": "STORAGE_UNAVAILABLE",
    "MATERIAL_LIBRARY_STORAGE_FAILED": "STORAGE_UNAVAILABLE",
    "MATERIAL_RUN_STORAGE_FAILED": "STORAGE_UNAVAILABLE",
    "STUDY_SESSION_STORAGE_FAILED": "STORAGE_UNAVAILABLE",
}


class ApiSettingsError(ValueError):
    """只保留可安全診斷的 fixed runtime stage。"""

    def __init__(self, component: str | None = None, reason: str | None = None) -> None:
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
    upload_max_bytes: int = MAX_FILE_BYTES

    def __post_init__(self) -> None:
        if (
            self.profile not in {"local", "test"}
            or type(self.secure_cookie) is not bool
        ):
            raise ValueError("API_SETTINGS_INVALID")
        if (
            type(self.upload_max_bytes) is not int
            or not 1 <= self.upload_max_bytes <= MAX_FILE_BYTES
        ):
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
        except MaterialRuntimeError as error:
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


_ERROR_STATUS.update(
    {
        "VIDEO_CONFLICT": (409, True),
        "VIDEO_SOURCE_CHANGED": (409, False),
        "SCENE_CONFLICT": (409, True),
        "SCENE_ALIGNMENT_INVALID": (422, True),
        "SCENE_ALIGNMENT_FAILED": (422, True),
        "SCENE_SOURCE_CHANGED": (409, False),
        "TOPIC_CONFLICT": (409, True),
        "TOPIC_PROVIDER_FAILED": (502, True),
        "RESEARCH_SCOPE_REQUIRED": (409, False),
        "RESEARCH_SNAPSHOT_UNAVAILABLE": (409, False),
        "RESEARCH_URL_REJECTED": (422, False),
        "RESEARCH_LICENSE_UNCONFIRMED": (422, False),
        "RESEARCH_SEARCH_FAILED": (502, True),
        "RESEARCH_DOWNLOAD_FAILED": (502, True),
        "VOICE_TURN_IN_PROGRESS": (409, True),
        "VOICE_PROVIDER_UNAVAILABLE": (503, True),
        "VOICE_PROVIDER_FAILED": (502, True),
        "VOICE_TRANSCRIPT_INVALID": (422, False),
        "VOICE_ANSWER_INVALID": (422, True),
        "VOICE_PODCAST_CONTEXT_INVALID": (409, False),
        "VOICE_CONTEXT_TOO_LARGE": (422, False),
        "RETRIEVAL_INDEX_REQUIRED": (409, False),
        "RETRIEVAL_INDEX_INVALID": (409, False),
        "MATERIAL_FORMAT_UNSUPPORTED": (409, False),
        "SOURCE_SCOPE_EMPTY": (422, False),
        "SOURCE_SCOPE_INVALID": (422, False),
        "SOURCE_SCOPE_UNRESOLVED": (422, False),
        "RESEARCH_CONTEXT_TOO_LARGE": (422, True),
    }
)


def _error_response(
    reason_code: str, *, status_code: int | None = None
) -> JSONResponse:
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
    """只把已知內部原因碼轉成固定的公開 API 原因碼。"""
    reason = str(error)
    if isinstance(error, assessment_state.AssessmentSetError):
        return _ASSESSMENT_SET_ERRORS.get(reason, "INTERNAL_ERROR")
    if (
        isinstance(
            error,
            (
                SourceError,
                NormalizationError,
                MaterialProcessingError,
                SessionError,
                card_sets.CardSetError,
                PodcastError,
            ),
        )
        and reason in _ERROR_STATUS
    ):
        return reason
    if isinstance(error, MaterialLibraryError) and reason in {
        "REQUEST_INVALID",
        "RESOURCE_NOT_FOUND",
        "MATERIAL_NOT_DISCARDABLE",
    }:
        return reason
    if isinstance(error, MaterialDiscardError) and reason == "RESOURCE_NOT_FOUND":
        return reason
    return _INTERNAL_ERRORS.get(reason, "INTERNAL_ERROR")


def _set_session_cookie(response: Response, token: str, settings: ApiSettings) -> None:
    response.set_cookie(
        request_context.SESSION_COOKIE,
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
    """補上來源上傳、原檔下載、PDF 預覽與 cookie/header 的固定契約。"""

    idempotent_paths = {
        "/v1/materials/{material_id}/podcasts",
        "/v1/materials/{material_id}/card-sets",
        "/v1/materials",
        "/v1/materials/{material_id}/sources",
        "/v1/materials/{material_id}/revisions",
        "/v1/material-processing-runs/{run_id}/retry",
        "/v1/card-sets/{card_set_id}/reviews",
        "/v1/materials/{material_id}/version-move",
        "/v1/study-sessions",
        "/v1/study-sessions/{study_session_id}/assessment-sets",
        "/v1/study-sessions/{study_session_id}/assessment-sets/{set_id}/retry",
        "/v1/study-sessions/{study_session_id}/assessment-sets/{set_id}/publish-partial",
        "/v1/study-sessions/{study_session_id}/assessment-sets/{set_id}/submissions",
        "/v1/study-sessions/{study_session_id}/assessment-sets/{set_id}/remediation",
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
        components.setdefault("schemas", {})["ApiErrorView"] = (
            ApiErrorView.model_json_schema(by_alias=True)
        )
        components.setdefault("securitySchemes", {})["CookieSession"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": request_context.SESSION_COOKIE,
        }
        error_response = {
            "description": "Fixed safe API error",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ApiErrorView"}
                }
            },
        }
        for path, path_item in schema["paths"].items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "delete"}:
                    continue
                operation.get("responses", {}).pop("422", None)
                if method in {"post", "delete"}:
                    operation.setdefault("parameters", []).append(
                        {
                            "name": "Origin",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    )
                if path in idempotent_paths and method == "post":
                    operation.setdefault("parameters", []).append(
                        {
                            "name": "Idempotency-Key",
                            "in": "header",
                            "required": True,
                            "schema": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 256,
                            },
                        }
                    )
                if path == "/v1/materials/{material_id}/sources" and method == "post":
                    operation.setdefault("parameters", []).append(
                        {
                            "name": "X-Material-Name",
                            "in": "header",
                            "required": True,
                            "description": (
                                "URI-encoded UTF-8 filename, 1–200 decoded characters; "
                                "first upload owns the name."
                            ),
                            "schema": {"type": "string", "maxLength": 2400},
                        }
                    )
                    operation["requestBody"] = {
                        "required": True,
                        "content": {
                            media: {"schema": {"type": "string", "format": "binary"}}
                            for media in MIME.values()
                        },
                    }
                if (
                    path == "/v1/podcasts/{podcast_id}/episodes/{episode_index}/audio"
                    and method == "get"
                ):
                    operation.setdefault("parameters", []).append(
                        {
                            "name": "Range",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string"},
                            "description": "Single byte range, including suffix ranges",
                        }
                    )
                    for code in ("200", "206"):
                        operation["responses"][code] = {
                            "description": "Owner-authorized verified Podcast audio",
                            "content": {
                                "audio/wav": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        }
                    operation["responses"]["416"] = {
                        "description": "Invalid or unsatisfiable byte range"
                    }
                if path == "/v1/artifacts/{artifact_id}" and method == "get":
                    operation["responses"]["200"] = {
                        "description": "Verified normalized PDF preview",
                        "content": {
                            "application/pdf": {
                                "schema": {"type": "string", "format": "binary"}
                            }
                        },
                    }
                if path == "/v1/artifacts/{artifact_id}/download" and method == "get":
                    operation["responses"]["200"] = {
                        "description": "Owner-authorized original file download",
                        "content": {
                            media: {"schema": {"type": "string", "format": "binary"}}
                            for media in MIME.values()
                        },
                        "headers": {
                            "Content-Disposition": {
                                "schema": {"type": "string"},
                                "description": "Attachment with original filename",
                            },
                        },
                    }
                if path not in public_paths:
                    operation["security"] = [{"CookieSession": []}]
                response_codes = {400, 500}
                if path == "/v1/accounts":
                    response_codes.add(409)
                if path == "/v1/session/login":
                    response_codes.add(401)
                if path == "/v1/account/password":
                    response_codes.add(429)
                if path.endswith("/resume") or path in {
                    "/v1/card-sets/{card_set_id}/update",
                    "/v1/card-sets/{card_set_id}/count",
                    "/v1/podcasts/{podcast_id}/actions",
                }:
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
                if path == "/v1/materials/{material_id}/sources" and method == "post":
                    response_codes.update({413, 415})
                response_codes.add(503)
                for code in sorted(response_codes):
                    operation.setdefault("responses", {})[str(code)] = deepcopy(
                        error_response
                    )
        components["schemas"].pop("HTTPValidationError", None)
        components["schemas"].pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi


def create_app(settings: ApiSettings) -> FastAPI:
    """建立帳號、教材與學習評量的 /v1 API。"""

    if not isinstance(settings, ApiSettings):
        raise ValueError("API_SETTINGS_INVALID")

    workers_ready = lambda: False

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal workers_ready
        with run_workers(settings.dsn, settings.local_config) as ready:
            workers_ready = ready
            try:
                yield
            finally:
                workers_ready = lambda: False

    app = FastAPI(
        title="Studydy API",
        version="v1",
        openapi_version="3.1.0",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.get("/v1/openapi.json", include_in_schema=False)
    async def openapi_document() -> Response:
        return Response(canonical_openapi_bytes(app), media_type="application/json")

    @app.get("/internal/ready", include_in_schema=False)
    def readiness() -> Response:
        ready = workers_ready() and database_ready(settings.dsn)
        return JSONResponse(
            {"status": "ready" if ready else "unavailable"},
            status_code=200 if ready else 503,
        )

    @app.middleware("http")
    async def enforce_transport_boundary(request: Request, call_next: Callable):
        partial_match = False
        for route in app.routes:
            match, _ = route.matches(request.scope)
            if match is Match.FULL:
                break
            partial_match = partial_match or match is Match.PARTIAL
        else:
            return (
                _error_response("REQUEST_INVALID", status_code=405)
                if partial_match
                else _error_response("RESOURCE_NOT_FOUND")
            )
        if request.headers.getlist("x-learner-id"):
            return _error_response("REQUEST_INVALID")
        if request.method in {"POST", "DELETE"}:
            origins = request.headers.getlist("origin")
            if len(origins) != 1 or origins[0] != settings.public_origin:
                return _error_response("ORIGIN_NOT_ALLOWED")
        try:
            return await call_next(request)
        except request_context.ApiFailure as error:
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

    @app.post(
        "/v1/accounts",
        status_code=201,
        response_model=LearnerIdentityView,
        operation_id="registerAccount",
        tags=["session"],
    )
    def register_account_route(
        request: Request, response: Response, body: AccountCredentials
    ) -> LearnerIdentityView:
        request_context.require_query(request, set())
        created = register_account(
            body.email,
            body.password,
            dsn=settings.dsn,
            label=device_label(request.headers.get("user-agent")),
        )
        _set_session_cookie(response, created.raw_token, settings)
        return LearnerIdentityView(learner_id=created.learner_id)

    @app.post(
        "/v1/session/login",
        response_model=LearnerIdentityView,
        operation_id="loginAccount",
        tags=["session"],
    )
    def login_account_route(
        request: Request, response: Response, body: AccountCredentials
    ) -> LearnerIdentityView:
        request_context.require_query(request, set())
        created = login_account(
            body.email,
            body.password,
            dsn=settings.dsn,
            label=device_label(request.headers.get("user-agent")),
        )
        _set_session_cookie(response, created.raw_token, settings)
        return LearnerIdentityView(learner_id=created.learner_id)

    @app.get(
        "/v1/session",
        response_model=LearnerIdentityView,
        operation_id="readIdentity",
        tags=["session"],
    )
    def read_identity_route(request: Request) -> LearnerIdentityView:
        request_context.require_query(request, set())
        return LearnerIdentityView(
            learner_id=request_context.trusted_learner(request, settings).learner_id
        )

    @app.get(
        "/v1/profile",
        response_model=LearnerProfileView,
        operation_id="readProfile",
        tags=["session"],
    )
    def read_profile_route(request: Request):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return learner_profile.read_profile(learner.learner_id, dsn=settings.dsn)

    @app.post(
        "/v1/profile",
        response_model=LearnerProfileView,
        operation_id="updateProfile",
        tags=["session"],
    )
    def update_profile_route(request: Request, body: LearnerProfileUpdate):
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return learner_profile.update_profile(
            learner.learner_id, body.display_name, dsn=settings.dsn
        )

    password_attempts = PasswordAttemptLimiter()

    def sessions_view(sessions) -> AccountSessionsView:
        return AccountSessionsView(
            sessions=[AccountSessionView(**vars(item)) for item in sessions]
        )

    @app.get(
        "/v1/account/sessions",
        response_model=AccountSessionsView,
        operation_id="readAccountSessions",
        tags=["session"],
    )
    def read_account_sessions_route(request: Request) -> AccountSessionsView:
        request_context.require_query(request, set())
        request_context.trusted_learner(request, settings)
        return sessions_view(
            list_active_sessions(
                request.cookies.get(request_context.SESSION_COOKIE), dsn=settings.dsn
            )
        )

    @app.delete(
        "/v1/account/sessions/others",
        response_model=AccountSessionsView,
        operation_id="revokeOtherSessions",
        tags=["session"],
        dependencies=[Depends(request_context.require_empty_body)],
    )
    def revoke_other_sessions_route(request: Request) -> AccountSessionsView:
        request_context.require_query(request, set())
        request_context.trusted_learner(request, settings)
        return sessions_view(
            revoke_other_sessions(
                request.cookies.get(request_context.SESSION_COOKIE), dsn=settings.dsn
            )
        )

    @app.delete(
        "/v1/account/sessions/{session_id}",
        response_model=AccountSessionsView,
        operation_id="revokeAccountSession",
        tags=["session"],
        dependencies=[Depends(request_context.require_empty_body)],
    )
    def revoke_account_session_route(
        request: Request, session_id: UUID
    ) -> AccountSessionsView:
        # 只能撤銷本人另一個登入；目前這個用 DELETE /v1/session 登出。
        request_context.require_query(request, set())
        request_context.trusted_learner(request, settings)
        return sessions_view(
            revoke_own_session(
                request.cookies.get(request_context.SESSION_COOKIE),
                session_id,
                dsn=settings.dsn,
            )
        )

    @app.post(
        "/v1/account/password",
        response_model=AccountSessionsView,
        operation_id="changePassword",
        tags=["session"],
    )
    def change_password_route(
        request: Request, body: PasswordChange
    ) -> AccountSessionsView:
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        # 節流在核對前檢查，暫停期間不再計算雜湊。
        if password_attempts.blocked(learner.learner_id):
            raise request_context.ApiFailure("TOO_MANY_ATTEMPTS")
        try:
            active = change_password(
                request.cookies.get(request_context.SESSION_COOKIE),
                body.current_password,
                body.new_password,
                dsn=settings.dsn,
            )
        except SessionError as error:
            if str(error) == "CURRENT_PASSWORD_INVALID":
                password_attempts.record_failure(learner.learner_id)
            raise
        password_attempts.reset(learner.learner_id)
        return sessions_view(active)

    @app.post(
        "/v1/session/refresh",
        response_model=LearnerIdentityView,
        operation_id="refreshSession",
        tags=["session"],
        dependencies=[Depends(request_context.require_empty_body)],
    )
    def refresh_session_route(
        request: Request, response: Response
    ) -> LearnerIdentityView:
        request_context.require_query(request, set())
        raw_token = request.cookies.get(request_context.SESSION_COOKIE)
        learner = refresh_session(raw_token, dsn=settings.dsn)
        if learner is None:
            raise request_context.ApiFailure("SESSION_REQUIRED")
        _set_session_cookie(response, raw_token or "", settings)
        return LearnerIdentityView(learner_id=learner.learner_id)

    @app.delete(
        "/v1/session",
        status_code=204,
        operation_id="deleteSession",
        tags=["session"],
        dependencies=[Depends(request_context.require_empty_body)],
    )
    def delete_session_route(request: Request, response: Response) -> None:
        request_context.require_query(request, set())
        revoke_session(
            request.cookies.get(request_context.SESSION_COOKIE), dsn=settings.dsn
        )
        response.delete_cookie(
            request_context.SESSION_COOKIE,
            path="/",
            secure=settings.secure_cookie,
            httponly=True,
            samesite="strict",
        )

    @app.get(
        "/v1/materials",
        response_model=MaterialLibraryView,
        operation_id="listMaterials",
        tags=["materials"],
    )
    def list_materials_route(request: Request) -> MaterialLibraryView:
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return MaterialLibraryView(
            materials=read_material_library(learner.learner_id, dsn=settings.dsn)
        )

    @app.get(
        "/v1/materials/{material_id}",
        response_model=MaterialLibraryItem,
        operation_id="getMaterial",
        tags=["materials"],
    )
    def read_material_route(request: Request, material_id: UUID) -> MaterialLibraryItem:
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        materials = read_material_library(
            learner.learner_id, material_id=material_id, dsn=settings.dsn
        )
        if not materials:
            raise request_context.ApiFailure("RESOURCE_NOT_FOUND")
        return MaterialLibraryItem.model_validate(materials[0])

    @app.post(
        "/v1/materials/{material_id}/rename",
        response_model=MaterialLibraryItem,
        operation_id="renameMaterial",
        tags=["materials"],
    )
    def rename_material_route(
        request: Request, material_id: UUID, body: MaterialRename
    ) -> MaterialLibraryItem:
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return MaterialLibraryItem.model_validate(
            rename_material(
                learner.learner_id,
                material_id,
                body.display_name,
                dsn=settings.dsn,
            )
        )

    @app.get("/v1/source-capabilities", response_model=SourceCapabilities)
    def source_capabilities(request: Request):
        request_context.require_query(request, set())
        request_context.trusted_learner(request, settings)
        enabled = normalizer_available()
        return SourceCapabilities(
            formats=[
                {
                    "extension": ext,
                    "media_type": media,
                    "max_bytes": settings.upload_max_bytes,
                }
                for ext, media in MIME.items()
                if ext == ".pdf" or enabled
            ],
        )

    @app.post("/v1/materials", status_code=201, response_model=MaterialDraftView)
    def create_material_draft(request: Request, body: MaterialDraftCreate):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return MaterialDraftView(
            material_id=create_draft(
                owner,
                body.display_name,
                request_context.idempotency_key(request),
                dsn=settings.dsn,
            )
        )

    @app.post(
        "/v1/materials/{material_id}/sources",
        status_code=202,
        response_model=SourceListView,
    )
    async def upload_material_source(request: Request, material_id: UUID):
        request_context.require_query(request, set())
        owner = (
            await run_in_threadpool(request_context.trusted_learner, request, settings)
        ).learner_id
        key = request_context.idempotency_key(request)
        names = request.headers.getlist("x-material-name")
        if len(names) != 1 or len(names[0]) > 2400:
            raise request_context.ApiFailure("REQUEST_INVALID")
        try:
            name = unquote(names[0], encoding="utf-8", errors="strict")
        except UnicodeError:
            raise request_context.ApiFailure("REQUEST_INVALID") from None
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > settings.upload_max_bytes:
                raise request_context.ApiFailure("MATERIAL_TOO_LARGE")
            data.extend(chunk)
        await run_in_threadpool(
            upload_source,
            owner,
            material_id,
            bytes(data),
            name,
            request.headers.get("content-type"),
            key,
            dsn=settings.dsn,
        )
        return await run_in_threadpool(read_sources, owner, material_id, dsn=settings.dsn)

    @app.get("/v1/materials/{material_id}/sources", response_model=SourceListView)
    def get_material_sources(request: Request, material_id: UUID):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return read_sources(owner, material_id, dsn=settings.dsn)

    @app.post(
        "/v1/materials/{material_id}/sources/{normalization_id}/retry",
        response_model=SourceListView,
        dependencies=[Depends(request_context.require_empty_body)],
    )
    def retry_material_source(
        request: Request, material_id: UUID, normalization_id: UUID
    ):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        retry_normalization(owner, material_id, normalization_id, dsn=settings.dsn)
        return read_sources(owner, material_id, dsn=settings.dsn)

    @app.post(
        "/v1/materials/{material_id}/revisions",
        status_code=202,
        response_model=MaterialProcessingRunView,
    )
    def create_material_revision(
        request: Request, material_id: UUID, body: RevisionCreate
    ):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return project_material_run(
            create_revision(
                owner,
                material_id,
                body.normalization_ids,
                request_context.idempotency_key(request),
                deepcopy(settings.local_config),
                base_revision=body.base_revision,
                dsn=settings.dsn,
            )
        )

    @app.post(
        "/v1/material-processing-runs/{run_id}/cancel",
        response_model=MaterialProcessingRunView,
    )
    def cancel_material_revision(request: Request, run_id: UUID, body: RevisionCancel):
        from ..material_processing import request_material_processing_cancellation

        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        run = read_material_processing_run(owner, run_id, dsn=settings.dsn)
        if run.base_revision != body.base_revision:
            raise request_context.ApiFailure("REQUEST_INVALID")
        return project_material_run(
            request_material_processing_cancellation(
                owner,
                run_id,
                update_only=True,
                dsn=settings.dsn,
            )
        )

    @app.get(
        "/v1/materials/{material_id}/version-move",
        response_model=VersionMovePreviewView,
        response_model_by_alias=True,
        operation_id="previewVersionMove",
        tags=["materials"],
    )
    def preview_version_move_route(request: Request, material_id: UUID):
        from ..version_moves import preview_version_move

        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return preview_version_move(learner, material_id, dsn=settings.dsn)

    @app.post(
        "/v1/materials/{material_id}/version-move",
        response_model=VersionMoveView,
        response_model_by_alias=True,
        operation_id="executeVersionMove",
        tags=["materials"],
    )
    def execute_version_move_route(
        request: Request, material_id: UUID, body: VersionMoveCreate
    ):
        from ..version_moves import execute_version_move

        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return execute_version_move(
            learner,
            material_id,
            body.to_revision,
            body.card_set_ids,
            body.include_study,
            body.preview_digest,
            request_context.idempotency_key(request),
            dsn=settings.dsn,
        )

    @app.post(
        "/v1/material-processing-runs/{run_id}/retry",
        status_code=202,
        response_model=MaterialProcessingRunView,
        dependencies=[Depends(request_context.require_empty_body)],
    )
    def retry_material_revision(request: Request, run_id: UUID):
        from ..source_revisions import retry_revision

        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return project_material_run(
            retry_revision(
                owner,
                run_id,
                request_context.idempotency_key(request),
                deepcopy(settings.local_config),
                dsn=settings.dsn,
            )
        )

    @app.delete(
        "/v1/materials/{material_id}/sources/{source_id}",
        response_model=SourceListView,
        dependencies=[Depends(request_context.require_empty_body)],
    )
    def remove_material_source(request: Request, material_id: UUID, source_id: UUID):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        remove_staged_source(owner, material_id, source_id, dsn=settings.dsn)
        return read_sources(owner, material_id, dsn=settings.dsn)

    @app.get(
        "/v1/materials/{material_id}/knowledge-structures/{revision}/evidence/{evidence_id}/source",
        response_model=EvidenceSourceView,
    )
    def evidence_source(
        request: Request, material_id: UUID, revision: str, evidence_id: str
    ):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        return resolve_evidence_source(
            owner,
            material_id,
            revision,
            evidence_id,
            dsn=settings.dsn,
        )

    @app.get("/v1/artifacts/{artifact_id}/download", response_class=StreamingResponse)
    def original_artifact(request: Request, artifact_id: UUID):
        request_context.require_query(request, set())
        owner = request_context.trusted_learner(request, settings).learner_id
        with database_session(settings.dsn) as session:
            artifact = session.get(Artifact, artifact_id)
            if (
                artifact is None
                or artifact.learner_id != owner
                or artifact.kind != "original"
            ):
                raise request_context.ApiFailure("RESOURCE_NOT_FOUND")
            media = artifact.media_type
            original_name = session.scalar(
                select(MaterialSource.original_name).where(
                    MaterialSource.learner_id == owner,
                    MaterialSource.original_artifact_id == artifact_id,
                )
            )
        context = open_verified_artifact(owner, artifact_id, dsn=settings.dsn)
        try:
            source = context.__enter__()
        except Exception:
            raise request_context.ApiFailure("RESOURCE_NOT_FOUND") from None
        extension = next((ext for ext, mime in MIME.items() if mime == media), ".bin")
        download_name = original_name or ("material" + extension)
        disposition = (
            f'attachment; filename="material{extension}"; '
            f"filename*=UTF-8''{quote(download_name, safe='')}"
        )
        return StreamingResponse(
            _verified_source_iterator(context, source),
            media_type=media,
            headers={
                "Content-Disposition": disposition,
                "X-Content-Type-Options": "nosniff",
                "Content-Length": str(source.size_bytes),
            },
        )

    @app.delete(
        "/v1/materials/{material_id}",
        status_code=202,
        response_model=MaterialDiscardView,
        response_model_by_alias=True,
        operation_id="discardMaterial",
        tags=["materials"],
        dependencies=[Depends(request_context.require_empty_body)],
    )
    def discard_material_route(
        request: Request, material_id: UUID
    ) -> MaterialDiscardView:
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        state = request_material_discard(
            learner.learner_id,
            material_id,
            dsn=settings.dsn,
        )
        return MaterialDiscardView(material_id=material_id, state=state)

    @app.get(
        "/v1/material-processing-runs/{run_id}",
        response_model=MaterialProcessingRunView,
        response_model_by_alias=True,
        operation_id="getMaterialProcessingRun",
        tags=["material-processing"],
    )
    def read_material_run_route(
        request: Request, run_id: UUID
    ) -> MaterialProcessingRunView:
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        return project_material_run(
            read_material_processing_run(
                learner.learner_id,
                run_id,
                dsn=settings.dsn,
            )
        )

    install_podcast_routes(app, settings)

    install_card_routes(app, settings)

    install_study_routes(app, settings)

    @app.get(
        "/v1/artifacts/{artifact_id}",
        operation_id="getSourceArtifact",
        tags=["artifacts"],
        response_class=StreamingResponse,
    )
    def read_artifact_route(request: Request, artifact_id: UUID) -> StreamingResponse:
        request_context.require_query(request, set())
        learner = request_context.trusted_learner(request, settings)
        context = open_verified_artifact(
            learner.learner_id, artifact_id, dsn=settings.dsn, kind="normalized_pdf"
        )
        try:
            source = context.__enter__()
        except Exception:
            raise request_context.ApiFailure("RESOURCE_NOT_FOUND") from None
        return StreamingResponse(
            _verified_source_iterator(context, source),
            media_type="application/pdf",
            headers={
                "Content-Length": str(source.size_bytes),
                "ETag": f'"sha256:{source.sha256}"',
            },
        )

    install_scenes(app, settings)
    install_videos(app, settings)
    install_topics(app, settings)
    install_research(app, settings)
    install_voice(app, settings)
    _install_openapi(app)
    return app


def canonical_openapi_bytes(app: FastAPI) -> bytes:
    return (
        json.dumps(
            app.openapi(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")
