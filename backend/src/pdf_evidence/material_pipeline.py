from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable
from uuid import uuid4

import httpx
import pymupdf

from knowledge_map.structure import (
    SemanticState,
    apply_semantic_response,
    build_document_context,
    build_knowledge_structure,
    build_semantic_bundles,
    semantic_request,
    semantic_response_schema,
)
from learning_resources.resources import load_resource_index
from runtime.semantic_service import (
    SemanticServiceError,
    material_request_fits,
    request_semantics,
    semantic_client,
)

from .local_ai_process import LocalAIError, start_ocr_process
from .ocr_page_evidence import (
    build_native_page_evidence,
    build_page_evidence,
    canonical_sha256,
    extract_page,
    route_page,
)
from .source_pdf import snapshot_whole_document_request


Progress = Callable[[str, int, int], None]


class MaterialAnalysisError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def validate_runtime_lock(lock: Any) -> dict[str, Any]:
    """Final lock 不允許第二 Python、verifier 或第二 semantic lifecycle。"""

    try:
        if not isinstance(lock, dict) or set(lock) != {
            "schema", "python", "packages", "ocr", "semantic_service",
            "material_semantics", "assessment",
        }:
            raise ValueError
        semantic = lock["semantic_service"]
        material = lock["material_semantics"]
        assessment = lock["assessment"]
        ocr = lock["ocr"]
        if (
            lock["schema"] != "studydy-runtime-lock/v15"
            or lock["python"] != "3.12"
            or lock["packages"] != {
                "studydy-local-ai": "0.1.0",
                "torch": "2.10.0+cu128",
                "torchvision": "0.25.0+cu128",
                "transformers": "4.57.1",
            }
            or set(ocr) != {
                "model_id", "revision", "prompt", "page_schema", "native_schema",
                "processing_policy", "normalizer_policy", "render",
            }
            or ocr["model_id"] != "Unlimited-OCR"
            or re.fullmatch(r"[0-9a-f]{40}", ocr["revision"]) is None
            or not isinstance(ocr["prompt"], str)
            or not ocr["prompt"]
            or ocr["render"] != {"dpi": 200, "colorspace": "RGB", "format": "PNG"}
            or set(semantic) != {
                "model_id", "revision", "api_protocol", "base_url", "max_model_len",
                "max_num_seqs", "server", "authentication",
            }
            or semantic["model_id"] != "Qwen/Qwen3.8-27B-FP8"
            or re.fullmatch(r"[0-9a-f]{40}", semantic["revision"]) is None
            or semantic["api_protocol"] != "openai-chat-completions/v1"
            or semantic["base_url"] != "http://127.0.0.1:8000"
            or semantic["max_model_len"] != 32768
            or semantic["max_num_seqs"] != 1
            or semantic["server"] != {
                "package": "vllm",
                "version": "0.28.0",
                "python": "3.12",
                "torch": "2.13.0+cu130",
                "cuda": "13.0",
                "transformers": "5.16.1",
            }
            or semantic["authentication"] != "environment-bearer:VLLM_API_KEY"
            or set(material) != {
                "request_schema", "response_schema", "bundle_policy",
                "max_tokens", "prompt", "retry_attempts", "generation",
            }
            or material["request_schema"] != "material-semantics-request/v2"
            or material["response_schema"] != "material-semantics-response/v2"
            or material["bundle_policy"] != "tokenized-contiguous-evidence/v2"
            or material["max_tokens"] != 4096
            or material["generation"] != {
                "temperature": 1.0, "top_p": 0.95, "top_k": 20,
                "min_p": 0.0, "presence_penalty": 0.0, "repetition_penalty": 1.0,
                "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "xhigh"},
            }
            or not isinstance(material["prompt"], str)
            or not material["prompt"]
            or set(assessment) != {
                "request_schema", "response_schema", "public_schema", "private_schema",
                "provenance_schema", "policy", "candidate_count", "option_count",
                "max_tokens", "generation", "prompt",
            }
            or assessment["request_schema"] != "assessment-semantics-request/v1"
            or assessment["response_schema"] != "assessment-semantics-response/v2"
            or assessment["public_schema"] != "single-choice-assessment/v2"
            or assessment["private_schema"] != "single-choice-answer/v2"
            or assessment["provenance_schema"] != "assessment-generation-provenance/v5"
            or assessment["policy"] != "source-span-single-choice/v4"
            or assessment["candidate_count"] != 3
            or assessment["option_count"] != 4
            or assessment["max_tokens"] != 4096
            or assessment["generation"] != {"chat_template_kwargs": {"enable_thinking": False}}
            or not isinstance(assessment["prompt"], str)
            or not assessment["prompt"]
            or ocr["page_schema"] != "page-evidence/v4"
            or ocr["native_schema"] != "page-native/v3"
            or ocr["processing_policy"] != "native-first-page-evidence/v3"
            or ocr["normalizer_policy"] != "ocr-text-nfc-line-preserving/v1"
            or material["retry_attempts"] != 2
        ):
            raise ValueError
        return lock
    except (KeyError, TypeError, ValueError):
        raise MaterialAnalysisError("RUNTIME_LOCK_INVALID") from None


