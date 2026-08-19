from __future__ import annotations

import base64
from contextlib import contextmanager
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
    CHAT_COMPLETIONS_PATH,
    MAX_TOKENS,
    TEMPERATURE,
    ConceptAPIError,
    chat_completions_url,
    request_concept_text,
)
from .concept_evidence_output import (
    build_output,
    validate_page_evidence,
)
from .concept_generation import (
    MAX_ATTEMPTS,
    PROMPT_SHA256,
    PROMPT_TEMPLATE,
    SemanticOutputError,
    build_semantic_request,
    retryable,
    semantic_cache_key,
    validate_concepts,
)
from .local_ai_process import LocalAIError, start_ocr_process
from .ocr_page_evidence import (
    NATIVE_SCHEMA,
    NORMALIZER_POLICY,
    PAGE_SCHEMA,
    PROCESSING_POLICY,
    RENDER_POLICY,
    build_page_evidence,
    canonical_bytes,
    canonical_sha256,
    extract_page,
    page_cache_key,
)
from .source_pdf import build_whole_document_request, copy_source_snapshot
from .text_first_bundle import build_producer_bundle, publish_run


_SHA256 = re.compile(r"[0-9a-f]{64}")
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


def _reason(error: BaseException) -> str:
    if isinstance(error, (ConceptAPIError, SemanticOutputError, LocalAIError)):
        return error.reason_code
    value = str(error)
    allowed = {
        "MEDIA_TYPE_INVALID",
        "SOURCE_READ_FAILED",
        "SOURCE_HASH_MISMATCH",
        "PDF_INVALID",
        "PDF_ENCRYPTED",
        "PAGE_SELECTION_INVALID",
        "RUNTIME_BINDING_INVALID",
        "RUNTIME_BUSY",
        "PROTOCOL_LIMIT_EXCEEDED",
        "CHILD_TIMEOUT",
        "CHILD_EXITED",
        "CHILD_RESPONSE_INVALID",
        "CONCEPT_API_CONFIG_INVALID",
        "CONCEPT_API_RESPONSE_INVALID",
        "CONCEPT_API_TIMEOUT",
        "CONCEPT_API_UNAVAILABLE",
        "MODEL_OOM",
        "MODEL_GENERATION_FAILED",
        "MODEL_INPUT_TOO_LARGE",
        "OCR_OUTPUT_INVALID",
        "OCR_LOCATOR_INVALID",
        "NO_USABLE_EVIDENCE",
        "MODEL_OUTPUT_TOO_LARGE",
        "MODEL_OUTPUT_INVALID_JSON",
        "MODEL_OUTPUT_TRUNCATED",
        "CANDIDATE_SCHEMA_INVALID",
        "INVALID_CONCEPT_COUNT",
        "INVALID_TEXT_FIELD",
        "INVALID_KEY_POINTS",
        "INVALID_EVIDENCE_REFERENCES",
        "DUPLICATE_EVIDENCE_REFERENCE",
        "UNKNOWN_EVIDENCE_ID",
        "NO_USABLE_CONCEPT",
        "CACHE_INVALID",
        "CACHE_WRITE_FAILED",
        "ARTIFACT_COLLISION",
        "FINAL_OUTPUT_WRITE_FAILED",
        "PRODUCER_BUNDLE_WRITE_FAILED",
        "PRODUCER_BUNDLE_INVALID",
    }
    return value if value in allowed else "INTERNAL_FAILURE"


def _hash_matches(value: str, expected: str) -> bool:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() == expected


