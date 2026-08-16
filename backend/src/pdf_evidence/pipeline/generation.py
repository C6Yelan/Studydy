from __future__ import annotations

from copy import deepcopy
import base64
import hashlib
import os
from pathlib import Path
from typing import Any, Callable

from . import cache, transport
from ..concept_candidates import (
    CONCEPT_BODY_SCHEMA,
    CONCEPT_PROMPT_SHA256,
    CONCEPT_PROMPT_VERSION,
    adjudicate_concept_candidate,
    build_provisional_concept_candidate,
)
from ..concept_content import (
    CONCEPT_CONTENT_BODY_SCHEMA,
    CONCEPT_CONTENT_PROMPT_SHA256,
    CONCEPT_CONTENT_PROMPT_VERSION,
    build_concept_content,
)
from ..page_alignment import adjudicate_visual_alignment
from ..page_structure_generation import (
    PAGE_STRUCTURE_BODY_SCHEMA,
    PAGE_STRUCTURE_PROMPT,
    PAGE_STRUCTURE_PROMPT_VERSION,
    finalize_page_structure,
)
from .transport import (
    _canonical_bytes,
    _canonical_sha256,
    _valid_sha256,
)


__all__ = [
    "generate_concept_candidate",
    "generate_concept_content",
    "generate_development_page_structure",
    "generate_visual_review",
]


REQUEST_SCHEMA = "structured-generation-request/v1"
API_CONTRACT_VERSION = "structured-generation-loopback/v1"
MAX_REQUEST_BYTES = 64 * 1024 * 1024

VISUAL_PROMPT_VERSION = "visual-alignment-adjudication-prompt/v1"
VISUAL_PROMPT = (
    "Compare the target render with the supplied Page Structure and alignment findings. "
    "Return retain only when the visible content supports the structure and findings; "
    "otherwise return reject. Do not rewrite content, findings, identity, or lineage."
)
VISUAL_BODY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision"],
    "properties": {"decision": {"type": "string", "enum": ["retain", "reject"]}},
}

