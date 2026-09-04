from __future__ import annotations

import base64
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
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
    fit_concept_request,
    request_concept_text,
    semantic_service_client,
)
from .concept_evidence_output import (
    RUNTIME_LOCK_SHA256,
    build_output,
    validate_page_evidence,
)
from .concept_generation import (
    claim_id,
    SemanticOutputError,
    build_semantic_request,
    combine_semantic_batches,
    failed_semantic_page,
    fitted_semantic_request_matches_source,
    split_semantic_request,
    validate_semantic_request,
    validate_concepts,
)
from .document_context import (
    build_document_contexts,
    validate_document_context,
)
from .local_ai_process import LocalAIError, start_ocr_process
from .ocr_page_evidence import (
    build_native_page_evidence,
    build_page_evidence,
    canonical_bytes,
    canonical_sha256,
    extract_page,
    route_page,
)
from .source_pdf import snapshot_whole_document_request
from .text_first_bundle import build_producer_bundle, publish_run


_PAGE_EXCLUSION_REASONS = {
    "NO_USABLE_EVIDENCE",
}
_CONCEPT_FAILURE_REASONS = {
    "NO_USABLE_CONCEPT",
    "MODEL_OUTPUT_TOO_LARGE",
    "MODEL_OUTPUT_INVALID_JSON",
    "MODEL_OUTPUT_TRUNCATED",
    "CANDIDATE_SCHEMA_INVALID",
    "INVALID_CONCEPT_COUNT",
    "INVALID_TEXT_FIELD",
    "INVALID_CLAIMS",
    "INVALID_EVIDENCE_REFERENCES",
    "CONCEPT_API_RESPONSE_INVALID",
    "CONCEPT_API_TIMEOUT",
    "CONCEPT_API_UNAVAILABLE",
}
_MATERIAL_ANALYSIS_OWNERSHIP = local()


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
        semantic = runtime_lock["semantic"]
        verifier_model = runtime_lock["verifier_model"]
        concept_equivalence = runtime_lock["concept_equivalence"]
        matches = (
            isinstance(runtime_lock, dict)
            and set(runtime_lock) == {
                "schema", "python", "packages", "page", "ocr", "semantic",
                "formal_resolution", "concept_equivalence", "verifier_model",
            }
            and canonical_sha256(runtime_lock) == RUNTIME_LOCK_SHA256
            and runtime_lock["schema"] == "studydy-local-ai-runtime-lock/v14"
            and runtime_lock["python"] == {"version": "3.12"}
            and semantic["model_id"] == "Qwen/Qwen3.8-27B-FP8"
            and semantic["revision"]
            == "017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"
            and semantic["service"]
            == {
                "host": "127.0.0.1",
                "port": 8000,
                "max_model_len": 32768,
                "max_num_seqs": 1,
                "authentication": "environment-bearer:VLLM_API_KEY",
            }
            and all(
                hashlib.sha256(runtime_lock[stage]["prompt"].encode("utf-8")).hexdigest()
                == runtime_lock[stage]["prompt_sha256"]
                for stage in ("semantic", "formal_resolution")
            )
            and runtime_lock["semantic"]["document_context"]
            == {
                "schema": "document-semantic-context/v1",
                "concept_envelope_schema": "concept-context-envelope/v3",
                "processing_policy": "document-reading-order-context/v1",
                "token_budget": 1024,
                "token_counter": "utf8-byte-upper-bound/v1",
                "model_calls": 0,
                "section_policy": "nearest-grounded-heading-flat-section/v1",
                "ambiguous_hierarchy": "needs-review/v1",
                "dispatch_fit": "evidence-only-batch-preserving/v1",
                "batch_binding": "exact-fitted-request-lineage/v1",
                "durable_output_schema": "concept-evidence-output/v6",
                "study_projection_schema": "study-material-output/v8",
            }
            and verifier_model["model_id"]
            == "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
            and verifier_model["revision"]
            == "8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c"
            and verifier_model["safe_loading"]
            == "safetensors-local-only-no-remote-code"
            and concept_equivalence
            == {
                "model_id": verifier_model["model_id"],
                "revision": verifier_model["revision"],
                "request_schema": "local-concept-equivalence-request/v1",
                "response_schema": "local-concept-equivalence-response/v1",
                "startup_schema": "local-concept-equivalence-startup/v1",
                "representation": "label-claims-evidence-headings/v1",
                "decision_rule": (
                    "bidirectional-entailment-threshold-and-argmax/v1"
                ),
                "entailment_threshold": 0.8,
                "maximum_tokens": 384,
                "startup_timeout_seconds": 120,
                "failure_policy": "veto-and-retain/v1",
                "safe_loading": "safetensors-local-only-no-remote-code",
            }
        )
    except (KeyError, RecursionError, TypeError, ValueError):
        matches = False
    if not matches:
        raise ValueError("RUNTIME_BINDING_INVALID")