def _validate_runtime_lock(runtime_lock: Any) -> None:
    if not isinstance(runtime_lock, dict) or set(runtime_lock) != {
        "schema",
        "python",
        "packages",
        "page",
        "ocr",
        "semantic",
    }:
        raise ValueError("RUNTIME_BINDING_INVALID")
    if runtime_lock["schema"] != "studydy-local-ai-runtime-lock/v2":
        raise ValueError("RUNTIME_BINDING_INVALID")
    python = runtime_lock["python"]
    packages = runtime_lock["packages"]
    page = runtime_lock["page"]
    ocr = runtime_lock["ocr"]
    semantic = runtime_lock["semantic"]
    if (
        python != {
            "version": "3.12.3",
            "executable_sha256": "d2bf63ac665084e548f1e32bdab323a559141dbd237bfa578640dc1041ddd44e",
        }
        or not isinstance(packages, dict)
        or set(packages)
        != {
            "setuptools",
            "torch",
            "torchvision",
            "transformers",
        }
        or packages.get("setuptools") != "84.0.0"
        or packages.get("torch") != "2.10.0+cu128"
        or packages.get("torchvision") != "0.25.0+cu128"
        or packages.get("transformers") != "4.57.1"
    ):
        raise ValueError("RUNTIME_BINDING_INVALID")
    expected_render = {
        "schema": "page-render/v1",
        "dpi": 200,
        "colorspace": "RGB",
        "format": "PNG",
        "coverage": "full_visible_page",
    }
    expected_normalization = {
        "version": "qualification-normalization/1",
        "operations": ["crlf_to_lf", "unicode_nfc", "remove_trailing_whitespace"],
    }
    if (
        not isinstance(page, dict)
        or set(page)
        != {
            "pymupdf_version",
            "schema",
            "native_schema",
            "render",
            "render_sha256",
            "normalization",
            "normalization_sha256",
            "processing",
            "normalizer",
            "code_hashes",
            "fixture_hashes",
        }
        or page.get("pymupdf_version") != "1.28.0"
        or page.get("schema") != PAGE_SCHEMA
        or page.get("native_schema") != NATIVE_SCHEMA
        or page.get("render") != expected_render
        or page.get("render_sha256") != canonical_sha256(expected_render)
        or page.get("normalization") != expected_normalization
        or page.get("normalization_sha256") != canonical_sha256(expected_normalization)
        or page.get("processing") != PROCESSING_POLICY
        or page.get("normalizer") != NORMALIZER_POLICY
        or page.get("code_hashes")
        != {
            "backend_ocr_page_evidence": "464dd905c89675ec57775e0d6170416f4702f18407d7e06dce95d054d7769f03"
        }
        or page.get("fixture_hashes") != {}
    ):
        raise ValueError("RUNTIME_BINDING_INVALID")
    expected_qualification_config = {
        "candidate": "unlimited",
        "round": 1,
        "image_mode": "gundam",
        "max_length": 32768,
        "no_repeat_ngram_size": 35,
        "ngram_window": 128,
        "temperature": 0.0,
    }
    expected_inference = {
        "base_size": 1024,
        "image_size": 640,
        "crop_mode": True,
        "eval_mode": True,
        "max_length": 32768,
        "no_repeat_ngram_size": 35,
        "ngram_window": 128,
        "temperature": 0.0,
        "save_results": False,
    }
    if (
        not isinstance(ocr, dict)
        or set(ocr)
        != {
            "model_id",
            "revision",
            "weight_sha256",
            "reviewed_code_revision",
            "reviewed_code",
            "config_sha256",
            "prompt",
            "prompt_sha256",
            "qualification_config",
            "qualification_config_sha256",
            "inference",
            "grammar",
            "code_hashes",
            "fixture_hashes",
        }
        or ocr.get("model_id") != "Unlimited-OCR"
        or ocr.get("revision") != "07dea832e22aefee32ad281d4b80551282e1c168"
        or ocr.get("weight_sha256") != "2bc48a7a110061ea58fff65d3169367eebe3aee371ca6968dc2219c1b2855fc6"
        or ocr.get("reviewed_code_revision") != "d49ff64afffc1f47ab563dc1c589bc2f78808fa4"
        or not isinstance(ocr.get("reviewed_code"), dict)
        or canonical_sha256(ocr.get("reviewed_code"))
        != "38d2c2fe605a05c59b3131868c17a5a675105a0af9d78140d32deb2a24f376a7"
        or ocr.get("config_sha256")
        != "27246d03fd670904ec9601b1cb0861fbb79ec076830771daa8d943d6229946f9"
        or ocr.get("prompt") != "<image>document parsing."
        or ocr.get("prompt_sha256") != "a210f5b991d35d2a9a7d6c6160f54165c0ae96e02fbfdd1bc38400232021c303"
        or not _hash_matches(ocr["prompt"], ocr["prompt_sha256"])
        or ocr.get("qualification_config") != expected_qualification_config
        or ocr.get("qualification_config_sha256") != canonical_sha256(expected_qualification_config)
        or ocr.get("inference") != expected_inference
        or ocr.get("grammar") != "p02-strict-det-only/v1"
        or ocr.get("code_hashes")
        != {
            "local_ai_protocol": "2cf8c64d90ea79f76606e22caaf465f16ffd4153adbe83c90c18e6aa51bead43",
            "local_ai_ocr": "d6f431c990630b60311ef0e9737ea4805896eb709eb69dd24644d93a580a232a",
        }
        or ocr.get("fixture_hashes") != {}
    ):
        raise ValueError("RUNTIME_BINDING_INVALID")
    expected_generation = {"temperature": TEMPERATURE, "max_tokens": MAX_TOKENS}
    expected_retry = {
        "max_attempts": 2,
        "fixed_retry_count": 1,
        "timeout_seconds": 300,
        "retryable_reasons": sorted(
            [
                "CONCEPT_API_RESPONSE_INVALID",
                "CONCEPT_API_TIMEOUT",
                "CONCEPT_API_UNAVAILABLE",
                "MODEL_OUTPUT_INVALID_JSON",
                "MODEL_OUTPUT_TRUNCATED",
                "CANDIDATE_SCHEMA_INVALID",
                "INVALID_CONCEPT_COUNT",
                "INVALID_TEXT_FIELD",
                "INVALID_KEY_POINTS",
                "INVALID_EVIDENCE_REFERENCES",
                "DUPLICATE_EVIDENCE_REFERENCE",
                "UNKNOWN_EVIDENCE_ID",
                "MODEL_OUTPUT_TOO_LARGE",
                "NO_USABLE_CONCEPT",
            ]
        ),
    }
    expected_policy = {
        "candidate_text_normalizer": "p02-nfkc-whitespace/v1",
        "cross_concept_evidence_reuse": True,
        "lexical_overlap_decision": False,
        "sanitation": "single-trailing-ascii-quote/v1",
        "fenced_json": "reject",
        "generation_termination": "finish-reason-stop-required/v1",
        "structurally_valid_decision": "review",
    }
    if (
        not isinstance(semantic, dict)
        or set(semantic)
        != {
            "model_id",
            "api_protocol",
            "api_path",
            "prompt",
            "prompt_sha256",
            "request_schema",
            "output_schema",
            "generation",
            "retry",
            "policy",
            "code_hashes",
            "fixture_hashes",
        }
        or semantic.get("model_id") != "Qwen/Qwen3-4B-Instruct-2507"
        or semantic.get("api_protocol") != "openai-chat-completions/v1"
        or semantic.get("api_path") != CHAT_COMPLETIONS_PATH
        or semantic.get("prompt") != PROMPT_TEMPLATE
        or semantic.get("prompt_sha256") != PROMPT_SHA256
        or not _hash_matches(semantic["prompt"], semantic["prompt_sha256"])
        or semantic.get("request_schema") != "semantic-qualification-input/v1"
        or semantic.get("output_schema") != "semantic-concepts/v1"
        or semantic.get("generation") != expected_generation
        or semantic.get("retry") != expected_retry
        or semantic.get("policy") != expected_policy
        or semantic.get("code_hashes")
        != {
            "backend_concept_api": "75dca2128f733555de5f5ea6dfca612b13da8809c6dbb790f88551defc92e4ac",
            "backend_concept_generation": "1a3ba77a2aca9238b41e0d82079792a0d51067f04bd27c49f1f07a89ba17bce1",
        }
        or semantic.get("fixture_hashes")
        != {
            "semantic_request.json": "dd1ebd2ff79a274df59f21d66f13789da25f262bf8fb03c66c73b79efd7a191d",
            "semantic_model_output.json": "8031a30b7c4bc4d1a9efa4cd8e7b643476878fb1e96e5cbd329379b75a691553",
        }
    ):
        raise ValueError("RUNTIME_BINDING_INVALID")


