from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
from threading import local
import time
from typing import Any
from uuid import uuid4

import httpx
import pymupdf

from .concept_api import (
    ConceptAPIError,
    chat_completions_url,
    request_concept_text,
    start_concept_server,
)
from .concept_evidence_output import (
    RUNTIME_LOCK_SHA256,
    build_output,
    validate_page_evidence,
)
from .concept_generation import (
    SemanticOutputError,
    build_semantic_request,
    validate_concepts,
)
from .local_ai_process import LocalAIError, start_ocr_process
from .ocr_page_evidence import (
    build_page_evidence,
    canonical_bytes,
    canonical_sha256,
    extract_page,
)
from .source_pdf import build_whole_document_request, copy_source_snapshot
from .text_first_bundle import build_producer_bundle, publish_run


_PAGE_EXCLUSION_REASONS = {
    "NO_USABLE_EVIDENCE",
    "NO_USABLE_CONCEPT",
    "MODEL_OUTPUT_TOO_LARGE",
    "MODEL_OUTPUT_INVALID_JSON",
    "MODEL_OUTPUT_TRUNCATED",
    "CANDIDATE_SCHEMA_INVALID",
    "INVALID_CONCEPT_COUNT",
    "INVALID_TEXT_FIELD",
    "INVALID_KEY_POINTS",
    "INVALID_EVIDENCE_REFERENCES",
    "DUPLICATE_EVIDENCE_REFERENCE",
}
_AGENT1_OWNERSHIP = local()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _reason(error: Exception) -> str:
    if isinstance(error, (ConceptAPIError, SemanticOutputError, LocalAIError)):
        return error.reason_code
    value = str(error)
    allowed = {
        "MEDIA_TYPE_INVALID",
        "SOURCE_READ_FAILED",
        "SOURCE_HASH_MISMATCH",
        "PDF_INVALID",
        "PDF_ENCRYPTED",
        "RUNTIME_BINDING_INVALID",
        "RUNTIME_BUSY",
        "PROTOCOL_LIMIT_EXCEEDED",
        "CHILD_RESPONSE_INVALID",
        "OCR_OUTPUT_INVALID",
        "OCR_LOCATOR_INVALID",
        "NO_USABLE_EVIDENCE",
        "NO_USABLE_CONCEPT",
        "CACHE_WRITE_FAILED",
        "ARTIFACT_COLLISION",
        "FINAL_OUTPUT_WRITE_FAILED",
        "PRODUCER_BUNDLE_WRITE_FAILED",
        "PRODUCER_BUNDLE_INVALID",
    }
    return value if value in allowed else "INTERNAL_FAILURE"


def _validate_runtime_lock(runtime_lock: Any) -> None:
    try:
        matches = (
            isinstance(runtime_lock, dict)
            and canonical_sha256(runtime_lock) == RUNTIME_LOCK_SHA256
        )
    except (RecursionError, TypeError, ValueError):
        matches = False
    if not matches:
        raise ValueError("RUNTIME_BINDING_INVALID")


@contextmanager
def _agent1_lock(runtime_root: Path, *, wait_seconds: float = 5):
    """跨 process 同時只允許一個本機 OCR 與 Concept API sequence。"""

    if type(wait_seconds) not in {int, float} or wait_seconds < 0:
        raise ValueError("RUNTIME_BINDING_INVALID")
    if runtime_root.is_symlink():
        raise ValueError("RUNTIME_BINDING_INVALID")
    runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = runtime_root / "agent1.lock"
    if lock_path.is_symlink():
        raise ValueError("RUNTIME_BINDING_INVALID")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError("RUNTIME_BUSY") from None
                time.sleep(0.05)
        previous_root = getattr(_AGENT1_OWNERSHIP, "runtime_root", None)
        _AGENT1_OWNERSHIP.runtime_root = runtime_root
        try:
            yield
        finally:
            if previous_root is None:
                del _AGENT1_OWNERSHIP.runtime_root
            else:
                _AGENT1_OWNERSHIP.runtime_root = previous_root
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _has_agent1_ownership(runtime_root: Path) -> bool:
    """只在目前 thread 已實際取得相同 runtime lock 時回傳真。"""

    return getattr(_AGENT1_OWNERSHIP, "runtime_root", None) == runtime_root