@contextmanager
def material_analysis_lock(runtime_root: Path, *, wait_seconds: float = 5):
    """避免同時載入多個 OCR sidecar；resident Qwen lifecycle 不在此鎖內。"""

    if not runtime_root.is_absolute() or runtime_root.is_symlink() or wait_seconds < 0:
        raise MaterialAnalysisError("RUNTIME_LAYOUT_INVALID")
    runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = runtime_root / "material-analysis.lock"
    if lock_path.is_symlink():
        raise MaterialAnalysisError("RUNTIME_LAYOUT_INVALID")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise MaterialAnalysisError("RUNTIME_BUSY") from None
                time.sleep(0.05)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _reason(error: Exception) -> str:
    reason = getattr(error, "reason_code", None) or str(error)
    allowed = {
        "CHILD_EXITED", "CHILD_TIMEOUT", "CHILD_RESPONSE_INVALID",
        "OCR_OUTPUT_INVALID", "OCR_LOCATOR_INVALID", "NO_USABLE_EVIDENCE",
        "PROTOCOL_LIMIT_EXCEEDED", "SEMANTIC_SERVICE_TIMEOUT",
        "SEMANTIC_SERVICE_UNAVAILABLE", "SEMANTIC_RESPONSE_INVALID",
        "SEMANTIC_OUTPUT_INVALID", "SEMANTIC_OUTPUT_TRUNCATED",
    }
    return reason if reason in allowed else "MATERIAL_ANALYSIS_FAILED"


def _excluded(page: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "page_ref": page["page_ref"],
        "page": page["page_number"],
        "stage": "evidence",
        "reason_code": reason,
    }