@contextmanager
def material_analysis_lock(runtime_root: Path, *, wait_seconds: float = 5):
    """跨 process 同時只允許一個 GPU-heavy backend sequence。"""

    if type(wait_seconds) not in {int, float} or wait_seconds < 0:
        raise ValueError("RUNTIME_BINDING_INVALID")
    if runtime_root.is_symlink():
        raise ValueError("RUNTIME_BINDING_INVALID")
    runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = runtime_root / "material-analysis.lock"
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
        previous_root = getattr(_MATERIAL_ANALYSIS_OWNERSHIP, "runtime_root", None)
        _MATERIAL_ANALYSIS_OWNERSHIP.runtime_root = runtime_root
        try:
            yield
        finally:
            if previous_root is None:
                del _MATERIAL_ANALYSIS_OWNERSHIP.runtime_root
            else:
                _MATERIAL_ANALYSIS_OWNERSHIP.runtime_root = previous_root
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _has_material_analysis_ownership(runtime_root: Path) -> bool:
    """只在目前 thread 已實際取得相同 runtime lock 時回傳真。"""

    return (
        getattr(_MATERIAL_ANALYSIS_OWNERSHIP, "runtime_root", None)
        == runtime_root
    )


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
        or artifact["schema"] != "semantic-page-concepts/v4"
        or artifact["input_binding"] != binding
        or artifact["attempt"] not in (1, 2)
        or artifact["processing"]
        != (
            "partial"
            if artifact["rejected_candidates"]
            or any(
                concept.get("processing") == "partial"
                for concept in artifact.get("concepts", [])
            )
            else "succeeded"
        )
        or artifact["decision"] != "review"
        or not isinstance(artifact["concepts"], list)
    ):
        return False
    batch_bindings = binding.get("batch_bindings")
    if not _semantic_batch_bindings_valid(batch_bindings, binding):
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
                "claims",
                "processing",
                "quality",
                "decision",
                "reason_codes",
            }
            or concept["page_ref"] != artifact["page_ref"]
            or concept["processing"] not in {"succeeded", "partial"}
            or concept["decision"] != "review"
        ):
            return False
        claims = concept["claims"]
        if any(
            not isinstance(claim, dict)
            or set(claim) != {"claim_id", "text", "evidence_ids"}
            or not claim["evidence_ids"]
            or len(claim["evidence_ids"]) != len(set(claim["evidence_ids"]))
            or not set(claim["evidence_ids"]) <= allowed
            for claim in claims
        ):
            return False
        if any(
            claim["claim_id"]
            != claim_id(
                artifact["page_ref"],
                {"text": claim["text"], "evidence_ids": claim["evidence_ids"]},
                index=index,
            )
            for index, claim in enumerate(claims)
        ):
            return False
        identity = {
            "page_ref": artifact["page_ref"],
            "label": concept["label"],
            "claims": concept["claims"],
        }
        if concept["concept_id"] != f"concept:sha256:{canonical_sha256(identity)}":
            return False
    return True