def _excluded_page(
    page: dict[str, Any], stage: str, reason_code: str
) -> dict[str, Any]:
    """保留被排除頁的 identity 與 truthful bundle 狀態。"""

    return {
        "page_ref": page["page_ref"],
        "page_number": page["page_number"],
        "page_evidence_id": page.get("page_evidence_id"),
        "last_stage": stage,
        "processing": "failed",
        "quality": "needs_review",
        "decision": "reject",
        "reason_codes": [reason_code],
    }


def _cache_path(root: Path, operation: str, key: str) -> Path:
    return root / "cache" / operation / f"{key}.json"


def _semantic_artifact_valid(artifact: Any, binding: dict[str, Any]) -> bool:
    fields = {
        "schema",
        "page_ref",
        "concepts",
        "rejected_candidates",
        "input_binding",
        "attempt",
        "processing_policy",
        "processing",
        "quality",
        "decision",
        "reason_codes",
    }
    if (
        not isinstance(artifact, dict)
        or set(artifact) != fields
        or artifact["schema"] != "semantic-page-concepts/v1"
        or artifact["input_binding"] != binding
        or artifact["attempt"] not in (1, 2)
        or artifact["processing"]
        != ("partial" if artifact["rejected_candidates"] else "succeeded")
        or artifact["decision"] != "review"
        or not isinstance(artifact["concepts"], list)
        or not artifact["concepts"]
    ):
        return False
    allowed = set(binding["evidence_allowlist"])
    for concept in artifact["concepts"]:
        if (
            not isinstance(concept, dict)
            or set(concept)
            != {
                "concept_id",
                "page_ref",
                "label",
                "definition",
                "key_points",
                "evidence_ids",
                "processing",
                "quality",
                "decision",
                "reason_codes",
            }
            or concept["page_ref"] != artifact["page_ref"]
            or not set(concept["evidence_ids"]) <= allowed
            or concept["processing"] != "succeeded"
            or concept["decision"] != "review"
        ):
            return False
        identity = {
            "page_ref": artifact["page_ref"],
            "label": concept["label"],
            "definition": concept["definition"],
            "key_points": concept["key_points"],
            "evidence_ids": concept["evidence_ids"],
        }
        if concept["concept_id"] != f"concept:sha256:{canonical_sha256(identity)}":
            return False
    return True


def _read_cache(
    path: Path,
    operation: str,
    key: str,
    binding: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool]:
    if not path.exists() and not path.is_symlink():
        return None, False
    if path.parent.is_symlink() or path.is_symlink():
        return None, True
    try:
        encoded = path.read_bytes()
        if len(encoded) > 4 * 1024 * 1024:
            return None, True
        record = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_cache_without_duplicates,
            parse_constant=_reject_cache_constant,
        )
        _check_cache_depth(record)
    except (OSError, UnicodeDecodeError, ValueError):
        return None, True
    try:
        valid = (
            isinstance(record, dict)
            and set(record)
            == {"schema", "operation", "cache_key", "input_binding", "artifact_sha256", "artifact"}
            and record["schema"] == "text-first-verified-cache/v1"
            and record["operation"] == operation
            and record["cache_key"] == key
            and record["input_binding"] == binding
            and record["artifact_sha256"] == canonical_sha256(record["artifact"])
        )
        if valid and operation == "page":
            valid = validate_page_evidence(
                record["artifact"],
                {
                    "source_sha256": binding.get("source_sha256"),
                    "page_numbers": [binding.get("page_number")],
                },
                {"page": binding.get("page"), "ocr": binding.get("ocr")},
                formal_reasons=False,
            )
        elif valid and operation == "semantic":
            valid = _semantic_artifact_valid(record["artifact"], binding)
        else:
            valid = False
    except (KeyError, TypeError, ValueError):
        valid = False
    return (record["artifact"], False) if valid else (None, True)


def _cache_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("CACHE_INVALID")
        value[key] = item
    return value


def _reject_cache_constant(_: str) -> None:
    raise ValueError("CACHE_INVALID")


