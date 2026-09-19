from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
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
            lock["schema"] != "studydy-runtime-lock/v18"
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
            or semantic["model_id"] != "google/gemma-4-31B-it-qat-w4a16-ct"
            or semantic["revision"] != "52f3f65bc7a02d555763bc923bd1d9094898219d"
            or semantic["api_protocol"] != "openai-chat-completions/v1"
            or semantic["base_url"] != "http://127.0.0.1:18000"
            or semantic["max_model_len"] != 32768
            or semantic["max_num_seqs"] != 1
            or semantic["server"] != {
                "package": "vllm",
                "version": "0.28.0",
                "python": "3.12",
                "torch": "2.13.0+cu130",
                "cuda": "13.0",
                "transformers": "5.15.1",
            }
            or semantic["authentication"] != "environment-bearer:VLLM_API_KEY"
            or set(material) != {
                "request_schema", "response_schema", "bundle_policy",
                "max_tokens", "prompt", "retry_attempts", "generation", "max_new_input_tokens",
            }
            or material["request_schema"] != "material-semantics-request/v3"
            or material["response_schema"] != "material-semantics-response/v5"
            or material["bundle_policy"] != "contiguous-evidence-new-input/v4"
            or material["max_new_input_tokens"] != 1536
            or material["max_tokens"] != 8192
            or material["generation"] != {
                "temperature": 1.0, "top_p": 0.95, "top_k": 64,
                "chat_template_kwargs": {"enable_thinking": True},
            }
            or not isinstance(material["prompt"], str)
            or not material["prompt"]
            or set(assessment) != {
                "request_schema", "response_schema", "public_schema", "private_schema",
                "provenance_schema", "policy", "candidate_count", "option_count",
                "max_tokens", "generation", "prompt", "check_max_tokens", "check_generation", "check_prompt",
            }
            or assessment["request_schema"] != "assessment-semantics-request/v1"
            or assessment["response_schema"] != "assessment-semantics-response/v2"
            or assessment["public_schema"] != "single-choice-assessment/v2"
            or assessment["private_schema"] != "single-choice-answer/v2"
            or assessment["provenance_schema"] != "assessment-generation-provenance/v6"
            or assessment["policy"] != "source-span-single-choice/v5"
            or assessment["candidate_count"] != 3
            or assessment["option_count"] != 4
            or assessment["max_tokens"] != 4096
            or assessment["generation"] != {
                "temperature": 1.0, "top_p": 0.95, "top_k": 64,
                "chat_template_kwargs": {"enable_thinking": True},
            }
            or not isinstance(assessment["prompt"], str)
            or not assessment["prompt"]
            or assessment["check_max_tokens"] != 1536
            or assessment["check_generation"] != {
                "temperature": 1.0, "top_p": 0.95, "top_k": 64,
                "chat_template_kwargs": {"enable_thinking": True},
            }
            or not isinstance(assessment["check_prompt"], str)
            or not assessment["check_prompt"]
            or ocr["page_schema"] != "page-evidence/v4"
            or ocr["native_schema"] != "page-native/v3"
            or ocr["processing_policy"] != "native-first-page-evidence/v7"
            or ocr["normalizer_policy"] != "ocr-text-nfc-line-preserving/v1"
            or material["retry_attempts"] != 2
        ):
            raise ValueError
        return lock
    except (KeyError, TypeError, ValueError):
        raise MaterialAnalysisError("RUNTIME_LOCK_INVALID") from None