def _page_evidence(
    source_path: Path,
    source_sha256: str,
    page_numbers: list[int],
    settings: dict[str, Any],
    produced_at: str,
    report: Progress,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    pages: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    ocr_calls = 0
    ocr = None
    document = pymupdf.open(source_path)
    try:
        for completed, page_number in enumerate(page_numbers, start=1):
            page = extract_page(document, source_sha256, page_number)
            try:
                route = route_page(page)
                binding = {
                    "source_sha256": source_sha256,
                    "page": page_number,
                    "render_sha256": page["render"]["sha256"],
                    "route": route,
                    "runtime_lock_sha256": canonical_sha256(settings["runtime_lock"]),
                }
                if route == "native_sufficient":
                    artifact = build_native_page_evidence(page, input_binding=binding, produced_at=produced_at)
                else:
                    if ocr is None:
                        ocr = start_ocr_process(settings)
                    ocr_calls += 1
                    response = ocr.request(
                        {
                            "schema": "local-ocr-request/v1",
                            "request_id": f"page-{page_number}",
                            "render": {
                                "sha256": page["render"]["sha256"],
                                "width": page["render"]["width"],
                                "height": page["render"]["height"],
                                "png_base64": base64.b64encode(page["png_bytes"]).decode("ascii"),
                            },
                        },
                        None,
                    )
                    if set(response) != {"schema", "request_id", "blocks"} or response["schema"] != "local-ocr-response/v1" or response["request_id"] != f"page-{page_number}":
                        raise LocalAIError("CHILD_RESPONSE_INVALID")
                    artifact = build_page_evidence(page, response["blocks"], input_binding=binding, produced_at=produced_at)
                pages.append(artifact)
            except (LocalAIError, ValueError) as error:
                excluded.append(_excluded(page, _reason(error)))
            finally:
                page.pop("png_bytes", None)
                page.pop("native_evidence", None)
                report("evidence", completed, len(page_numbers))
    except Exception:
        if ocr is not None:
            ocr.abort()
        raise
    else:
        if ocr is not None:
            ocr.close()
    finally:
        document.close()
    if not pages:
        raise MaterialAnalysisError("NO_USABLE_EVIDENCE")
    return pages, excluded, ocr_calls


def analyze_material(
    request: dict[str, Any],
    settings: dict[str, Any],
    *,
    run_id: str | None = None,
    produced_at: str | None = None,
    progress_callback: Progress | None = None,
    client: httpx.Client | None = None,
    semantic_call: Callable[..., dict[str, Any]] = request_semantics,
) -> dict[str, Any]:
    """Evidence → unified Qwen semantics → deterministic canonical structure。"""

    lock = validate_runtime_lock(settings.get("runtime_lock"))
    resolved_run = run_id or str(uuid4())
    resolved_time = produced_at or datetime.now(UTC).isoformat()
    report = progress_callback or (lambda _stage, _completed, _total: None)
    runtime_root = Path(settings["private_runtime_root"])
    with material_analysis_lock(runtime_root):
        evidence_started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="studydy-source-") as directory:
            snapshot = Path(directory) / "source.pdf"
            checked = snapshot_whole_document_request(request, snapshot)
            page_numbers = checked["page_numbers"]
            pages, excluded, ocr_calls = _page_evidence(
                snapshot,
                checked["expected_source_sha256"],
                page_numbers,
                settings,
                resolved_time,
                report,
            )
        evidence_duration_ms = round((time.monotonic() - evidence_started) * 1000)
        context = build_document_context(
            pages, page_count=len(page_numbers), excluded_pages=excluded
        )
        state = SemanticState()
        semantic_calls = 0
        semantic_started = time.monotonic()
        owned_client = client is None
        http = semantic_client() if client is None else client
        try:
            for bundle in build_semantic_bundles(
                context, state=state,
                fits=lambda request: material_request_fits(http, lock, request),
            ):
                request_document = semantic_request(context, bundle, state)
                last_error: Exception | None = None
                for _attempt in range(lock["material_semantics"]["retry_attempts"]):
                    candidate_state = deepcopy(state)
                    try:
                        semantic_calls += 1
                        response = semantic_call(
                            http,
                            runtime_lock=lock,
                            task="material_semantics",
                            request=request_document,
                            response_schema=semantic_response_schema(),
                        )
                        apply_semantic_response(
                            response,
                            context=context,
                            bundle=bundle,
                            state=candidate_state,
                        )
                        state.concepts = candidate_state.concepts
                        state.relations = candidate_state.relations
                        state.rejected_claims = candidate_state.rejected_claims
                        state.rejected_relations = candidate_state.rejected_relations
                        state.literal_repairs = candidate_state.literal_repairs
                        last_error = None
                        break
                    except SemanticServiceError as error:
                        last_error = error
                    except ValueError as error:
                        last_error = error
                if last_error is not None:
                    raise MaterialAnalysisError(_reason(last_error)) from None
                report("semantics", len(page_numbers), len(page_numbers))
        finally:
            if owned_client:
                http.close()
        semantic_duration_ms = round((time.monotonic() - semantic_started) * 1000)
    service = lock["semantic_service"]
    return build_knowledge_structure(
        context,
        state,
        source_sha256=checked["expected_source_sha256"],
        run_id=resolved_run,
        produced_at=resolved_time,
        runtime_lock_sha256=canonical_sha256(lock),
        model_id=service["model_id"],
        model_revision=service["revision"],
        semantic_calls=semantic_calls,
        ocr_calls=ocr_calls,
        evidence_duration_ms=evidence_duration_ms,
        semantic_duration_ms=semantic_duration_ms,
        resource_index=load_resource_index(),
    )