def _check_cache_depth(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("CACHE_INVALID")
    if isinstance(value, dict):
        for item in value.values():
            _check_cache_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_cache_depth(item, depth + 1)


def _write_cache(
    path: Path,
    operation: str,
    key: str,
    binding: dict[str, Any],
    artifact: dict[str, Any],
    *,
    replace_invalid: bool,
) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise OSError("CACHE_WRITE_FAILED")
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": "text-first-verified-cache/v1",
        "operation": operation,
        "cache_key": key,
        "input_binding": binding,
        "artifact_sha256": canonical_sha256(artifact),
        "artifact": artifact,
    }
    encoded = canonical_bytes(record)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix="cache-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(encoded)
        if os.path.lexists(path):
            if replace_invalid:
                os.replace(temporary, path)
            elif path.read_bytes() != encoded:
                raise FileExistsError("ARTIFACT_COLLISION")
        else:
            os.replace(temporary, path)
    except FileExistsError:
        raise
    except OSError as error:
        raise OSError("CACHE_WRITE_FAILED") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _process_pdf(
    request: dict[str, Any],
    settings: dict[str, Any],
    *,
    run_id: str,
    produced_at: str,
    runtime_binding_sha256: str,
) -> dict[str, Any]:
    """依序執行整份 PDF 的 OCR 與 Concept API。"""

    started = time.monotonic()
    ocr_calls = 0
    concept_calls = 0
    ocr_loads = 0
    concept_loads = 0
    page_count = 0
    cache_invalid = False
    excluded_pages: list[dict[str, Any]] = []
    snapshot_directory: tempfile.TemporaryDirectory[str] | None = None
    root = Path(settings["private_runtime_root"])
    runtime_lock = settings.get("runtime_lock")
    try:
        _validate_runtime_lock(runtime_lock)
        chat_completions_url(settings.get("concept_api_base_url"))
        if settings.get("concept_model") != runtime_lock["semantic"]["model_id"]:
            raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
        if (
            type(settings.get("concept_kv_cache_bytes")) is not int
            or settings["concept_kv_cache_bytes"] < 1
        ):
            raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
        if (
            type(settings.get("concept_max_concurrency")) is not int
            or settings["concept_max_concurrency"] not in {1, 2}
        ):
            raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
        if (
            type(settings.get("concept_max_model_len")) is not int
            or settings["concept_max_model_len"] < 1
        ):
            raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
        checked_request = build_whole_document_request(request)
        snapshot_directory = tempfile.TemporaryDirectory(prefix="studydy-source-")
        snapshot_path = Path(snapshot_directory.name) / "source.pdf"
        if copy_source_snapshot(checked_request["source_path"], snapshot_path) is not None:
            raise ValueError("SOURCE_READ_FAILED")
        checked_request = build_whole_document_request(
            {**request, "source_path": str(snapshot_path)}
        )
        source_path = Path(checked_request["source_path"])
        page_numbers = checked_request["page_numbers"]
        source_sha256 = checked_request["expected_source_sha256"]
        page_count = len(page_numbers)
        page_artifacts = []
        ocr = None
        document = pymupdf.open(source_path)
        try:
            for page_number in page_numbers:
                page = extract_page(document, source_sha256, page_number)
                try:
                    binding = {
                        "source_sha256": source_sha256,
                        "page_number": page_number,
                        "render_sha256": page["render"]["sha256"],
                        "page": runtime_lock["page"],
                        "ocr": runtime_lock["ocr"],
                    }
                    key = canonical_sha256(binding)
                    artifact, invalid = _read_cache(
                        _cache_path(root, "page", key), "page", key, binding
                    )
                    cache_invalid = cache_invalid or invalid
                    if artifact is None:
                        if ocr is None:
                            ocr = start_ocr_process(settings)
                            ocr_loads += 1
                        ocr_calls += 1
                        response = ocr.request(
                            {
                                "schema": "local-ocr-request/v1",
                                "request_id": f"page-{page_number}",
                                "render": {
                                    "sha256": page["render"]["sha256"],
                                    "width": page["render"]["width"],
                                    "height": page["render"]["height"],
                                    "png_base64": base64.b64encode(
                                        page["png_bytes"]
                                    ).decode("ascii"),
                                },
                            },
                            120,
                        )
                        if (
                            set(response) != {"schema", "request_id", "blocks"}
                            or response["schema"] != "local-ocr-response/v1"
                            or response["request_id"] != f"page-{page_number}"
                        ):
                            raise ValueError("CHILD_RESPONSE_INVALID")
                        artifact = build_page_evidence(
                            page,
                            response["blocks"],
                            input_binding=binding,
                            produced_at=produced_at,
                        )
                        _write_cache(
                            _cache_path(root, "page", key),
                            "page",
                            key,
                            binding,
                            artifact,
                            replace_invalid=invalid,
                        )
                    page_artifacts.append(artifact)
                except Exception as error:
                    reason_code = _reason(error)
                    if reason_code in _PAGE_EXCLUSION_REASONS:
                        excluded_pages.append(
                            _excluded_page(page, "page_evidence", reason_code)
                        )
                        continue
                    raise
                finally:
                    page.pop("png_bytes", None)
                    page.pop("native_evidence", None)
        except Exception:
            if ocr is not None:
                ocr.abort()
            raise
        else:
            if ocr is not None:
                ocr.close()
        finally:
            document.close()

        semantic_work = []
        for page in page_artifacts:
            try:
                semantic_request = build_semantic_request(page)
                binding = {
                    "page_evidence_sha256": canonical_sha256(page),
                    "semantic_request_sha256": canonical_sha256(semantic_request),
                    "evidence_allowlist": [
                        item["evidence_id"] for item in semantic_request["evidence"]
                    ],
                    "semantic": runtime_lock["semantic"],
                    "concept_api": {
                        "base_url": settings["concept_api_base_url"],
                        "model": settings["concept_model"],
                        "kv_cache_bytes": settings["concept_kv_cache_bytes"],
                        "max_concurrency": settings["concept_max_concurrency"],
                        "max_model_len": settings["concept_max_model_len"],
                    },
                }
                key = canonical_sha256(binding)
                artifact, invalid = _read_cache(
                    _cache_path(root, "semantic", key),
                    "semantic",
                    key,
                    binding,
                )
                cache_invalid = cache_invalid or invalid
                semantic_work.append(
                    {
                        "page": page,
                        "semantic_request": semantic_request,
                        "binding": binding,
                        "key": key,
                        "replace_invalid": invalid,
                        "artifact": artifact,
                        "error": None,
                    }
                )
            except Exception as error:
                reason_code = _reason(error)
                if reason_code in _PAGE_EXCLUSION_REASONS:
                    excluded_pages.append(_excluded_page(page, "concept", reason_code))
                    continue
                raise

        missing_semantic = [
            work for work in semantic_work if work["artifact"] is None
        ]
        concept_server = None
        concept_client: httpx.Client | None = None
        try:
            if missing_semantic:
                concept_loads += 1
                concept_server = start_concept_server(settings)
                concept_client = httpx.Client(
                    trust_env=False,
                    follow_redirects=False,
                )

            def generate(
                work: dict[str, Any],
            ) -> tuple[dict[str, Any] | None, int, Exception | None]:
                calls = 0
                try:
                    artifact = None
                    retry_policy = runtime_lock["semantic"]["retry"]
                    max_attempts = retry_policy["max_attempts"]
                    retryable_reasons = set(retry_policy["retryable_reasons"])
                    for attempt in range(1, max_attempts + 1):
                        try:
                            assert concept_client is not None
                            model_text = request_concept_text(
                                concept_client,
                                base_url=settings["concept_api_base_url"],
                                model=settings["concept_model"],
                                semantic_request=work["semantic_request"],
                                timeout_seconds=retry_policy["timeout_seconds"],
                            )
                            artifact = validate_concepts(
                                model_text,
                                semantic_request=work["semantic_request"],
                                page_ref=work["page"]["page_ref"],
                                input_binding=work["binding"],
                                attempt=attempt,
                            )
                            calls += 1
                            break
                        except (ConceptAPIError, SemanticOutputError) as error:
                            calls += 1
                            if (
                                attempt == max_attempts
                                or error.reason_code not in retryable_reasons
                            ):
                                raise
                    if artifact is None:
                        raise RuntimeError("INTERNAL_FAILURE")
                    _write_cache(
                        _cache_path(root, "semantic", work["key"]),
                        "semantic",
                        work["key"],
                        work["binding"],
                        artifact,
                        replace_invalid=work["replace_invalid"],
                    )
                    return artifact, calls, None
                except Exception as error:
                    return None, calls, error

            if missing_semantic:
                with ThreadPoolExecutor(
                    max_workers=settings["concept_max_concurrency"]
                ) as executor:
                    generated = executor.map(generate, missing_semantic)
                    for work, (artifact, calls, error) in zip(
                        missing_semantic, generated, strict=True
                    ):
                        work["artifact"] = artifact
                        work["error"] = error
                        concept_calls += calls
        finally:
            try:
                if concept_client is not None:
                    concept_client.close()
            finally:
                if concept_server is not None:
                    concept_server.close()

        semantic_pages = []
        included_pages = []
        for work in semantic_work:
            error = work["error"]
            if error is not None:
                reason_code = _reason(error)
                if reason_code in _PAGE_EXCLUSION_REASONS:
                    excluded_pages.append(
                        _excluded_page(work["page"], "concept", reason_code)
                    )
                    continue
                raise error
            semantic_pages.append(work["artifact"])
            included_pages.append(work["page"])
        output = build_output(
            run_id=run_id,
            produced_at=produced_at,
            source_binding={"source_sha256": source_sha256, "page_numbers": page_numbers},
            pages=included_pages,
            semantic_pages=semantic_pages,
            runtime_binding=runtime_lock,
            run_reasons=["CACHE_INVALID"] if cache_invalid else [],
            excluded_pages=excluded_pages,
        )
        bundle = build_producer_bundle(
            run_id=run_id,
            produced_at=produced_at,
            output=output,
            runtime_binding_sha256=runtime_binding_sha256,
            reasons=output["reason_codes"],
            duration_ms=int((time.monotonic() - started) * 1000),
            ocr_calls=ocr_calls,
            concept_calls=concept_calls,
            ocr_loads=ocr_loads,
            concept_loads=concept_loads,
            page_count=page_count,
            excluded_pages=excluded_pages,
        )
        publish_run(root, bundle, output)
        return bundle
    except Exception as error:
        reason = _reason(error)
        bundle = build_producer_bundle(
            run_id=run_id,
            produced_at=produced_at,
            output=None,
            runtime_binding_sha256=runtime_binding_sha256,
            reasons=[reason],
            duration_ms=int((time.monotonic() - started) * 1000),
            ocr_calls=ocr_calls,
            concept_calls=concept_calls,
            ocr_loads=ocr_loads,
            concept_loads=concept_loads,
            page_count=page_count,
            excluded_pages=excluded_pages,
        )
        try:
            publish_run(root, bundle, None)
        except Exception as bundle_error:
            raise RuntimeError("PRODUCER_BUNDLE_WRITE_FAILED") from bundle_error
        return bundle
    finally:
        if snapshot_directory is not None:
            snapshot_directory.cleanup()
