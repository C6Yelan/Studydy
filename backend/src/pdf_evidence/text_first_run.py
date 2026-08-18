"""以本機 OCR 與 Qwen 依序處理單一 PDF 的指定頁面。"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
from uuid import uuid4

import pymupdf

from .concept_evidence_output import build_output, build_terminal, publish_run
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
from .local_ai_process import LocalAIError, start_concept_process, start_ocr_process
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
from .pipeline.source import _copy_source_snapshot


_SHA256 = re.compile(r"[0-9a-f]{64}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _reason(error: BaseException) -> str:
    if isinstance(error, (SemanticOutputError, LocalAIError)):
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
        "PROTOCOL_LIMIT_EXCEEDED",
        "CHILD_TIMEOUT",
        "CHILD_EXITED",
        "CHILD_RESPONSE_INVALID",
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
        "RUN_TERMINAL_WRITE_FAILED",
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
            "studydy_local_ai_wheel_sha256",
            "setuptools",
            "torch",
            "torchvision",
            "transformers",
        }
        or packages.get("setuptools") != "84.0.0"
        or packages.get("torch") != "2.10.0+cu128"
        or packages.get("torchvision") != "0.25.0+cu128"
        or packages.get("transformers") != "4.57.1"
        or packages.get("studydy_local_ai_wheel_sha256")
        != "afc5f9fd62817ddfd1fe4505d402a7aeeb923f7a3e6af131205d57771588b6c8"
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
            "local_ai_protocol": "e37a91ffcda3df8e6190797da2cbe2db24f3c28db4eea367b4d7e3653d32b254",
            "local_ai_ocr": "d6f431c990630b60311ef0e9737ea4805896eb709eb69dd24644d93a580a232a",
        }
        or ocr.get("fixture_hashes") != {}
    ):
        raise ValueError("RUNTIME_BINDING_INVALID")
    expected_generation = {"do_sample": False, "max_new_tokens": 1536, "use_cache": True}
    expected_chat_template = {
        "messages": "single_user",
        "tokenize": True,
        "add_generation_prompt": True,
        "return_tensors": "pt",
        "input_token_limit": 4096,
    }
    expected_retry = {
        "max_attempts": 2,
        "fixed_retry_count": 1,
        "timeout_seconds": 300,
        "retryable_reasons": sorted(
            [
                "CHILD_TIMEOUT",
                "MODEL_OOM",
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
        "generation_termination": "final-token-eos-required/v1",
        "structurally_valid_decision": "review",
    }
    if (
        not isinstance(semantic, dict)
        or set(semantic)
        != {
            "model_id",
            "revision",
            "dtype",
            "trust_remote_code",
            "binding_manifest_sha256",
            "required_file_count",
            "required_files",
            "prompt",
            "prompt_sha256",
            "request_schema",
            "output_schema",
            "chat_template",
            "generation",
            "retry",
            "policy",
            "code_hashes",
            "fixture_hashes",
        }
        or semantic.get("model_id") != "Qwen/Qwen3-4B-Instruct-2507"
        or semantic.get("revision") != "cdbee75f17c01a7cc42f958dc650907174af0554"
        or semantic.get("dtype") != "bfloat16"
        or semantic.get("trust_remote_code") is not False
        or semantic.get("binding_manifest_sha256") != "61cbb8e0973dcbefc6009f66ddfc2da2fe3d9aba4094ade8a82043f6624651c4"
        or semantic.get("required_file_count") != 10
        or not isinstance(semantic.get("required_files"), list)
        or canonical_sha256(semantic.get("required_files"))
        != "687ce9aa177c2df2f453dd906f03f71e437c5e9ddab2c9a93e10ef1190b97ecc"
        or semantic.get("prompt") != PROMPT_TEMPLATE
        or semantic.get("prompt_sha256") != PROMPT_SHA256
        or not _hash_matches(semantic["prompt"], semantic["prompt_sha256"])
        or semantic.get("request_schema") != "semantic-qualification-input/v1"
        or semantic.get("output_schema") != "semantic-concepts/v1"
        or semantic.get("chat_template") != expected_chat_template
        or semantic.get("generation") != expected_generation
        or semantic.get("retry") != expected_retry
        or semantic.get("policy") != expected_policy
        or semantic.get("code_hashes")
        != {
            "local_ai_protocol": "e37a91ffcda3df8e6190797da2cbe2db24f3c28db4eea367b4d7e3653d32b254",
            "local_ai_concept": "3e94ed380a2adc6ea16c14bc9d9c72d4b424b2fae9f3d8383653543e1aab15cb",
            "backend_concept_generation": "805a0b3830db291a17af7f5b0da85b8a89c757ce7abeb81538ddede5cf25d446",
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
        or not 1 <= len(pages) <= 32
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


def _page_artifact_valid(artifact: Any, binding: dict[str, Any]) -> bool:
    fields = {
        "schema",
        "material_id",
        "material_revision",
        "section_id",
        "page_ref",
        "page_number",
        "geometry",
        "coordinate_space",
        "native_evidence_ref",
        "render",
        "evidence_blocks",
        "images",
        "input_binding",
        "processing_policy",
        "normalizer_policy",
        "produced_at",
        "processing",
        "quality",
        "decision",
        "reason_codes",
        "page_evidence_id",
    }
    if (
        not isinstance(artifact, dict)
        or set(artifact) != fields
        or artifact["schema"] != PAGE_SCHEMA
        or artifact["input_binding"] != binding
        or artifact["processing"] != "partial"
        or artifact["quality"] != "needs_review"
        or artifact["decision"] != "review"
        or not isinstance(artifact["evidence_blocks"], list)
        or not artifact["evidence_blocks"]
    ):
        return False
    identity = dict(artifact)
    page_evidence_id = identity.pop("page_evidence_id")
    if page_evidence_id != f"page-evidence:sha256:{canonical_sha256(identity)}":
        return False
    evidence_ids: set[str] = set()
    for block in artifact["evidence_blocks"]:
        if (
            not isinstance(block, dict)
            or set(block)
            != {
                "evidence_id",
                "block_id",
                "ocr_type",
                "kind",
                "text",
                "reading_order",
                "locator",
                "render_region",
                "source",
            }
            or block["source"] != "unlimited_ocr"
            or block["evidence_id"] in evidence_ids
            or block["locator"].get("page") != artifact["page_number"]
            or block["locator"].get("block_id") != block["block_id"]
        ):
            return False
        evidence_ids.add(block["evidence_id"])
    return True


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
    validator = _page_artifact_valid if operation == "page" else _semantic_artifact_valid
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
            and validator(record["artifact"], binding)
        )
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
    concept_process: Any,
    request_id: str,
    semantic_request: dict[str, Any],
    binding: dict[str, Any],
    page_ref: str,
    attempt: int,
) -> dict[str, Any]:
    response = concept_process.request(
        {
            "schema": "local-concept-request/v1",
            "request_id": request_id,
            "attempt": attempt,
            "semantic_request": semantic_request,
        },
        300,
    )
    if response.get("request_id") != request_id or response.get("attempt") != attempt:
        raise SemanticOutputError("CHILD_RESPONSE_INVALID")
    if response.get("schema") == "local-concept-failure/v1":
        if set(response) != {"schema", "request_id", "attempt", "reason_code"}:
            raise SemanticOutputError("CHILD_RESPONSE_INVALID")
        raise SemanticOutputError(response["reason_code"])
    if set(response) != {"schema", "request_id", "attempt", "model_text"} or response.get(
        "schema"
    ) != "local-concept-response/v1":
        raise SemanticOutputError("CHILD_RESPONSE_INVALID")
    return validate_concepts(
        response["model_text"],
        semantic_request=semantic_request,
        page_ref=page_ref,
        input_binding=binding,
        attempt=attempt,
    )


def run_text_first_pdf(request: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """成功只發布 partial/review output；任一 failure 不建立成功 artifact。"""
    run_id = f"text-first-run:{uuid4()}"
    produced_at = _now()
    started = time.monotonic()
    ocr_calls = 0
    concept_calls = 0
    output: dict[str, Any] | None = None
    cache_invalid = False
    snapshot_directory: tempfile.TemporaryDirectory[str] | None = None
    root = Path(settings["private_runtime_root"])
    runtime_lock = settings.get("runtime_lock")
    runtime_binding_sha256 = canonical_sha256(runtime_lock)
    try:
        _validate_runtime_lock(runtime_lock)
        source_path, page_numbers, source_sha256 = _validate_request(request)
        snapshot_directory = tempfile.TemporaryDirectory(prefix="studydy-source-")
        snapshot_path = Path(snapshot_directory.name) / "source.pdf"
        if _copy_source_snapshot(source_path, snapshot_path) is not None:
            raise ValueError("SOURCE_READ_FAILED")
        source_path, page_numbers, source_sha256 = _validate_request(
            {**request, "source_path": str(snapshot_path)}
        )
        prepared_pages: list[tuple[dict[str, Any], dict[str, Any], str, dict[str, Any] | None, bool]] = []
        for page_number in page_numbers:
            page = extract_page(source_path, source_sha256, page_number)
            _write_native(root, page)
            binding = {
                "source_sha256": source_sha256,
                "page_number": page_number,
                "render_sha256": page["render"]["sha256"],
                "page": runtime_lock["page"],
                "ocr": runtime_lock["ocr"],
            }
            key = page_cache_key(binding)
            cached, invalid = _read_cache(_cache_path(root, "page", key), "page", key, binding)
            cache_invalid = cache_invalid or invalid
            prepared_pages.append((page, binding, key, cached, invalid))
        generated_pages: dict[int, dict[str, Any]] = {}
        if any(cached is None for _, _, _, cached, _ in prepared_pages):
            ocr = start_ocr_process(settings)
            try:
                for page, binding, key, cached, invalid in prepared_pages:
                    if cached is not None:
                        continue
                    page_number = page["page_number"]
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
                    generated_pages[page_number] = artifact
            finally:
                ocr.close()
        page_artifacts = [
            cached if cached is not None else generated_pages[page["page_number"]]
            for page, _, _, cached, _ in prepared_pages
        ]

        prepared_semantic: list[
            tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, dict[str, Any] | None, bool]
        ] = []
        for page in page_artifacts:
            semantic_request = build_semantic_request(page)
            binding = {
                "page_evidence_sha256": canonical_sha256(page),
                "semantic_request_sha256": canonical_sha256(semantic_request),
                "evidence_allowlist": [item["evidence_id"] for item in semantic_request["evidence"]],
                "semantic": runtime_lock["semantic"],
            }
            key = semantic_cache_key(binding)
            cached, invalid = _read_cache(
                _cache_path(root, "semantic", key), "semantic", key, binding
            )
            cache_invalid = cache_invalid or invalid
            prepared_semantic.append((page, semantic_request, binding, key, cached, invalid))
        generated_semantic: dict[str, dict[str, Any]] = {}
        if any(cached is None for _, _, _, _, cached, _ in prepared_semantic):
            concept = start_concept_process(settings)
            try:
                for page, semantic_request, binding, key, cached, invalid in prepared_semantic:
                    if cached is not None:
                        continue
                    request_id = page["page_ref"].split(":")[-1][:32]
                    artifact = None
                    last_error: BaseException | None = None
                    for attempt in range(1, MAX_ATTEMPTS + 1):
                        try:
                            artifact = _semantic_response(
                                concept,
                                request_id,
                                semantic_request,
                                binding,
                                page["page_ref"],
                                attempt,
                            )
                            concept_calls += 1
                            break
                        except LocalAIError as error:
                            concept_calls += 1
                            last_error = error
                            if attempt == MAX_ATTEMPTS or not retryable(error.reason_code):
                                raise
                            if error.reason_code == "CHILD_TIMEOUT":
                                concept.abort()
                                concept = start_concept_process(settings)
                        except SemanticOutputError as error:
                            concept_calls += 1
                            last_error = error
                            if attempt == MAX_ATTEMPTS or not retryable(error.reason_code):
                                raise
                    if artifact is None:
                        raise last_error or SemanticOutputError("NO_USABLE_CONCEPT")
                    _write_cache(
                        _cache_path(root, "semantic", key),
                        "semantic",
                        key,
                        binding,
                        artifact,
                        replace_invalid=invalid,
                    )
                    generated_semantic[page["page_ref"]] = artifact
            finally:
                concept.close()
        semantic_pages = [
            cached if cached is not None else generated_semantic[page["page_ref"]]
            for page, _, _, _, cached, _ in prepared_semantic
        ]
        output = build_output(
            run_id=run_id,
            produced_at=produced_at,
            source_binding={"source_sha256": source_sha256, "page_numbers": page_numbers},
            pages=page_artifacts,
            semantic_pages=semantic_pages,
            runtime_binding=runtime_lock,
            run_reasons=["CACHE_INVALID"] if cache_invalid else [],
        )
        terminal = build_terminal(
            run_id=run_id,
            produced_at=produced_at,
            output=output,
            runtime_binding_sha256=runtime_binding_sha256,
            reasons=output["reason_codes"],
            duration_ms=int((time.monotonic() - started) * 1000),
            ocr_calls=ocr_calls,
            concept_calls=concept_calls,
        )
        publish_run(root, run_id, output, terminal)
        return terminal
    except BaseException as error:
        reason = _reason(error)
        terminal = build_terminal(
            run_id=run_id,
            produced_at=produced_at,
            output=None,
            runtime_binding_sha256=runtime_binding_sha256,
            reasons=[reason],
            duration_ms=int((time.monotonic() - started) * 1000),
            ocr_calls=ocr_calls,
            concept_calls=concept_calls,
        )
        try:
            publish_run(root, run_id, None, terminal)
        except BaseException as terminal_error:
            raise RuntimeError("RUN_TERMINAL_WRITE_FAILED") from terminal_error
        return terminal
    finally:
        if snapshot_directory is not None:
            snapshot_directory.cleanup()