@contextmanager
def material_analysis_lock(runtime_root: Path, *, wait_seconds: float = 5):
    """避免同時載入多個 OCR sidecar；resident Gemma lifecycle 不在此鎖內。"""

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
        "SEMANTIC_INPUT_TOO_LARGE", "SEMANTIC_BUDGET_EXHAUSTED",
        "KNOWLEDGE_STRUCTURE_INVALID", "MATERIAL_IDENTITY_INVALID",
        "SEMANTIC_ARTIFACT_WRITE_FAILED",
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
    cancellation_check: Callable[[], None],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    pages: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    ocr_calls = 0
    ocr = None
    document = pymupdf.open(source_path)
    try:
        for completed, page_number in enumerate(page_numbers, start=1):
            cancellation_check()
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
    cancellation_check: Callable[[], None] | None = None,
    client: httpx.Client | None = None,
    semantic_call: Callable[..., dict[str, Any]] = request_semantics,
    source_inputs: list[dict[str, Any]] | None = None,
    input_binding: dict[str, Any] | None = None,
    base_structure: dict[str, Any] | None = None,
    analysis_archive: Any | None = None,
) -> dict[str, Any]:
    """Evidence → 設定的語意模型 → deterministic canonical structure。"""

    lock = validate_runtime_lock(settings.get("runtime_lock"))
    resolved_run = run_id or str(uuid4())
    resolved_time = produced_at or datetime.now(UTC).isoformat()
    report = progress_callback or (lambda _stage, _completed, _total: None)
    check_cancel = cancellation_check or (lambda: None)
    runtime_root = Path(settings["private_runtime_root"])
    restored = analysis_archive.load_checkpoint() if analysis_archive is not None else None
    with material_analysis_lock(runtime_root):
        check_cancel()
        if restored is not None:
            context = restored["context"]
            checked = {"expected_source_sha256": restored["source_sha256"]}
            expected_digest = input_binding["source_set_digest"] if input_binding else request["expected_source_sha256"]
            if checked["expected_source_sha256"] != expected_digest:
                raise MaterialAnalysisError("ANALYSIS_CHECKPOINT_INVALID")
            page_numbers = list(range(1, context["page_count"] + 1))
            state = SemanticState(**restored["state"])
            cursor = restored["cursor"]
            semantic_calls = restored["semantic_calls"]
            ocr_calls = restored["ocr_calls"]
            evidence_duration_ms = restored["evidence_duration_ms"]
            previous_semantic_ms = restored["semantic_duration_ms"]
            report("evidence", len(page_numbers), len(page_numbers))
            if cursor and not restored.get("restart_semantics"):
                report("semantics", context["evidence"][cursor - 1]["page"], len(page_numbers))
        else:
            evidence_started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix="studydy-source-") as directory:
                if source_inputs is not None:
                    from .source_set import collect_source_set
                    checked = {"expected_source_sha256": input_binding["source_set_digest"]}
                    page_numbers = list(range(1, len(input_binding["bundle"]["pages"]) + 1))
                    pages, excluded, ocr_calls = collect_source_set(source_inputs, input_binding, base_structure,
                        Path(directory), settings, resolved_time, report, check_cancel, _page_evidence)
                else:
                    snapshot = Path(directory) / "source.pdf"
                    checked = snapshot_whole_document_request(request, snapshot)
                    page_numbers = checked["page_numbers"]
                    pages, excluded, ocr_calls = _page_evidence(snapshot, checked["expected_source_sha256"],
                        page_numbers, settings, resolved_time, report, check_cancel)
            evidence_duration_ms = round((time.monotonic() - evidence_started) * 1000)
            check_cancel()
            context = build_document_context(
                pages, page_count=len(page_numbers), excluded_pages=excluded,
                source_pages=input_binding["bundle"]["pages"] if input_binding else None,
            )
            if base_structure is not None:
                from knowledge_map.source_identity import seed_incremental_state
                state = seed_incremental_state(base_structure, context, input_binding)
                context["incremental"] = True
                state.rejected_claims = base_structure["metrics"]["rejected_claims"]
                state.rejected_relations = base_structure["metrics"]["rejected_relations"]
                state.literal_repairs = base_structure["metrics"]["literal_repairs"]
            else:
                state = SemanticState()
            cursor = semantic_calls = previous_semantic_ms = 0
        if restored is not None and restored.get("restart_semantics"):
            if base_structure is not None:
                from knowledge_map.source_identity import seed_incremental_state
                state = seed_incremental_state(base_structure, context, input_binding)
                state.rejected_claims = base_structure["metrics"]["rejected_claims"]
                state.rejected_relations = base_structure["metrics"]["rejected_relations"]
                state.literal_repairs = base_structure["metrics"]["literal_repairs"]
            else:
                state = SemanticState()
            cursor = 0
        semantic_started = time.monotonic()
        complete = bool(restored and restored.get("complete"))
        evidence_indices = {item["evidence_id"]: index for index, item in enumerate(context["evidence"])}
        def save_checkpoint():
            if analysis_archive is not None:
                analysis_archive.save_checkpoint({
                    "context": context, "state": asdict(state), "cursor": cursor,
                    "complete": complete,
                    "source_sha256": checked["expected_source_sha256"],
                    "semantic_calls": semantic_calls, "ocr_calls": ocr_calls,
                    "evidence_duration_ms": evidence_duration_ms,
                    "semantic_duration_ms": previous_semantic_ms + round((time.monotonic() - semantic_started) * 1000),
                })
        save_checkpoint()
        owned_client = client is None and not complete
        http = semantic_client() if owned_client else client
        try:
            bundles = iter(build_semantic_bundles(
                context, state=state,
                fits=lambda request: material_request_fits(http, lock, request),
                minimum_page=base_structure["page_count"] + 1 if base_structure else 1,
                minimum_evidence_index=cursor,
            ))
            while True:
                check_cancel()
                try:
                    bundle = next(bundles)
                except StopIteration:
                    complete = True
                    break
                except ValueError as error:
                    # 分批器也可能在呼叫模型前拒絕輸入，保留可公開的原因碼。
                    raise MaterialAnalysisError(_reason(error)) from None
                request_document = semantic_request(context, bundle, state)
                last_error: Exception | None = None
                for _attempt in range(lock["material_semantics"]["retry_attempts"]):
                    check_cancel()
                    candidate_state = deepcopy(state)
                    try:
                        response = analysis_archive.reuse_review_response(request_document) if analysis_archive is not None else None
                        if response is None:
                            semantic_calls += 1
                            from runtime.command_semantics import retain_call_outputs
                            output_directory = analysis_archive.prepare_call(semantic_calls, request_document) if analysis_archive is not None else None
                            with retain_call_outputs(output_directory):
                                response = semantic_call(
                                    http,
                                    runtime_lock=lock,
                                    task="material_semantics",
                                    request=request_document,
                                    response_schema=semantic_response_schema([
                                        row[0] for section in request_document["sections"] for row in section["evidence"]
                                    ], incremental=base_structure is not None),
                                )
                        if analysis_archive is not None:
                            analysis_archive.save_response(semantic_calls, request_document, response)
                        if base_structure is not None:
                            if not isinstance(response, dict) or type(response.get("review_required")) is not bool:
                                raise ValueError("SEMANTIC_OUTPUT_INVALID")
                            candidate_state.source_review_required |= response["review_required"]
                            response = {key: value for key, value in response.items() if key != "review_required"}
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
                        state.source_review_required = candidate_state.source_review_required
                        last_error = None
                        break
                    except SemanticServiceError as error:
                        last_error = error
                    except ValueError as error:
                        last_error = error
                if last_error is not None:
                    raise MaterialAnalysisError(_reason(last_error)) from None
                cursor = evidence_indices[bundle["evidence"][-1]["evidence_id"]] + 1
                save_checkpoint()
                report("semantics", bundle["evidence"][-1]["page"], len(page_numbers))
        finally:
            if owned_client:
                http.close()
            save_checkpoint()
        semantic_duration_ms = previous_semantic_ms + round((time.monotonic() - semantic_started) * 1000)
    from runtime.command_semantics import identity
    command=identity(lock)
    service = lock["semantic_service"] if command is None else {"model_id":command["model_id"],"revision":command["model_revision"]}
    try:
        return build_knowledge_structure(
            context,
            state,
            source_sha256=checked["expected_source_sha256"],
            run_id=resolved_run,
            produced_at=resolved_time,
            runtime_lock_sha256=canonical_sha256(lock) if command is None else command["runtime_lock_sha256"],
            model_id=service["model_id"],
            model_revision=service["revision"],
            execution_identity=command,
            semantic_calls=semantic_calls,
            ocr_calls=ocr_calls,
            evidence_duration_ms=evidence_duration_ms,
            semantic_duration_ms=semantic_duration_ms,
        )
    except ValueError as error:
        raise MaterialAnalysisError(_reason(error)) from None