def run_full_text_first_pdf(
    request: dict[str, Any],
    settings: dict[str, Any],
    *,
    run_id: str | None = None,
    produced_at: str | None = None,
    runtime_binding_sha256: str | None = None,
) -> dict[str, Any]:
    """鎖定全域本機模型後，處理 PDF 的精確 1..N 頁。"""

    root = Path(settings["private_runtime_root"])
    resolved_run_id = run_id or f"text-first-run:{uuid4()}"
    resolved_produced_at = produced_at or _now()
    resolved_binding_sha256 = runtime_binding_sha256 or canonical_sha256(
        settings.get("runtime_lock")
    )

    def execute() -> dict[str, Any]:
        return _process_pdf(
            request,
            settings,
            run_id=resolved_run_id,
            produced_at=resolved_produced_at,
            runtime_binding_sha256=resolved_binding_sha256,
        )

    try:
        if _has_agent1_ownership(root):
            return execute()
        with _agent1_lock(root):
            return execute()
    except ValueError as error:
        if _reason(error) != "RUNTIME_BUSY":
            raise
        bundle = build_producer_bundle(
            run_id=resolved_run_id,
            produced_at=resolved_produced_at,
            output=None,
            runtime_binding_sha256=resolved_binding_sha256,
            reasons=["RUNTIME_BUSY"],
            duration_ms=5_000,
            ocr_calls=0,
            concept_calls=0,
        )
        publish_run(root, bundle, None)
        return bundle