def _generation_result(
    processing: str,
    reason_code: str,
    *,
    provider_call_count: int = 0,
    cache_hit: bool = False,
    artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """建立不包含路徑、endpoint 或 raw response 的固定結果。"""
    return {
        "processing": processing,
        "reason_code": reason_code,
        "provider_call_count": provider_call_count,
        "cache_hit": cache_hit,
        "artifact": deepcopy(artifact) if artifact is not None else None,
    }


def _runtime_binding(
    operation: str,
    local_config: dict[str, Any],
    render_schema: str,
) -> dict[str, Any]:
    """建立會影響 model output 的完整非敏感 runtime binding。"""
    if operation == "page_structure":
        prompt_version = PAGE_STRUCTURE_PROMPT_VERSION
        prompt_sha256 = hashlib.sha256(PAGE_STRUCTURE_PROMPT.encode()).hexdigest()
        body_schema = PAGE_STRUCTURE_BODY_SCHEMA
        response_schema_id = "page-structure-body/v1"
    elif operation == "visual_alignment_adjudication":
        prompt_version = VISUAL_PROMPT_VERSION
        prompt_sha256 = hashlib.sha256(VISUAL_PROMPT.encode()).hexdigest()
        body_schema = VISUAL_BODY_SCHEMA
        response_schema_id = "visual-alignment-decision/v1"
    elif operation == "concept_candidate":
        prompt_version = CONCEPT_PROMPT_VERSION
        prompt_sha256 = CONCEPT_PROMPT_SHA256
        body_schema = CONCEPT_BODY_SCHEMA
        response_schema_id = "concept-candidate-body/v1"
    else:
        prompt_version = CONCEPT_CONTENT_PROMPT_VERSION
        prompt_sha256 = CONCEPT_CONTENT_PROMPT_SHA256
        body_schema = CONCEPT_CONTENT_BODY_SCHEMA
        response_schema_id = "concept-content-body/v3"
    return {
        "runtime_id": local_config["runtime_id"],
        "model_id": local_config["model_id"],
        "model_revision": local_config["model_revision"],
        "model_artifact_sha256": local_config["model_artifact_sha256"],
        "api_contract_version": API_CONTRACT_VERSION,
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_sha256,
        "response_schema_id": response_schema_id,
        "response_schema_sha256": _canonical_sha256(body_schema),
        "render_schema": render_schema,
        "projector_sha256": local_config["projector_sha256"],
        "processing_policy_version": local_config["processing_policy_version"],
    }


def _valid_page_evidence(page_evidence: Any) -> bool:
    """確認 Provider request 依賴的頁面 identity、hash 與 render binding。"""
    if (
        not isinstance(page_evidence, dict)
        or page_evidence.get("schema") != "page-evidence/v1"
        or page_evidence.get("status") != "succeeded"
    ):
        return False
    page_number = page_evidence.get("page_number")
    hashes = page_evidence.get("hashes")
    render = page_evidence.get("render")
    if (
        isinstance(page_number, bool)
        or not isinstance(page_number, int)
        or page_number < 1
        or not isinstance(hashes, dict)
        or not isinstance(render, dict)
        or render.get("schema") != "page-render/v1"
    ):
        return False
    source_sha256 = hashes.get("source_sha256")
    native_sha256 = hashes.get("native_sha256")
    render_sha256 = hashes.get("render_sha256")
    if not all(
        _valid_sha256(value)
        for value in (source_sha256, native_sha256, render_sha256)
    ):
        return False
    page_sha256 = hashlib.sha256(
        f"{source_sha256}:{page_number}".encode("ascii")
    ).hexdigest()
    evidence_sha256 = hashlib.sha256(
        f"{source_sha256}:{page_number}:{native_sha256}:{render_sha256}".encode(
            "ascii"
        )
    ).hexdigest()
    return (
        page_evidence.get("material_ref") == f"material:sha256:{source_sha256}"
        and page_evidence.get("page_ref") == f"page:sha256:{page_sha256}"
        and page_evidence.get("evidence_ref")
        == f"evidence:sha256:{evidence_sha256}"
    )

def _outbound_page_evidence(page_evidence: dict[str, Any]) -> dict[str, Any]:
    """只傳送 generation/consumer 真正需要的 Page Evidence 欄位。"""
    fields = (
        "schema",
        "status",
        "material_ref",
        "page_ref",
        "evidence_ref",
        "page_number",
        "hashes",
        "geometry",
        "render",
        "coordinate_transform",
    )
    return {field: deepcopy(page_evidence[field]) for field in fields}


def _page_payload(
    page_evidence: Any,
    render_bytes: Any,
    nearby_pages: Any,
) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    """驗證目標與相鄰 render，並建立 frozen Page Structure payload。"""
    if (
        not _valid_page_evidence(page_evidence)
        or not isinstance(render_bytes, bytes)
        or hashlib.sha256(render_bytes).hexdigest()
        != page_evidence["hashes"]["render_sha256"]
    ):
        return None
    if nearby_pages is None:
        nearby_pages = []
    if not isinstance(nearby_pages, list) or len(nearby_pages) > 2:
        return None
    source_sha256 = page_evidence["hashes"]["source_sha256"]
    page_number = page_evidence["page_number"]
    allowed = {page_number - 1, page_number + 1}
    seen = set()
    checked_nearby = []
    artifact_sha256s = {
        "page_evidence": _canonical_sha256(page_evidence),
        "target_render": page_evidence["hashes"]["render_sha256"],
    }
    for item in nearby_pages:
        if not isinstance(item, dict) or set(item) != {
            "page_evidence",
            "render_bytes",
        }:
            return None
        evidence = item["page_evidence"]
        current_render = item["render_bytes"]
        if (
            not _valid_page_evidence(evidence)
            or evidence["hashes"]["source_sha256"] != source_sha256
            or evidence["page_number"] not in allowed
            or evidence["page_number"] in seen
            or not isinstance(current_render, bytes)
            or hashlib.sha256(current_render).hexdigest()
            != evidence["hashes"]["render_sha256"]
        ):
            return None
        seen.add(evidence["page_number"])
        artifact_sha256s[f"nearby_render_{evidence['page_number']}"] = evidence[
            "hashes"
        ]["render_sha256"]
        checked_nearby.append(
            {
                "page_number": evidence["page_number"],
                "render_sha256": evidence["hashes"]["render_sha256"],
                "render_base64": base64.b64encode(current_render).decode("ascii"),
            }
        )
    checked_nearby.sort(key=lambda item: item["page_number"])
    payload = {
        "target_page_evidence": _outbound_page_evidence(page_evidence),
        "target_render_base64": base64.b64encode(render_bytes).decode("ascii"),
        "nearby_pages": checked_nearby,
    }
    input_binding = {
        "material_ref": page_evidence["material_ref"],
        "source_sha256": source_sha256,
        "context_sha256": _canonical_sha256(payload),
        "artifact_sha256s": artifact_sha256s,
    }
    return payload, input_binding, page_evidence["render"]["schema"]


def _simple_input(
    material_ref: Any,
    payload: dict[str, Any],
    artifact_sha256s: dict[str, Any],
    render_schema: str,
) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    """建立 Concept、content 或 visual 的 frozen input binding。"""
    context_sha256 = _canonical_sha256(payload)
    if context_sha256 is None or any(
        value is None for value in artifact_sha256s.values()
    ):
        return None
    input_binding = {
        "material_ref": material_ref,
        "source_sha256": material_ref.removeprefix("material:sha256:"),
        "context_sha256": context_sha256,
        "artifact_sha256s": artifact_sha256s,
    }
    return payload, input_binding, render_schema


def _generate(
    operation: str,
    prepared: tuple[dict[str, Any], dict[str, Any], str] | None,
    local_config: Any,
    consume: Callable[[Any], tuple[dict[str, Any] | None, str | None]],
    ready_reason: str,
) -> dict[str, Any]:
    """共用 frozen transport；operation-specific consumer 仍由目前 domain code 負責。"""
    if prepared is None:
        return _generation_result("failed", "GENERATION_INPUT_BINDING_INVALID")
    payload, input_binding, render_schema = prepared
    runtime_binding = _runtime_binding(operation, local_config, render_schema)
    request_core = {
        "operation": operation,
        "input_binding": input_binding,
        "runtime_binding": runtime_binding,
        "payload": payload,
    }
    request_sha256 = _canonical_sha256(request_core)
    if request_sha256 is None:
        return _generation_result("failed", "GENERATION_INPUT_BINDING_INVALID")
    request = {
        "schema": REQUEST_SCHEMA,
        "request_id": f"structured-generation-request:sha256:{request_sha256}",
        **request_core,
    }
    request_body = _canonical_bytes(request)
    if request_body is None or len(request_body) > MAX_REQUEST_BYTES:
        return _generation_result("failed", "LOCAL_PROVIDER_AUTH_OR_CONFIG_ERROR")
    cache_key = hashlib.sha256(request_body).hexdigest()
    raw_cache_root = Path(local_config["cache_dir"])
    if (
        ".." in raw_cache_root.parts
        or (os.path.lexists(raw_cache_root) and not raw_cache_root.is_dir())
    ):
        return _generation_result("failed", "GENERATION_CACHE_INVALID")
    path = cache._cache_path(
        local_config, operation, cache_key
    )
    artifact, cache_error, cache_exists = cache._read_cache(
        path,
        cache_key=cache_key,
        operation=operation,
        input_binding=input_binding,
        runtime_binding=runtime_binding,
        consume=consume,
    )
    if cache_error is not None:
        return _generation_result("failed", cache_error)
    if cache_exists:
        return _generation_result(
            "succeeded",
            f"{ready_reason}_CACHE_HIT",
            cache_hit=True,
            artifact=artifact,
        )

    response_output, last_reason, provider_call_count = (
        transport._post_with_retry(
            local_config["endpoint_url"],
            request,
            request_body,
            _canonical_sha256(runtime_binding),
            local_config,
        )
    )
    if response_output is None:
        return _generation_result(
            "failed",
            last_reason,
            provider_call_count=provider_call_count,
        )
    try:
        artifact, reason = consume(response_output)
    except (
        AttributeError,
        IndexError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        artifact, reason = None, "MODEL_OUTPUT_CONSUMER_REJECTED"
    if artifact is None or reason is not None:
        return _generation_result(
            "failed",
            reason or "MODEL_OUTPUT_CONSUMER_REJECTED",
            provider_call_count=provider_call_count,
        )
    record = {
        "schema": cache.CACHE_SCHEMA,
        "cache_key": cache_key,
        "operation": operation,
        "input_binding": input_binding,
        "runtime_binding": runtime_binding,
        "output_sha256": _canonical_sha256(response_output),
        "output": response_output,
    }
    if not cache._write_cache(path, record):
        return _generation_result(
            "failed",
            "GENERATION_CACHE_WRITE_FAILED",
            provider_call_count=provider_call_count,
        )
    return _generation_result(
        "succeeded",
        ready_reason,
        provider_call_count=provider_call_count,
        artifact=artifact,
    )


def generate_development_page_structure(
    page_evidence: Any,
    render_bytes: Any,
    local_config: Any,
    nearby_pages: Any = None,
) -> dict[str, Any]:
    """用 frozen loopback 產生並驗證單頁 Page Structure。"""
    prepared = _page_payload(page_evidence, render_bytes, nearby_pages)

    def consume(output: Any) -> tuple[dict[str, Any] | None, str | None]:
        try:
            structure = finalize_page_structure(output, page_evidence)
        except OverflowError:
            return None, "PAGE_STRUCTURE_INVALID"
        return (
            (structure, None)
            if structure is not None
            else (None, "PAGE_STRUCTURE_INVALID")
        )

    return _generate(
        "page_structure", prepared, local_config, consume, "PAGE_STRUCTURE_READY"
    )


def generate_visual_review(
    page_evidence: Any,
    page_structure: Any,
    alignment: Any,
    render_bytes: Any,
    local_config: Any,
) -> dict[str, Any]:
    """讓 loopback 只裁決既有 needs-review alignment，不改寫內容。"""
    if (
        not _valid_page_evidence(page_evidence)
        or not isinstance(render_bytes, bytes)
        or hashlib.sha256(render_bytes).hexdigest()
        != page_evidence["hashes"]["render_sha256"]
        or not isinstance(alignment, dict)
    ):
        prepared = None
    else:
        expected_identity = {
            "material_ref": page_evidence["material_ref"],
            "page_ref": page_evidence["page_ref"],
            "page_number": page_evidence["page_number"],
        }
        expected_alignment_binding = {
            "evidence_ref": page_evidence["evidence_ref"],
            "page_structure_sha256": _canonical_sha256(page_structure),
            "native_sha256": page_evidence["hashes"]["native_sha256"],
        }
        if (
            alignment.get("identity") != expected_identity
            or alignment.get("input_binding") != expected_alignment_binding
            or alignment.get("processing") != "succeeded"
            or alignment.get("quality") != "needs_review"
            or alignment.get("decision") != "review"
        ):
            prepared = None
        else:
            payload = {
                "page_evidence": _outbound_page_evidence(page_evidence),
                "page_structure": deepcopy(page_structure),
                "alignment": deepcopy(alignment),
                "target_render_base64": base64.b64encode(render_bytes).decode(
                    "ascii"
                ),
            }
            prepared = _simple_input(
                page_evidence["material_ref"],
                payload,
                {
                    "page_evidence": _canonical_sha256(page_evidence),
                    "page_structure": _canonical_sha256(page_structure),
                    "alignment": _canonical_sha256(alignment),
                    "target_render": page_evidence["hashes"]["render_sha256"],
                },
                page_evidence["render"]["schema"],
            )

    def consume(output: Any) -> tuple[dict[str, Any] | None, str | None]:
        if (
            not isinstance(output, dict)
            or set(output) != {"decision"}
            or output["decision"] not in {"retain", "reject"}
        ):
            return None, "VISUAL_ALIGNMENT_OUTPUT_INVALID"
        adjudicated = adjudicate_visual_alignment(alignment, output["decision"])
        return (
            (adjudicated, None)
            if adjudicated is not None
            else (None, "VISUAL_ALIGNMENT_OUTPUT_INVALID")
        )

    return _generate(
        "visual_alignment_adjudication",
        prepared,
        local_config,
        consume,
        "VISUAL_ALIGNMENT_READY",
    )

def generate_concept_candidate(
    context: Any,
    local_config: Any,
    *,
    generation_run_id: Any,
) -> dict[str, Any]:
    """產生 Evidence-bound provisional Concept，通過後固定 retain。"""
    payload = {"concept_context": deepcopy(context)}
    prepared = _simple_input(
        context["identity"]["material_ref"],
        payload,
        {"concept_context": _canonical_sha256(context)},
        "page-render/v1",
    )

    def consume(output: Any) -> tuple[dict[str, Any] | None, str | None]:
        try:
            provisional = build_provisional_concept_candidate(
                context,
                output,
                generation_run_id=generation_run_id,
                generation_identity={
                    "role": "development-structured-generation",
                    "model": local_config["model_id"],
                },
            )
        except (AttributeError, IndexError, KeyError, TypeError):
            return None, "CONCEPT_CONTEXT_INVALID"
        if provisional.get("processing") != "succeeded":
            return None, provisional.get("reason_code", "CONCEPT_BODY_INVALID")
        candidate = adjudicate_concept_candidate(provisional, "retain")
        return (
            (candidate, None)
            if candidate is not None
            else (None, "CONCEPT_CANDIDATE_INVALID")
        )

    return _generate(
        "concept_candidate",
        prepared,
        local_config,
        consume,
        "CONCEPT_CANDIDATE_READY",
    )


def generate_concept_content(
    context: Any,
    local_config: Any,
) -> dict[str, Any]:
    """產生同教材 Concept 摘要與 reviewable relation clues。"""
    payload = {"summary_context": deepcopy(context)}
    prepared = _simple_input(
        context["material_ref"],
        payload,
        {"summary_context": _canonical_sha256(context)},
        "not-applicable",
    )

    def consume(output: Any) -> tuple[dict[str, Any] | None, str | None]:
        try:
            content = build_concept_content(context, output)
        except (AttributeError, IndexError, KeyError, TypeError):
            return None, "CONCEPT_CONTENT_CONTEXT_INVALID"
        if content.get("processing") != "succeeded":
            return None, content.get("reason_code", "CONCEPT_CONTENT_BODY_INVALID")
        return content, None

    return _generate(
        "concept_content",
        prepared,
        local_config,
        consume,
        "CONCEPT_CONTENT_READY",
    )