def _validate_request(request: Any) -> tuple[Path, list[int], str]:
    if not isinstance(request, dict) or set(request) != {
        "media_type",
        "source_path",
        "expected_source_sha256",
        "page_numbers",
    }:
        raise ValueError("SOURCE_READ_FAILED")
    if request["media_type"] != "application/pdf":
        raise ValueError("MEDIA_TYPE_INVALID")
    source_sha256 = request["expected_source_sha256"]
    if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
        raise ValueError("SOURCE_HASH_MISMATCH")
    pages = request["page_numbers"]
    if (
        not isinstance(pages, list)
        or not pages
        or any(type(page) is not int for page in pages)
        or pages != sorted(set(pages))
        or pages[0] < 1
    ):
        raise ValueError("PAGE_SELECTION_INVALID")
    try:
        source_path = Path(request["source_path"])
        with source_path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise ValueError("PDF_INVALID")
            source.seek(0)
            actual_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
    except ValueError:
        raise
    except (OSError, TypeError) as error:
        raise ValueError("SOURCE_READ_FAILED") from error
    if actual_sha256 != source_sha256:
        raise ValueError("SOURCE_HASH_MISMATCH")
    try:
        document = pymupdf.open(source_path)
    except Exception as error:
        raise ValueError("PDF_INVALID") from error
    try:
        if document.needs_pass:
            raise ValueError("PDF_ENCRYPTED")
        if pages[-1] > document.page_count:
            raise ValueError("PAGE_SELECTION_INVALID")
    finally:
        document.close()
    return source_path, pages, source_sha256


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