def _semantic_batch_bindings_valid(
    batch_bindings: Any,
    binding: dict[str, Any],
) -> bool:
    if not isinstance(batch_bindings, list):
        return False
    try:
        source_request = binding["source_semantic_request"]
        if not validate_semantic_request(source_request):
            return False
        for index, batch in enumerate(batch_bindings):
            if (
                not isinstance(batch, dict)
                or set(batch)
                != {"batch_index", "semantic_request_sha256", "semantic_request"}
                or batch["batch_index"] != index
                or not isinstance(batch["semantic_request_sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", batch["semantic_request_sha256"])
                is None
                or canonical_sha256(batch["semantic_request"])
                != batch["semantic_request_sha256"]
                or not validate_semantic_request(batch["semantic_request"])
                or not fitted_semantic_request_matches_source(
                    batch["semantic_request"], source_request
                )
            ):
                return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _is_heading_only_page(page: dict[str, Any]) -> bool:
    """只有明確 heading Evidence 的頁面不建立學習 Concept。"""

    evidence_blocks = page.get("evidence_blocks")
    return (
        isinstance(evidence_blocks, list)
        and bool(evidence_blocks)
        and all(
            isinstance(block, dict) and block.get("kind") == "heading"
            for block in evidence_blocks
        )
    )


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
            == {
                "schema", "operation", "lookup_key", "lookup_binding",
                "cache_key", "input_binding", "artifact_sha256", "artifact",
            }
            and record["schema"] == "text-first-verified-cache/v2"
            and record["operation"] == operation
            and record["lookup_key"] == key
            and record["lookup_binding"] == binding
            and record["cache_key"] == canonical_sha256(record["input_binding"])
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
            valid = _semantic_artifact_valid(
                record["artifact"], record["input_binding"]
            )
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
    input_binding: dict[str, Any] | None = None,
) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise OSError("CACHE_WRITE_FAILED")
    path.parent.mkdir(parents=True, exist_ok=True)
    exact_binding = input_binding or binding
    record = {
        "schema": "text-first-verified-cache/v2",
        "operation": operation,
        "lookup_key": key,
        "lookup_binding": binding,
        "cache_key": canonical_sha256(exact_binding),
        "input_binding": exact_binding,
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
    progress_callback: Callable[[str, int, int], None] | None = None,
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

    def report_progress(stage: str, completed: int, total: int) -> None:
        if progress_callback is not None:
            progress_callback(stage, completed, total)

    try:
        _validate_runtime_lock(runtime_lock)
        chat_completions_url(settings.get("concept_api_base_url"))
        if settings.get("concept_model") != runtime_lock["semantic"]["model_id"]:
            raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
        if (
            type(settings.get("concept_max_concurrency")) is not int
            or settings["concept_max_concurrency"] != 1
        ):
            raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
        if (
            type(settings.get("concept_max_model_len")) is not int
            or settings["concept_max_model_len"] != 32_768
        ):
            raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
        snapshot_directory = tempfile.TemporaryDirectory(prefix="studydy-source-")
        snapshot_path = Path(snapshot_directory.name) / "source.pdf"
        checked_request = snapshot_whole_document_request(
            request, snapshot_path
        )
        source_path = Path(checked_request["source_path"])
        page_numbers = checked_request["page_numbers"]
        source_sha256 = checked_request["expected_source_sha256"]
        page_count = len(page_numbers)
        report_progress("page_evidence", 0, page_count)
        page_artifacts = []
        completed_page_evidence = 0
        ocr = None
        document = pymupdf.open(source_path)
        try:
            for page_number in page_numbers:
                page = extract_page(document, source_sha256, page_number)
                try:
                    route = route_page(page)
                    binding = {
                        "source_sha256": source_sha256,
                        "page_number": page_number,
                        "render_sha256": page["render"]["sha256"],
                        "route": route,
                        "page": runtime_lock["page"],
                        "ocr": runtime_lock["ocr"],
                    }
                    key = canonical_sha256(binding)
                    artifact, invalid = _read_cache(
                        _cache_path(root, "page", key), "page", key, binding
                    )
                    cache_invalid = cache_invalid or invalid
                    if artifact is None:
                        if route == "native_sufficient":
                            artifact = build_native_page_evidence(
                                page,
                                input_binding=binding,
                                produced_at=produced_at,
                            )
                        else:
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
                                None,
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
                    completed_page_evidence += 1
                    report_progress(
                        "page_evidence", completed_page_evidence, page_count
                    )
        except Exception:
            if ocr is not None:
                ocr.abort()
            raise
        else:
            if ocr is not None:
                ocr.close()
        finally:
            document.close()

        completed_concepts = page_count - len(page_artifacts)
        report_progress("concept_generation", completed_concepts, page_count)
        document_contexts = (
            build_document_contexts(page_artifacts) if page_artifacts else []
        )
        contexts_by_page = {
            context["page_ref"]: context for context in document_contexts
        }
        if (
            len(contexts_by_page) != len(page_artifacts)
            or any(
                not validate_document_context(context, page_artifacts)
                for context in document_contexts
            )
        ):
            raise ValueError("PRODUCER_BUNDLE_INVALID")
        semantic_work = []
        for page in page_artifacts:
            try:
                document_context = contexts_by_page[page["page_ref"]]
                semantic_request, evidence_aliases = build_semantic_request(
                    page, document_context
                )
                source_binding = {
                    "page_evidence_sha256": canonical_sha256(page),
                    "source_context_sha256": canonical_sha256(document_context),
                    "source_semantic_request": deepcopy(semantic_request),
                    "evidence_allowlist": list(evidence_aliases.values()),
                    "semantic": runtime_lock["semantic"],
                    "concept_api": {
                        "base_url": settings["concept_api_base_url"],
                        "model": settings["concept_model"],
                        "max_concurrency": settings["concept_max_concurrency"],
                        "max_model_len": settings["concept_max_model_len"],
                    },
                }
                key = canonical_sha256(source_binding)
                artifact, invalid = _read_cache(
                    _cache_path(root, "semantic", key),
                    "semantic",
                    key,
                    source_binding,
                )
                cache_invalid = cache_invalid or invalid
                if artifact is None and _is_heading_only_page(page):
                    exact_binding = {
                        **source_binding,
                        "batch_bindings": [],
                    }
                    artifact = validate_concepts(
                        '{"concepts":[]}',
                        semantic_request=semantic_request,
                        evidence_aliases=evidence_aliases,
                        page_ref=page["page_ref"],
                        input_binding=exact_binding,
                        attempt=1,
                    )
                    _write_cache(
                        _cache_path(root, "semantic", key),
                        "semantic",
                        key,
                        source_binding,
                        artifact,
                        replace_invalid=invalid,
                        input_binding=exact_binding,
                    )
                semantic_work.append(
                    {
                        "page": page,
                        "semantic_request": semantic_request,
                        "evidence_aliases": evidence_aliases,
                        "source_binding": source_binding,
                        "key": key,
                        "replace_invalid": invalid,
                        "artifact": artifact,
                        "error": None,
                    }
                )
                if artifact is not None:
                    completed_concepts += 1
                    report_progress(
                        "concept_generation", completed_concepts, page_count
                    )
            except Exception as error:
                reason_code = _reason(error)
                if reason_code in _CONCEPT_FAILURE_REASONS:
                    semantic_work.append(
                        {
                            "page": page,
                            "artifact": failed_semantic_page(
                                page_ref=page["page_ref"],
                                input_binding={"batch_bindings": []},
                                reason_code=reason_code,
                            ),
                            "error": None,
                        }
                    )
                    completed_concepts += 1
                    report_progress(
                        "concept_generation", completed_concepts, page_count
                    )
                    continue
                raise

        missing_semantic = [
            work for work in semantic_work if work["artifact"] is None
        ]
        concept_client: httpx.Client | None = None
        try:
            if missing_semantic:
                concept_client = semantic_service_client()

            def generate(
                work: dict[str, Any],
            ) -> tuple[dict[str, Any] | None, int, Exception | None]:
                calls = 0
                try:
                    retry_policy = runtime_lock["semantic"]["retry"]
                    max_attempts = retry_policy["max_attempts"]
                    retryable_reasons = set(retry_policy["retryable_reasons"])
                    pending = [work["semantic_request"]]
                    batches = []
                    batch_bindings = []
                    while pending:
                        request = pending.pop(0)
                        artifact = None
                        for attempt in range(1, max_attempts + 1):
                            try:
                                assert concept_client is not None
                                fitted_request = fit_concept_request(
                                    concept_client,
                                    base_url=settings["concept_api_base_url"],
                                    model=settings["concept_model"],
                                    prompt_template=runtime_lock["semantic"]["prompt"],
                                    semantic_request=request,
                                    max_model_len=settings["concept_max_model_len"],
                                )
                                validate_semantic_request(fitted_request)
                                batch_binding = {
                                    "batch_index": len(batch_bindings),
                                    "semantic_request_sha256": canonical_sha256(
                                        fitted_request
                                    ),
                                    "semantic_request": deepcopy(fitted_request),
                                }
                                model_text = request_concept_text(
                                    concept_client,
                                    base_url=settings["concept_api_base_url"],
                                    model=settings["concept_model"],
                                    prompt_template=runtime_lock["semantic"]["prompt"],
                                    semantic_request=fitted_request,
                                    max_model_len=settings["concept_max_model_len"],
                                    already_fitted=True,
                                )
                                calls += 1
                                aliases = {
                                    item["id"]: work["evidence_aliases"][item["id"]]
                                    for item in fitted_request["evidence"]
                                }
                                artifact = validate_concepts(
                                    model_text,
                                    semantic_request=fitted_request,
                                    evidence_aliases=aliases,
                                    page_ref=work["page"]["page_ref"],
                                    input_binding={
                                        **work["source_binding"],
                                        "batch_bindings": [batch_binding],
                                    },
                                    attempt=attempt,
                                )
                                batch_bindings.append(batch_binding)
                                break
                            except ConceptAPIError as error:
                                if error.reason_code == "MODEL_INPUT_TOO_LARGE":
                                    first, second = split_semantic_request(request)
                                    pending[0:0] = [first, second]
                                    break
                                calls += 1
                                if (
                                    attempt == max_attempts
                                    or error.reason_code not in retryable_reasons
                                ):
                                    raise
                            except SemanticOutputError as error:
                                if (
                                    attempt == max_attempts
                                    or error.reason_code not in retryable_reasons
                                ):
                                    raise
                        if artifact is not None:
                            batches.append(artifact)
                    exact_binding = {
                        **work["source_binding"],
                        "batch_bindings": batch_bindings,
                    }
                    artifact = combine_semantic_batches(
                        batches,
                        page_ref=work["page"]["page_ref"],
                        input_binding=exact_binding,
                    )
                    _write_cache(
                        _cache_path(root, "semantic", work["key"]),
                        "semantic",
                        work["key"],
                        work["source_binding"],
                        artifact,
                        replace_invalid=work["replace_invalid"],
                        input_binding=exact_binding,
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
                        completed_concepts += 1
                        report_progress(
                            "concept_generation", completed_concepts, page_count
                        )
        finally:
            if concept_client is not None:
                concept_client.close()

        semantic_pages = []
        included_pages = []
        for work in semantic_work:
            error = work["error"]
            if error is not None:
                reason_code = _reason(error)
                if reason_code in _CONCEPT_FAILURE_REASONS:
                    semantic_pages.append(
                        failed_semantic_page(
                            page_ref=work["page"]["page_ref"],
                            input_binding={
                                **work["source_binding"],
                                "batch_bindings": [],
                            },
                            reason_code=reason_code,
                        )
                    )
                    included_pages.append(work["page"])
                    continue
                raise error
            semantic_pages.append(work["artifact"])
            included_pages.append(work["page"])
        if not included_pages:
            bundle = build_producer_bundle(
                run_id=run_id,
                produced_at=produced_at,
                output=None,
                runtime_binding_sha256=runtime_binding_sha256,
                reasons=[
                    reason
                    for page in excluded_pages
                    for reason in page["reason_codes"]
                ],
                duration_ms=int((time.monotonic() - started) * 1000),
                ocr_calls=ocr_calls,
                concept_calls=concept_calls,
                ocr_loads=ocr_loads,
                concept_loads=concept_loads,
                page_count=page_count,
                excluded_pages=excluded_pages,
            )
            publish_run(root, bundle, None)
            return bundle
        output = build_output(
            run_id=run_id,
            produced_at=produced_at,
            source_binding={"source_sha256": source_sha256, "page_numbers": page_numbers},
            pages=included_pages,
            context_pages=page_artifacts,
            document_contexts=[
                contexts_by_page[page["page_ref"]]
                for page in included_pages
            ],
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
    progress_callback: Callable[[str, int, int], None] | None = None,
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
            progress_callback=progress_callback,
        )

    try:
        if _has_material_analysis_ownership(root):
            return execute()
        with material_analysis_lock(root):
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