def _write_native(root: Path, page: dict[str, Any]) -> None:
    expected_hash = page["native_evidence_ref"].removeprefix("native-evidence:sha256:")
    encoded = canonical_bytes(page["native_evidence"])
    if hashlib.sha256(encoded).hexdigest() != expected_hash:
        raise ValueError("OCR_LOCATOR_INVALID")
    directory = root / "artifacts" / "native"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise OSError("CACHE_WRITE_FAILED")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{expected_hash}.json"
    if path.is_symlink():
        raise OSError("CACHE_WRITE_FAILED")
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError("ARTIFACT_COLLISION")
        return
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix="native-", dir=directory)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    except FileExistsError:
        raise
    except OSError as error:
        raise OSError("CACHE_WRITE_FAILED") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _semantic_artifact_valid(artifact: Any, binding: dict[str, Any]) -> bool:
    fields = {
        "schema",
        "page_ref",
        "concepts",
        "rejected_candidates",
        "input_binding",
        "attempt",
        "sanitation_policy",
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
        or artifact["processing"] != "partial"
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
            or concept["processing"] != "partial"
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
            destination.flush()
            os.fsync(destination.fileno())
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


def _semantic_response(
    concept_client: httpx.Client,
    settings: dict[str, Any],
    semantic_request: dict[str, Any],
    binding: dict[str, Any],
    page_ref: str,
    attempt: int,
) -> dict[str, Any]:
    model_text = request_concept_text(
        concept_client,
        base_url=settings["concept_api_base_url"],
        model=settings["concept_model"],
        semantic_request=semantic_request,
        timeout_seconds=settings["runtime_lock"]["semantic"]["retry"]["timeout_seconds"],
    )
    return validate_concepts(
        model_text,
        semantic_request=semantic_request,
        page_ref=page_ref,
        input_binding=binding,
        attempt=attempt,
    )


def _run_text_first_pdf(
    request: dict[str, Any],
    settings: dict[str, Any],
    *,
    whole_document: bool,
    requested_run_id: str | None = None,
    requested_produced_at: str | None = None,
    requested_runtime_binding_sha256: str | None = None,
) -> dict[str, Any]:
    """依序執行 OCR 與 Concept API；formal 模式可明確排除個別內容頁。"""

    run_id = requested_run_id or f"text-first-run:{uuid4()}"
    produced_at = requested_produced_at or _now()
    started = time.monotonic()
    ocr_calls = 0
    concept_calls = 0
    ocr_loads = 0
    concept_loads = 0
    page_count = 0
    output: dict[str, Any] | None = None
    cache_invalid = False
    excluded_pages: list[dict[str, Any]] = []
    snapshot_directory: tempfile.TemporaryDirectory[str] | None = None
    root = Path(settings["private_runtime_root"])
    runtime_lock = settings.get("runtime_lock")
    runtime_binding_sha256 = requested_runtime_binding_sha256 or canonical_sha256(
        runtime_lock
    )
    try:
        _validate_runtime_lock(runtime_lock)
        chat_completions_url(settings.get("concept_api_base_url"))
        if settings.get("concept_model") != runtime_lock["semantic"]["model_id"]:
            raise ConceptAPIError("CONCEPT_API_CONFIG_INVALID")
        checked_request = build_whole_document_request(request) if whole_document else request
        source_path, page_numbers, source_sha256 = _validate_request(checked_request)
        page_count = len(page_numbers)
        snapshot_directory = tempfile.TemporaryDirectory(prefix="studydy-source-")
        snapshot_path = Path(snapshot_directory.name) / "source.pdf"
        if copy_source_snapshot(source_path, snapshot_path) is not None:
            raise ValueError("SOURCE_READ_FAILED")
        source_path, page_numbers, source_sha256 = _validate_request(
            {**checked_request, "source_path": str(snapshot_path)}
        )
        page_artifacts = []
        ocr = None
        try:
            for page_number in page_numbers:
                page = extract_page(source_path, source_sha256, page_number)
                try:
                    _write_native(root, page)
                    binding = {
                        "source_sha256": source_sha256,
                        "page_number": page_number,
                        "render_sha256": page["render"]["sha256"],
                        "page": runtime_lock["page"],
                        "ocr": runtime_lock["ocr"],
                    }
                    key = page_cache_key(binding)
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
                except BaseException as error:
                    reason_code = _reason(error)
                    if whole_document and reason_code in _PAGE_EXCLUSION_REASONS:
                        excluded_pages.append(
                            _excluded_page(page, "page_evidence", reason_code)
                        )
                        continue
                    raise
                finally:
                    page.pop("png_bytes", None)
        finally:
            if ocr is not None:
                ocr.close()

        semantic_pages = []
        included_pages = []
        concept_client: httpx.Client | None = None
        try:
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
                        },
                    }
                    key = semantic_cache_key(binding)
                    artifact, invalid = _read_cache(
                        _cache_path(root, "semantic", key),
                        "semantic",
                        key,
                        binding,
                    )
                    cache_invalid = cache_invalid or invalid
                    if artifact is None:
                        if concept_client is None:
                            concept_client = httpx.Client(
                                trust_env=False,
                                follow_redirects=False,
                            )
                        last_error: BaseException | None = None
                        for attempt in range(1, MAX_ATTEMPTS + 1):
                            try:
                                artifact = _semantic_response(
                                    concept_client,
                                    settings,
                                    semantic_request,
                                    binding,
                                    page["page_ref"],
                                    attempt,
                                )
                                concept_calls += 1
                                break
                            except (ConceptAPIError, SemanticOutputError) as error:
                                concept_calls += 1
                                last_error = error
                                if attempt == MAX_ATTEMPTS or not retryable(
                                    error.reason_code
                                ):
                                    raise
                        if artifact is None:
                            raise last_error or SemanticOutputError(
                                "NO_USABLE_CONCEPT"
                            )
                        _write_cache(
                            _cache_path(root, "semantic", key),
                            "semantic",
                            key,
                            binding,
                            artifact,
                            replace_invalid=invalid,
                        )
                    semantic_pages.append(artifact)
                    included_pages.append(page)
                except BaseException as error:
                    reason_code = _reason(error)
                    if whole_document and reason_code in _PAGE_EXCLUSION_REASONS:
                        excluded_pages.append(_excluded_page(page, "concept", reason_code))
                        continue
                    raise
        finally:
            if concept_client is not None:
                concept_client.close()
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
        if not whole_document:
            output["processing"] = "partial"
            for concept_item in output["concepts"]:
                concept_item["processing"] = "partial"
            output.pop("output_id")
            output["output_id"] = (
                "concept-evidence-output:sha256:" + canonical_sha256(output)
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
    except BaseException as error:
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
        except BaseException as bundle_error:
            raise RuntimeError("PRODUCER_BUNDLE_WRITE_FAILED") from bundle_error
        return bundle
    finally:
        if snapshot_directory is not None:
            snapshot_directory.cleanup()


def run_text_first_pdf(request: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """保留單次指定頁 qualification 入口，formal runtime 不呼叫此入口。"""

    return _run_text_first_pdf(request, settings, whole_document=False)


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
        return _run_text_first_pdf(
            request,
            settings,
            whole_document=True,
            requested_run_id=resolved_run_id,
            requested_produced_at=resolved_produced_at,
            requested_runtime_binding_sha256=resolved_binding_sha256,
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
